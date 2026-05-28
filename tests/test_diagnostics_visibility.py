"""T-158: Failure visibility diagnostics.

Per-failure-mode coverage proving an operator can explain any row's state
without reading bot logs. Two surfaces under test:

  • GET /api/calendar              — FullCalendar events with
                                     extendedProps.{diagnosticLabel,
                                     diagnosticDetail, errorMessage, status,
                                     willSend}.
  • GET /api/diagnostics/planner-day — per-row diagnostic_reason +
                                      topic_verified + payload + routing.

Each test seeds a row in a known state, hits both endpoints, and asserts
the operator-visible explanation matches the actual cause. Failure modes
covered (per MASTER_PLAN.md T-158):

    draft, scheduled (future), scheduled (past unverified-topic hazard),
    sent, failed (generic), failed (stale-drop), failed (no group ID),
    skipped (with reason), unverified topic, missing routing, invalid
    poll options.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import dashboard.app as dashboard_app
from bot.database.db import Database


_IL_TZ = ZoneInfo("Asia/Jerusalem")


class _DiagnosticsBase(unittest.TestCase):
    """Per-test temp DB; dashboard endpoints reopen against our DB_PATH."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        # Today's date in IL time — used so /api/diagnostics/planner-day
        # default-date logic lines up with our seeded rows.
        self.today = datetime.now(_IL_TZ).date().isoformat()

    def tearDown(self):
        try:
            Path(self.db_path).unlink()
        except FileNotFoundError:
            pass

    async def _seed(self, **kwargs) -> int:
        db = Database(self.db_path)
        await db.init()
        try:
            kwargs.setdefault("text", "diagnostic body")
            kwargs.setdefault("message_type", "custom")
            kwargs.setdefault("channel_topic_id", None)
            kwargs.setdefault("target_group", "test")
            kwargs.setdefault("scheduled_date", self.today)
            kwargs.setdefault("scheduled_time", "23:30")
            kwargs.setdefault("status", "scheduled")
            return await db.create_scheduled_message(**kwargs)
        finally:
            await db.close()

    async def _set_status(self, msg_id: int, *, status: str,
                          error_message: str | None = None,
                          sent_message_id: int | None = None) -> None:
        db = Database(self.db_path)
        await db.init()
        try:
            if status == "sent":
                await db.mark_message_sent(msg_id, sent_message_id or 0)
            elif status == "failed":
                await db.mark_message_failed(msg_id, error_message or "")
            elif status == "skipped":
                await db.mark_message_skipped(msg_id, error_message or "")
            else:
                await db.update_scheduled_message(msg_id, status=status,
                                                   error_message=error_message)
        finally:
            await db.close()

    def _client(self):
        return patch.object(dashboard_app, "DB_PATH", self.db_path), TestClient(dashboard_app.app)

    def _login(self, client: TestClient):
        r = client.post("/login", data={"password": dashboard_app.DASHBOARD_PASSWORD},
                        follow_redirects=False)
        self.assertEqual(r.status_code, 303, r.text)

    def _calendar_event(self, msg_id: int):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.get("/api/calendar", params={"start": self.today, "end": self.today})
            self.assertEqual(r.status_code, 200, r.text)
            for ev in r.json():
                if int(ev["id"]) == int(msg_id):
                    return ev
            self.fail(f"event {msg_id} not in /api/calendar response")

    def _diagnostics_row(self, msg_id: int):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.get("/api/diagnostics/planner-day", params={"date": self.today})
            self.assertEqual(r.status_code, 200, r.text)
            for row in r.json()["rows"]:
                if int(row["id"]) == int(msg_id):
                    return row, r.json()
            self.fail(f"row {msg_id} not in /api/diagnostics/planner-day response")


