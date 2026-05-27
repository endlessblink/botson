import asyncio
import unittest
from types import SimpleNamespace

from bot.database.db import Database
from bot.handlers.topic_tracker import track_topic


class TestTopicTracker(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_created_topic_is_auto_verified(self):
        async def scenario():
            db = Database(":memory:")
            await db.init()
            try:
                update = SimpleNamespace(message=SimpleNamespace(
                    chat_id=-1001,
                    message_thread_id=7777,
                    text=None,
                    forum_topic_created=SimpleNamespace(name="ערוץ חדש"),
                    forum_topic_edited=None,
                    forum_topic_closed=None,
                    reply_to_message=None,
                ))
                context = SimpleNamespace(bot_data={"db": db})

                await track_topic(update, context)

                verified = await db.get_verified_forum_topic_by_id(7777)
                self.assertIsNotNone(verified)
                self.assertEqual(verified["category_key"], "topic_7777")
                self.assertEqual(verified["verified_name"], "ערוץ חדש")
            finally:
                await db.close()

        self.run_async(scenario())

    def test_edited_topic_preserves_category_key(self):
        async def scenario():
            db = Database(":memory:")
            await db.init()
            try:
                await db.upsert_verified_forum_topic(7777, "שם ישן", "custom_key", "manual")
                update = SimpleNamespace(message=SimpleNamespace(
                    chat_id=-1001,
                    message_thread_id=7777,
                    text=None,
                    forum_topic_created=None,
                    forum_topic_edited=SimpleNamespace(name="שם חדש"),
                    forum_topic_closed=None,
                    reply_to_message=None,
                ))
                context = SimpleNamespace(bot_data={"db": db})

                await track_topic(update, context)

                verified = await db.get_verified_forum_topic_by_id(7777)
                self.assertEqual(verified["category_key"], "custom_key")
                self.assertEqual(verified["verified_name"], "שם חדש")
            finally:
                await db.close()

        self.run_async(scenario())

    def test_closed_topic_is_removed(self):
        async def scenario():
            db = Database(":memory:")
            await db.init()
            try:
                await db.upsert_verified_forum_topic(7777, "ערוץ", "topic_7777", "auto")
                update = SimpleNamespace(message=SimpleNamespace(
                    chat_id=-1001,
                    message_thread_id=7777,
                    text=None,
                    forum_topic_created=None,
                    forum_topic_edited=None,
                    forum_topic_closed=SimpleNamespace(),
                    reply_to_message=None,
                ))
                context = SimpleNamespace(bot_data={"db": db})

                await track_topic(update, context)

                self.assertIsNone(await db.get_verified_forum_topic_by_id(7777))
            finally:
                await db.close()

        self.run_async(scenario())
