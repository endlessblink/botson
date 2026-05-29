"""T-169 regression: the daily auto-refill must not cycle the same text.

Failure mode observed 2026-05-15 in `data/bot.db`: 582 `created_by='auto'`
rows showed the same 2 discussion questions reappearing every 7 days
("סרט שראיתם יותר מ-3 פעמים?" on Apr 20 / Apr 27 / May 4) and the same
3 morning phrases cycling. Root cause was `materializer.py` truncating
`used_texts` to a non-deterministic `list(set)[:25]` slice before rendering
the "do not repeat" block, plus `freshness_rejection` only catching exact
substring repeats.

This test pins:
- The full windowed history is rendered into the prompt (not a 25-item slice).
- The number of in-prompt example shots is bounded by
  `settings.materializer.examples_per_prompt` (default 2).
- A model that echoes a prior text verbatim gets rejected and the gate retries.
- A near-duplicate paraphrase is also rejected (Jaccard ≥ 0.55).
- The retry budget comes from `settings.materializer.retry_budget`.
"""

from __future__ import annotations

import unittest
import tempfile
from unittest.mock import patch, AsyncMock

from bot.database.db import Database
from bot.scheduler import materializer


class MaterializerPromptRendersFullDedupWindow(unittest.IsolatedAsyncioTestCase):
    async def test_full_used_texts_reach_prompt_not_just_first_25(self):
        captured: dict = {}

        async def fake(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"text":"שאלה חדשה ספציפית בדיוק לערב הזה?"}'

        # 40 historical texts. The legacy `[:25]` slice silently dropped 15
        # of these so the LLM had no idea they were prior repeats.
        history = [f"שאלה היסטורית מספר {i} עם תוכן ייחודי?" for i in range(40)]

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake)):
            result = await materializer._generate_fresh_text(
                "evening",
                category=None,
                examples=[],
                used_texts=history,
                scheduled_date="2026-05-20",
                scheduled_time="21:00",
            )

        self.assertIsNotNone(result)
        prompt = captured["prompt"]
        # The 30th historical entry (well past the legacy 25-cap) MUST
        # appear in the prompt — that's the whole point of the fix.
        self.assertIn("שאלה היסטורית מספר 30", prompt)
        self.assertIn("שאלה היסטורית מספר 39", prompt)


class MaterializerSlotClashIndex(unittest.IsolatedAsyncioTestCase):
    async def test_committed_index_marks_whole_minute_occupied(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                await db.create_scheduled_message(
                    text="game",
                    message_type="emoji_puzzle",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="22:00",
                    status="scheduled",
                )

                _, _, committed_times, skipped_times = await materializer._build_committed_index(
                    db, "2099-01-01", "2099-01-01"
                )
            finally:
                await db.close()

        self.assertIn(("2099-01-01", "22:00"), committed_times)
        self.assertNotIn(("2099-01-01", "22:00"), skipped_times)


class MaterializerLimitsExampleShots(unittest.IsolatedAsyncioTestCase):
    async def test_example_block_bounded_by_setting(self):
        captured: dict = {}

        async def fake(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"text":"חדשה לחלוטין, לא דומה לכלום?"}'

        examples = [f"דוגמה מספר {i} מהמאגר הסטטי?" for i in range(10)]
        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake)):
            await materializer._generate_fresh_text(
                "discussion",
                category="movies",
                examples=examples,
                used_texts=[],
                scheduled_date="2026-05-20",
                scheduled_time="18:00",
            )

        prompt = captured["prompt"]
        # Default is 2 shots. Count which examples landed in the prompt.
        hits = sum(1 for i in range(10) if f"דוגמה מספר {i}" in prompt)
        self.assertLessEqual(hits, 2, msg=f"expected ≤2 example shots, got {hits}")


class MaterializerRejectsVerbatimRepeatAndRetries(unittest.IsolatedAsyncioTestCase):
    async def test_verbatim_repeat_triggers_retry(self):
        # First call echoes a prior text; second call returns fresh.
        prior = "סרט שראיתם יותר מ-3 פעמים?"
        calls = {"n": 0}

        async def fake(prompt: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return f'{{"text":"{prior}"}}'
            return '{"text":"איזה סרט פתח לכם דלת לאיזה ז\'אנר חדש לגמרי?"}'

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake)):
            result = await materializer._generate_fresh_text(
                "discussion",
                category="movies",
                examples=[],
                used_texts=[prior],
                scheduled_date="2026-05-20",
                scheduled_time="18:00",
            )

        self.assertIsNotNone(result)
        self.assertNotEqual(result, prior)
        self.assertGreaterEqual(calls["n"], 2, msg="gate must retry on verbatim repeat")


class MaterializerRejectsPoolExampleEcho(unittest.IsolatedAsyncioTestCase):
    async def test_echoing_source_example_is_rejected(self):
        # Model lazily echoes a pool example; gate must reject.
        example = "דעה לא פופולרית על סרט שכולם אוהבים?"
        calls = {"n": 0}

        async def fake(prompt: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return f'{{"text":"{example}"}}'
            return '{"text":"איזה סרט קלאסי דווקא אכזב אתכם בצפייה שנייה?"}'

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake)):
            result = await materializer._generate_fresh_text(
                "discussion",
                category="movies",
                examples=[example, "אחר?"],
                used_texts=[],
                scheduled_date="2026-05-20",
                scheduled_time="18:00",
            )

        self.assertIsNotNone(result)
        self.assertNotEqual(result, example)
        self.assertGreaterEqual(calls["n"], 2)


class MaterializerRejectsNearDuplicate(unittest.IsolatedAsyncioTestCase):
    async def test_paraphrase_within_threshold_is_rejected(self):
        prior = "סרט שראיתם בקולנוע בלי לדעת עליו כלום ויצאתם מופתעים?"
        # Paraphrase — same content tokens, slight reshape. Legacy substring
        # dedup let this through; near-dup Jaccard catches it.
        paraphrase = "סרט שיצאתם מופתעים אחרי שראיתם אותו בקולנוע בלי לדעת עליו כלום?"
        calls = {"n": 0}

        async def fake(prompt: str) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                return f'{{"text":"{paraphrase}"}}'
            return '{"text":"באיזה ז\'אנר אתם מהמרים עיוורת ובדרך כלל לא מתחרטים?"}'

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fake)):
            result = await materializer._generate_fresh_text(
                "discussion",
                category="movies",
                examples=[],
                used_texts=[prior],
                scheduled_date="2026-05-20",
                scheduled_time="18:00",
            )

        self.assertIsNotNone(result)
        self.assertNotEqual(result, paraphrase)


class MaterializerRetryBudgetFromSettings(unittest.IsolatedAsyncioTestCase):
    async def test_retry_budget_honors_setting(self):
        # All candidates fail → return None after exactly `retry_budget` attempts.
        prior = "אותה שאלה ישנה שמסתובבת כבר 7 ימים?"

        async def always_repeat(prompt: str) -> str:
            return f'{{"text":"{prior}"}}'

        from bot.utils.config import get_settings
        settings = get_settings()
        configured = int((settings.get("materializer") or {}).get("retry_budget") or 3)

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=always_repeat)):
            with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=always_repeat)) as mock_call:
                result = await materializer._generate_fresh_text(
                    "evening",
                    category=None,
                    examples=[],
                    used_texts=[prior],
                    scheduled_date="2026-05-20",
                    scheduled_time="21:00",
                )
                self.assertIsNone(result)
                self.assertEqual(mock_call.await_count, configured)


if __name__ == "__main__":
    unittest.main()
