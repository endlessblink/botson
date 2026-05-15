"""T-171 guardian: `config/discussions.yaml` must pass the deterministic
discussion-pool validator (modulo `config/discussion_pool_baseline.yaml`).

Pool quality matters because the materializer feeds these entries to the
LLM as few-shot anchors. A weak entry = a weak anchor = more weak output.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.validate_discussions import (  # noqa: E402
    _has_disallowed_english,
    _is_single_question,
    _is_too_short_or_generic,
    _load_baseline,
    _load_configured_categories,
    validate,
)
from bot.utils.config import load_yaml  # noqa: E402


class DiscussionPoolGuardian(unittest.TestCase):
    def test_live_pool_passes_validator_modulo_baseline(self):
        discussions = load_yaml("discussions.yaml") or {}
        configured = _load_configured_categories()
        baseline = _load_baseline()
        failures = validate(discussions, configured_categories=configured)
        new_failures = [
            f for f in failures
            if (str(f["category"]), str(f["text"])) not in baseline
        ]
        msg_lines = [
            f"  [{f['category']}] {f['text'][:80]}: {'; '.join(f['reasons'])}"
            for f in new_failures
        ]
        self.assertEqual(
            new_failures, [],
            msg="discussion pool has new validation failures:\n" + "\n".join(msg_lines),
        )

    def test_pool_size_is_meaningful(self):
        """Sanity: every category has at least 5 entries. Smaller pools
        starve the materializer's few-shot prompt and force repeats."""
        discussions = load_yaml("discussions.yaml") or {}
        underfilled = [
            (cat, len(items))
            for cat, items in discussions.items()
            if isinstance(items, list) and len(items) < 5
        ]
        self.assertEqual(
            underfilled, [],
            msg=f"underfilled categories (need ≥5 entries): {underfilled}",
        )


class ValidatorPrimitiveSanity(unittest.TestCase):
    def test_single_question_accepts_pool_styles(self):
        self.assertTrue(_is_single_question("מה הסרט שאתם חוזרים אליו תמיד?"))
        self.assertTrue(_is_single_question("תראו לנו פרט קטן מיצירה שלכם 👇"))
        self.assertTrue(_is_single_question("ארוחת ערב מושלמת = ___ + ___ + ___."))
        # Two questions in one prompt — reject.
        self.assertFalse(_is_single_question("מה הסרט? ולמה?"))

    def test_too_short_or_generic_blocks_stubs(self):
        self.assertTrue(_is_too_short_or_generic("שאלה?"))
        self.assertTrue(_is_too_short_or_generic("מה דעתכם?"))
        self.assertFalse(_is_too_short_or_generic("סרט שהטריילר עשה לו עוול לגמרי?"))

    def test_english_jargon_blocks_unknown_runs(self):
        # "Notion" is not allowlisted — should be flagged.
        self.assertEqual(_has_disallowed_english("השתמשתם פעם ב-Notion?"), "Notion")
        # "Netflix" is allowlisted — no flag.
        self.assertIsNone(_has_disallowed_english("איזה סדרה ב-Netflix השפיעה עליכם?"))

    def test_validator_catches_exact_duplicate_across_categories(self):
        result = validate({
            "art": ["סרט שראיתם השבוע ושינה לכם משהו?"],
            "movies": ["סרט שראיתם השבוע ושינה לכם משהו?"],
        })
        # Either category-direction match is acceptable.
        reasons = " ".join(r for f in result for r in f["reasons"])
        self.assertIn("exact duplicate", reasons)

    def test_validator_catches_near_duplicate(self):
        result = validate({
            "movies": [
                "סרט שראיתם בקולנוע בלי לדעת עליו כלום ויצאתם מופתעים?",
                "סרט שיצאתם מופתעים אחרי שראיתם אותו בקולנוע בלי לדעת עליו כלום?",
            ],
        })
        reasons = " ".join(r for f in result for r in f["reasons"])
        self.assertIn("near-duplicate", reasons)


if __name__ == "__main__":
    unittest.main()
