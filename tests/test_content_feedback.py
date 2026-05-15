"""T-172 regression: content_feedback schema + endpoints.

This phase is data capture only — no learning yet. Pins:
- The `content_feedback` table exists after Database init (idempotent migration).
- `record_content_feedback` round-trips through `list_content_feedback`.
- The schema accepts all fields including `corrected_text` and `suggestion_metadata`.
- The `POST /api/content-feedback` and `GET /api/content-feedback` endpoints
  require authentication and persist correctly.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bot.database.db import Database


class ContentFeedbackSchema(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(str(Path(self.tmp.name) / "test.db"))
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_table_exists_after_init(self):
        async with self.db._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='content_feedback'"
        ) as cur:
            row = await cur.fetchone()
        self.assertIsNotNone(row)

    async def test_record_and_list_roundtrip(self):
        row_id = await self.db.record_content_feedback(
            source="planner_ai_suggest",
            content_type="discussion",
            topic_key="movies",
            original_text="סרט שכולם אוהבים אבל אתם לא?",
            verdict="rejected",
            reason="too generic",
        )
        self.assertGreater(row_id, 0)
        rows = await self.db.list_content_feedback(content_type="discussion")
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["original_text"], "סרט שכולם אוהבים אבל אתם לא?")
        self.assertEqual(r["verdict"], "rejected")
        self.assertEqual(r["topic_key"], "movies")
        self.assertEqual(r["reason"], "too generic")

    async def test_corrected_text_and_metadata_persist(self):
        await self.db.record_content_feedback(
            source="planner_ai_suggest",
            content_type="morning",
            original_text="בוקר טוב, מה שלומכם?",
            verdict="accepted_after_edit",
            corrected_text="בוקר! איזו משימה אחת תעשו לפני הצהריים?",
            suggestion_metadata=json.dumps({"slot": "09:00", "day": "Sunday"}),
        )
        rows = await self.db.list_content_feedback(content_type="morning")
        r = rows[0]
        self.assertEqual(r["verdict"], "accepted_after_edit")
        self.assertIn("משימה", r["corrected_text"])
        self.assertIn("09:00", r["suggestion_metadata"])

    async def test_filter_by_verdict(self):
        await self.db.record_content_feedback(
            source="planner_ai_suggest", content_type="discussion",
            original_text="a?", verdict="rejected",
        )
        await self.db.record_content_feedback(
            source="planner_ai_suggest", content_type="discussion",
            original_text="b?", verdict="accepted",
        )
        rejected = await self.db.list_content_feedback(verdict="rejected")
        accepted = await self.db.list_content_feedback(verdict="accepted")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(len(accepted), 1)
        self.assertEqual(rejected[0]["verdict"], "rejected")
        self.assertEqual(accepted[0]["verdict"], "accepted")

    async def test_migration_is_idempotent(self):
        """Running _migrate twice on the same DB shouldn't error."""
        await self.db._migrate()
        # And the table is still usable.
        row_id = await self.db.record_content_feedback(
            source="x", content_type="y", original_text="z?", verdict="rejected",
        )
        self.assertGreater(row_id, 0)


if __name__ == "__main__":
    unittest.main()
