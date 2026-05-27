from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from bot.handlers import emoji_puzzle


class FakeEmojiRuntimeDb:
    def __init__(self):
        self.started_rounds = []
        self.closed = []
        self.completed = []

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
