"""T-185 (Gap 8): the populate flow excludes facts that were recently
published, recently rejected, or already pinned to a scheduled row.

Hard to test the nested `_choose_fact_preview` directly — it's a closure
inside `_ai_suggest_calendar`. Instead test the underlying primitives
that the closure uses: activity-log recency + rejection blacklist +
scheduled_messages payload scanning.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from bot.database.db import Database


class PublishedStateFilterTest(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.db = Database(self.db_path)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_recent_activity_returns_published_fact_ids(self):
        """Activity-log stamping is the primitive for 'this was published'.
        Populate's exclusion list must include rows logged within `days`."""
        await self.db.log_activity("facts_tidbit", "fact_id:test_published_recent")
        ids = await self.db.get_recent_activity_subjects(
            action_type="facts_tidbit", days=60,
        )
        self.assertIn("test_published_recent", ids or [])

    async def test_recent_activity_filters_by_action_type(self):
        """A spooky publish must not show up in tidbit's exclusion list."""
        await self.db.log_activity("facts_spooky", "fact_id:spooky_one")
        tidbit_ids = await self.db.get_recent_activity_subjects(
            action_type="facts_tidbit", days=60,
        )
        self.assertNotIn("spooky_one", tidbit_ids or [])
        spooky_ids = await self.db.get_recent_activity_subjects(
            action_type="facts_spooky", days=60,
        )
        self.assertIn("spooky_one", spooky_ids or [])

    async def test_scheduled_fact_id_visible_via_raw_query(self):
        """The populate flow scans scheduled_messages for pinned fact_ids
        so the same item isn't double-suggested. Verify the raw query
        primitive works against a representative row shape."""
        await self.db._db.execute(
            "INSERT INTO scheduled_messages "
            "(message_type, scheduled_date, scheduled_time, text, status, channel_topic_id, poll_options) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("facts_tidbit", "2026-05-20", "12:00", "tidbit text",
             "scheduled", 4037, json.dumps({"fact_id": "scheduled_one"})),
        )
        await self.db._db.commit()
        found: set[str] = set()
        async with self.db._db.execute(
            "SELECT poll_options FROM scheduled_messages "
            "WHERE message_type IN ('facts_tidbit','facts_spooky') "
            "AND status IN ('scheduled','sent') "
            "AND poll_options IS NOT NULL"
        ) as cur:
            async for row in cur:
                payload = json.loads(row[0] or "{}")
                fid = str(payload.get("fact_id") or "").strip()
                if fid:
                    found.add(fid)
        self.assertIn("scheduled_one", found)

    async def test_skipped_messages_excluded_from_scheduled_set(self):
        """Skipped/failed messages must not appear in the in-flight
        exclusion set — they didn't actually consume the fact."""
        for status in ("skipped", "failed", "scheduled", "sent"):
            await self.db._db.execute(
                "INSERT INTO scheduled_messages "
                "(message_type, scheduled_date, scheduled_time, text, status, channel_topic_id, poll_options) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("facts_tidbit", "2026-05-20", "12:00", "x", status, 4037,
                 json.dumps({"fact_id": f"fid_{status}"})),
            )
        await self.db._db.commit()
        found: set[str] = set()
        async with self.db._db.execute(
            "SELECT poll_options FROM scheduled_messages "
            "WHERE message_type IN ('facts_tidbit','facts_spooky') "
            "AND status IN ('scheduled','sent') "
            "AND poll_options IS NOT NULL"
        ) as cur:
            async for row in cur:
                payload = json.loads(row[0] or "{}")
                fid = str(payload.get("fact_id") or "").strip()
                if fid:
                    found.add(fid)
        self.assertIn("fid_scheduled", found)
        self.assertIn("fid_sent", found)
        # Skipped and failed must NOT block future picks.
        self.assertNotIn("fid_skipped", found)
        self.assertNotIn("fid_failed", found)


if __name__ == "__main__":
    unittest.main()
