from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import json
import tempfile
import unittest

from bot.database.db import Database
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

    def test_clean_title_handles_empty_text_without_crashing(self):
        # Regression: splitlines()[0] on empty/whitespace text raised IndexError
        # and took down the whole 09:30 digest job. Should fall back to the
        # message-type label instead.
        self.assertEqual(digest._clean_title({"text": "", "message_type": "trivia_round"}),
                         digest._DIGEST_TYPES.get("trivia_round", "trivia_round"))
        self.assertEqual(digest._clean_title({"text": "   ", "message_type": "emoji_puzzle"}),
                         digest._DIGEST_TYPES.get("emoji_puzzle", "emoji_puzzle"))
        # Also covers a row built straight through the keyboard builder.
        kb = digest.build_daily_activity_keyboard([
            {"scheduled_time": "21:00", "message_type": "trivia_round", "status": "scheduled", "text": ""},
        ])
        self.assertIsNotNone(kb)

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
            edit_message_reply_markup=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=123, username="noam", full_name="Noam"),
        )
        context = SimpleNamespace(job_queue=job_queue)

        await digest.handle_daily_digest_button(update, context)

        query.answer.assert_awaited_once()
        self.assertIn("אזכיר לך לפני", query.answer.await_args.args[0])
        self.assertTrue(query.answer.await_args.kwargs["show_alert"])
        query.edit_message_reply_markup.assert_awaited_once()
        updated_markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
        self.assertIn("✅", updated_markup.inline_keyboard[0][0].text)
        self.assertIn("@noam", updated_markup.inline_keyboard[0][0].text)
        self.assertEqual(len(job_queue.calls), 1)
        self.assertIn(123, digest._REMINDER_INTEREST["daily_digest:0"]["users"])

    async def test_digest_game_button_records_rsvp_on_paired_warmup(self):
        marker = "warmup-rsvp:emoji:2099-01-01:20:00"
        rows = [{
            "id": 594,
            "scheduled_date": "2099-01-01",
            "scheduled_time": "20:00",
            "message_type": "emoji_puzzle",
            "status": "scheduled",
            "text": "",
            "poll_options": json.dumps({"warmup_marker": marker}),
        }]
        digest.build_daily_activity_keyboard(rows)

        class FakeDb:
            async def get_warmup_announcement_for_marker(self, warmup_marker):
                self.marker = warmup_marker
                return {"id": 593, "status": "scheduled"}

        query = SimpleNamespace(
            data="daily_digest:0",
            message=SimpleNamespace(chat_id=-1001, message_thread_id=341),
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        user = SimpleNamespace(id=123, username="noam", full_name="Noam")
        db = FakeDb()
        update = SimpleNamespace(callback_query=query, effective_user=user)
        context = SimpleNamespace(job_queue=None, bot=object(), bot_data={"db": db})

        with patch("bot.handlers.trivia_interest.record_trivia_interest",
                   new=AsyncMock(return_value={"count": 1, "already": False, "names": "@noam"})) as record, \
             patch("bot.handlers.trivia_interest.refresh_warmup_group_button",
                   new=AsyncMock()) as refresh:
            await digest.handle_daily_digest_button(update, context)

        self.assertEqual(db.marker, marker)
        record.assert_awaited_once()
        self.assertEqual(record.await_args.args[2], 593)
        self.assertIs(record.await_args.args[3], user)
        refresh.assert_awaited_once()

    async def test_digest_game_button_persists_rsvp_in_real_db(self):
        marker = "warmup-rsvp:emoji:2099-01-01:20:00"
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                warmup_id = await db.create_scheduled_message(
                    text="warmup",
                    message_type="trivia_warmup_rsvp",
                    channel_topic_id=4502,
                    target_group="test",
                    scheduled_date="2099-01-01",
                    scheduled_time="18:30",
                    poll_options=json.dumps({
                        "warmup_marker": marker,
                        "game_time": "20:00",
                        "min_ready_players": 2,
                    }),
                    status="scheduled",
                )
                rows = [{
                    "id": 594,
                    "scheduled_date": "2099-01-01",
                    "scheduled_time": "20:00",
                    "message_type": "emoji_puzzle",
                    "status": "scheduled",
                    "text": "",
                    "poll_options": json.dumps({"warmup_marker": marker}),
                }]
                digest.build_daily_activity_keyboard(rows)
                query = SimpleNamespace(
                    data="daily_digest:0",
                    message=SimpleNamespace(chat_id=-1001, message_thread_id=341),
                    answer=AsyncMock(),
                    edit_message_reply_markup=AsyncMock(),
                )
                user = SimpleNamespace(
                    id=123,
                    username="noam",
                    first_name="Noam",
                    last_name=None,
                    full_name="Noam",
                )
                update = SimpleNamespace(callback_query=query, effective_user=user)
                context = SimpleNamespace(job_queue=None, bot=object(), bot_data={"db": db})

                await digest.handle_daily_digest_button(update, context)

                responses = await db.get_trivia_interest_responses(warmup_id)
                self.assertEqual(len(responses), 1)
                self.assertEqual(responses[0]["user_id"], 123)
                self.assertEqual(responses[0]["display_name"], "Noam")
                await db.mark_message_sent(warmup_id, 7003)
                users = await db.get_warmup_rsvp_user_map(marker)
                self.assertEqual(users, {123: "Noam"})
            finally:
                await db.close()

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
