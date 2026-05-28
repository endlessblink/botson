"""Orphan-game guard + same-topic spacing guard (2026-05-23 clash).

Background: an emoji game solo-launched at 22:00 with no warm-up announcement
and landed on top of an unrelated story posted at 22:01 in the same topic
(botson_corner). Two protections were added:

  * ``_enforce_warmup_announcement_present`` skips a trivia/emoji row that
    carries a ``warmup_marker`` but has no scheduled warm-up announcement — the
    orphan game that caused the incident;
  * the same-topic spacing guard defers non-time-critical content (facts,
    discussion, morning/evening, custom) that would stack onto a topic which
    just received a post, while NEVER deferring live games / warm-ups /
    reminders / polls / events (they claim the slot).

Run with: .venv/bin/python -m unittest tests.test_orphan_game_and_topic_spacing
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("TEST_GROUP_ID", "-1003747545764")
os.environ.setdefault("GROUP_ID", "-1003873409631")
os.environ.setdefault("BOT_TOKEN", "12345:fake_test_token_passes_format_check")

from zoneinfo import ZoneInfo  # noqa: E402

from bot.database.db import Database  # noqa: E402
from bot.handlers import calendar as calendar_handler  # noqa: E402
from bot.utils import config as cfg  # noqa: E402
from bot.utils.scheduling_errors import SkippedActivity  # noqa: E402

_IL_TZ = ZoneInfo("Asia/Jerusalem")
TEST_GROUP_ID = int(os.environ["TEST_GROUP_ID"])


class OrphanGameGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self._tmp.name)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def _game(self, marker, message_type="emoji_puzzle"):
        return {
            "id": 1,
            "message_type": message_type,
            "poll_options": json.dumps({"warmup_marker": marker, "min_ready_players": 2}),
        }

    async def _seed_announcement(self, marker):
        return await self.db.create_scheduled_message(
            text="warmup", message_type="trivia_warmup_rsvp", channel_topic_id=4037,
            target_group="main", scheduled_date="2026-05-23", scheduled_time="21:00",
            poll_options=json.dumps({"warmup_marker": marker}), status="scheduled",
        )

    async def test_orphan_marker_without_announcement_is_skipped(self):
        with patch.object(cfg, "get_settings", return_value={"trivia": {"require_warmup_announcement": True}}):
            with self.assertRaises(SkippedActivity):
                await calendar_handler._enforce_warmup_announcement_present(
                    self.db, self._game("warmup-rsvp:emoji:2026-05-23:22:00")
                )

    async def test_present_announcement_allows_launch(self):
        marker = "warmup-rsvp:emoji:2026-05-23:22:00"
        await self._seed_announcement(marker)
        with patch.object(cfg, "get_settings", return_value={"trivia": {"require_warmup_announcement": True}}):
            # Must NOT raise — the warm-up was scheduled.
            await calendar_handler._enforce_warmup_announcement_present(self.db, self._game(marker))

    async def test_threshold_game_with_unsent_announcement_is_skipped(self):
        marker = "warmup-rsvp:emoji:2026-05-23:22:00"
        await self._seed_announcement(marker)
        self.assertTrue(await self.db.warmup_announcement_exists(marker))
        with patch.object(cfg, "get_settings", return_value={"trivia": {"rsvp_gate_enabled": True}}):
            with self.assertRaisesRegex(SkippedActivity, "no sent warm-up announcement"):
                await calendar_handler._enforce_warmup_rsvp_gate(
                    self.db, self._game(marker), bot=AsyncMock(), group_id=TEST_GROUP_ID,
                )

    async def test_game_without_marker_is_noop(self):
        with patch.object(cfg, "get_settings", return_value={"trivia": {"require_warmup_announcement": True}}):
            await calendar_handler._enforce_warmup_announcement_present(
                self.db, {"id": 1, "message_type": "trivia_round", "poll_options": "{}"}
            )

    async def test_disabled_flag_allows_orphan(self):
        with patch.object(cfg, "get_settings", return_value={"trivia": {"require_warmup_announcement": False}}):
            await calendar_handler._enforce_warmup_announcement_present(self.db, self._game("orphan-marker"))

    async def test_warmup_announcement_exists_db(self):
        self.assertFalse(await self.db.warmup_announcement_exists("m"))
        self.assertFalse(await self.db.warmup_announcement_exists(""))
        await self._seed_announcement("m")
        self.assertTrue(await self.db.warmup_announcement_exists("m"))
        self.assertFalse(await self.db.warmup_announcement_exists("other"))


class TopicSpacingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db = Database(self._tmp.name)
        await self.db.init()
        now = datetime.now(_IL_TZ)
        self.today = now.strftime("%Y-%m-%d")
        self.now_hm = now.strftime("%H:%M")
        self._bot = AsyncMock()
        self._bot.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=800001, chat=SimpleNamespace(id=TEST_GROUP_ID))
        )
        self._bot.send_photo = AsyncMock(
            return_value=SimpleNamespace(message_id=800002, chat=SimpleNamespace(id=TEST_GROUP_ID))
        )
        self._bot.pin_chat_message = AsyncMock(return_value=None)
        # Dispatch builds Bot(token) lazily via `from telegram import Bot`.
        self._bot_patch = patch("telegram.Bot", new=lambda token: self._bot)
        self._bot_patch.start()

    async def asyncTearDown(self):
        self._bot_patch.stop()
        await self.db.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    def _ctx(self):
        return SimpleNamespace(bot=self._bot, bot_data={"db": self.db})

    async def _sched(self, message_type, *, topic=4037, status="scheduled", text="post", poll_options=None):
        return await self.db.create_scheduled_message(
            text=text, message_type=message_type, channel_topic_id=topic,
            target_group="test", scheduled_date=self.today, scheduled_time=self.now_hm,
            status=status, poll_options=poll_options,
        )

    async def _status(self, msg_id):
        async with self.db._db.execute(
            "SELECT status FROM scheduled_messages WHERE id = ?", (msg_id,)
        ) as cur:
            row = await cur.fetchone()
        return row["status"] if row else None

    async def _row(self, msg_id):
        async with self.db._db.execute(
            "SELECT * FROM scheduled_messages WHERE id = ?", (msg_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def test_last_topic_send_dt_returns_recent_send(self):
        self.assertIsNone(await self.db.last_topic_send_dt(4037, "test"))
        mid = await self._sched("discussion")
        await self.db.mark_message_sent(mid, 700001)
        dt = await self.db.last_topic_send_dt(4037, "test")
        self.assertIsNotNone(dt)
        self.assertLess(abs((datetime.now(_IL_TZ) - dt).total_seconds()), 180)
        # A different topic / group is unaffected.
        self.assertIsNone(await self.db.last_topic_send_dt(999, "test"))
        self.assertIsNone(await self.db.last_topic_send_dt(4037, "main"))

    async def test_static_content_deferred_when_topic_recently_used(self):
        prior = await self._sched("discussion", text="prior")
        await self.db.mark_message_sent(prior, 700010)  # sent_at = now
        late = await self._sched("discussion", text="late")
        with patch.object(cfg, "get_settings", return_value={"min_topic_spacing_minutes": 5}):
            await calendar_handler.check_and_send_due_messages(self._ctx())
        # Deferred: row stays scheduled, nothing was sent for it.
        self.assertEqual(await self._status(late), "scheduled")
        self.assertIsNone((await self._row(late))["error_message"])
        self._bot.send_message.assert_not_called()
        self._bot.send_photo.assert_not_called()

    async def test_static_content_deferral_releases_dispatch_claim_for_next_tick(self):
        prior = await self._sched("discussion", text="prior")
        await self.db.mark_message_sent(prior, 700011)
        late = await self._sched("discussion", text="late")

        with patch.object(cfg, "get_settings", return_value={"min_topic_spacing_minutes": 5}):
            await calendar_handler.check_and_send_due_messages(self._ctx())

        row = await self._row(late)
        self.assertEqual(row["status"], "scheduled")
        self.assertIsNone(row["error_message"])
        self.assertTrue(await self.db.claim_scheduled_message(late))

    async def test_static_content_sends_when_topic_quiet(self):
        # No prior send to the topic → no spacing deferral.
        only = await self._sched("discussion", text="solo")
        with patch.object(cfg, "get_settings", return_value={"min_topic_spacing_minutes": 5}):
            await calendar_handler.check_and_send_due_messages(self._ctx())
        self.assertEqual(await self._status(only), "sent")
        self._bot.send_message.assert_called()

    async def test_spacing_disabled_does_not_defer(self):
        prior = await self._sched("discussion", text="prior")
        await self.db.mark_message_sent(prior, 700030)
        late = await self._sched("discussion", text="late")
        with patch.object(cfg, "get_settings", return_value={"min_topic_spacing_minutes": 0}):
            await calendar_handler.check_and_send_due_messages(self._ctx())
        self.assertEqual(await self._status(late), "sent")

    async def test_slot_claiming_type_not_deferred(self):
        prior = await self._sched("discussion", text="prior")
        await self.db.mark_message_sent(prior, 700040)
        warm = await self._sched(
            "trivia_warmup_rsvp", text="warmup",
            poll_options=json.dumps({"warmup_marker": "m1", "min_ready_players": 2}),
        )
        with patch.object(cfg, "get_settings", return_value={"min_topic_spacing_minutes": 5}):
            await calendar_handler.check_and_send_due_messages(self._ctx())
        # A time-anchored warm-up claims its slot even when the topic was just used.
        self.assertEqual(await self._status(warm), "sent")
        self._bot.send_message.assert_called()


if __name__ == "__main__":
    unittest.main()
