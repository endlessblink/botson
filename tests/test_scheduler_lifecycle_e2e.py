"""T-156: Scheduler due-row lifecycle proof.

End-to-end coverage for `bot.handlers.calendar.check_and_send_due_messages`:

- Every executable message_type (plain, poll, event, trivia_round, emoji_puzzle,
  facts_tidbit, facts_spooky, weekly_roundup, weekly_leaderboard, free_games,
  trivia_warmup_rsvp) reaches a visible terminal state (sent/failed/skipped)
  with a message_id stored.
- Negative paths (stale-drop, missing GROUP_ID, unverified topic) mark the row
  failed/skipped without an outbound Telegram call.
- Terminal state is visible via the dashboard endpoints that operators use to
  diagnose the scheduler without log access:
    GET /api/calendar
    GET /api/diagnostics/planner-day

Regression target (from MASTER_PLAN.md T-156): a due row cannot remain silently
`scheduled` after a send attempt, and diagnostics must reveal why.

Mocking strategy: real Database on a temp SQLite file; the Telegram send seams
(`Bot`, `send_message_with_optional_cover`, `send_poll_message`, executable
handlers `start_scheduled_trivia_round`, `start_emoji_night`, `send_free_games`,
`send_scheduled_fact`, `send_weekly_roundup`, `send_weekly_leaderboard`,
`safe_send`) are patched. The dispatcher's own status logic stays exercised.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

from bot.database.db import Database
from bot.handlers import calendar as cal
from bot.utils.scheduling_errors import SkippedActivity
from bot.utils.topic_guard import UnverifiedTopicError


_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _make_frozen_datetime(now: datetime):
    """Build a stand-in for `datetime` whose `.now(tz)` returns a frozen value.

    All other classmethods (`strptime`, `fromisoformat`, arithmetic) delegate
    to the real `datetime`, so callers in the dispatcher behave normally.
    """
    real = datetime

    class _Frozen(real):  # type: ignore[misc]
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    return _Frozen


def _seed_due_row(
    db: Database,
    *,
    message_type: str,
    scheduled_dt: datetime,
    text: str = "test scheduled body",
    target_group: str = "test",
    channel_topic_id: int | None = None,
    poll_options: str | None = None,
    poll_duration: int | None = None,
    auto_pin: bool = False,
) -> int:
    return asyncio.get_event_loop().run_until_complete(  # pragma: no cover
        db.create_scheduled_message(
            text=text,
            message_type=message_type,
            channel_topic_id=channel_topic_id,
            target_group=target_group,
            scheduled_date=scheduled_dt.strftime("%Y-%m-%d"),
            scheduled_time=scheduled_dt.strftime("%H:%M"),
            status="scheduled",
            poll_options=poll_options,
            poll_duration=poll_duration,
            auto_pin=auto_pin,
        )
    )


class SchedulerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """Each test seeds one due row, runs one scheduler tick, asserts terminal state."""

    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(self._tmp.name)
        await self.db.init()
        # Freeze "now" to a deterministic instant; rows are seeded relative to it.
        self.now = datetime(2099, 6, 15, 12, 0, tzinfo=_IL_TZ)
        # Default scheduled_dt is 2 min before now so the row is due and fresh
        # (well within the 30-min stale-drop threshold).
        self.due_dt = self.now - timedelta(minutes=2)
        # Patch env so the worker resolves a non-zero group id without main-group
        # topic-guard rejection. target_group="test" routes to TEST_GROUP_ID.
        self._env_patch = patch.dict(
            os.environ,
            {"BOT_TOKEN": "token-x", "TEST_GROUP_ID": "-1000000002", "GROUP_ID": "-1000000001"},
        )
        self._env_patch.start()
        # Freeze datetime inside the calendar module.
        self._dt_patch = patch.object(cal, "datetime", _make_frozen_datetime(self.now))
        self._dt_patch.start()

    async def asyncTearDown(self):
        self._dt_patch.stop()
        self._env_patch.stop()
        await self.db.close()
        try:
            Path(self._tmp.name).unlink()
        except FileNotFoundError:
            pass

    async def _seed(self, **kwargs) -> int:
        kwargs.setdefault("scheduled_date", self.due_dt.strftime("%Y-%m-%d"))
        kwargs.setdefault("scheduled_time", self.due_dt.strftime("%H:%M"))
        kwargs.setdefault("status", "scheduled")
        kwargs.setdefault("target_group", "test")
        kwargs.setdefault("text", "test body")
        kwargs.setdefault("channel_topic_id", None)
        return await self.db.create_scheduled_message(**kwargs)

    async def _tick(self):
        context = SimpleNamespace(bot_data={"db": self.db}, bot=SimpleNamespace())
        await cal.check_and_send_due_messages(context)

    async def _row(self, msg_id: int) -> dict:
        rows = await self.db.get_scheduled_messages(
            self.due_dt.strftime("%Y-%m-%d"),
            self.due_dt.strftime("%Y-%m-%d"),
            include_cancelled=True,
        )
        for r in rows:
            if int(r["id"]) == int(msg_id):
                return r
        raise AssertionError(f"row {msg_id} not found")

    # ── Happy paths ─────────────────────────────────────────────────────

    async def test_plain_message_marked_sent_with_message_id(self):
        msg_id = await self._seed(message_type="custom", text="plain body")
        sent_obj = SimpleNamespace(message_id=999)
        with patch.object(cal, "send_message_with_optional_cover", new=AsyncMock(return_value=sent_obj)) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 999)
        send_text.assert_awaited_once()

    async def test_poll_message_routes_to_send_poll(self):
        msg_id = await self._seed(
            message_type="poll",
            text="poll question",
            poll_options=json.dumps(["yes", "no", "maybe"]),
            poll_duration=24,
        )
        sent_obj = SimpleNamespace(message_id=777)
        with patch.object(cal, "send_poll_message", new=AsyncMock(return_value=sent_obj)) as send_poll, \
             patch.object(cal, "send_message_with_optional_cover", new=AsyncMock()) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 777)
        send_poll.assert_awaited_once()
        send_text.assert_not_called()

    async def test_poll_without_valid_options_falls_back_to_text(self):
        msg_id = await self._seed(
            message_type="poll",
            text="poll with no options",
            poll_options=json.dumps(["only one"]),
        )
        sent_obj = SimpleNamespace(message_id=778)
        with patch.object(cal, "send_poll_message", new=AsyncMock()) as send_poll, \
             patch.object(cal, "send_message_with_optional_cover", new=AsyncMock(return_value=sent_obj)) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        send_text.assert_awaited_once()
        send_poll.assert_not_called()

    async def test_event_row_creates_event_and_attaches_rsvp(self):
        msg_id = await self._seed(
            message_type="event",
            text="כותרת אירוע\nגוף האירוע",
        )
        sent_obj = SimpleNamespace(message_id=555)
        bot_mock = SimpleNamespace(
            edit_message_reply_markup=AsyncMock(),
            pin_chat_message=AsyncMock(),
        )
        with patch.object(cal, "send_message_with_optional_cover", new=AsyncMock(return_value=sent_obj)) as send_text, \
             patch("telegram.Bot", return_value=bot_mock):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 555)
        send_text.assert_awaited_once()
        bot_mock.edit_message_reply_markup.assert_awaited_once()

    async def test_trivia_round_launches_via_handler(self):
        msg_id = await self._seed(message_type="trivia_round", text="trivia launch")
        with patch.object(cal, "start_scheduled_trivia_round", new=AsyncMock(return_value=42)) as start_trivia, \
             patch.object(cal, "_enforce_warmup_rsvp_gate", new=AsyncMock()) as gate, \
             patch.object(cal, "send_message_with_optional_cover", new=AsyncMock()) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 42)
        start_trivia.assert_awaited_once()
        gate.assert_awaited_once()
        send_text.assert_not_called()

    async def test_emoji_puzzle_starts_session(self):
        msg_id = await self._seed(message_type="emoji_puzzle", text="emoji launch")
        with patch.object(cal, "start_emoji_night", new=AsyncMock(return_value=314)) as start_emoji, \
             patch.object(cal, "emoji_skip_reason", new=AsyncMock(return_value=None)) as skip_reason, \
             patch.object(cal, "_enforce_warmup_rsvp_gate", new=AsyncMock()), \
             patch.object(cal, "send_message_with_optional_cover", new=AsyncMock()) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 314)
        start_emoji.assert_awaited_once()
        skip_reason.assert_awaited_once()
        send_text.assert_not_called()

    async def test_emoji_puzzle_skipped_when_pool_exhausted(self):
        msg_id = await self._seed(message_type="emoji_puzzle", text="emoji exhausted")
        with patch.object(cal, "start_emoji_night", new=AsyncMock(return_value=999)) as start_emoji, \
             patch.object(cal, "emoji_skip_reason", new=AsyncMock(return_value="cooldown")) as skip_reason, \
             patch.object(cal, "_enforce_warmup_rsvp_gate", new=AsyncMock()), \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "skipped")
        self.assertIn("cooldown", row["error_message"] or "")
        start_emoji.assert_not_called()
        skip_reason.assert_awaited_once()

    async def test_facts_tidbit_sends_from_pool(self):
        msg_id = await self._seed(message_type="facts_tidbit", text="fact body")
        with patch.object(cal, "send_scheduled_fact", new=AsyncMock(return_value=True)) as send_fact, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        kwargs = send_fact.await_args.kwargs
        self.assertEqual(kwargs.get("pool"), "tidbit")

    async def test_facts_spooky_failure_marks_failed(self):
        msg_id = await self._seed(message_type="facts_spooky", text="spooky body")
        with patch.object(cal, "send_scheduled_fact", new=AsyncMock(return_value=False)), \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "failed")
        self.assertIn("spooky", row["error_message"] or "")

    async def test_weekly_roundup_row_is_skipped_because_cron_owns_it(self):
        # Regression for 2026-05-23: weekly_roundup/weekly_leaderboard are sent by
        # the APScheduler cron jobs, not the calendar dispatcher. A scheduled_messages
        # row of these types must self-skip so it can't double-fire with the cron.
        msg_id = await self._seed(message_type="weekly_roundup", text="roundup")
        with patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "skipped")
        self.assertIn("cron", row["error_message"] or "")

    async def test_weekly_leaderboard_row_is_skipped_because_cron_owns_it(self):
        msg_id = await self._seed(message_type="weekly_leaderboard", text="leaderboard")
        with patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "skipped")
        self.assertIn("cron", row["error_message"] or "")

    async def test_free_games_row_is_skipped_because_cron_owns_it(self):
        # free_games is cron-owned (bot/scheduler/dispatch_owner.py): its daily cron
        # automates it, so a scheduled_messages row must self-skip rather than
        # double-fire with the cron.
        msg_id = await self._seed(message_type="free_games", text="free games")
        with patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "skipped")
        self.assertIn("cron", row["error_message"] or "")

    async def test_trivia_warmup_rsvp_attaches_button(self):
        msg_id = await self._seed(message_type="trivia_warmup_rsvp", text="warmup")
        sent_obj = SimpleNamespace(message_id=222)
        with patch.object(cal, "safe_send", new=AsyncMock(return_value=sent_obj)) as safe_send_mock, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 222)
        call = safe_send_mock.await_args
        markup = call.kwargs.get("reply_markup")
        self.assertIsNotNone(markup, "warmup RSVP must attach an inline keyboard")

    # ── Negative paths ─────────────────────────────────────────────────

    async def test_stale_drop_marks_failed_without_send(self):
        # Seed 60 min before now (default stale_drop_minutes=30).
        stale_dt = self.now - timedelta(minutes=60)
        msg_id = await self.db.create_scheduled_message(
            text="stale body",
            message_type="custom",
            channel_topic_id=None,
            target_group="test",
            scheduled_date=stale_dt.strftime("%Y-%m-%d"),
            scheduled_time=stale_dt.strftime("%H:%M"),
            status="scheduled",
        )
        with patch.object(cal, "send_message_with_optional_cover", new=AsyncMock()) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        # Use the stale date for lookup
        rows = await self.db.get_scheduled_messages(
            stale_dt.strftime("%Y-%m-%d"), stale_dt.strftime("%Y-%m-%d"),
        )
        row = next(r for r in rows if int(r["id"]) == msg_id)
        self.assertEqual(row["status"], "failed")
        self.assertTrue((row["error_message"] or "").startswith("stale:"))
        send_text.assert_not_called()

    async def test_missing_group_id_for_main_target_marks_failed(self):
        msg_id = await self._seed(target_group="main", message_type="custom", text="no group")
        # Override GROUP_ID to 0 to simulate misconfiguration.
        with patch.dict(os.environ, {"GROUP_ID": "0"}, clear=False), \
             patch.object(cal, "send_message_with_optional_cover", new=AsyncMock()) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "failed")
        self.assertIn("No group ID", row["error_message"] or "")
        send_text.assert_not_called()

    async def test_send_seam_exception_marks_failed_with_reason(self):
        msg_id = await self._seed(message_type="custom", text="explode please")
        boom = AsyncMock(side_effect=RuntimeError("telegram exploded"))
        with patch.object(cal, "send_message_with_optional_cover", new=boom), \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "failed")
        self.assertIn("telegram exploded", row["error_message"] or "")

    async def test_unverified_topic_error_marks_failed_not_silent(self):
        msg_id = await self._seed(
            message_type="custom",
            text="topic-guarded",
            target_group="main",
            channel_topic_id=999_999,  # never verified
        )
        with patch.object(
            cal, "send_message_with_optional_cover",
            new=AsyncMock(side_effect=UnverifiedTopicError("topic 999999 not verified")),
        ), patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "failed")
        self.assertIn("999999", row["error_message"] or "")

    async def test_daily_recurrence_creates_next_day_row_after_send(self):
        """REG-T156-a: prove the scheduler's recurrence branch fires.

        A successful send of a `recurrence='daily'` row must create a fresh
        `scheduled` row dated tomorrow with the same text/type/topic, so the
        bot keeps firing on subsequent days without operator intervention.
        Without this test pinning the branch, a regression there would only
        surface after a missed daily slot in production."""
        msg_id = await self._seed(
            message_type="custom",
            text="daily prompt",
            recurrence="daily",
        )
        sent_obj = SimpleNamespace(message_id=1234)
        with patch.object(cal, "send_message_with_optional_cover",
                          new=AsyncMock(return_value=sent_obj)), \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        # Original row now sent.
        original = await self._row(msg_id)
        self.assertEqual(original["status"], "sent")
        # A new row exists for the next day with the same shape.
        next_dt = self.due_dt + timedelta(days=1)
        next_date = next_dt.strftime("%Y-%m-%d")
        rows = await self.db.get_scheduled_messages(next_date, next_date)
        future_rows = [r for r in rows if int(r["id"]) != msg_id]
        self.assertEqual(
            len(future_rows), 1,
            "daily recurrence must create exactly one next-day row",
        )
        nxt = future_rows[0]
        self.assertEqual(nxt["status"], "scheduled")
        self.assertEqual(nxt["text"], "daily prompt")
        self.assertEqual(nxt["message_type"], "custom")
        self.assertEqual(nxt["recurrence"], "daily")
        self.assertEqual(nxt["created_by"], "recurrence")

    async def test_due_messages_filters_drafts_and_cancelled(self):
        # Draft row is not due even at the same date/time.
        draft_id = await self.db.create_scheduled_message(
            text="draft body",
            message_type="custom",
            channel_topic_id=None,
            target_group="test",
            scheduled_date=self.due_dt.strftime("%Y-%m-%d"),
            scheduled_time=self.due_dt.strftime("%H:%M"),
            status="draft",
        )
        cancelled_id = await self._seed(message_type="custom", text="to cancel")
        await self.db.delete_scheduled_message(cancelled_id)  # sets status='cancelled'
        with patch.object(cal, "send_message_with_optional_cover", new=AsyncMock()) as send_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick()
        # Neither row sent.
        draft = await self._row(draft_id)
        cancelled = await self._row(cancelled_id)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(cancelled["status"], "cancelled")
        send_text.assert_not_called()


class SchedulerLifecycleVisibilityTests(unittest.TestCase):
    """After a tick, /api/calendar and /api/diagnostics/planner-day must reflect
    the terminal state of each row — operators rely on these surfaces instead of
    SSH'ing into SQLite."""

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db_path = self._tmp.name
        self.now = datetime(2099, 6, 15, 12, 0, tzinfo=_IL_TZ)
        self.due_dt = self.now - timedelta(minutes=2)

    def tearDown(self):
        try:
            Path(self.db_path).unlink()
        except FileNotFoundError:
            pass

    async def _seed_and_tick(self):
        db = Database(self.db_path)
        await db.init()
        ids = {}
        # sent
        ids["sent"] = await db.create_scheduled_message(
            text="visible sent", message_type="custom", channel_topic_id=None,
            target_group="test",
            scheduled_date=self.due_dt.strftime("%Y-%m-%d"),
            scheduled_time=self.due_dt.strftime("%H:%M"),
            status="scheduled",
        )
        # failed (will raise in send seam)
        ids["failed"] = await db.create_scheduled_message(
            text="visible failed", message_type="custom", channel_topic_id=None,
            target_group="test",
            scheduled_date=self.due_dt.strftime("%Y-%m-%d"),
            scheduled_time=self.due_dt.strftime("%H:%M"),
            status="scheduled",
        )
        # skipped (free_games returns blackout)
        ids["skipped"] = await db.create_scheduled_message(
            text="visible skipped", message_type="free_games", channel_topic_id=None,
            target_group="test",
            scheduled_date=self.due_dt.strftime("%Y-%m-%d"),
            scheduled_time=self.due_dt.strftime("%H:%M"),
            status="scheduled",
        )

        seam_calls = {"n": 0}

        async def fake_send(*args, **kwargs):
            seam_calls["n"] += 1
            # First call (sent row) succeeds; second (failed row) raises.
            if seam_calls["n"] == 1:
                return SimpleNamespace(message_id=4242)
            raise RuntimeError("simulated send failure")

        with patch.dict(
            os.environ,
            {"BOT_TOKEN": "tok", "TEST_GROUP_ID": "-1000000002", "GROUP_ID": "-1000000001"},
        ), patch.object(cal, "datetime", _make_frozen_datetime(self.now)), \
             patch.object(cal, "send_message_with_optional_cover", side_effect=fake_send), \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            context = SimpleNamespace(bot_data={"db": db}, bot=SimpleNamespace())
            await cal.check_and_send_due_messages(context)
        await db.close()
        return ids

    def test_calendar_and_diagnostics_endpoints_reflect_terminal_state(self):
        from fastapi.testclient import TestClient
        import dashboard.app as dashboard_app

        ids = asyncio.new_event_loop().run_until_complete(self._seed_and_tick()) \
            if False else asyncio.run(self._seed_and_tick())

        # Point the dashboard at the same temp DB for the duration of this test.
        with patch.object(dashboard_app, "DB_PATH", self.db_path):
            with TestClient(dashboard_app.app) as client:
                login = client.post(
                    "/login",
                    data={"password": dashboard_app.DASHBOARD_PASSWORD},
                    follow_redirects=False,
                )
                self.assertEqual(login.status_code, 303)

                date_str = self.due_dt.strftime("%Y-%m-%d")
                cal_resp = client.get(
                    "/api/calendar", params={"start": date_str, "end": date_str}
                )
                self.assertEqual(cal_resp.status_code, 200)
                events = cal_resp.json()
                by_id = {int(ev["id"]): ev for ev in events}
                # sent → diagnosticLabel "נשלח", willSend false (already sent)
                sent_ev = by_id[ids["sent"]]
                sent_ext = sent_ev.get("extendedProps", {})
                self.assertEqual(sent_ext.get("status"), "sent")
                self.assertEqual(sent_ext.get("diagnosticLabel"), "נשלח")
                # failed → diagnosticLabel "נכשל", errorMessage carries the reason
                failed_ev = by_id[ids["failed"]]
                failed_ext = failed_ev.get("extendedProps", {})
                self.assertEqual(failed_ext.get("status"), "failed")
                self.assertEqual(failed_ext.get("diagnosticLabel"), "נכשל")
                self.assertIn("simulated send failure", failed_ext.get("errorMessage") or "")
                # skipped → diagnosticLabel "דולג"
                skipped_ev = by_id[ids["skipped"]]
                skipped_ext = skipped_ev.get("extendedProps", {})
                self.assertEqual(skipped_ext.get("status"), "skipped")
                self.assertEqual(skipped_ext.get("diagnosticLabel"), "דולג")

                diag_resp = client.get(
                    "/api/diagnostics/planner-day", params={"date": date_str}
                )
                self.assertEqual(diag_resp.status_code, 200)
                diag = diag_resp.json()
                statuses = {int(r["id"]): r for r in diag["rows"]}
                self.assertEqual(statuses[ids["sent"]]["status"], "sent")
                self.assertEqual(statuses[ids["sent"]]["sent_message_id"], 4242)
                self.assertEqual(statuses[ids["failed"]]["status"], "failed")
                self.assertIn(
                    "simulated send failure",
                    statuses[ids["failed"]]["error_message"] or "",
                )
                self.assertEqual(statuses[ids["skipped"]]["status"], "skipped")
                by_status = diag["counts"]["by_status"]
                self.assertEqual(by_status.get("sent"), 1)
                self.assertEqual(by_status.get("failed"), 1)
                self.assertEqual(by_status.get("skipped"), 1)


if __name__ == "__main__":
    unittest.main()
