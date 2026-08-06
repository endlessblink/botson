"""T-169 regression: freshness_rejection must catch echoes of pool examples
and paraphrased near-duplicates of prior scheduled texts.

The legacy implementation only caught exact substring matches, which let
two failure modes through:

1. The LLM echoed a pool example verbatim — historically allowed because
   the substring check happened, but apparently the model's whitespace or
   emoji decoration broke exact match and the row shipped.
2. The LLM lightly paraphrased a prior scheduled text — same content
   tokens in a different order — and substring dedup missed it.

This test pins both behaviors and the Hebrew-normalization primitive.
"""

from __future__ import annotations

import unittest

from bot.utils.freshness import (
    freshness_rejection,
    hebrew_normalize,
    near_duplicate,
)


class HebrewNormalizeStripsNoise(unittest.TestCase):
    def test_strips_nikud_and_punctuation(self):
        raw = "שָׁלוֹם! מה שלומך?"
        normalized = hebrew_normalize(raw)
        self.assertNotIn("ָ", normalized)
        self.assertNotIn("ֹ", normalized)
        self.assertNotIn("!", normalized)
        self.assertIn("שלום", normalized)
        self.assertIn("מה", normalized)

    def test_collapses_whitespace_and_emoji(self):
        raw = "🎬   סרט   טוב 🍿?"
        normalized = hebrew_normalize(raw)
        self.assertEqual(normalized, "סרט טוב")

    def test_empty_input(self):
        self.assertEqual(hebrew_normalize(""), "")
        self.assertEqual(hebrew_normalize(None), "")  # type: ignore[arg-type]


class NearDuplicateCatchesParaphrase(unittest.TestCase):
    def test_paraphrase_within_threshold(self):
        a = "סרט שראיתם בקולנוע בלי לדעת עליו כלום ויצאתם מופתעים?"
        b = "סרט שיצאתם מופתעים אחרי שראיתם אותו בקולנוע בלי לדעת עליו כלום?"
        hit = near_duplicate(a, [b])
        self.assertEqual(hit, b)

    def test_distinct_questions_pass(self):
        a = "סרט שהשפיע עליכם בילדות?"
        b = "ספר שאתם תמיד חוזרים אליו לפני שינה?"
        self.assertIsNone(near_duplicate(a, [b]))

    def test_very_short_text_not_flagged(self):
        self.assertIsNone(near_duplicate("?", ["? כן"]))

    def test_threshold_tunable(self):
        a = "סרט שהשפיע עליכם בילדות?"
        b = "סרט מילדות שהשפיע עליכם?"
        # Default threshold catches it
        self.assertIsNotNone(near_duplicate(a, [b]))
        # Aggressively high threshold lets it through
        self.assertIsNone(near_duplicate(a, [b], threshold=0.99))


class FreshnessRejectsSourceExampleEcho(unittest.TestCase):
    def test_known_bad_series_list_wording_rejected(self):
        reason = freshness_rejection("📺 סדרה שהתקתם בקצה הרשימה — ביום שני בערב היא כל מה שרציתם?")
        self.assertEqual(reason, "forbidden fragment: בקצה הרשימה")

    def test_verbatim_echo_of_pool_example_rejected(self):
        example = "דעה לא פופולרית על סרט שכולם אוהבים?"
        reason = freshness_rejection(example, source_examples={example})
        self.assertIsNotNone(reason)
        self.assertIn("copied static example", str(reason))

    def test_paraphrased_echo_of_pool_example_rejected(self):
        example = "סרט שראיתם בקולנוע בלי לדעת עליו כלום ויצאתם מופתעים?"
        paraphrase = "סרט שיצאתם מופתעים אחרי שראיתם אותו בקולנוע בלי לדעת עליו כלום?"
        reason = freshness_rejection(paraphrase, source_examples={example})
        self.assertIsNotNone(reason)
        self.assertIn("near-duplicate of static example", str(reason))


class FreshnessRejectsNearDuplicateOfPriorScheduled(unittest.TestCase):
    def test_paraphrase_of_prior_scheduled_rejected(self):
        prior = "סרט שראיתם בקולנוע בלי לדעת עליו כלום ויצאתם מופתעים?"
        paraphrase = "סרט שיצאתם מופתעים אחרי שראיתם אותו בקולנוע בלי לדעת עליו כלום?"
        reason = freshness_rejection(paraphrase, avoid_texts={prior})
        self.assertIsNotNone(reason)
        # Either substring or near-dup branch — both are acceptable rejections.
        self.assertTrue(
            "repeated scheduled text" in str(reason)
            or "near-duplicate of prior text" in str(reason),
            msg=f"unexpected rejection reason: {reason!r}",
        )

    def test_distinct_text_passes(self):
        prior = "סרט שראיתם בקולנוע בלי לדעת עליו כלום?"
        fresh = "ספר שאיכזב אתכם דווקא כי כולם המליצו עליו?"
        self.assertIsNone(freshness_rejection(fresh, avoid_texts={prior}))


if __name__ == "__main__":
    unittest.main()
