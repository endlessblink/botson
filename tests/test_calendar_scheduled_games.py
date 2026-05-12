import unittest
import json
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database.db import Database
from bot.handlers import calendar


class FakeScheduledDb:
    def __init__(self, row):
        self.row = row
        self.sent = []
        self.failed = []
        self.skipped = []
        self.activities = []

    async def get_due_messages(self, current_date, current_time):
        return [dict(self.row)]

    async def mark_message_sent(self, msg_id, sent_message_id):
        self.sent.append((msg_id, sent_message_id))

    async def mark_message_failed(self, msg_id, error):
        self.failed.append((msg_id, error))

    async def mark_message_skipped(self, msg_id, reason):
        self.skipped.append((msg_id, reason))

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
    async def test_deleted_calendar_item_is_not_due_for_dispatch(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                msg_id = await db.create_scheduled_message(
                    text="scheduled text",
                    message_type="discussion",
                    channel_topic_id=None,
                    target_group="test",
                    scheduled_date="2099-01-01",
                    scheduled_time="09:00",
                    status="scheduled",
                )

                await db.delete_scheduled_message(msg_id)

                due = await db.get_due_messages("2099-01-01", "09:00")
                self.assertEqual(due, [])
            finally:
                await db.close()

    async def test_trivia_round_row_launches_game_without_plain_send(self):
        db = FakeScheduledDb(_base_row("trivia_round"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "start_scheduled_trivia_round", new=AsyncMock(return_value=456)) as start_trivia, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_trivia.assert_awaited_once()
        sent_msg = start_trivia.await_args.args[1]
        self.assertEqual(sent_msg["_resolved_chat_id"], -1002)
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 456)])
        self.assertEqual(db.failed, [])

    async def test_emoji_puzzle_row_starts_session_without_plain_send(self):
        row = _base_row("emoji_puzzle")
        row["poll_options"] = json.dumps({"theme_label": "סרטים", "media_types": ["movie"]}, ensure_ascii=False)
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "emoji_skip_reason", new=AsyncMock(return_value=None)), \
             patch.object(calendar, "start_emoji_night", new=AsyncMock(return_value=77)) as start_emoji, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_emoji.assert_awaited_once_with(
            context, -1002, None, force=True, media_types=["movie"], theme_label="סרטים",
        )
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 77)])
        self.assertEqual(db.failed, [])

    async def test_emoji_announcement_text_stays_plain_message(self):
        row = _base_row("discussion")
        row["text"] = "🧩 הערב ב-22:00: חידת אימוג'י בנושא סרטים וסדרות."
        row["channel_topic_id"] = 4037
        row["target_group"] = "main"
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())
        sent = SimpleNamespace(message_id=789)

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "GROUP_ID": "-1001"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "start_emoji_night", new=AsyncMock()) as start_emoji, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock(return_value=sent)) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_emoji.assert_not_awaited()
        send_text.assert_awaited_once()
        self.assertEqual(db.sent, [(123, 789)])
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

    async def test_free_games_row_runs_existing_handler_without_plain_send(self):
        db = FakeScheduledDb(_base_row("free_games"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_free_games", new=AsyncMock(return_value={"posted": 1})) as send_free_games, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        send_free_games.assert_awaited_once_with(context, force=True)
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 1)])
        self.assertEqual(db.failed, [])

    async def test_fact_rows_pick_pool_without_plain_send(self):
        for message_type, pool in (("facts_tidbit", "tidbit"), ("facts_spooky", "spooky")):
            with self.subTest(message_type=message_type):
                row = _base_row(message_type)
                row["channel_topic_id"] = 4037
                db = FakeScheduledDb(row)
                context = SimpleNamespace(bot_data={"db": db}, bot=object())
                bot = object()

                with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
                     patch("telegram.Bot", return_value=bot), \
                     patch.object(calendar, "send_scheduled_fact", new=AsyncMock(return_value=True)) as send_fact, \
                     patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
                    await calendar.check_and_send_due_messages(context)

                send_fact.assert_awaited_once_with(
                    bot, db, pool=pool, chat_id=-1002, thread_id=4037, fact_id=None
                )
                send_text.assert_not_awaited()
                self.assertEqual(db.sent, [(123, 1)])
                self.assertEqual(db.failed, [])

    async def test_fact_rows_pass_pinned_preview_fact_id(self):
        row = _base_row("facts_tidbit")
        row["channel_topic_id"] = 4037
        row["poll_options"] = json.dumps({"fact_id": "roman_concrete_self_healing"})
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())
        bot = object()

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=bot), \
             patch.object(calendar, "send_scheduled_fact", new=AsyncMock(return_value=True)) as send_fact:
            await calendar.check_and_send_due_messages(context)

        send_fact.assert_awaited_once_with(
            bot, db, pool="tidbit", chat_id=-1002, thread_id=4037,
            fact_id="roman_concrete_self_healing",
        )

    async def test_weekly_roundup_row_runs_existing_handler_without_plain_send(self):
        db = FakeScheduledDb(_base_row("weekly_roundup"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_weekly_roundup", new=AsyncMock(return_value=555)) as send_roundup, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        send_roundup.assert_awaited_once_with(context, force=True)
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 555)])
        self.assertEqual(db.failed, [])

    async def test_weekly_leaderboard_row_runs_existing_handler_without_plain_send(self):
        db = FakeScheduledDb(_base_row("weekly_leaderboard"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_weekly_leaderboard", new=AsyncMock(return_value=666)) as send_leaderboard, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        send_leaderboard.assert_awaited_once_with(context)
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 666)])
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
             patch.object(calendar, "start_scheduled_trivia_round", new=AsyncMock(return_value=456)) as start_trivia, \
             patch.object(calendar, "send_message_with_optional_cover", new=AsyncMock()) as send_text:
            await calendar.check_and_send_due_messages(context)

        start_trivia.assert_awaited_once()
        sent_msg = start_trivia.await_args.args[1]
        self.assertEqual(sent_msg["message_type"], "trivia_round")
        self.assertEqual(sent_msg["channel_topic_id"], 4037)
        self.assertIn('"מוזיקה"', sent_msg["poll_options"])
        self.assertIn('"teaser_topic_id": 54', sent_msg["poll_options"])
        send_text.assert_not_awaited()
        self.assertEqual(db.sent, [(123, 456)])
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

    async def test_trivia_launch_failure_marks_failed_not_sent(self):
        db = FakeScheduledDb(_base_row("trivia_round"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "start_scheduled_trivia_round", new=AsyncMock(side_effect=RuntimeError("not enough questions"))):
            await calendar.check_and_send_due_messages(context)

        self.assertEqual(db.sent, [])
        self.assertEqual(len(db.failed), 1)
        self.assertEqual(db.failed[0][0], 123)
        self.assertIn("not enough questions", db.failed[0][1])

    async def test_scheduled_trivia_with_too_few_questions_fails_before_background_task(self):
        row = _base_row("trivia_round")
        row["_resolved_chat_id"] = -1002
        row["poll_options"] = json.dumps({
            "pre_roll_s": 30,
            "theme_label": "גיימינג",
            "categories": ["גיימינג"],
            "question_count": 10,
        }, ensure_ascii=False)
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        from bot.handlers import trivia_round
        with patch.object(trivia_round, "_pick_questions", return_value=[{"text": "q", "options": ["a", "b"], "correct": 0}]), \
             patch.object(trivia_round, "_top_up_scheduled_questions_if_possible", new=AsyncMock()) as topup, \
             patch.object(trivia_round.asyncio, "create_task") as create_task:
            with self.assertRaisesRegex(RuntimeError, "not enough questions"):
                await trivia_round.start_scheduled_trivia_round(context, row)

        topup.assert_awaited_once()
        create_task.assert_not_called()

    async def test_scheduled_trivia_runtime_topup_retries_question_pick(self):
        row = _base_row("trivia_round")
        row["_resolved_chat_id"] = -1002
        row["poll_options"] = json.dumps({
            "pre_roll_s": 30,
            "theme_label": "גיימינג",
            "categories": ["גיימינג"],
            "question_count": 2,
            "min_ready_players": 2,
        }, ensure_ascii=False)
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=object())
        q = {"text": "q", "options": ["a", "b", "c", "d"], "correct": 0}

        from bot.handlers import trivia_round
        with patch.object(trivia_round, "_pick_questions", side_effect=[[q], [dict(q), dict(q, text="q2")]]), \
             patch.object(trivia_round, "_top_up_scheduled_questions_if_possible", new=AsyncMock()) as topup, \
             patch.object(trivia_round, "_send_round_teaser_and_announcement", new=AsyncMock(return_value=456)) as send_announcement, \
             patch.object(trivia_round.asyncio, "create_task", side_effect=lambda coro: coro.close()) as create_task:
            result = await trivia_round.start_scheduled_trivia_round(context, row)

        self.assertEqual(result, 456)
        topup.assert_awaited_once()
        create_task.assert_called_once()
        send_kwargs = send_announcement.await_args.kwargs
        self.assertEqual(send_kwargs["min_ready_players"], 2)
        trivia_round._active_rounds.pop(-1002, None)

    async def test_trivia_ready_gate_cancels_before_first_question_when_no_players(self):
        from bot.handlers import trivia_round
        db = FakeScheduledDb(_base_row("trivia_round"))
        q = {"text": "q", "options": ["a", "b", "c", "d"], "correct": 0}
        state = trivia_round._create_round_state([q], 1, min_ready_players=2)
        trivia_round._active_rounds[-1002] = state

        with patch.object(trivia_round, "safe_send", new=AsyncMock(return_value=SimpleNamespace(message_id=999))) as safe_send, \
             patch.object(trivia_round, "_post_question", new=AsyncMock()) as post_question:
            await trivia_round._continue_round_after_announcement(
                object(), db, -1002, 4037, pre_roll_s=1, round_state=state,
            )

        post_question.assert_not_awaited()
        self.assertTrue(any("לא מתחילה" in call.kwargs.get("text", "") for call in safe_send.await_args_list))
        self.assertTrue(any("בוטל" in activity[1] for activity in db.activities))
        trivia_round._active_rounds.pop(-1002, None)

    async def test_free_games_without_post_marks_failed_not_sent(self):
        db = FakeScheduledDb(_base_row("free_games"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_free_games", new=AsyncMock(return_value={"posted": 0, "error": "no candidates"})):
            await calendar.check_and_send_due_messages(context)

        self.assertEqual(db.sent, [])
        self.assertEqual(len(db.failed), 1)
        self.assertIn("free_games did not post", db.failed[0][1])

    async def test_free_games_no_new_candidates_marks_skipped_not_failed(self):
        db = FakeScheduledDb(_base_row("free_games"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_free_games", new=AsyncMock(return_value={"posted": 0, "skipped": 7, "error": None})):
            await calendar.check_and_send_due_messages(context)

        self.assertEqual(db.sent, [])
        self.assertEqual(db.failed, [])
        self.assertEqual(len(db.skipped), 1)
        self.assertIn("free_games", db.skipped[0][1])

    async def test_weekly_leaderboard_no_leaders_marks_skipped_not_failed(self):
        db = FakeScheduledDb(_base_row("weekly_leaderboard"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_weekly_leaderboard", new=AsyncMock(return_value={"skipped": "no weekly leaders"})):
            await calendar.check_and_send_due_messages(context)

        self.assertEqual(db.sent, [])
        self.assertEqual(db.failed, [])
        self.assertEqual(len(db.skipped), 1)
        self.assertIn("no weekly leaders", db.skipped[0][1])

    async def test_weekly_roundup_without_message_id_marks_failed_not_sent(self):
        db = FakeScheduledDb(_base_row("weekly_roundup"))
        context = SimpleNamespace(bot_data={"db": db}, bot=object())

        with patch.dict(calendar.os.environ, {"BOT_TOKEN": "token", "TEST_GROUP_ID": "-1002"}), \
             patch("telegram.Bot", return_value=object()), \
             patch.object(calendar, "send_weekly_roundup", new=AsyncMock(return_value=None)):
            await calendar.check_and_send_due_messages(context)

        self.assertEqual(db.sent, [])
        self.assertEqual(len(db.failed), 1)
        self.assertIn("weekly_roundup did not return", db.failed[0][1])


if __name__ == "__main__":
    unittest.main()
