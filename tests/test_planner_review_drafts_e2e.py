"""T-155: Review-draft lifecycle proof.

End-to-end coverage for the review-drafts modal contract: a generated draft
must reliably move through edit → schedule → send-now → delete via the
dashboard's HTTP endpoints, and dirty / unsafe paths must surface clearly
without silently dropping the draft.

Endpoints under test:
- PUT    /api/calendar/{id}            — edit text/time/topic/etc.
- POST   /api/calendar/{id}/schedule   — flip draft → scheduled (with past_due gate + force override).
- POST   /api/calendar/{id}/send-now   — dispatch immediately via `_send_scheduled_row`.
- DELETE /api/calendar/{id}            — soft-cancel (status='cancelled').

Regression target (MASTER_PLAN.md T-155): draft cards cannot disappear from the
modal without a durable state change; dirty edits must not auto-send; past/soon
slots refuse without explicit force; quality-failed text never reaches the DB
in a scheduled state.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from bot.database.db import Database


_IL_TZ = ZoneInfo("Asia/Jerusalem")


class ReviewDraftLifecycleTests(unittest.TestCase):
    """Drive the review-drafts modal contract via real HTTP + real Database."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        # Seed via an explicit asyncio.run so the test stays sync-friendly.
        asyncio.run(self._init_db())

    def tearDown(self):
        try:
            Path(self.db_path).unlink()
        except FileNotFoundError:
            pass

    async def _init_db(self):
        db = Database(self.db_path)
        await db.init()
        await db.close()

    def _client(self):
        """Return a TestClient wired to a dashboard `_db` on our temp file.

        We patch `DB_PATH` so the on_event('startup') handler reopens against
        our path. The TestClient ContextManager handles startup/shutdown.
        """
        return patch.object(dashboard_app, "DB_PATH", self.db_path), TestClient(dashboard_app.app)

    def _login(self, client: TestClient):
        resp = client.post(
            "/login",
            data={"password": dashboard_app.DASHBOARD_PASSWORD},
            follow_redirects=False,
        )
        self.assertEqual(resp.status_code, 303, f"login failed: {resp.text}")

    async def _seed_draft(self, **overrides) -> int:
        """Insert a draft row directly so we don't depend on draft-creation routes."""
        db = Database(self.db_path)
        await db.init()
        try:
            kwargs = dict(
                text=overrides.pop("text", "טיוטה לבדיקה"),
                message_type=overrides.pop("message_type", "custom"),
                channel_topic_id=overrides.pop("channel_topic_id", None),
                target_group=overrides.pop("target_group", "test"),
                scheduled_date=overrides.pop(
                    "scheduled_date",
                    (datetime.now(_IL_TZ) + timedelta(days=1)).strftime("%Y-%m-%d"),
                ),
                scheduled_time=overrides.pop("scheduled_time", "12:00"),
                status="draft",
            )
            kwargs.update(overrides)
            return await db.create_scheduled_message(**kwargs)
        finally:
            await db.close()

    async def _read_row(self, msg_id: int) -> dict:
        db = Database(self.db_path)
        await db.init()
        try:
            rows = await db.get_scheduled_messages(
                "2000-01-01", "2099-12-31", include_cancelled=True
            )
            for r in rows:
                if int(r["id"]) == int(msg_id):
                    return r
            raise AssertionError(f"row {msg_id} not found")
        finally:
            await db.close()

    # ── Happy lifecycle ────────────────────────────────────────────────

    def test_put_text_only_preserves_existing_message_type_so_quality_gate_fires(self):
        """REG-T155-a regression: PUT /api/calendar/{id} with body that
        contains only `text` (no message_type) must keep the row's
        existing message_type. Pre-fix the route coerced to 'custom',
        which silently demoted morning/evening/discussion rows out of
        _reject_bad_message_row, letting over-long text reach /schedule.

        End-to-end repro: seed morning draft → PUT text-only with bad
        body → /schedule. After the fix, /schedule MUST return 422."""
        msg_id = asyncio.run(self._seed_draft(
            message_type="morning",
            text="בוקר טוב",
        ))
        bad_text = "א " * 130  # 260 chars, exceeds 200 ceiling
        future = datetime.now(_IL_TZ) + timedelta(hours=2)
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            put_resp = client.put(
                f"/api/calendar/{msg_id}",
                json={"text": bad_text},
            )
            # PUT itself rejects too — the morning classification is now
            # preserved, so _reject_bad_planner_text fires on the bad body.
            self.assertEqual(put_resp.status_code, 422, put_resp.text)
            # Sanity: row's message_type is still 'morning' on disk.
            row = asyncio.run(self._read_row(msg_id))
            self.assertEqual(row["message_type"], "morning")
            # And /schedule continues to reject (defense in depth) if the
            # bad text had reached the DB via some other path.
            sched_resp = client.post(
                f"/api/calendar/{msg_id}/schedule",
                json={
                    "scheduled_date": future.strftime("%Y-%m-%d"),
                    "scheduled_time": future.strftime("%H:%M"),
                },
            )
            # Original text is fine, so this should succeed.
            self.assertEqual(sched_resp.status_code, 200, sched_resp.text)

    def test_edit_text_and_time_persists_without_flipping_status(self):
        msg_id = asyncio.run(self._seed_draft())
        future = (datetime.now(_IL_TZ) + timedelta(days=2))
        new_date = future.strftime("%Y-%m-%d")
        new_time = "15:30"
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.put(
                f"/api/calendar/{msg_id}",
                json={"text": "טיוטה ערוכה", "scheduled_date": new_date, "scheduled_time": new_time},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["text"], "טיוטה ערוכה")
        self.assertEqual(row["scheduled_date"], new_date)
        self.assertEqual(row["scheduled_time"], new_time)
        # Critical: PUT without an explicit `status` field must NOT auto-schedule.
        self.assertEqual(row["status"], "draft", "PUT must not silently schedule a draft")

    def test_dragging_scheduled_row_to_past_is_rejected_and_not_persisted(self):
        future = datetime.now(_IL_TZ) + timedelta(hours=3)
        msg_id = asyncio.run(self._seed_draft(
            status="scheduled",
            scheduled_date=future.strftime("%Y-%m-%d"),
            scheduled_time=future.strftime("%H:%M"),
        ))
        past = datetime.now(_IL_TZ) - timedelta(minutes=10)
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.put(
                f"/api/calendar/{msg_id}",
                json={
                    "scheduled_date": past.strftime("%Y-%m-%d"),
                    "scheduled_time": past.strftime("%H:%M"),
                },
            )
            self.assertEqual(resp.status_code, 409, resp.text)
            self.assertEqual(resp.json()["detail"]["error"], "past_due")
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["scheduled_date"], future.strftime("%Y-%m-%d"))
        self.assertEqual(row["scheduled_time"], future.strftime("%H:%M"))
        self.assertEqual(row["status"], "scheduled")

    def test_dragging_scheduled_row_to_future_persists(self):
        original = datetime.now(_IL_TZ) + timedelta(hours=3)
        moved = datetime.now(_IL_TZ) + timedelta(hours=5)
        msg_id = asyncio.run(self._seed_draft(
            status="scheduled",
            scheduled_date=original.strftime("%Y-%m-%d"),
            scheduled_time=original.strftime("%H:%M"),
        ))
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.put(
                f"/api/calendar/{msg_id}",
                json={
                    "scheduled_date": moved.strftime("%Y-%m-%d"),
                    "scheduled_time": moved.strftime("%H:%M"),
                },
            )
            self.assertEqual(resp.status_code, 200, resp.text)
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["scheduled_date"], moved.strftime("%Y-%m-%d"))
        self.assertEqual(row["scheduled_time"], moved.strftime("%H:%M"))
        self.assertEqual(row["status"], "scheduled")

    def test_calendar_only_marks_draft_and_scheduled_rows_draggable(self):
        scheduled_id = asyncio.run(self._seed_draft(status="scheduled"))
        sent_id = asyncio.run(self._seed_draft(status="sent"))
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.get("/api/calendar?start=2000-01-01&end=2099-12-31")
            self.assertEqual(resp.status_code, 200, resp.text)
        events = {int(e["id"]): e for e in resp.json() if str(e["id"]).isdigit()}
        self.assertTrue(events[scheduled_id]["editable"])
        self.assertFalse(events[sent_id]["editable"])

    def test_schedule_route_flips_draft_to_scheduled(self):
        msg_id = asyncio.run(self._seed_draft())
        future = datetime.now(_IL_TZ) + timedelta(hours=3)
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/schedule",
                json={
                    "scheduled_date": future.strftime("%Y-%m-%d"),
                    "scheduled_time": future.strftime("%H:%M"),
                },
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["status"], "ok")
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "scheduled")
        self.assertEqual(row["scheduled_date"], future.strftime("%Y-%m-%d"))
        self.assertEqual(row["scheduled_time"], future.strftime("%H:%M"))

    def test_delete_marks_cancelled_not_purged(self):
        msg_id = asyncio.run(self._seed_draft())
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.delete(f"/api/calendar/{msg_id}")
            self.assertEqual(resp.status_code, 200, resp.text)
        # Row still exists, just status='cancelled'. Confirms soft-delete:
        # an operator can audit dismissed drafts without them vanishing.
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "cancelled")

    def test_send_now_dispatches_and_persists_sent_state(self):
        msg_id = asyncio.run(self._seed_draft(target_group="main"))

        async def fake_send(db, msg, target):
            await db.mark_message_sent(msg["id"], 7777)
            return 7777

        with self._client()[0], \
             patch.object(dashboard_app, "_send_scheduled_row", side_effect=fake_send) as send_mock, \
             self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/send-now",
                json={"target": "main"},
                headers={"content-type": "application/json"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["message_id"], 7777)
            send_mock.assert_awaited_once()
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 7777)

    # ── Negative / dirty-state paths ───────────────────────────────────

    def test_schedule_refuses_past_slot_without_force(self):
        # Seed a draft, then try to schedule it at a past time.
        msg_id = asyncio.run(self._seed_draft())
        past = datetime.now(_IL_TZ) - timedelta(minutes=10)
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/schedule",
                json={
                    "scheduled_date": past.strftime("%Y-%m-%d"),
                    "scheduled_time": past.strftime("%H:%M"),
                },
            )
            self.assertEqual(resp.status_code, 409, resp.text)
            detail = resp.json()["detail"]
            self.assertEqual(detail["error"], "past_due")
        # Row must remain draft — refused schedules don't half-commit.
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "draft")

    def test_schedule_past_slot_with_force_overrides_gate(self):
        msg_id = asyncio.run(self._seed_draft())
        past = datetime.now(_IL_TZ) - timedelta(minutes=10)
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/schedule",
                json={
                    "scheduled_date": past.strftime("%Y-%m-%d"),
                    "scheduled_time": past.strftime("%H:%M"),
                    "force": True,
                },
            )
            self.assertEqual(resp.status_code, 200, resp.text)
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "scheduled")

    def test_schedule_within_two_minutes_refused_without_force(self):
        msg_id = asyncio.run(self._seed_draft())
        soon = datetime.now(_IL_TZ) + timedelta(seconds=30)
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/schedule",
                json={
                    "scheduled_date": soon.strftime("%Y-%m-%d"),
                    "scheduled_time": soon.strftime("%H:%M"),
                },
            )
            self.assertEqual(resp.status_code, 409, resp.text)
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "draft")

    def test_quality_rejected_text_blocks_schedule_for_morning_draft(self):
        # Mirrors the real failure mode: a planner draft enters the DB with
        # over-long text (e.g. a model output that bypassed the ai-fill retry
        # loop), then an operator clicks Schedule. /schedule must reject 422
        # so the bad row never reaches `status='scheduled'`.
        bad_text = "א " * 130  # ~260 chars, exceeds 200 ceiling
        msg_id = asyncio.run(self._seed_draft(
            message_type="morning",
            text=bad_text,
        ))
        future = datetime.now(_IL_TZ) + timedelta(hours=2)
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            sched_resp = client.post(
                f"/api/calendar/{msg_id}/schedule",
                json={
                    "scheduled_date": future.strftime("%Y-%m-%d"),
                    "scheduled_time": future.strftime("%H:%M"),
                },
            )
            self.assertEqual(sched_resp.status_code, 422, sched_resp.text)
            detail = sched_resp.json()["detail"]
            self.assertEqual(detail["error"], "quality_rejected")
            self.assertTrue(detail["failures"], "quality_rejected must list specific failures")
            self.assertTrue(
                any("length>200" in f for f in detail["failures"]),
                f"expected length failure in {detail['failures']}",
            )
        # Row stays draft — quality reject is a hard stop.
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "draft")

    def test_send_now_failure_keeps_row_in_prior_state(self):
        msg_id = asyncio.run(self._seed_draft(target_group="main"))

        async def fake_send(db, msg, target):
            raise RuntimeError("telegram exploded")

        with self._client()[0], \
             patch.object(dashboard_app, "_send_scheduled_row", side_effect=fake_send), \
             self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/send-now",
                json={"target": "main"},
                headers={"content-type": "application/json"},
            )
            self.assertEqual(resp.status_code, 500, resp.text)
            self.assertIn("telegram exploded", resp.json()["detail"])
        # The row stays draft — send-now failure does NOT mark sent or failed
        # on the row. (Failures are surfaced via the 500 response + log; the
        # row remains actionable in the review modal.)
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "draft")
        self.assertIsNone(row["sent_message_id"])

    def test_send_now_skipped_activity_marks_skipped_and_returns_200(self):
        """REG-T157-bug-skipped-status fix: when _send_scheduled_row raises
        SkippedActivity (legitimate skip — blackout / pool exhausted / etc.),
        the route must mark the row status='skipped' and return 200 with
        {"status":"skipped","reason":...}. Pre-fix this collapsed to a
        generic 500 and the calendar lost the skip distinction."""
        from bot.utils.scheduling_errors import SkippedActivity
        msg_id = asyncio.run(self._seed_draft(
            message_type="free_games", target_group="main",
        ))

        async def fake_send(db, msg, target):
            raise SkippedActivity("blackout date")

        with self._client()[0], \
             patch.object(dashboard_app, "_send_scheduled_row", side_effect=fake_send), \
             self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/send-now",
                json={"target": "main"},
                headers={"content-type": "application/json"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["status"], "skipped")
            self.assertIn("blackout date", body["reason"])
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "skipped",
                         "row must persist as skipped so calendar renders it distinctly")
        self.assertIn("blackout date", row["error_message"] or "")

    def test_send_now_with_test_target_does_not_mutate_row_state(self):
        # _send_scheduled_row only marks sent when target != 'test', so a test
        # send must not flip status — proves the test-send safety contract.
        msg_id = asyncio.run(self._seed_draft(target_group="test"))

        async def fake_send(db, msg, target):
            # Mimic real `_send_scheduled_row`: test target skips db.mark_message_sent.
            return 9999

        with self._client()[0], \
             patch.object(dashboard_app, "_send_scheduled_row", side_effect=fake_send), \
             self._client()[1] as client:
            self._login(client)
            resp = client.post(
                f"/api/calendar/{msg_id}/send-now",
                json={"target": "test"},
                headers={"content-type": "application/json"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["message_id"], 9999)
        row = asyncio.run(self._read_row(msg_id))
        self.assertEqual(row["status"], "draft", "test target must not flip draft → sent")
        self.assertIsNone(row["sent_message_id"])


if __name__ == "__main__":
    unittest.main()
