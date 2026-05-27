import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database.db import Database
from bot.scheduler import topic_sync
from bot.scheduler.topic_sync import SyncedForumTopic


class TestTopicSync(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_reconcile_upserts_new_topics_and_removes_missing(self):
        async def scenario():
            db = Database(":memory:")
            await db.init()
            try:
                await db.upsert_verified_forum_topic(111, "ישן", "old_key", "manual")
                await db.upsert_verified_forum_topic(222, "נשאר", "keep_key", "manual")

                result = await topic_sync.reconcile_forum_topics(db, [
                    SyncedForumTopic(222, "נשאר מעודכן"),
                    SyncedForumTopic(333, "חדש"),
                ])

                self.assertEqual(result, {"upserted": 2, "removed": 1, "live": 2})
                rows = await db.get_verified_forum_topics()
                by_id = {row["topic_id"]: row for row in rows}
                self.assertNotIn(111, by_id)
                self.assertEqual(by_id[222]["category_key"], "keep_key")
                self.assertEqual(by_id[222]["verified_name"], "נשאר מעודכן")
                self.assertEqual(by_id[333]["category_key"], "topic_333")
            finally:
                await db.close()

        self.run_async(scenario())

    def test_empty_live_list_does_not_delete_everything(self):
        async def scenario():
            db = Database(":memory:")
            await db.init()
            try:
                await db.upsert_verified_forum_topic(111, "קיים", "existing", "manual")

                result = await topic_sync.reconcile_forum_topics(db, [])

                self.assertEqual(result, {"upserted": 0, "removed": 0, "live": 0})
                self.assertIsNotNone(await db.get_verified_forum_topic_by_id(111))
            finally:
                await db.close()

        self.run_async(scenario())

    def test_job_uses_fetcher_and_reconcile(self):
        async def scenario():
            db = Database(":memory:")
            await db.init()
            try:
                with patch.object(
                    topic_sync,
                    "fetch_forum_topics",
                    new=AsyncMock(return_value=[SyncedForumTopic(333, "חדש")]),
                ):
                    await topic_sync.topic_sync_job(SimpleNamespace(bot_data={"db": db}))

                row = await db.get_verified_forum_topic_by_id(333)
                self.assertEqual(row["category_key"], "topic_333")
            finally:
                await db.close()

        self.run_async(scenario())

    def test_register_job_even_when_credentials_are_missing(self):
        class FakeJobQueue:
            def __init__(self):
                self.calls = []

            def run_repeating(self, callback, **kwargs):
                self.calls.append((callback, kwargs))

        job_queue = FakeJobQueue()
        app = SimpleNamespace(job_queue=job_queue)

        with patch.dict("os.environ", {"TELEGRAM_API_ID": "", "TELEGRAM_API_HASH": ""}, clear=False):
            registered = topic_sync.register_topic_sync_job(app)

        self.assertTrue(registered)
        self.assertEqual(len(job_queue.calls), 1)
        self.assertEqual(job_queue.calls[0][1]["name"], "forum_topic_sync")

    def test_telethon_request_import_path_when_available(self):
        try:
            from telethon.tl.functions.channels import GetForumTopicsRequest
        except ImportError:
            self.skipTest("telethon not installed in this test environment")

        self.assertIsNotNone(GetForumTopicsRequest)