class CalendarDiagnosticDetailTests(_DiagnosticsBase):
    """Per-status surface in /api/calendar.extendedProps."""

    def test_draft_status_has_distinct_label_and_detail(self):
        msg_id = asyncio.run(self._seed(status="draft"))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertEqual(ext["status"], "draft")
        self.assertEqual(ext["diagnosticLabel"], "טיוטה")
        self.assertIn("טיוטה", ext["diagnosticDetail"])

    def test_scheduled_status_label_signals_future_dispatch(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertEqual(ext["diagnosticLabel"], "יישלח")
        self.assertIn("הסקזולר", ext["diagnosticDetail"])

    def test_scheduled_dispatch_claim_is_hidden_from_operator_error(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(
            msg_id,
            status="scheduled",
            error_message="dispatch_claim:2099-01-01 09:00:00",
        ))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertEqual(ext["diagnosticLabel"], "יישלח")
        self.assertEqual(ext["errorMessage"], "")
        self.assertNotIn("dispatch_claim", ext["diagnosticDetail"])

    def test_sent_status_label_marks_completion(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="sent", sent_message_id=4242))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertEqual(ext["diagnosticLabel"], "נשלח")
        self.assertEqual(ext["status"], "sent")

    def test_failed_status_includes_error_message_in_detail(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="failed",
                                     error_message="telegram exploded"))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertEqual(ext["diagnosticLabel"], "נכשל")
        self.assertIn("telegram exploded", ext["diagnosticDetail"])
        self.assertIn("telegram exploded", ext["errorMessage"])

    def test_failed_stale_drop_renders_as_distinct_lateness_label(self):
        """Stale-drop is operationally distinct from a Telegram failure.
        Pinned by T-158: must surface a separate label so operators don't
        chase a phantom Telegram bug for a row that simply ran too late."""
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="failed",
                                     error_message="stale:60min"))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertEqual(ext["diagnosticLabel"], "איחור")
        self.assertIn("stale:60min", ext["diagnosticDetail"])
        self.assertNotEqual(ext["diagnosticLabel"], "נכשל",
                            "stale-drop must not collapse to generic failed")

    def test_skipped_status_includes_skip_reason_in_detail(self):
        """Pre-T-158, skipped detail dropped the reason. Now the operator
        sees WHY the scheduler skipped (e.g., 'emoji_puzzle: cooldown')."""
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="skipped",
                                     error_message="emoji_puzzle: cooldown"))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertEqual(ext["diagnosticLabel"], "דולג")
        self.assertIn("cooldown", ext["diagnosticDetail"])

    def test_failed_with_no_group_id_surfaces_config_hint(self):
        msg_id = asyncio.run(self._seed(status="scheduled", target_group="main"))
        asyncio.run(self._set_status(msg_id, status="failed",
                                     error_message="No group ID for target 'main'"))
        ev = self._calendar_event(msg_id)
        ext = ev["extendedProps"]
        self.assertIn("No group ID", ext["diagnosticDetail"])


