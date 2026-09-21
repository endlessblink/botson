from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from bot.handlers import weekly_state_review


def test_build_weekly_state_review_uses_configured_question_and_tags():
    text = weekly_state_review.build_weekly_state_review({
        "question": "על איזה פרויקט בינה מלאכותית עבדתם השבוע?",
        "tag_usernames": ["noam", "friend", "builder"],
    })

    assert text == "על איזה פרויקט בינה מלאכותית עבדתם השבוע?\n\n@noam @friend @builder"


def test_build_weekly_state_review_returns_none_without_question():
    assert weekly_state_review.build_weekly_state_review({"question": "", "tag_usernames": []}) is None


class WeeklyStateReviewAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_weekly_state_review_skips_when_disabled(self):
        context = SimpleNamespace(bot=object(), bot_data={"db": object()})
        with patch.object(weekly_state_review, "get_settings", return_value={
            "weekly_state_review": {"enabled": False},
        }), patch.object(weekly_state_review, "safe_send", new=AsyncMock()) as safe_send:
            self.assertIsNone(await weekly_state_review.send_weekly_state_review(context))
        safe_send.assert_not_awaited()
