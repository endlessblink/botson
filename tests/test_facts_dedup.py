"""Regression tests for Phase A.1 dedup wiring.

Covers:
- Database.get_recent_activity_subjects parses fact_id markers correctly
  and respects the time window.
- pick_fact excludes ids returned by get_recent_activity_subjects.
- send_scheduled_fact stamps fact_id into the activity_log description
  on the next call (end-to-end so the silent-hasattr regression class
  can't return).
- Database.get_recent_emoji_puzzle_ids returns ids from recent rounds
  and respects the window.
"""
import asyncio
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from bot.database.db import Database
from bot.handlers import facts as facts_handler


class GetRecentActivitySubjectsTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_fact_id_markers(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                await db.log_activity("facts_spooky", "fact_id:dybbuk_origin")
                await db.log_activity("facts_spooky", "fact_id:montefiore_ghost")
                await db.log_activity("facts_spooky", "no marker here")
                await db.log_activity("facts_tidbit", "fact_id:roman_concrete")
                ids = await db.get_recent_activity_subjects(
                    action_type="facts_spooky", days=60,
                )
                self.assertCountEqual(ids, ["dybbuk_origin", "montefiore_ghost"])
            finally:
                await db.close()

    async def test_zero_days_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                await db.log_activity("facts_spooky", "fact_id:dybbuk_origin")
                ids = await db.get_recent_activity_subjects(
                    action_type="facts_spooky", days=0,
                )
                self.assertEqual(ids, [])
            finally:
                await db.close()

    async def test_alternate_key(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                await db.log_activity("discussion", "category:cute hash:abc123")
                ids = await db.get_recent_activity_subjects(
                    action_type="discussion", days=14, key="hash",
                )
                self.assertEqual(ids, ["abc123"])
            finally:
                await db.close()


class PickFactExcludesRecentTests(unittest.TestCase):
    def test_pick_fact_excludes_recent_ids(self):
        # Use the real spooky pool but recently-sent excludes most ids.
        # If the pool has N items and we exclude N-1, only the remaining
        # one is eligible.
        items = facts_handler.load_facts_pool("spooky")
        if len(items) < 2:
            self.skipTest("spooky pool too small for this test")
        keep = items[-1]["id"]
        recent = [i["id"] for i in items[:-1]]
        # Run multiple times: result must be deterministic (only one
        # eligible) → always returns the kept item.
        for _ in range(10):
            picked = facts_handler.pick_fact("spooky", recent)
            self.assertIsNotNone(picked, "pick_fact returned None despite eligible item")
            self.assertEqual(picked["id"], keep)

    def test_pick_fact_returns_none_when_pool_empty(self):
        items = facts_handler.load_facts_pool("spooky")
        all_ids = [i["id"] for i in items]
        picked = facts_handler.pick_fact("spooky", all_ids)
        self.assertIsNone(picked)


class SendScheduledFactStampsActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_scheduled_fact_logs_fact_id_marker(self):
        """End-to-end: after send_scheduled_fact, activity_log has a row
        whose description starts with 'fact_id:<id>' so the next call's
        cooldown lookup excludes it."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                # Mock the send path so we don't actually hit Telegram/kie.ai.
                with patch.object(
                    facts_handler, "_resolve_fact_photo",
                    new=AsyncMock(return_value=("dummy-photo", "caption")),
                ), patch.object(
                    facts_handler, "safe_send",
                    new=AsyncMock(return_value=None),
                ):
                    sent_ok = await facts_handler.send_scheduled_fact(
                        bot=object(), db=db, pool="spooky",
                        chat_id=-1001, thread_id=4037,
                    )
                self.assertTrue(sent_ok)
                rows = await db.get_activity_log(limit=10)
                facts_rows = [r for r in rows if r["action_type"] == "facts_spooky"]
                self.assertTrue(facts_rows, "expected at least one facts_spooky log row")
                self.assertTrue(
                    facts_rows[0]["description"].startswith("fact_id:"),
                    f"description should start with 'fact_id:', got: {facts_rows[0]['description']!r}",
                )
                # Round-trip via the cooldown query
                ids = await db.get_recent_activity_subjects(
                    action_type="facts_spooky", days=60,
                )
                self.assertEqual(len(ids), 1)
                self.assertEqual(
                    facts_rows[0]["description"],
                    f"fact_id:{ids[0]}",
                )
            finally:
                await db.close()


class GetRecentEmojiPuzzleIdsTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_distinct_puzzle_ids_from_recent_rounds(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                # Create a session, then rounds for 3 distinct puzzles.
                session_id = await db.create_emoji_session(-1001, 4037, 5)
                await db.start_emoji_round(session_id, 1, -1001, 100, 4037, 0)
                await db.start_emoji_round(session_id, 2, -1001, 101, 4037, 0)
                await db.start_emoji_round(session_id, 1, -1001, 102, 4037, 0)  # dup
                await db.start_emoji_round(session_id, 3, -1001, 103, 4037, 0)
                ids = await db.get_recent_emoji_puzzle_ids(days=30)
                self.assertEqual(ids, {1, 2, 3})
            finally:
                await db.close()

    async def test_zero_days_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                session_id = await db.create_emoji_session(-1001, 4037, 5)
                await db.start_emoji_round(session_id, 1, -1001, 100, 4037, 0)
                ids = await db.get_recent_emoji_puzzle_ids(days=0)
                self.assertEqual(ids, set())
            finally:
                await db.close()


if __name__ == "__main__":
    unittest.main()
