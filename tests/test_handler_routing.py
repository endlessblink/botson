import tempfile
import unittest
from pathlib import Path

from bot.database.db import Database


SEEDED_HANDLERS = {
    "trivia_round",
    "trivia_scheduled",
    "trivia_warmup",
    "emoji_puzzle",
    "free_games",
    "facts_tidbit",
    "facts_spooky",
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
        await db._normalize_game_warmup_routing()
        row = await db.get_handler_routing("trivia_round")
        self.assertEqual(row["play_topic_id"], 54)
        warmup = await db.get_handler_routing("trivia_warmup")
        self.assertEqual(warmup["play_topic_id"], 4037)

    async def test_trivia_warmup_routing_is_independent_public_teaser_route(self):
        db = await self._fresh_db()
        await db.set_handler_routing("trivia_warmup", play_topic_id=341, teaser_topic_ids=[])
        trivia = await db.get_handler_routing("trivia_round")
        warmup = await db.get_handler_routing("trivia_warmup")
        self.assertEqual(trivia["play_topic_id"], 4037)
        self.assertEqual(warmup["play_topic_id"], 341)

    async def test_startup_preserves_legacy_warmup_route(self):
        db = await self._fresh_db()
        await db._db.execute(
            "UPDATE bot_message_routing SET play_topic_id = 4037 WHERE handler = 'trivia_round'"
        )
        await db._db.execute(
            "UPDATE bot_message_routing SET play_topic_id = 341 WHERE handler = 'trivia_warmup'"
        )
        await db._db.commit()

        await db._normalize_game_warmup_routing()

        warmup = await db.get_handler_routing("trivia_warmup")
        self.assertEqual(warmup["play_topic_id"], 341)

    async def test_pending_game_warmup_normalization_preserves_teaser_topic(self):
        db = await self._fresh_db()
        await db._db.execute(
            "UPDATE bot_message_routing SET play_topic_id = 4037 WHERE handler = 'trivia_round'"
        )
        await db._db.execute(
            "UPDATE bot_message_routing SET play_topic_id = 341 WHERE handler = 'trivia_warmup'"
        )
        await db._db.commit()
        msg_id = await db.create_scheduled_message(
            text="warmup",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=341,
            target_group="main",
            scheduled_date="2099-01-01",
            scheduled_time="20:00",
            poll_options='{"warmup_marker":"warmup-rsvp:trivia:2099-01-01:21:00"}',
            status="scheduled",
        )

        await db._normalize_pending_game_warmup_topics()
        row = await db.get_scheduled_message(msg_id)

        self.assertEqual(row["channel_topic_id"], 341)

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
