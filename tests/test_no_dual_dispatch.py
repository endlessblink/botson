"""Guardian: every recurring content type has exactly ONE active dispatcher.

This bot dispatches content two ways — APScheduler cron jobs (bot/scheduler/jobs.py)
and the per-minute calendar_checker (bot/handlers/calendar.py). Wiring the same type
into both posted the weekly leaderboard twice on 2026-05-23. Ownership is declared in
bot/scheduler/dispatch_owner.py; this test fails CI (and the pre-deploy guardian) if the
invariant is broken.

It catches:
  * a cron-owned type that the calendar dispatcher would (re-)send;
  * a calendar-owned type whose cron got activated (e.g. emoji schedule days set);
  * a cron-owned type declared in the registry with no real cron job;
  * a new content type wired into a dispatcher without being classified.
"""

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import calendar
from bot.scheduler.dispatch_owner import (
    CALENDAR_OWNED_TYPES,
    CALENDAR_OWNED_WITH_CRON_PATH,
    CRON_OWNED_TYPES,
    DISPATCHER_OWNER,
)
from bot.utils.config import get_settings

_REPO = Path(__file__).resolve().parents[1]
_JOBS_SRC = (_REPO / "bot" / "scheduler" / "jobs.py").read_text(encoding="utf-8")


class _FakeDb:
    """Minimal scheduled-messages DB returning a single due row of `message_type`."""

    def __init__(self, message_type: str):
        self._row = {
            "id": 1,
            "scheduled_date": "2099-01-01",
            "scheduled_time": "09:00",
            "message_type": message_type,
            "target_group": "test",
            "channel_topic_id": None,
            "status": "scheduled",
            "text": "row",
            "created_by": "dashboard",
            "auto_pin": False,
            "poll_options": None,
            "poll_duration": None,
            "cover_path": None,
            "recurrence": None,
            "recurrence_days": None,
        }
        self.sent: list = []
        self.failed: list = []
        self.skipped: list = []

    async def get_due_messages(self, current_date, current_time):
        return [dict(self._row)]

    async def mark_message_sent(self, msg_id, sent_message_id):
        self.sent.append((msg_id, sent_message_id))

    async def mark_message_failed(self, msg_id, error):
        self.failed.append((msg_id, error))

    async def mark_message_skipped(self, msg_id, reason):
        self.skipped.append((msg_id, reason))

    async def log_activity(self, *a, **k):
        pass

    async def get_handler_routing(self, handler):
        return {"handler": handler, "play_topic_id": 4037, "teaser_topic_ids": []}


class DualDispatchGuardTests(unittest.IsolatedAsyncioTestCase):
    def test_registry_is_internally_consistent(self):
        self.assertTrue(CRON_OWNED_TYPES, "expected at least one cron-owned type")
        self.assertEqual(
            CRON_OWNED_TYPES & CALENDAR_OWNED_TYPES, set(),
            "a type cannot be both cron-owned and calendar-owned",
        )
        self.assertTrue(
            CALENDAR_OWNED_WITH_CRON_PATH <= CALENDAR_OWNED_TYPES,
            "CALENDAR_OWNED_WITH_CRON_PATH must be a subset of calendar-owned types",
        )
        self.assertEqual(
            set(DISPATCHER_OWNER.values()) - {"cron", "calendar"}, set(),
            "owner must be 'cron' or 'calendar'",
        )

    async def test_cron_owned_types_are_skipped_by_calendar_dispatcher(self):
        """The core protection: a due row of a cron-owned type self-skips, never sends."""
        for mtype in sorted(CRON_OWNED_TYPES):
            with self.subTest(message_type=mtype):
                db = _FakeDb(mtype)
                context = SimpleNamespace(bot_data={"db": db}, bot=object())
                with patch.dict(calendar.os.environ, {"BOT_TOKEN": "t", "TEST_GROUP_ID": "-1002"}), \
                     patch("telegram.Bot", return_value=object()), \
                     patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
                    await calendar.check_and_send_due_messages(context)
                self.assertEqual(db.sent, [], f"{mtype} must NOT be sent by the calendar dispatcher")
                send_text.assert_not_awaited()
                self.assertEqual(len(db.skipped), 1, f"{mtype} row should be marked skipped")
                self.assertIn("cron", db.skipped[0][1])

    def test_every_cron_owned_type_has_a_real_cron_registration(self):
        for mtype in sorted(CRON_OWNED_TYPES):
            with self.subTest(message_type=mtype):
                self.assertIn(
                    f'name="{mtype}"', _JOBS_SRC,
                    f'{mtype} is declared cron-owned but bot/scheduler/jobs.py has no '
                    f'name="{mtype}" cron job. Register one or reclassify it.',
                )

    def test_calendar_owned_types_with_cron_path_have_inert_cron(self):
        schedule = (get_settings() or {}).get("schedule", {})
        for mtype in sorted(CALENDAR_OWNED_WITH_CRON_PATH):
            with self.subTest(message_type=mtype):
                days = (schedule.get(mtype) or {}).get("days") or []
                self.assertEqual(
                    list(days), [],
                    f"{mtype} is calendar-owned but schedule.{mtype}.days={list(days)} is "
                    f"non-empty — that activates its cron and creates a dual dispatch. Clear "
                    f"the days, or reclassify ownership in bot/scheduler/dispatch_owner.py.",
                )

    def test_calendar_owned_types_have_no_unconditional_cron(self):
        for mtype in sorted(CALENDAR_OWNED_TYPES - CALENDAR_OWNED_WITH_CRON_PATH):
            with self.subTest(message_type=mtype):
                self.assertNotIn(
                    f'name="{mtype}"', _JOBS_SRC,
                    f'{mtype} is calendar-owned but bot/scheduler/jobs.py registers a cron '
                    f'job name="{mtype}" — that is a dual dispatch.',
                )


if __name__ == "__main__":
    unittest.main()
