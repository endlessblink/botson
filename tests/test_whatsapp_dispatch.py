"""Botson rows targeted at WhatsApp are delivered through Botty's WAHA session."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from bot.handlers import calendar
from bot.utils import whatsapp_sender
from test_calendar_scheduled_games import FakeScheduledDb, _base_row

WA_ENV = {
    "WHATSAPP_WAHA_URL": "http://127.0.0.1:3051",
    "WHATSAPP_WAHA_API_KEY": "k",
    "WHATSAPP_GROUP_ID": "111@g.us",
    "WHATSAPP_TEST_GROUP_ID": "222@g.us",
    "GROUP_ID": "-100",
    "TEST_GROUP_ID": "-200",
    "BOT_TOKEN": "123:test",
}


class WhatsAppDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, row, env=None):
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=AsyncMock())
        text = AsyncMock(return_value="wa-text-id")
        poll = AsyncMock(return_value="wa-poll-id")
        tg_send = AsyncMock()
        with patch.dict("os.environ", env or WA_ENV, clear=False), \
             patch.object(whatsapp_sender, "send_text", new=text), \
             patch.object(whatsapp_sender, "send_poll", new=poll), \
             patch.object(calendar, "_conversation_gate", new=AsyncMock(return_value=None)), \
             patch("bot.utils.freshness.freshness_rejection", return_value=None), \
             patch.object(calendar, "send_message_with_optional_cover", new=tg_send), \
             patch.object(calendar, "send_poll_message", new=tg_send):
            await calendar.check_and_send_due_messages(context)
        return db, text, poll, tg_send

    async def test_poll_goes_to_whatsapp_as_native_poll(self):
        row = _base_row("poll")
        row.update(target_group="whatsapp", text="מה עדיף?", poll_options=json.dumps(["א", "ב"]))
        db, text, poll, tg_send = await self.dispatch(row)
        poll.assert_awaited_once_with("111@g.us", "מה עדיף?", ["א", "ב"])
        text.assert_not_awaited()
        tg_send.assert_not_awaited()
        self.assertEqual(db.sent, [(123, None)])

    async def test_discussion_goes_to_whatsapp_test_group_as_text(self):
        row = _base_row("discussion")
        row.update(target_group="whatsapp_test", text="שאלה")
        db, text, poll, tg_send = await self.dispatch(row)
        text.assert_awaited_once_with("222@g.us", "שאלה")
        tg_send.assert_not_awaited()
        self.assertEqual(db.sent, [(123, None)])

    async def test_live_game_is_skipped_on_whatsapp(self):
        row = _base_row("trivia_warmup_rsvp")
        row.update(target_group="whatsapp")
        db, text, poll, tg_send = await self.dispatch(row)
        text.assert_not_awaited()
        poll.assert_not_awaited()
        self.assertIn("whatsapp_unsupported_type", db.skipped[0][1])

    async def test_generated_conversation_row_is_never_sent(self):
        row = _base_row("morning")
        row.update(target_group="whatsapp", created_by="ai-fill-today")
        db, text, _, _ = await self.dispatch(row)
        text.assert_not_awaited()
        self.assertIn("conversation_not_scheduler_authored", db.skipped[0][1])

    async def test_missing_group_id_fails_without_sending(self):
        row = _base_row("custom")
        row.update(target_group="whatsapp")
        env = {**WA_ENV, "WHATSAPP_GROUP_ID": ""}
        db, text, _, _ = await self.dispatch(row, env)
        text.assert_not_awaited()
        self.assertFalse(db.sent)
        self.assertTrue(db.failed)


class WhatsAppTargetValidationTests(unittest.TestCase):
    def test_dashboard_accepts_whatsapp_targets(self):
        from dashboard.app import _validated_target_group

        self.assertEqual(_validated_target_group("whatsapp"), "whatsapp")
        self.assertEqual(_validated_target_group("whatsapp_test"), "whatsapp_test")
        with self.assertRaises(HTTPException):
            _validated_target_group("signal")


if __name__ == "__main__":
    unittest.main()
