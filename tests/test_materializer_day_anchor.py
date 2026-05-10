"""Regression: materializer prompt must anchor on the Hebrew day-of-week.

The bug (observed 2026-05-10): the bot fired its Sunday morning prompt
correctly per schedule, but the AI-generated text said "בוקר של שבת"
(Saturday morning). Root cause — the materializer prompt only sent
``תאריך: 2026-05-10`` (ISO date) and left it to the LLM to do the date
arithmetic, which it got wrong.

Fix — translate the ISO date to a Hebrew day-name *inside* the prompt
and add an explicit constraint forbidding the model from naming any
other day. This test pins both behaviors.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, AsyncMock

from bot.scheduler import materializer
from bot.utils.time_context import hebrew_day_name


class MaterializerPromptIncludesHebrewDayAnchor(unittest.IsolatedAsyncioTestCase):
    async def test_sunday_prompt_names_sunday_in_hebrew(self):
        captured: dict = {}

        async def fake(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"text":"בדיקה"}'

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake)):
            await materializer._generate_fresh_text(
                "morning",
                category=None,
                examples=[],
                used_texts=set(),
                scheduled_date="2026-05-10",  # Sunday
                scheduled_time="09:00",
            )

        prompt = captured["prompt"]
        self.assertIn("יום ראשון", prompt, msg=f"prompt missing יום ראשון anchor: {prompt[:300]}")
        self.assertIn("2026-05-10", prompt)
        self.assertNotIn(
            'יום בשבוע, חייב להיות בדיוק "שבת"',
            prompt,
            msg="prompt must constrain to ראשון on a Sunday, not שבת",
        )
        # The explicit constraint line must reference *the actual* Hebrew day.
        self.assertIn('בדיוק "ראשון"', prompt)

    async def test_saturday_prompt_names_saturday(self):
        captured: dict = {}

        async def fake(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"text":"בדיקה"}'

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake)):
            await materializer._generate_fresh_text(
                "morning",
                category=None,
                examples=[],
                used_texts=set(),
                scheduled_date="2026-05-09",  # Saturday
                scheduled_time="09:00",
            )

        self.assertIn("יום שבת", captured["prompt"])
        self.assertIn('בדיוק "שבת"', captured["prompt"])


class HebrewDayHelperRoundtrip(unittest.TestCase):
    def test_helper_maps_iso_dates_to_correct_hebrew_days(self):
        cases = {
            "2026-05-10": "ראשון",  # Sunday
            "2026-05-11": "שני",
            "2026-05-12": "שלישי",
            "2026-05-13": "רביעי",
            "2026-05-14": "חמישי",
            "2026-05-15": "שישי",
            "2026-05-16": "שבת",
        }
        for iso, expected in cases.items():
            self.assertEqual(hebrew_day_name(iso), expected, msg=f"{iso} → expected {expected}")

    def test_helper_returns_none_on_unparseable_input(self):
        self.assertIsNone(hebrew_day_name(""))
        self.assertIsNone(hebrew_day_name("not-a-date"))


if __name__ == "__main__":
    unittest.main()
