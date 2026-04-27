import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import calendar


class FakeScheduledDb:
    def __init__(self, row):
        self.row = row
        self.sent = []
        self.failed = []
        self.activities = []

    async def get_due_messages(self, current_date, current_time):
        return [dict(self.row)]

    async def mark_message_sent(self, msg_id, sent_message_id):
        self.sent.append((msg_id, sent_message_id))

    async def mark_message_failed(self, msg_id, error):
        self.failed.append((msg_id, error))

    async def log_activity(self, action_type, description, target_channel=None):
        self.activities.append((action_type, description, target_channel))

    async def get_handler_routing(self, handler):
        return {"handler": handler, "play_topic_id": 4037, "teaser_topic_ids": []}


def _base_row(message_type):
    return {
        "id": 123,
        "scheduled_date": "2099-01-01",
        "scheduled_time": "09:00",
        "message_type": message_type,
        "target_group": "test",
        "channel_topic_id": None,
        "status": "scheduled",
        "text": "scheduled game",
        "created_by": "dashboard",
        "auto_pin": False,
        "poll_options": None,
        "poll_duration": None,
        "cover_path": None,
        "recurrence": None,
        "recurrence_days": None,
    }


class ScheduledGameDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_trivia_round_row_launches_game_without_plain_send(self):
        db = FakeScheduledDb(_base_row("trivia_round"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "start_scheduled_trivia_round", new=AsyncMock()) as start_trivia, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_trivia.assert_awaited_once()
        sent_msg = start_trivia.await_args.args[1]
        self.assertEqual(sent_msg["_resolved_chat_id"], -1002)
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 0)])
        self.assertEqual(db.failed, [])

    async def test_emoji_puzzle_row_starts_session_without_plain_send(self):
        db = FakeScheduledDb(_base_row("emoji_puzzle"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "start_emoji_night", new=AsyncMock(return_value=77)) as start_emoji, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_emoji.assert_awaited_once_with(context, -1002, None, force=True)
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 0)])
        self.assertEqual(db.failed, [])

    async def test_emoji_puzzle_internal_row_keeps_existing_dispatch_path(self):
        row = _base_row("emoji_puzzle_round")
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())
        sent = SimpleNamespace(message_id=456)

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_scheduled_emoji_message", new=AsyncMock(return_value=sent)) as send_emoji:
            await calendar.check_and_send_due_messages(context)

        send_emoji.assert_awaited_once()
        self.assertEqual(db.sent, [(123, 456)])
        self.assertEqual(db.failed, [])

    async def test_trivia_discussion_text_is_coerced_to_game_launch(self):
        row = _base_row("discussion")
        row["text"] = "🧠 הערב ב-22:00 — סיבוב טריוויה מוזיקלי! 5 שאלות"
        row["channel_topic_id"] = 54
        row["target_group"] = "main"
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "GROUP_ID": "-1001"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "start_scheduled_trivia_round", new=AsyncMock()) as start_trivia, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_trivia.assert_awaited_once()
        sent_msg = start_trivia.await_args.args[1]
        self.assertEqual(sent_msg["message_type"], "trivia_round")
        self.assertEqual(sent_msg["channel_topic_id"], 4037)
        self.assertIn('"מוזיקה"', sent_msg["poll_options"])
        self.assertIn('"teaser_topic_id": 54', sent_msg["poll_options"])
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 0)])
        self.assertEqual(db.failed, [])

    async def test_trivia_reminder_text_stays_plain_message(self):
        row = _base_row("discussion")
        row["text"] = "🧠 בעוד 10 דקות — סיבוב טריוויה מוזיקה! מתחממים?"
        row["channel_topic_id"] = 4037
        row["target_group"] = "main"
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())
        sent = SimpleNamespace(message_id=789)

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "GROUP_ID": "-1001"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "start_scheduled_trivia_round", new=AsyncMock()) as start_trivia, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock(return_value=sent)) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_trivia.assert_not_awaited()
        send_text.assert_awaited_once()
        self.assertEqual(db.sent, [(123, 789)])
        self.assertEqual(db.failed, [])


if __name__ == "__main__":
    unittest.main()
