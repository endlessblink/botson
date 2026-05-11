"""Layer 2 regression: freshness_rejection rejects wrong-day text.

If an LLM ignores the prompt anchor and generates "בוקר של שבת" on a
Sunday row anyway, the freshness check now catches it on the output
side and the materializer's 3-candidate gate retries. This test pins
the rejection patterns and ensures correct-day text and no-day text
both pass through.
"""

from __future__ import annotations

import unittest

from bot.utils.freshness import day_anchor_rejection, freshness_rejection


# 2026-05-10 is Sunday (יום ראשון), 2026-05-09 is Saturday (יום שבת).
SUNDAY = "2026-05-10"
SATURDAY = "2026-05-09"
MONDAY = "2026-05-11"


class DayAnchorRejectsWrongDay(unittest.TestCase):
    def test_rejects_boker_shel_shabat_on_sunday(self):
        # The exact regression seen in prod 2026-05-10.
        reason = day_anchor_rejection("בוקר של שבת — מה דבר שנדחה?", SUNDAY)
        self.assertIsNotNone(reason)
        self.assertIn("שבת", reason or "")
        self.assertIn("ראשון", reason or "")

    def test_rejects_yom_shabat_on_sunday(self):
        reason = day_anchor_rejection("יום שבת רגוע ומזמין", SUNDAY)
        self.assertIsNotNone(reason)

    def test_rejects_erev_shel_wrong_day(self):
        reason = day_anchor_rejection("ערב של חמישי לפני סוף שבוע", SUNDAY)
        self.assertIsNotNone(reason)

    def test_rejects_boker_day_without_shel(self):
        reason = day_anchor_rejection("בוקר שבת — משהו שנשאר לסיים?", SUNDAY)
        self.assertIsNotNone(reason)

    def test_rejects_day_evening_order(self):
        reason = day_anchor_rejection("שני בערב — מה נתתם לעצמכם?", SUNDAY)
        self.assertIsNotNone(reason)

    def test_rejects_day_specific_greeting_on_wrong_day(self):
        reason = day_anchor_rejection("שבת שלום לכולם", SUNDAY)
        self.assertIsNotNone(reason)

    def test_rejects_yom_sheni_on_sunday(self):
        # יום שני = Monday in Hebrew; not "second day" in this fixed pattern.
        reason = day_anchor_rejection("יום שני קשה", SUNDAY)
        self.assertIsNotNone(reason)


class DayAnchorAcceptsCorrectDay(unittest.TestCase):
    def test_accepts_yom_rishon_on_sunday(self):
        self.assertIsNone(day_anchor_rejection("יום ראשון בוקר טוב", SUNDAY))

    def test_accepts_boker_shel_rishon_on_sunday(self):
        self.assertIsNone(day_anchor_rejection("בוקר של ראשון, מה התחלות?", SUNDAY))

    def test_accepts_boker_rishon_without_shel_on_sunday(self):
        self.assertIsNone(day_anchor_rejection("בוקר ראשון, מה התחלות?", SUNDAY))

    def test_accepts_rishon_evening_order_on_sunday(self):
        self.assertIsNone(day_anchor_rejection("ראשון בערב, מה התחלות?", SUNDAY))

    def test_accepts_yom_shabat_on_saturday(self):
        self.assertIsNone(day_anchor_rejection("יום שבת רגוע", SATURDAY))

    def test_accepts_yom_sheni_on_monday(self):
        self.assertIsNone(day_anchor_rejection("יום שני קשה", MONDAY))


class DayAnchorIgnoresUnrelatedText(unittest.TestCase):
    def test_no_day_mention_passes(self):
        # Generic morning content that doesn't claim a specific day.
        text = "מה הדבר הכי קטן שעשיתם השבוע שלא היה קל?"
        self.assertIsNone(day_anchor_rejection(text, SUNDAY))

    def test_missing_scheduled_date_skips_check(self):
        # When the caller doesn't pass a date, the check is a no-op.
        self.assertIsNone(day_anchor_rejection("בוקר של שבת", None))

    def test_unparseable_date_skips_check(self):
        self.assertIsNone(day_anchor_rejection("בוקר של שבת", "not-a-date"))


class FreshnessRejectionIntegratesDayAnchor(unittest.TestCase):
    def test_freshness_rejection_with_scheduled_date_rejects_wrong_day(self):
        reason = freshness_rejection(
            "בוקר של שבת — מה דבר שבשבוע רק נדחק?",
            scheduled_date=SUNDAY,
        )
        self.assertIsNotNone(reason)
        self.assertIn("wrong day-of-week", reason or "")

    def test_freshness_rejection_without_scheduled_date_keeps_old_behavior(self):
        # No date passed → day check is skipped. Wrong-day text still
        # passes (preserves backwards compatibility for any caller that
        # hasn't migrated).
        reason = freshness_rejection("בוקר של שבת — מה דבר שבשבוע רק נדחק?")
        self.assertIsNone(reason)

    def test_freshness_rejection_correct_day_passes(self):
        reason = freshness_rejection(
            "יום ראשון — מה התחלה קטנה שתעשו השבוע?",
            scheduled_date=SUNDAY,
        )
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
