import os
import tempfile
from unittest import IsolatedAsyncioTestCase

from bot.database.db import Database


class MemberActivityDatabaseTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.path = handle.name
        self.db = Database(self.path)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()
        os.unlink(self.path)

    async def test_report_counts_signals_and_respects_opt_in(self):
        await self.db.upsert_chat_member(1, 10, "active", "Active")
        await self.db.record_member_activity(1, 10, "message", "message-1")
        await self.db.record_member_activity(1, 10, "reaction", "reaction-1")
        await self.db.upsert_chat_member(1, 11, None, "Quiet")

        report = await self.db.get_member_activity_report(1, window_days=90)
        assert report["total"] == 2
        assert report["active"] == 1
        assert report["candidate_count"] == 1

        campaign_id = await self.db.create_member_cleanup_campaign(
            1, deadline_at="2099-01-01 00:00:00", activity_window_days=90
        )
        await self.db.record_member_cleanup_optin(campaign_id, 1, 11)
        report = await self.db.get_member_activity_report(
            1, window_days=90, campaign_id=campaign_id
        )
        assert report["opted_in"] == 1
        assert report["candidate_count"] == 0
