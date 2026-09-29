"""Approved conversation posts are checked early: hard-rule blocks are warned
ahead of time, and the AI quality review is advice that never cancels a post."""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import calendar


def _row(minutes_ahead=120, text="approved question", created_by="dashboard", row_id=7):
    due = datetime.now(calendar._IL_TZ) + timedelta(minutes=minutes_ahead)
    return {
        "id": row_id, "status": "scheduled", "message_type": "discussion",
        "scheduled_date": due.strftime("%Y-%m-%d"), "scheduled_time": due.strftime("%H:%M"),
        "text": text, "created_by": created_by,
    }


class PrecheckTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        calendar._PRECHECK_REPORTED.clear()
        calendar._PRECHECK_REVIEWED.clear()

    async def run_precheck(self, rows, *, verdict=(True, "fine"), freshness=None):
        db = SimpleNamespace(get_scheduled_messages=AsyncMock(return_value=rows))
        context = SimpleNamespace(bot_data={"db": db}, bot=AsyncMock())
        notify = AsyncMock(return_value=1)
        review = AsyncMock(return_value=verdict)
        with patch.object(calendar, "notify_admins", new=notify), \
             patch("bot.utils.conversation_quality.review_conversation", new=review), \
             patch("bot.scheduler.materializer._used_texts_for_type", new=AsyncMock(return_value=[])), \
             patch("bot.utils.freshness.freshness_rejection", return_value=freshness), \
             patch("bot.utils.config.get_settings", return_value={"schedule": {"conversation_precheck": {
                 "enabled": True, "lookahead_minutes": 1440, "max_reviews_per_run": 3}}}):
            await calendar.precheck_scheduled_conversations(context)
        return notify, review

    async def test_weak_post_gets_one_advisory_alert(self):
        rows = [_row()]
        notify, review = await self.run_precheck(rows, verdict=(False, "too generic"))
        notify.assert_awaited_once()
        self.assertIn("too generic", notify.call_args.args[1])
        notify, review = await self.run_precheck(rows, verdict=(False, "too generic"))
        notify.assert_not_awaited()
        review.assert_not_awaited()

    async def test_good_post_is_silent(self):
        notify, review = await self.run_precheck([_row()])
        review.assert_awaited_once()
        notify.assert_not_awaited()

    async def test_hard_block_is_warned_before_send_time(self):
        notify, review = await self.run_precheck([_row()], freshness="duplicate")
        notify.assert_awaited_once()
        self.assertIn("conversation_freshness", notify.call_args.args[1])
        review.assert_not_awaited()

    async def test_reviewer_outage_is_retried_later_not_alerted(self):
        rows = [_row()]
        notify, _ = await self.run_precheck(rows, verdict=(False, "semantic review unavailable: x"))
        notify.assert_not_awaited()
        notify, review = await self.run_precheck(rows, verdict=(False, "weak"))
        review.assert_awaited_once()
        notify.assert_awaited_once()

    async def test_edited_text_is_reviewed_again(self):
        await self.run_precheck([_row(text="v1")], verdict=(False, "weak"))
        notify, review = await self.run_precheck([_row(text="v2")], verdict=(False, "weak"))
        review.assert_awaited_once()
        notify.assert_awaited_once()

    async def test_past_and_far_rows_are_ignored(self):
        notify, review = await self.run_precheck([_row(minutes_ahead=-5), _row(minutes_ahead=3000, row_id=8)])
        review.assert_not_awaited()
        notify.assert_not_awaited()

    async def test_review_budget_per_run(self):
        rows = [_row(row_id=i, text=f"q{i}") for i in range(5)]
        _, review = await self.run_precheck(rows)
        self.assertEqual(review.await_count, 3)
