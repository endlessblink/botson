from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from bot.handlers import daily_activity_digest as digest


class FakeDigestDb:
    def __init__(self, rows):
        self.rows = rows
        self.activities = []

    async def get_scheduled_messages(self, date_from, date_to):
        return list(self.rows)

    async def log_activity(self, action_type, description, target_channel=None):
        self.activities.append((action_type, description, target_channel))


class DailyActivityDigestTests(unittest.IsolatedAsyncioTestCase):
    def test_build_digest_includes_only_scheduled_bot_activities(self):
        text = digest.build_daily_activity_digest([
            {"scheduled_time": "21:00", "message_type": "trivia_round", "status": "scheduled", "text": "🧠 סיבוב טריוויה — מדע"},
            {"scheduled_time": "22:00", "message_type": "emoji_puzzle", "status": "scheduled", "text": "🧩 Emoji Night — סדרות"},
            {"scheduled_time": "18:00", "message_type": "discussion", "status": "scheduled", "text": "שאלה רגילה"},
            {"scheduled_time": "12:00", "message_type": "facts_tidbit", "status": "scheduled", "text": "🔎 עובדה מעניינת"},
        ])

        self.assertIsNotNone(text)
        self.assertIn("📅 היום בבוטסון", text)
        self.assertIn("יש 3 פעילויות", text)
        keyboard = digest.build_daily_activity_keyboard([
            {"scheduled_time": "21:00", "message_type": "trivia_round", "status": "scheduled", "text": "🧠 סיבוב טריוויה — מדע"},
            {"scheduled_time": "22:00", "message_type": "emoji_puzzle", "status": "scheduled", "text": "🧩 Emoji Night — סדרות"},
            {"scheduled_time": "18:00", "message_type": "discussion", "status": "scheduled", "text": "שאלה רגילה"},
            {"scheduled_time": "12:00", "message_type": "facts_tidbit", "status": "scheduled", "text": "🔎 עובדה מעניינת"},
        ])
        self.assertIsNotNone(keyboard)
        button_texts = [row[0].text for row in keyboard.inline_keyboard]
        self.assertIn("‏🔎 עובדה מעניינת · ‎12:00‎", button_texts)
        self.assertIn("‏🧠 טריוויה · ‎21:00‎", button_texts)
        self.assertIn("‏🧩 חידת אימוג'י · ‎22:00‎", button_texts)
        self.assertNotIn("שאלה רגילה", text)

    def test_build_digest_returns_none_without_activities(self):
        self.assertIsNone(digest.build_daily_activity_digest([
            {"scheduled_time": "18:00", "message_type": "discussion", "status": "scheduled", "text": "שאלה רגילה"},
        ]))

    async def test_send_daily_digest_routes_to_welcome_topic(self):
        db = FakeDigestDb([
            {"scheduled_time": "21:00", "message_type": "trivia_round", "status": "scheduled", "text": "🧠 סיבוב טריוויה — מדע"},
        ])
        context = SimpleNamespace(bot=object(), bot_data={"db": db})
        settings = {
            "topics": {"welcome": 341},
            "schedule": {"daily_activity_digest": {"enabled": True}},
            "holiday_blackouts": [],
        }

        with patch.object(digest, "get_settings", return_value=settings), \
             patch.object(digest, "GROUP_ID", -1001), \
             patch.object(digest, "safe_send", new=AsyncMock(return_value=SimpleNamespace(message_id=55))) as safe_send:
            msg_id = await digest.send_daily_activity_digest(context)

        self.assertEqual(msg_id, 55)
        safe_send.assert_awaited_once()
        self.assertEqual(safe_send.await_args.kwargs["message_thread_id"], 341)
        self.assertIsNotNone(safe_send.await_args.kwargs["reply_markup"])
        self.assertIn("daily_activity_digest", db.activities[0])

    async def test_send_daily_digest_skips_when_empty(self):
        db = FakeDigestDb([])
        context = SimpleNamespace(bot=object(), bot_data={"db": db})
        settings = {
            "topics": {"welcome": 341},
            "schedule": {"daily_activity_digest": {"enabled": True}},
            "holiday_blackouts": [],
        }

        with patch.object(digest, "get_settings", return_value=settings), \
             patch.object(digest, "safe_send", new=AsyncMock()) as safe_send:
            msg_id = await digest.send_daily_activity_digest(context)

        self.assertIsNone(msg_id)
        safe_send.assert_not_awaited()

    async def test_digest_button_registers_tagged_reminder(self):
        rows = [{
            "scheduled_date": "2099-01-01",
            "scheduled_time": "21:00",
            "message_type": "trivia_round",
            "status": "scheduled",
            "text": "🧠 סיבוב טריוויה — מדע",
        }]
        digest.build_daily_activity_keyboard(rows)

        class FakeJobQueue:
            def __init__(self):
                self.calls = []

            def run_once(self, callback, **kwargs):
                self.calls.append((callback, kwargs))

        job_queue = FakeJobQueue()
        query = SimpleNamespace(
            data="daily_digest:0",
            message=SimpleNamespace(chat_id=-1001, message_thread_id=341),
            answer=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=123, username="noam", full_name="Noam"),
        )
        context = SimpleNamespace(job_queue=job_queue)

        await digest.handle_daily_digest_button(update, context)

        query.answer.assert_awaited_once()
        self.assertIn("אזכיר לך לפני", query.answer.await_args.args[0])
        self.assertEqual(len(job_queue.calls), 1)
        self.assertIn(123, digest._REMINDER_INTEREST["daily_digest:0"]["users"])

    async def test_activity_reminder_mentions_registered_users(self):
        digest._REMINDER_INTEREST["daily_digest:0"] = {
            "detail": {
                "title": "🧠 סיבוב טריוויה — מדע",
                "row": {"message_type": "trivia_round", "scheduled_time": "21:00"},
            },
            "users": {123: "@noam"},
            "job_scheduled": True,
        }
        context = SimpleNamespace(
            bot=object(),
            bot_data={"db": object()},
            job=SimpleNamespace(data={"key": "daily_digest:0", "chat_id": -1001, "thread_id": 341}),
        )

        with patch.object(digest, "safe_send", new=AsyncMock()) as safe_send:
            await digest._send_activity_reminder(context)

        safe_send.assert_awaited_once()
        self.assertIn("@noam", safe_send.await_args.kwargs["text"])
        self.assertIn("ביקשת תזכורת", safe_send.await_args.kwargs["text"])
        self.assertIn("מתחיל ב־", safe_send.await_args.kwargs["text"])
        self.assertEqual(safe_send.await_args.kwargs["message_thread_id"], 341)


if __name__ == "__main__":
    unittest.main()
