import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database.db import Database
from bot.handlers import polls


class RiddleLeaderboardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmpdir.name, "test.db"))
        await self.db.init()
        await self.db.upsert_member(1, None, "Iris")
        await self.db.upsert_member(2, None, "Dan")

    async def asyncTearDown(self):
        await self.db.close()
        self.tmpdir.cleanup()

    async def _quiz_point(self, uid, name, msg_id, points=5):
        await self.db.log_activity("points", f"+{points} quiz:{msg_id} {name}", uid)

    async def test_counts_only_quiz_points_and_ranks_by_points(self):
        await self._quiz_point(1, "Iris", 100)
        await self._quiz_point(1, "Iris", 101)
        await self._quiz_point(2, "Dan", 100)
        await self.db.log_activity("points", "+10 trivia Dan", 2)  # not a riddle

        leaders = await self.db.get_weekly_riddle_leaders()

        self.assertEqual([(m["user_id"], m["points"], m["wins"]) for m in leaders],
                         [(1, 10, 2), (2, 5, 1)])

    async def test_no_winners_skips_without_sending(self):
        context = SimpleNamespace(bot_data={"db": self.db}, bot=object())
        with patch.object(polls, "safe_send", new=AsyncMock()) as send:
            result = await polls.send_riddle_leaderboard(context)
        self.assertEqual(result, {"skipped": "no riddle winners"})
        send.assert_not_awaited()

    async def test_posts_to_routed_topic_and_tags_winners(self):
        await self._quiz_point(1, "Iris", 100)
        routing = await self.db.get_handler_routing("riddle_leaderboard")
        self.assertIsNotNone(routing, "riddle_leaderboard routing row must be seeded")

        context = SimpleNamespace(bot_data={"db": self.db}, bot=object())
        with patch.object(polls, "safe_send", new=AsyncMock(return_value=SimpleNamespace(message_id=7))) as send, \
             patch.object(polls, "is_auto_blocked_on", return_value=False):
            result = await polls.send_riddle_leaderboard(context)

        self.assertEqual(result, 7)
        kwargs = send.await_args.kwargs
        self.assertEqual(kwargs["message_thread_id"], routing["play_topic_id"])
        self.assertEqual(kwargs["parse_mode"], "HTML")
        self.assertIn("tg://user?id=1", kwargs["text"])
        self.assertNotIn("[copy missing", kwargs["text"])


if __name__ == "__main__":
    unittest.main()
