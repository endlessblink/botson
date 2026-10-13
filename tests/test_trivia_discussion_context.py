import unittest
from datetime import datetime, timedelta, timezone

from dashboard.trivia_context import discussion_trivia_subject, TriviaContextUnavailable


class DiscussionTriviaContextTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 11, 20, tzinfo=timezone.utc)
        self.message = dict(source="member", chat_id=-100, thread_id=42,
                            message_id=7, text="בדוגמה לבדיקה דיברו על אסטרונומיה",
                            occurred_at=self.now.isoformat())
        self.selection = dict(theme_label="אסטרונומיה", anchor="אסטרונומיה", source_message_ids=[7])

    def select(self, messages=None, selection=None, **kwargs):
        return discussion_trivia_subject(
            [self.message] if messages is None else messages,
            self.selection if selection is None else selection,
            chat_id=-100, context_receipt=kwargs.pop("context_receipt", "test-receipt"),
            now=self.now, thread_id=42, **kwargs,
        )

    def test_explicit_strict_theme_keeps_source_evidence(self):
        result = self.select()
        self.assertEqual(result["categories"], ["אסטרונומיה"])
        self.assertEqual(result["discussion_context"]["source_message_ids"], [7])
        self.assertEqual(result["discussion_context"]["context_receipt"], "test-receipt")

    def test_generated_or_bot_posts_never_establish_subject(self):
        for source in ("botson", "generated", "calendar", None):
            with self.subTest(source=source), self.assertRaises(TriviaContextUnavailable):
                self.select([dict(self.message, source=source)])

    def test_future_expired_and_naive_context_rejected(self):
        for date in (self.now + timedelta(seconds=1), self.now - timedelta(hours=25), self.now.replace(tzinfo=None)):
            with self.subTest(date=date), self.assertRaises(TriviaContextUnavailable):
                self.select([dict(self.message, occurred_at=date.isoformat())])

    def test_wrong_group_or_topic_never_establish_subject(self):
        for replacement in (dict(chat_id=-200), dict(thread_id=43)):
            with self.subTest(replacement=replacement), self.assertRaises(TriviaContextUnavailable):
                self.select([dict(self.message, **replacement)])

    def test_receipt_and_actual_source_required(self):
        with self.assertRaisesRegex(TriviaContextUnavailable, "receipt"):
            self.select(context_receipt="")
        for selection in (dict(self.selection, source_message_ids=[8]), dict(self.selection, anchor="היסטוריה")):
            with self.subTest(selection=selection), self.assertRaises(TriviaContextUnavailable):
                self.select(selection=selection)

    def test_no_pool_or_general_fallback_without_context(self):
        with self.assertRaisesRegex(TriviaContextUnavailable, "fresh_member_discussion"):
            self.select([])

    def test_unrelated_bank_theme_cannot_reuse_real_discussion_evidence(self):
        with self.assertRaisesRegex(TriviaContextUnavailable, "theme_must_match"):
            self.select(selection=dict(self.selection, theme_label="גיאוגרפיה"))


if __name__ == "__main__":
    unittest.main()
