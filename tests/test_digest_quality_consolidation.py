"""Tests for the centralized rules + draft validator + retry loop.

Pinned by `~/.claude/plans/low-quality-wording-and-foamy-planet.md`. Covers:
  - both prompts pull rules from `config/question_quality.md`
  - the digest prompt still fits the 28k CLI timeout budget
  - `_validate_draft_text` rejects the patterns we've seen fail
  - validator does not false-positive on the curated discussions.yaml pool
  - the per-slot retry loop replaces a bad slot when the retry produces a clean one
  - the loop drops a slot (and surfaces it in notes_for_admin) when the retry also fails
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from dashboard import app as dashboard_app


class QualityRulesShortLoaderTests(unittest.TestCase):

    def setUp(self) -> None:
        # The short loader caches its result; reset between tests so we
        # see the real disk read each time.
        dashboard_app._QUALITY_RULES_CACHE = None
        dashboard_app._QUALITY_RULES_SHORT_CACHE = None

    def test_full_rules_contain_all_sections(self):
        rules = dashboard_app._load_quality_rules()
        self.assertIn("## Hard rules", rules)
        self.assertIn("## Concrete failures to refuse", rules)
        self.assertIn("## Anti-patterns", rules)
        self.assertIn("## Pattern mix", rules)

    def test_short_rules_keep_hard_and_concrete_failures(self):
        short = dashboard_app._load_quality_rules_short()
        self.assertIn("## Hard rules", short)
        self.assertIn("## Concrete failures to refuse", short)
        # Real concrete-failure example from the new section.
        self.assertIn("בולמוס של תוכן", short)

    def test_short_rules_drop_anti_patterns_and_below(self):
        short = dashboard_app._load_quality_rules_short()
        self.assertNotIn("## Anti-patterns", short)
        self.assertNotIn("## Pattern mix", short)
        self.assertNotIn("## Per-channel hint", short)
        self.assertNotIn("## Output rules", short)


class ValidateDraftTextTests(unittest.TestCase):

    def test_passes_clean_drafts(self):
        clean = [
            "🎬 סרט שכדאי לצפות לבד בשישי בלילה שקט?",
            "🌙 מה הטקס שמסמן לכם שהשבת הסתיימה?",
            "🤔 מה הרגל שבניתם לעצמכם כסינגלים שאתם לא בטוחים שתוכלו לוותר עליו גם בזוגיות?",
            "מה המדיום האהוב עליכם? (ציור, דיגיטל, חימר)",
            "מה הג'אנר האהוב עליכם במשחקים?",
        ]
        for s in clean:
            with self.subTest(text=s):
                self.assertEqual(dashboard_app._validate_draft_text(s), [])

    def test_rejects_known_bad_patterns(self):
        cases = [
            ("מה עולה על הסדר בשבוע שמתחיל מחר?", "concrete_failure_agenda"),
            ("מה עולה הערב? משהו ספציפי", "concrete_failure_agenda"),
            ("בולמוס של תוכן הערב", "concrete_failure_invented"),
            ("מה הפלן הערב חברים?", "concrete_failure_plan"),
            ("ספרו על החיים שלכם", "rule_anti_pattern_vague"),
            ("מה היה היום בעבודה?", "rule11_generic_day"),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                failures = dashboard_app._validate_draft_text(text)
                self.assertIn(expected, failures, f"expected {expected} in {failures}")

    def test_rejects_english_jargon_mid_hebrew(self):
        text = "מה ה-mechanic של המשחק?"
        failures = dashboard_app._validate_draft_text(text)
        self.assertTrue(any(f.startswith("english_jargon") for f in failures))

    def test_brand_names_in_latin_pass(self):
        # Brand names are explicitly allowed — Netflix, Mozart, etc.
        text = "מה הסדרה האחרונה ש-Netflix הוסיפו ששווה משהו?"
        self.assertEqual(dashboard_app._validate_draft_text(text), [])

    def test_rejects_three_or_more_question_marks(self):
        # Two-question rhetorical pairs are allowed (real curated entry uses them);
        # three or more is the chained-question anti-pattern.
        self.assertEqual(dashboard_app._validate_draft_text("על מה אתם מרוצים? על מה פחות?"), [])
        failures = dashboard_app._validate_draft_text("מה? איך? למה? בכלל?")
        self.assertIn("multiple question marks", failures)

    def test_rejects_empty(self):
        self.assertEqual(dashboard_app._validate_draft_text(""), ["empty draft"])
        self.assertEqual(dashboard_app._validate_draft_text("   "), ["empty draft"])

    def test_rejects_overlong(self):
        text = "א" * 250
        failures = dashboard_app._validate_draft_text(text)
        self.assertTrue(any(f.startswith("length>200") for f in failures))


class ValidatorPoolFalsePositiveSweepTests(unittest.TestCase):
    """Run every regex against the live discussions.yaml + prompts.yaml pools.

    If a real curated entry fails the validator, the validator is too aggressive
    and would silently drop legit content from ai-fill-today's plan. This test
    is the regression net for that.
    """

    def test_no_false_positives_against_curated_pools(self):
        from bot.utils.config import load_yaml
        offenders: list[tuple[str, str, list[str]]] = []
        disc = load_yaml("discussions.yaml") or {}
        for cat, items in disc.items():
            if not isinstance(items, list):
                continue
            for entry in items:
                if not isinstance(entry, str):
                    continue
                failures = dashboard_app._validate_draft_text(entry)
                if failures:
                    offenders.append((cat, entry, failures))
        prompts = load_yaml("prompts.yaml") or {}
        for kind in ("morning", "evening"):
            for entry in prompts.get(kind, []) or []:
                if not isinstance(entry, str):
                    continue
                failures = dashboard_app._validate_draft_text(entry)
                if failures:
                    offenders.append((kind, entry, failures))
        self.assertEqual(
            offenders, [],
            f"validator falsely rejected {len(offenders)} curated entries: {offenders[:5]}",
        )


class DigestPromptStaysUnderTimeoutBudgetTests(unittest.TestCase):
    """Mirror of the existing budget assertion, but specifically pinned to the
    post-consolidation `_build_digest_cli_prompt()` output. Provides a clearer
    failure message when the budget breaks because of a rules/few-shot edit.
    """

    def test_post_consolidation_prompt_under_budget(self):
        # Bundle shape mirrors the assertion test in test_planner_coercion_and_chips.
        bundle = {
            "today": "2026-05-03",
            "hebrew_day_name": "ראשון",
            "hebrew_day_num": 0,
            "now_time_il": "10:00",
            "events_today": [],
            "scheduled_messages_today": [],
            "existing_drafts_today": [],
            "this_week_previews": [],
            "recent_sent_samples_by_type": {},
            "verified_topic_ids": [4037],
            "verified_topic_names": {"4037": "פינה"},
            "existing_trivia_categories": [],
            "existing_emoji_answers_sample": [],
            "schedule": {},
            "active_discussion_categories": [],
            "activity_coverage_requirements": [],
            "goals_topic_id": 2184,
        }
        prompt = dashboard_app._build_cli_digest_prompt(bundle)
        self.assertLess(len(prompt), 28000, f"digest prompt is {len(prompt)} chars, budget is 28000")


class RetryFailedRegularSlotsTests(unittest.IsolatedAsyncioTestCase):
    """The retry loop is the consistency lever — it converts variable model
    output into either-good-or-honestly-empty. These tests pin the contract.
    """

    async def test_clean_plan_is_passthrough(self):
        plan = {
            "regular_slots": [
                {"type": "discussion", "category": "movies",
                 "scheduled_time": "21:00", "topic_id": 54,
                 "text": "🎬 סרט שכדאי לצפות לבד בשישי בלילה שקט?"},
            ],
        }
        out, notes = await dashboard_app._retry_failed_regular_slots(
            plan, db=AsyncMock(), today_iso="2026-05-03",
        )
        self.assertEqual(notes, [])
        self.assertEqual(len(out["regular_slots"]), 1)
        self.assertEqual(out["regular_slots"][0]["text"],
                         "🎬 סרט שכדאי לצפות לבד בשישי בלילה שקט?")

    async def test_failed_slot_replaced_when_retry_passes(self):
        plan = {
            "regular_slots": [
                {"type": "discussion", "category": "movies",
                 "scheduled_time": "21:00", "topic_id": 54,
                 "text": "מה עולה על הסדר השבוע?"},  # banned
            ],
        }
        # Use a quote-free replacement so the cleaner's `.replace("'", "")` is a no-op
        # and the assertion can compare bytes-for-bytes.
        good_replacement = "🎬 איזו סצנה אחרונה גרמה לכם להגיד וואו בקול רם?"
        with patch.object(dashboard_app, "_fetch_recent_sent_for_dedup",
                          new=AsyncMock(return_value=[])), \
             patch.object(dashboard_app, "_generate_via_cli",
                          new=AsyncMock(return_value=good_replacement)), \
             patch.object(dashboard_app, "_generate_via_api",
                          new=AsyncMock(return_value=good_replacement)):
            out, notes = await dashboard_app._retry_failed_regular_slots(
                plan, db=AsyncMock(), today_iso="2026-05-03",
            )
        self.assertEqual(notes, [])
        self.assertEqual(len(out["regular_slots"]), 1)
        replaced_text = out["regular_slots"][0]["text"]
        self.assertNotIn("מה עולה על הסדר", replaced_text)
        self.assertIn("וואו", replaced_text)

    async def test_irrecoverable_slot_dropped_with_note(self):
        plan = {
            "regular_slots": [
                {"type": "discussion", "category": "general",
                 "scheduled_time": "18:00", "topic_id": 4037,
                 "text": "ספרו על החיים שלכם"},  # banned: starts with "ספרו על"
            ],
        }
        # Both retry transports also produce a banned phrase.
        with patch.object(dashboard_app, "_fetch_recent_sent_for_dedup",
                          new=AsyncMock(return_value=[])), \
             patch.object(dashboard_app, "_generate_via_cli",
                          new=AsyncMock(return_value="בולמוס של תוכן")), \
             patch.object(dashboard_app, "_generate_via_api",
                          new=AsyncMock(return_value="בולמוס של תוכן")):
            out, notes = await dashboard_app._retry_failed_regular_slots(
                plan, db=AsyncMock(), today_iso="2026-05-03",
            )
        self.assertEqual(out["regular_slots"], [])
        self.assertEqual(len(notes), 1)
        self.assertIn("18:00", notes[0])
        self.assertIn("retry also failed", notes[0])

    async def test_non_retriable_slot_type_dropped(self):
        # `custom`/`poll`/etc. don't have a per-row builder path; the loop
        # drops them with a "(not retriable)" note rather than calling Claude.
        plan = {
            "regular_slots": [
                {"type": "custom", "category": "",
                 "scheduled_time": "18:00", "topic_id": 4037,
                 "text": "מה עולה על הסדר?"},  # banned
            ],
        }
        cli_mock = AsyncMock()
        api_mock = AsyncMock()
        with patch.object(dashboard_app, "_generate_via_cli", new=cli_mock), \
             patch.object(dashboard_app, "_generate_via_api", new=api_mock):
            out, notes = await dashboard_app._retry_failed_regular_slots(
                plan, db=AsyncMock(), today_iso="2026-05-03",
            )
        self.assertEqual(out["regular_slots"], [])
        self.assertEqual(len(notes), 1)
        self.assertIn("not retriable", notes[0])
        cli_mock.assert_not_called()
        api_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
