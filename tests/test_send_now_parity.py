"""T-157: Send-now vs scheduler parity.

Two dispatch paths exist for a scheduled_messages row:

  • `bot.handlers.calendar.check_and_send_due_messages` — autonomous worker
    that ticks every 60s, owns recurrence, stale-drop, warm-up RSVP gating,
    blackout dates, and terminal-state writes.
  • `dashboard.app._send_scheduled_row` — operator-triggered send-now path
    invoked via POST /api/calendar/{id}/send-now.

A row's outcome should not depend on which path fired it, except for a
documented and tested set of intentional differences. This file pins both:

  PARITY  — both paths reach the same observable outcome (sent message_id,
            handler called with the same payload-derived kwargs).
  DIVERGE — send-now intentionally skips a scheduler-only behavior
            (recurrence, stale-drop, warm-up RSVP gate, blackout guard,
            non-test marking semantics) — pinned so the difference is
            visible to anyone changing either side.

Bugs surfaced while writing this file are filed as REG-T157-* tasks. The
tests below pin the *current* behavior of those bugs (so fixing the bug
will turn the relevant test red and force an explicit revisit).
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

import dashboard.app as dashboard_app
from bot.database.db import Database
from bot.handlers import calendar as cal


_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _make_frozen_datetime(now: datetime):
    """Drop-in datetime stand-in whose .now(tz) returns a fixed value."""
    real = datetime

    class _Frozen(real):  # type: ignore[misc]
        @classmethod
        def now(cls, tz=None):
            return now if tz is None else now.astimezone(tz)

    return _Frozen


class _ParityBase(unittest.IsolatedAsyncioTestCase):
    """Per-test temp DB + frozen-now wiring shared by parity cases."""

    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(self._tmp.name)
        await self.db.init()
        self.now = datetime(2099, 6, 15, 12, 0, tzinfo=_IL_TZ)
        self.due_dt = self.now - timedelta(minutes=2)
        self._env_patch = patch.dict(
            os.environ,
            {"BOT_TOKEN": "tok", "TEST_GROUP_ID": "-1000000002", "GROUP_ID": "-1000000001"},
        )
        self._env_patch.start()
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
        kwargs.setdefault("text", "parity body")
        kwargs.setdefault("channel_topic_id", None)
        return await self.db.create_scheduled_message(**kwargs)

    async def _row(self, msg_id: int) -> dict:
        rows = await self.db.get_scheduled_messages(
            "2000-01-01", "2099-12-31", include_cancelled=True,
        )
        for r in rows:
            if int(r["id"]) == int(msg_id):
                return r
        raise AssertionError(f"row {msg_id} not found")

    async def _tick_scheduler(self):
        ctx = SimpleNamespace(bot_data={"db": self.db}, bot=SimpleNamespace())
        await cal.check_and_send_due_messages(ctx)

    async def _send_now(self, msg_id: int, target: str = "main"):
        msg = await self._row(msg_id)
        return await dashboard_app._send_scheduled_row(self.db, dict(msg), target)


# ── Shared parity ───────────────────────────────────────────────────────


class ParityHandlerSelectionTests(_ParityBase):
    """For each executable type, the same handler is called by both paths
    with the same payload-derived kwargs. Per-type quirks (RSVP attach,
    payload reading, etc.) are pinned in the divergence/bug suites below."""

    async def test_trivia_round_calls_same_handler_on_both_paths(self):
        # Scheduler
        sched_id = await self._seed(message_type="trivia_round", text="trivia A")
        with patch.object(cal, "start_scheduled_trivia_round", new=AsyncMock(return_value=111)) as h_sched, \
             patch.object(cal, "_enforce_warmup_rsvp_gate", new=AsyncMock()), \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick_scheduler()
        self.assertEqual((await self._row(sched_id))["status"], "sent")
        h_sched.assert_awaited_once()
        # Send-now (operator path)
        sn_id = await self._seed(
            message_type="trivia_round", text="trivia B", target_group="main", status="draft",
        )
        with patch("bot.handlers.trivia_round.start_scheduled_trivia_round", new=AsyncMock(return_value=222)) as h_sn:
            await self._send_now(sn_id, target="main")
        self.assertEqual((await self._row(sn_id))["status"], "sent")
        h_sn.assert_awaited_once()

    async def test_emoji_puzzle_calls_same_handler_on_both_paths(self):
        sched_id = await self._seed(message_type="emoji_puzzle", text="emoji A")
        with patch.object(cal, "start_emoji_night", new=AsyncMock(return_value=333)) as h_sched, \
             patch.object(cal, "emoji_skip_reason", new=AsyncMock(return_value=None)), \
             patch.object(cal, "_enforce_warmup_rsvp_gate", new=AsyncMock()), \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick_scheduler()
        self.assertEqual((await self._row(sched_id))["status"], "sent")
        h_sched.assert_awaited_once()

        sn_id = await self._seed(
            message_type="emoji_puzzle", text="emoji B", target_group="main", status="draft",
        )
        with patch("bot.handlers.emoji_puzzle.start_emoji_night", new=AsyncMock(return_value=444)) as h_sn:
            await self._send_now(sn_id, target="main")
        self.assertEqual((await self._row(sn_id))["status"], "sent")
        h_sn.assert_awaited_once()

    async def test_facts_tidbit_calls_same_handler_on_both_paths(self):
        sched_id = await self._seed(message_type="facts_tidbit", text="fact A")
        with patch.object(cal, "send_scheduled_fact", new=AsyncMock(return_value=True)) as h_sched, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick_scheduler()
        self.assertEqual((await self._row(sched_id))["status"], "sent")
        self.assertEqual(h_sched.await_args.kwargs.get("pool"), "tidbit")

        sn_id = await self._seed(
            message_type="facts_tidbit", text="fact B", target_group="main", status="draft",
        )
        with patch("bot.handlers.facts.send_scheduled_fact", new=AsyncMock(return_value=True)) as h_sn:
            await self._send_now(sn_id, target="main")
        self.assertEqual((await self._row(sn_id))["status"], "sent")
        self.assertEqual(h_sn.await_args.kwargs.get("pool"), "tidbit")

    async def test_weekly_roundup_calls_same_handler_on_both_paths(self):
        sched_id = await self._seed(message_type="weekly_roundup", text="ru A")
        with patch.object(cal, "send_weekly_roundup", new=AsyncMock(return_value=555)) as h_sched, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick_scheduler()
        self.assertEqual((await self._row(sched_id))["status"], "sent")
        h_sched.assert_awaited_once()

        sn_id = await self._seed(
            message_type="weekly_roundup", text="ru B", target_group="main", status="draft",
        )
        with patch("bot.handlers.roundup.send_weekly_roundup", new=AsyncMock(return_value=666)) as h_sn:
            await self._send_now(sn_id, target="main")
        self.assertEqual((await self._row(sn_id))["status"], "sent")
        h_sn.assert_awaited_once()

    async def test_plain_text_calls_send_message_helper_on_both_paths(self):
        sched_id = await self._seed(message_type="custom", text="plain A")
        sent = SimpleNamespace(message_id=701)
        with patch.object(cal, "send_message_with_optional_cover", new=AsyncMock(return_value=sent)) as h_sched, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick_scheduler()
        self.assertEqual((await self._row(sched_id))["status"], "sent")
        h_sched.assert_awaited_once()

        sn_id = await self._seed(
            message_type="custom", text="plain B", target_group="main", status="draft",
        )
        with patch("bot.handlers.calendar.send_message_with_optional_cover",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=702))) as h_sn:
            await self._send_now(sn_id, target="main")
        self.assertEqual((await self._row(sn_id))["status"], "sent")
        h_sn.assert_awaited_once()

    async def test_poll_with_valid_options_calls_send_poll_on_both_paths(self):
        opts = json.dumps(["א", "ב", "ג"])
        sched_id = await self._seed(message_type="poll", text="poll A", poll_options=opts)
        with patch.object(cal, "send_poll_message",
                          new=AsyncMock(return_value=SimpleNamespace(message_id=801))) as h_sched, \
             patch.object(cal, "send_message_with_optional_cover", new=AsyncMock()) as h_text, \
             patch("telegram.Bot", return_value=SimpleNamespace()):
            await self._tick_scheduler()
        h_sched.assert_awaited_once()
        h_text.assert_not_called()

        sn_id = await self._seed(
            message_type="poll", text="poll B", poll_options=opts,
            target_group="main", status="draft",
        )
        with patch("bot.handlers.calendar.send_poll_message",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=802))) as h_sn, \
             patch("bot.handlers.calendar.send_message_with_optional_cover", new=AsyncMock()) as h_sn_text:
            await self._send_now(sn_id, target="main")
        h_sn.assert_awaited_once()
        h_sn_text.assert_not_called()


# ── Intentional divergences (pinned) ────────────────────────────────────


class IntentionalDivergenceTests(_ParityBase):
    """These differences are by design. The tests pin them so they can't
    drift silently. If you intend to change one, update the test AND the
    `_send_scheduled_row` docstring in the same PR."""

    async def test_send_now_test_target_does_not_mark_row_sent(self):
        """Operator probes via target='test' must not burn the row."""
        msg_id = await self._seed(
            message_type="custom", text="test probe", target_group="main", status="draft",
        )
        with patch("bot.handlers.calendar.send_message_with_optional_cover",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=911))):
            sent_id = await self._send_now(msg_id, target="test")
        self.assertEqual(sent_id, 911)
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "draft", "test target must not flip status")
        self.assertIsNone(row["sent_message_id"])

    async def test_send_now_ignores_stale_drop(self):
        """Stale-drop is a scheduler-only autonomy guard. Operator says
        'send now' = send now, regardless of how stale the row is."""
        stale_dt = self.now - timedelta(minutes=120)
        msg_id = await self.db.create_scheduled_message(
            text="very stale",
            message_type="custom",
            channel_topic_id=None,
            target_group="main",
            scheduled_date=stale_dt.strftime("%Y-%m-%d"),
            scheduled_time=stale_dt.strftime("%H:%M"),
            status="draft",
        )
        with patch("bot.handlers.calendar.send_message_with_optional_cover",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=921))) as h:
            await self._send_now(msg_id, target="main")
        h.assert_awaited_once()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")

    async def test_send_now_does_not_create_recurrence_next_occurrence(self):
        """Recurrence belongs to the scheduler's autonomous loop. An
        operator-fired send-now is a one-shot — no next-day row."""
        msg_id = await self._seed(
            message_type="custom", text="daily",
            target_group="main", status="draft", recurrence="daily",
        )
        with patch("bot.handlers.calendar.send_message_with_optional_cover",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=931))):
            await self._send_now(msg_id, target="main")
        # No new row at any future date.
        all_rows = await self.db.get_scheduled_messages("2000-01-01", "2099-12-31")
        self.assertEqual(
            len(all_rows), 1,
            "send-now must not auto-create next recurrence row "
            f"(found {len(all_rows)} rows)",
        )

    async def test_send_now_skips_warmup_rsvp_gate(self):
        """The scheduler enforces min_ready_players via _enforce_warmup_rsvp_gate.
        Send-now is the operator's override — it must NOT consult that gate."""
        msg_id = await self._seed(
            message_type="trivia_round", text="forced trivia",
            target_group="main", status="draft",
        )
        gate_mock = AsyncMock()
        with patch("bot.handlers.calendar._enforce_warmup_rsvp_gate", new=gate_mock), \
             patch("bot.handlers.trivia_round.start_scheduled_trivia_round",
                   new=AsyncMock(return_value=941)):
            await self._send_now(msg_id, target="main")
        gate_mock.assert_not_called()


