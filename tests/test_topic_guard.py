import asyncio
import tempfile
import unittest
from pathlib import Path

from bot.database.db import Database
from bot.utils import topic_guard
from bot.utils.topic_guard import UnverifiedTopicError, verify_send_target


class FakeDatabase:
    def __init__(self, verified_ids: set[int]):
        self._verified_ids = verified_ids

    async def is_verified_topic_id(self, topic_id: int) -> bool:
        return int(topic_id) in self._verified_ids


class TopicGuardTests(unittest.TestCase):
    def setUp(self):
        # Pin GROUP_ID / TEST_GROUP_ID the guard reads from env-bound module globals.
        self._orig_group = topic_guard.GROUP_ID
        self._orig_test = topic_guard.TEST_GROUP_ID
        topic_guard.GROUP_ID = -1001
        topic_guard.TEST_GROUP_ID = -1002

    def tearDown(self):
        topic_guard.GROUP_ID = self._orig_group
        topic_guard.TEST_GROUP_ID = self._orig_test

    def _run(self, coro):
        return asyncio.run(coro)

    def test_dm_send_passes(self):
        db = FakeDatabase(verified_ids=set())
        self._run(verify_send_target(db, chat_id=42, message_thread_id=None))

    def test_test_group_send_passes(self):
        db = FakeDatabase(verified_ids=set())
        self._run(verify_send_target(db, chat_id=-1002, message_thread_id=None))
        self._run(verify_send_target(db, chat_id=-1002, message_thread_id=9999))

    def test_main_group_root_send_blocked(self):
        db = FakeDatabase(verified_ids={7, 4037})
        with self.assertRaises(UnverifiedTopicError) as ctx:
            self._run(verify_send_target(db, chat_id=-1001, message_thread_id=None))
        self.assertIn("no message_thread_id", str(ctx.exception))

    def test_main_group_unverified_thread_blocked(self):
        db = FakeDatabase(verified_ids={7, 4037})
        with self.assertRaises(UnverifiedTopicError) as ctx:
            self._run(verify_send_target(db, chat_id=-1001, message_thread_id=999))
        self.assertIn("not in verified_forum_topics", str(ctx.exception))

    def test_main_group_verified_thread_passes(self):
        db = FakeDatabase(verified_ids={7, 4037})
        self._run(verify_send_target(db, chat_id=-1001, message_thread_id=4037))

    def test_bypass_verification_skips_all_checks(self):
        db = FakeDatabase(verified_ids=set())
        self._run(
            verify_send_target(
                db,
                chat_id=-1001,
                message_thread_id=999,
                bypass_verification=True,
            )
        )
        self._run(
            verify_send_target(
                db,
                chat_id=-1001,
                message_thread_id=None,
                bypass_verification=True,
            )
        )


class DatabaseVerifiedLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_is_verified_topic_id_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "t.db"
            db = Database(str(db_path))
            await db.init()
            self.assertFalse(await db.is_verified_topic_id(4037))
            await db.upsert_verified_forum_topic(
                topic_id=4037,
                verified_name="הפינה של בוטסון",
                category_key="botson_corner",
                verification_source="test",
            )
            self.assertTrue(await db.is_verified_topic_id(4037))
            self.assertFalse(await db.is_verified_topic_id(999))
            await db.close()


if __name__ == "__main__":
    unittest.main()
