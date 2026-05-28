from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from bot.handlers import emoji_puzzle


class FakeEmojiRuntimeDb:
    def __init__(self):
        self.started_rounds = []
        self.closed = []
        self.completed = []
        self.sessions = []

    async def get_active_session(self, chat_id, thread_id):
        return None

    async def create_emoji_session(self, chat_id, thread_id, puzzle_count):
        session_id = len(self.sessions) + 55
        self.sessions.append((session_id, chat_id, thread_id, puzzle_count))
        return session_id

    async def start_emoji_round(self, session_id, puzzle_id, chat_id, message_id, thread_id, award_points):
        self.started_rounds.append((session_id, puzzle_id, chat_id, message_id, thread_id, award_points))

    async def get_session_leaderboard(self, session_id):
        return []

    async def get_session_unsolved_rounds(self, session_id):
        return []

    async def close_session_rounds(self, session_id):
        self.closed.append(session_id)

    async def complete_emoji_session(self, session_id, leaderboard):
        self.completed.append((session_id, leaderboard))


class EmojiPuzzleRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_emoji_night_returns_real_intro_message_id_for_scheduler(self):
        db = FakeEmojiRuntimeDb()
        ctx = SimpleNamespace(bot_data={"db": db}, bot=object())
        puzzles = [
            {"id": 1, "emoji_prompt": "🍄👨‍🔧🐢", "media_type": "game"},
            {"id": 2, "emoji_prompt": "🧙‍♂️💍", "media_type": "movie"},
        ]

        async def fake_send(*args, **kwargs):
            return SimpleNamespace(message_id=9001, chat_id=kwargs.get("chat_id"))

        def fake_create_task(coro):
            coro.close()
            return SimpleNamespace(add_done_callback=lambda callback: None)

        with patch.object(emoji_puzzle, "is_feature_enabled", return_value=True), \
             patch.object(emoji_puzzle, "_emoji_settings", return_value=({}, {
                 "puzzle_count": 2,
                 "interval_seconds": 60,
                 "intro_offset_seconds": 10,
                 "wrap_offset_seconds": 20,
             })), \
             patch.object(emoji_puzzle, "_pick_session_puzzles", new=AsyncMock(return_value=puzzles)), \
             patch.object(emoji_puzzle, "safe_send", new=AsyncMock(side_effect=fake_send)) as safe_send, \
             patch.object(emoji_puzzle.asyncio, "sleep", new=AsyncMock()) as sleep, \
             patch.object(emoji_puzzle.asyncio, "create_task", side_effect=fake_create_task):
            result = await emoji_puzzle.start_emoji_night(
                ctx, -1001, 4037, return_launch_info=True,
            )

        self.assertEqual(result, {"session_id": 55, "message_id": 9001})
        self.assertEqual(db.sessions, [(55, -1001, 4037, 2)])
        sleep.assert_awaited_once_with(10)
        safe_send.assert_awaited_once()

    async def test_start_emoji_night_fails_before_success_when_intro_send_fails(self):
        db = FakeEmojiRuntimeDb()
        ctx = SimpleNamespace(bot_data={"db": db}, bot=object())
        puzzles = [{"id": 1, "emoji_prompt": "🍄", "media_type": "game"}]

        with patch.object(emoji_puzzle, "is_feature_enabled", return_value=True), \
             patch.object(emoji_puzzle, "_emoji_settings", return_value=({}, {
                 "puzzle_count": 1,
                 "intro_offset_seconds": 0,
             })), \
             patch.object(emoji_puzzle, "_pick_session_puzzles", new=AsyncMock(return_value=puzzles)), \
             patch.object(emoji_puzzle, "safe_send", new=AsyncMock(side_effect=RuntimeError("telegram down"))), \
             patch.object(emoji_puzzle.asyncio, "create_task") as create_task:
            with self.assertRaisesRegex(RuntimeError, "telegram down"):
                await emoji_puzzle.start_emoji_night(
                    ctx, -1001, 4037, return_launch_info=True,
                )

        create_task.assert_not_called()
        self.assertEqual(db.completed, [(55, [])])

    async def test_wrap_wait_sends_configured_countdown_messages_before_answers(self):
        db = FakeEmojiRuntimeDb()
        puzzles = [
            {"id": 1, "emoji_prompt": "🍄👨‍🔧🐢", "media_type": "game"},
            {"id": 2, "emoji_prompt": "🧙‍♂️💍", "media_type": "movie"},
        ]
        sent_ids = iter(range(100, 110))

        async def fake_send(*args, **kwargs):
            return SimpleNamespace(message_id=next(sent_ids), chat_id=kwargs.get("chat_id"))

        with patch.object(emoji_puzzle, "safe_send", new=AsyncMock(side_effect=fake_send)) as safe_send, \
             patch.object(emoji_puzzle.asyncio, "sleep", new=AsyncMock()) as sleep:
            await emoji_puzzle._run_emoji_session(
                bot=object(),
                db=db,
                session_id=55,
                chat_id=-1001,
                thread_id=4037,
                puzzles=puzzles,
                theme_label="משחקים",
                intro_offset_seconds=0,
                interval_seconds=60,
                wrap_offset_seconds=900,
                wrap_countdown_minutes=[15, 10, 5, 1],
            )

        texts = [call.kwargs["text"] for call in safe_send.await_args_list]
        self.assertEqual(len(texts), 8)
        self.assertIn("15", texts[3])
        self.assertIn("10", texts[4])
        self.assertIn("5", texts[5])
        self.assertIn("1", texts[6])
        self.assertIn("Emoji Night הסתיים", texts[7])
        self.assertGreaterEqual(sleep.await_count, 5)
        self.assertEqual(db.closed, [55])
        self.assertEqual(db.completed, [(55, [])])


if __name__ == "__main__":
    unittest.main()
