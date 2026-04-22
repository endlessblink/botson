import tempfile
import unittest
from pathlib import Path

from bot.database.db import Database


SEEDED_HANDLERS = {
    "trivia_round",
    "trivia_scheduled",
    "emoji_puzzle",
    "free_games",
    "weekly_roundup",
    "weekly_leaderboard",
    "events_publish",
    "events_reminder",
}


class HandlerRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def _fresh_db(self):
        tmp = tempfile.TemporaryDirectory()
        db = Database(str(Path(tmp.name) / "t.db"))
        await db.init()
        self.addAsyncCleanup(db.close)
        self.addCleanup(tmp.cleanup)
        return db

    async def test_seeded_defaults_present_after_init(self):
        db = await self._fresh_db()
        rows = await db.list_handler_routings()
        handlers = {row["handler"] for row in rows}
        self.assertEqual(handlers, SEEDED_HANDLERS)

    async def test_seeded_default_targets(self):
        db = await self._fresh_db()
        self.assertEqual((await db.get_handler_routing("trivia_round"))["play_topic_id"], 4037)
        self.assertEqual((await db.get_handler_routing("weekly_roundup"))["play_topic_id"], 4037)
        self.assertEqual((await db.get_handler_routing("events_publish"))["play_topic_id"], 341)
        self.assertEqual((await db.get_handler_routing("events_reminder"))["play_topic_id"], 341)

    async def test_seed_does_not_overwrite_operator_edits(self):
        db = await self._fresh_db()
        await db.set_handler_routing("trivia_round", play_topic_id=54, teaser_topic_ids=[])
        # Re-run the seed (simulates a re-migration). Operator value must persist.
        await db._seed_default_handler_routing()
        row = await db.get_handler_routing("trivia_round")
        self.assertEqual(row["play_topic_id"], 54)

    async def test_set_and_get_routing_with_teasers(self):
        db = await self._fresh_db()
        await db.set_handler_routing("trivia_round", play_topic_id=4037, teaser_topic_ids=[54, 1431])
        row = await db.get_handler_routing("trivia_round")
        self.assertEqual(row["play_topic_id"], 4037)
        self.assertEqual(row["teaser_topic_ids"], [54, 1431])

    async def test_get_unknown_handler_returns_none(self):
        db = await self._fresh_db()
        self.assertIsNone(await db.get_handler_routing("no_such_handler"))

    async def test_null_play_topic_id_effectively_disables(self):
        db = await self._fresh_db()
        await db.set_handler_routing("weekly_roundup", play_topic_id=None, teaser_topic_ids=[])
        row = await db.get_handler_routing("weekly_roundup")
        self.assertIsNone(row["play_topic_id"])


if __name__ == "__main__":
    unittest.main()
