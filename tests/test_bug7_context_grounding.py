"""BUG-7 regressions for scheduler context grounding and silent fallback."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from bot.scheduler import materializer


class BotsonContextGroundingReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_rejects_captured_generic_then_accepts_grounded_text(self):
        with (
            patch.object(
                materializer,
                "_generate_with_claude",
                new=AsyncMock(side_effect=[
                    '{"text":"מה אתם לוקחים מהשבוע שעבר?"}',
                    '{"text":"איזו החלטה קטנה סביב מעבר הדירה חסכה לכם כאב ראש השבוע?"}',
                ]),
            ),
            patch.object(materializer, "_quality_gate_candidates", return_value=2),
        ):
            text = await materializer._generate_fresh_text(
                "discussion",
                category="general",
                examples=[],
                used_texts=[],
                scheduled_date="2099-01-01",
                scheduled_time="18:00",
            )

        self.assertEqual(text, "איזו החלטה קטנה סביב מעבר הדירה חסכה לכם כאב ראש השבוע?")

    async def test_scheduler_silently_skips_when_generation_is_unavailable(self):
        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(return_value=None)):
            text = await materializer._generate_fresh_text(
                "discussion",
                category="general",
                examples=[],
                used_texts=[],
                scheduled_date="2099-01-01",
                scheduled_time="18:00",
            )

        self.assertIsNone(text)


if __name__ == "__main__":
    unittest.main()
