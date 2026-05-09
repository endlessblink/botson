"""T-134: every Hebrew-text generation path must include the canonical
quality rules from `config/question_quality.md`.

The check is: the prompt sent to the LLM must contain a marker (the
`## Hard rules` heading, or a `❌` example) anchored in the rules file.
We do NOT assert on the model output — only on the prompt the caller
constructed. Mocking the LLM call lets us capture the prompt without
spending API tokens.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, AsyncMock


# Markers that must show up in every wired prompt. The Hard-rules heading
# is the most stable; the ❌ marker is a secondary witness that the
# "Concrete failures to refuse" block is included.
RULE_MARKERS = ("## Hard rules", "❌")


def _assert_contains_rules(testcase: unittest.TestCase, prompt: str, where: str) -> None:
    for marker in RULE_MARKERS:
        testcase.assertIn(
            marker,
            prompt,
            msg=f"{where}: prompt missing canonical-rules marker {marker!r}",
        )


class ActivityCopyPromptIncludesRules(unittest.IsolatedAsyncioTestCase):
    async def test_activity_copy_default_kind_includes_canonical_rules(self):
        from dashboard import app as dashboard_app

        captured: dict = {}

        async def fake_cli(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"text":"בדיקה"}'

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=fake_cli)):
            await dashboard_app._generate_activity_copy("trivia_warmup_rsvp", theme="גיימינג")

        _assert_contains_rules(self, captured["prompt"], "_generate_activity_copy(default)")

    async def test_activity_copy_reminder_kind_includes_canonical_rules(self):
        from dashboard import app as dashboard_app

        captured: dict = {}

        async def fake_cli(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"text":"בדיקה"}'

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=fake_cli)):
            await dashboard_app._generate_activity_copy(
                "warmup_reminder", is_reminder=True, theme="גיימינג", min_ready_players=4
            )

        _assert_contains_rules(self, captured["prompt"], "_generate_activity_copy(reminder)")


class MaterializerFreshTextPromptIncludesRules(unittest.IsolatedAsyncioTestCase):
    async def test_generate_fresh_text_includes_canonical_rules(self):
        from bot.scheduler import materializer

        captured: dict = {}

        async def fake_claude(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"text":"מה הסצנה הכי משונה שראית בשוק לאחרונה"}'

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake_claude)):
            await materializer._generate_fresh_text(
                "discussion",
                category="movies",
                examples=["דוגמה אחת", "דוגמה שתיים"],
                used_texts=set(),
                scheduled_date="2099-01-01",
                scheduled_time="18:00",
            )

        _assert_contains_rules(self, captured["prompt"], "materializer._generate_fresh_text")


class SharedLoaderRoundtrip(unittest.TestCase):
    def test_shared_loader_returns_hard_rules_section(self):
        from bot.utils.quality_rules import load_quality_rules_short, reset_cache

        reset_cache()
        block = load_quality_rules_short()
        self.assertTrue(block.startswith("## Hard rules"), msg=f"unexpected start: {block[:80]!r}")
        self.assertNotIn("## Anti-patterns", block, msg="short block should stop before Anti-patterns")
        self.assertIn("❌", block, msg="short block should retain Concrete failures examples")

    def test_dashboard_and_bot_load_same_rules(self):
        from bot.utils.quality_rules import load_quality_rules_short, reset_cache
        from dashboard import app as dashboard_app

        reset_cache()
        self.assertEqual(dashboard_app._load_quality_rules_short(), load_quality_rules_short())


if __name__ == "__main__":
    unittest.main()