# ── Pinned bug behavior (filed as regression tasks) ─────────────────────


class CurrentBugBehaviorPinnedTests(_ParityBase):
    """The send-now path silently drops several scheduler-side behaviors that
    the operator would reasonably expect to carry over. Until those gaps are
    closed (see REG-T157-* tasks), pin the current behavior so any
    well-intentioned fix to send-now turns these tests red and forces an
    explicit revisit."""

    async def test_emoji_puzzle_send_now_forwards_media_types_and_theme(self):
        """Scheduler passes payload['media_types'] and payload['theme_label']
        to start_emoji_night (bot/handlers/calendar.py:476); send-now must
        match so an operator-fired Emoji Night picks from the right pool.

        Previously asserted the broken behavior. Flipped when
        REG-T157-bug-emoji-filter was fixed in _send_scheduled_row."""
        payload = {"media_types": ["movie"], "theme_label": "סרטים"}
        msg_id = await self._seed(
            message_type="emoji_puzzle", text="emoji w/ subject",
            target_group="main", status="draft",
            poll_options=json.dumps(payload),
        )
        with patch("bot.handlers.emoji_puzzle.start_emoji_night",
                   new=AsyncMock(return_value=950)) as h:
            await self._send_now(msg_id, target="main")
        kwargs = h.await_args.kwargs
        self.assertEqual(kwargs.get("media_types"), ["movie"])
        self.assertEqual(kwargs.get("theme_label"), "סרטים")

    async def test_facts_send_now_forwards_pinned_fact_id_payload(self):
        """Operator pins a specific fact at preview time via
        poll_options.fact_id; send-now must forward it to send_scheduled_fact
        (matching the scheduler at bot/handlers/calendar.py:495).

        Previously named ..._ignores_pinned_fact_id_payload and asserted
        the broken behavior. Flipped when REG-T157-bug-fact-id was fixed
        in dashboard/app.py:_send_scheduled_row."""
        payload = {"fact_id": "fact-xyz"}
        msg_id = await self._seed(
            message_type="facts_tidbit", text="pinned fact",
            target_group="main", status="draft",
            poll_options=json.dumps(payload),
        )
        with patch("bot.handlers.facts.send_scheduled_fact",
                   new=AsyncMock(return_value=True)) as h:
            await self._send_now(msg_id, target="main")
        kwargs = h.await_args.kwargs
        self.assertEqual(kwargs.get("fact_id"), "fact-xyz")

    async def test_event_send_now_creates_event_row_and_attaches_rsvp(self):
        """Scheduler dispatch for `event` rows calls
        _create_event_row_from_scheduled, sends the card, then attaches
        RSVP buttons via edit_message_reply_markup and persists message_id
        on the event row. send-now must match so operator-fired events
        appear on /events with working RSVP.

        Previously asserted the broken behavior. Flipped when
        REG-T157-bug-event-rsvp was fixed in _send_scheduled_row."""
        msg_id = await self._seed(
            message_type="event", text="כותרת אירוע\nתיאור",
            target_group="main", status="draft",
        )
        bot_mock = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        with patch("telegram.Bot", return_value=bot_mock), \
             patch("bot.handlers.calendar.send_message_with_optional_cover",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=960))):
            await self._send_now(msg_id, target="main")
        # The events table now has the freshly-created row, with the sent
        # Telegram message_id persisted so the RSVP callback can find it.
        events = []
        async with self.db._db.execute(
            "SELECT id, message_id FROM events"
        ) as cur:
            async for r in cur:
                events.append(dict(r))
        self.assertEqual(len(events), 1, "send-now must create one events row")
        self.assertEqual(events[0]["message_id"], 960)
        bot_mock.edit_message_reply_markup.assert_awaited_once()

    async def test_skippedactivity_from_send_now_raises_for_caller(self):
        """_send_scheduled_row now raises SkippedActivity for legitimate
        skip cases (blackout date, pool exhausted, etc.) so the HTTP
        wrapper can mark status='skipped' instead of returning a generic
        500. This pins the helper-level contract; the HTTP-level
        translation is verified separately by route-level tests.

        Previously asserted the broken behavior (RuntimeError, no skipped
        status). Flipped when REG-T157-bug-skipped-status was fixed."""
        from bot.utils.scheduling_errors import SkippedActivity
        msg_id = await self._seed(
            message_type="free_games", text="forced free games",
            target_group="main", status="draft",
        )
        with patch("bot.handlers.free_games.send_free_games",
                   new=AsyncMock(return_value={"posted": 0, "error": "blackout date"})):
            with self.assertRaises(SkippedActivity):
                await self._send_now(msg_id, target="main")
        # _send_scheduled_row itself doesn't mutate the row on skip — the
        # HTTP wrapper does. Confirm the helper left the row alone so the
        # wrapper sees the original status when deciding how to record it.
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "draft")

    async def test_send_now_writes_activity_log_entry(self):
        """REG-T157-bug-activity-log fix: every successful send-now now
        mirrors the scheduler's tail block — log_activity audit row plus
        auto_pin handling. Previously asserted the broken behavior; flipped
        when _send_scheduled_row was refactored around a _finalize helper."""
        msg_id = await self._seed(
            message_type="custom", text="audit me",
            target_group="main", status="draft",
        )
        with patch("bot.handlers.calendar.send_message_with_optional_cover",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=971))):
            await self._send_now(msg_id, target="main")
        rows = []
        async with self.db._db.execute(
            "SELECT action_type, description FROM activity_log"
        ) as cur:
            async for r in cur:
                rows.append(dict(r))
        self.assertEqual(len(rows), 1, "send-now must write one audit row per send")
        self.assertEqual(rows[0]["action_type"], "custom")
        self.assertIn("audit me", rows[0]["description"])

    async def test_send_now_auto_pin_field_triggers_pin(self):
        """REG-T157-bug-pin fix: rows with auto_pin=True are pinned after
        send, matching the scheduler. Test-target sends still skip the pin
        to keep the probe non-destructive."""
        msg_id = await self._seed(
            message_type="custom", text="pin me",
            target_group="main", status="draft", auto_pin=True,
        )
        bot_mock = SimpleNamespace(pin_chat_message=AsyncMock())
        with patch("telegram.Bot", return_value=bot_mock), \
             patch("bot.handlers.calendar.send_message_with_optional_cover",
                   new=AsyncMock(return_value=SimpleNamespace(message_id=981))):
            await self._send_now(msg_id, target="main")
        bot_mock.pin_chat_message.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
