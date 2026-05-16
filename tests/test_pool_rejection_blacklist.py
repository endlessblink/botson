"""T-183 (Gap 5): pool selectors must skip items the operator rejected.

The facts pool is the demonstration. The same pattern extends to emoji
and trivia later.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from bot.database.db import Database
from bot.handlers import facts as facts_handler


class PoolBlacklistTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.db = Database(self.db_path)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_rejected_text_added_to_blacklist_set(self):
        """get_rejected_pool_texts returns operator-rejected texts."""
        await self.db.record_content_feedback(
            source="qa_scoring",
            content_type="facts_tidbit",
            topic_key=None,
            original_text="עובדה גרועה שהאופרטור פסל",
            verdict="rejected",
            reason="off-tone",
        )
        await self.db.record_content_feedback(
            source="qa_scoring",
            content_type="facts_tidbit",
            topic_key=None,
            original_text="עובדה אחרת — אישור",
            verdict="accepted",
            reason="good",
        )
        excluded = await self.db.get_rejected_pool_texts(content_type="facts_tidbit")
        self.assertIn("עובדה גרועה שהאופרטור פסל", excluded)
        # Accepted items must NOT be in the blacklist.
        self.assertNotIn("עובדה אחרת — אישור", excluded)

    async def test_blacklist_filters_by_content_type(self):
        """A movies rejection must not blacklist a facts pick (different
        content_type buckets)."""
        await self.db.record_content_feedback(
            source="qa_scoring",
            content_type="discussion",  # movies discussion
            topic_key="movies",
            original_text="מה הסרט האהוב?",
            verdict="rejected",
            reason="generic",
        )
        excluded_facts = await self.db.get_rejected_pool_texts(content_type="facts_tidbit")
        self.assertNotIn("מה הסרט האהוב?", excluded_facts)
        excluded_discussion = await self.db.get_rejected_pool_texts(content_type="discussion")
        self.assertIn("מה הסרט האהוב?", excluded_discussion)

    async def test_pick_fact_skips_excluded_text(self):
        """The pool selector honors `excluded_texts` and refuses to pick
        a matched item even if it's the only one in the pool."""
        sample_pool = [
            {"id": "f1", "text_he": "אחת עובדה רגילה", "source": "X", "source_url": "http://x"},
            {"id": "f2", "text_he": "פסולה — אופרטור דחה", "source": "Y", "source_url": "http://y"},
            {"id": "f3", "text_he": "עובדה שלישית", "source": "Z", "source_url": "http://z"},
        ]
        with patch.object(facts_handler, "load_facts_pool", return_value=sample_pool):
            picked_ids = set()
            for _ in range(30):
                f = facts_handler.pick_fact(
                    "tidbit", [], excluded_texts={"פסולה — אופרטור דחה"},
                )
                self.assertIsNotNone(f)
                picked_ids.add(f["id"])
            # The blacklisted item must never be picked.
            self.assertNotIn("f2", picked_ids)
            # The other two must be reachable (sanity: not a degenerate filter).
            self.assertIn("f1", picked_ids)
            self.assertIn("f3", picked_ids)

    async def test_pick_fact_returns_none_when_all_excluded(self):
        """When every pool item is on the blacklist, return None rather
        than picking a rejected item — caller will log + skip."""
        sample_pool = [
            {"id": "f1", "text_he": "פסולה 1", "source": "X", "source_url": "http://x"},
            {"id": "f2", "text_he": "פסולה 2", "source": "Y", "source_url": "http://y"},
        ]
        with patch.object(facts_handler, "load_facts_pool", return_value=sample_pool):
            f = facts_handler.pick_fact(
                "tidbit", [], excluded_texts={"פסולה 1", "פסולה 2"},
            )
            self.assertIsNone(f)

    async def test_pick_fact_normalises_whitespace(self):
        """Trailing whitespace / line breaks in either side must not
        defeat the filter."""
        sample_pool = [
            {"id": "f1", "text_he": "טקסט\nעם שורות", "source": "X", "source_url": "http://x"},
        ]
        with patch.object(facts_handler, "load_facts_pool", return_value=sample_pool):
            # Excluded with different whitespace must still match.
            f = facts_handler.pick_fact(
                "tidbit", [], excluded_texts={"טקסט עם שורות"},
            )
            self.assertIsNone(f, "whitespace-normalised match should filter")

    async def test_empty_excluded_set_is_noop(self):
        """When no rejections exist, the selector behaves exactly as before."""
        sample_pool = [
            {"id": "f1", "text_he": "x", "source": "X", "source_url": "http://x"},
        ]
        with patch.object(facts_handler, "load_facts_pool", return_value=sample_pool):
            f = facts_handler.pick_fact("tidbit", [], excluded_texts=set())
            self.assertIsNotNone(f)
            self.assertEqual(f["id"], "f1")
            f = facts_handler.pick_fact("tidbit", [], excluded_texts=None)
            self.assertIsNotNone(f)
            self.assertEqual(f["id"], "f1")


if __name__ == "__main__":
    unittest.main()