class PlannerDayDiagnosticReasonTests(_DiagnosticsBase):
    """Per-status diagnostic_reason field on /api/diagnostics/planner-day."""

    def test_sent_reason_marks_success(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="sent", sent_message_id=1))
        row, _ = self._diagnostics_row(msg_id)
        self.assertIn("נשלח", row["diagnostic_reason"])

    def test_failed_generic_reason_includes_error(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="failed",
                                     error_message="boom"))
        row, _ = self._diagnostics_row(msg_id)
        self.assertIn("נכשל", row["diagnostic_reason"])
        self.assertIn("boom", row["diagnostic_reason"])

    def test_failed_stale_reason_uses_distinct_lateness_phrasing(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="failed",
                                     error_message="stale:90min"))
        row, _ = self._diagnostics_row(msg_id)
        self.assertIn("איחור", row["diagnostic_reason"])
        self.assertIn("stale:90min", row["diagnostic_reason"])

    def test_failed_no_group_id_reason_explains_config(self):
        msg_id = asyncio.run(self._seed(status="scheduled", target_group="main"))
        asyncio.run(self._set_status(msg_id, status="failed",
                                     error_message="No group ID for target 'main'"))
        row, _ = self._diagnostics_row(msg_id)
        self.assertIn("GROUP_ID", row["diagnostic_reason"])

    def test_skipped_reason_includes_skip_explanation(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(msg_id, status="skipped",
                                     error_message="blackout date"))
        row, _ = self._diagnostics_row(msg_id)
        self.assertIn("דולג", row["diagnostic_reason"])
        self.assertIn("blackout date", row["diagnostic_reason"])

    def test_draft_reason_explains_no_auto_send(self):
        msg_id = asyncio.run(self._seed(status="draft"))
        row, _ = self._diagnostics_row(msg_id)
        self.assertIn("טיוטה", row["diagnostic_reason"])

    def test_scheduled_dispatch_claim_is_hidden_from_day_diagnostics(self):
        msg_id = asyncio.run(self._seed(status="scheduled"))
        asyncio.run(self._set_status(
            msg_id,
            status="scheduled",
            error_message="dispatch_claim:2099-01-01 09:00:00",
        ))
        row, _ = self._diagnostics_row(msg_id)
        self.assertNotIn("dispatch_claim", row["diagnostic_reason"])
        self.assertEqual(row["error_message"], "dispatch_claim:2099-01-01 09:00:00")

    def test_scheduled_with_unverified_main_topic_warns(self):
        """Main-group target + topic not in verified_forum_topics → the
        topic guard will reject at send time. Operator should see the
        hazard at scheduled-state, not after the failure."""
        msg_id = asyncio.run(self._seed(
            status="scheduled", target_group="main",
            channel_topic_id=987654,  # arbitrary unverified topic
        ))
        row, _ = self._diagnostics_row(msg_id)
        reason = row["diagnostic_reason"]
        self.assertIn("987654", reason)
        self.assertIn("לא מאומת", reason)
        self.assertFalse(row["topic_verified"])

    def test_scheduled_executable_with_no_routing_warns(self):
        """Executable type (e.g. trivia_round) with no row in
        bot_message_routing means the handler will dispatch with topic=None.
        Surface that BEFORE the row fires.

        Implementation note: Database.init seeds default routing for every
        handler via INSERT OR IGNORE, so we must delete the row AFTER the
        TestClient's startup hook has run (otherwise startup re-inserts it).
        """
        msg_id = asyncio.run(self._seed(
            status="scheduled", message_type="trivia_round",
            target_group="main",
        ))
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            # Now startup is done; safe to drop routing.
            async def _drop_routing():
                db = Database(self.db_path)
                await db.init()
                try:
                    await db._db.execute(
                        "DELETE FROM bot_message_routing WHERE handler = 'trivia_round'"
                    )
                    await db._db.commit()
                finally:
                    await db.close()
            asyncio.run(_drop_routing())
            r = client.get("/api/diagnostics/planner-day", params={"date": self.today})
            self.assertEqual(r.status_code, 200, r.text)
            response = r.json()
            row = next(rr for rr in response["rows"] if int(rr["id"]) == msg_id)
        self.assertNotIn("trivia_round", response["routing"],
                         "seed precondition: trivia_round routing should be deleted")
        self.assertIn("trivia_round", row["diagnostic_reason"])
        self.assertIn("ניתוב", row["diagnostic_reason"])

    def test_game_row_exposes_marker_wide_rsvp_diagnostics(self):
        marker = "diag-rsvp-marker"

        async def _seed_game_bundle():
            db = Database(self.db_path)
            await db.init()
            try:
                warmup_id = await db.create_scheduled_message(
                    text="warmup",
                    message_type="trivia_warmup_rsvp",
                    channel_topic_id=4037,
                    target_group="test",
                    scheduled_date=self.today,
                    scheduled_time="20:00",
                    poll_options=json.dumps({"warmup_marker": marker}),
                    status="scheduled",
                )
                await db.mark_message_sent(warmup_id, 7001)
                await db.add_trivia_interest_response(warmup_id, 101, "Lotem")
                await db.add_trivia_interest_response(warmup_id, 202, "Refeli")
                game_id = await db.create_scheduled_message(
                    text="game",
                    message_type="trivia_round",
                    channel_topic_id=4037,
                    target_group="test",
                    scheduled_date=self.today,
                    scheduled_time="21:00",
                    poll_options=json.dumps({
                        "warmup_marker": marker,
                        "min_ready_players": 3,
                    }),
                    status="scheduled",
                )
                return game_id
            finally:
                await db.close()

        game_id = asyncio.run(_seed_game_bundle())
        row, _ = self._diagnostics_row(game_id)

        self.assertEqual(row["game_rsvp"]["warmup_marker"], marker)
        self.assertEqual(row["game_rsvp"]["sent_warmup_count"], 1)
        self.assertEqual(row["game_rsvp"]["marker_rsvp_count"], 2)
        self.assertEqual(row["game_rsvp"]["min_ready_players"], 3)
        self.assertIn("2/3", row["diagnostic_reason"])

    def test_game_row_warns_when_threshold_has_no_sent_warmup(self):
        marker = "diag-no-sent-warmup"

        async def _seed_game_bundle():
            db = Database(self.db_path)
            await db.init()
            try:
                await db.create_scheduled_message(
                    text="warmup",
                    message_type="trivia_warmup_rsvp",
                    channel_topic_id=4037,
                    target_group="test",
                    scheduled_date=self.today,
                    scheduled_time="20:00",
                    poll_options=json.dumps({"warmup_marker": marker}),
                    status="scheduled",
                )
                return await db.create_scheduled_message(
                    text="game",
                    message_type="emoji_puzzle",
                    channel_topic_id=4037,
                    target_group="test",
                    scheduled_date=self.today,
                    scheduled_time="21:00",
                    poll_options=json.dumps({
                        "warmup_marker": marker,
                        "min_ready_players": 2,
                    }),
                    status="scheduled",
                )
            finally:
                await db.close()

        game_id = asyncio.run(_seed_game_bundle())
        row, _ = self._diagnostics_row(game_id)

        self.assertEqual(row["game_rsvp"]["warmup_count"], 1)
        self.assertEqual(row["game_rsvp"]["sent_warmup_count"], 0)
        self.assertIn("אין הכרזת RSVP", row["diagnostic_reason"])

    def test_invalid_poll_options_surface_as_payload_or_options_field(self):
        """A poll row with malformed poll_options must be visible enough for
        an operator to spot the JSON breakage. diagnostic surface today: the
        endpoint sets `payload` to None and `poll_options` to None when the
        JSON is unparseable; the row stays scheduled."""
        bad_json = "{not valid json"
        msg_id = asyncio.run(self._seed(
            status="scheduled", message_type="poll",
            poll_options=bad_json,
        ))
        row, _ = self._diagnostics_row(msg_id)
        # Neither parsed shape carries data — operator sees `payload` empty
        # AND `poll_options` None, signaling the row's payload is unreadable.
        # (T-158 doesn't add a new field for this; it pins the existing
        # surface as adequate.)
        self.assertFalse(row["payload"],
                         f"malformed JSON should produce empty payload, got {row['payload']!r}")
        self.assertIsNone(row["poll_options"])

    def test_routing_table_is_exposed_at_response_top_level(self):
        """The routing map is essential context for explaining
        why an executable row's topic landed where it did. The endpoint
        must expose it so operators don't have to query the DB."""
        msg_id = asyncio.run(self._seed(status="scheduled"))
        _, response = self._diagnostics_row(msg_id)
        self.assertIn("routing", response)
        self.assertIsInstance(response["routing"], dict)


if __name__ == "__main__":
    unittest.main()
