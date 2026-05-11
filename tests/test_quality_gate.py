"""T-135: 3-candidate quality gate in the materializer.

`_generate_fresh_text` calls `_generate_with_claude` up to N times, picking
the first candidate that passes `freshness_rejection`. If all N fail, it
returns None (the existing contract — caller skips the slot, daily refill
retries) and logs a single warning naming the failure modes.

Rejection in these tests is driven by ``used_texts`` (deduplication
against already-scheduled rows) rather than by fragment-ban hits, because
``_extract_generated_text`` strips apostrophes from candidates before
they reach ``freshness_rejection`` — that strips matching characters out
of some YAML-listed banned fragments. ``used_texts`` matching is what
``materialize_forward`` actually relies on day-to-day, so it's also the
better behavior to pin in tests.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, AsyncMock

from bot.scheduler import materializer
from bot.utils.freshness import freshness_rejection


def _wrap(text: str) -> str:
    return f'{{"text": "{text}"}}'


_GOOD_TEXTS = (
    "מה הסצנה הכי משונה שראיתם בשוק לאחרונה",
    "איזה רגע השבוע הצחיק אתכם הכי הרבה",
    "מה הספר האחרון שלא הצלחתם להניח",
    "מה הדבר הכי מוזר ששמעתם השבוע",
)


class QualityGateAllPassReturnsFirst(unittest.IsolatedAsyncioTestCase):
    async def test_all_three_pass_returns_first_no_extra_calls(self):
        idx = {"i": 0}

        async def deliver(_prompt: str) -> str:
            r = _wrap(_GOOD_TEXTS[idx["i"]])
            idx["i"] += 1
            return r

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=deliver)):
            out = await materializer._generate_fresh_text(
                "discussion",
                category="movies",
                examples=[],
                used_texts=set(),
                scheduled_date="2099-01-01",
                scheduled_time="18:00",
            )

        self.assertEqual(out, _GOOD_TEXTS[0])
        self.assertEqual(idx["i"], 1, "first pass should short-circuit further generation")


class QualityGateRetriesUntilOnePasses(unittest.IsolatedAsyncioTestCase):
    async def test_first_two_fail_third_passes(self):
        # First two candidates exactly match used_texts → repeated → rejected.
        # Third is fresh → accepted.
        used_a = _GOOD_TEXTS[0]
        used_b = _GOOD_TEXTS[1]
        winner = _GOOD_TEXTS[2]
        responses = [_wrap(used_a), _wrap(used_b), _wrap(winner)]
        idx = {"i": 0}

        async def deliver(_prompt: str) -> str:
            r = responses[idx["i"]]
            idx["i"] += 1
            return r

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=deliver)):
            with self.assertLogs("bot.scheduler.materializer", level="INFO") as cm:
                out = await materializer._generate_fresh_text(
                    "discussion",
                    category="movies",
                    examples=[],
                    used_texts={used_a, used_b},
                    scheduled_date="2099-01-01",
                    scheduled_time="18:00",
                )

        self.assertEqual(out, winner)
        self.assertEqual(idx["i"], 3, "should have tried all three candidates")
        self.assertTrue(
            any("quality gate accepted candidate 3/3" in m for m in cm.output),
            msg=f"missing acceptance log; saw: {cm.output}",
        )


class QualityGateAllFailReturnsNoneWithWarning(unittest.IsolatedAsyncioTestCase):
    async def test_zero_of_three_pass_returns_none(self):
        used_a = _GOOD_TEXTS[0]
        used_b = _GOOD_TEXTS[1]
        used_c = _GOOD_TEXTS[2]
        responses = [_wrap(used_a), _wrap(used_b), _wrap(used_c)]
        idx = {"i": 0}

        async def deliver(_prompt: str) -> str:
            r = responses[idx["i"]]
            idx["i"] += 1
            return r

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=deliver)):
            with self.assertLogs("bot.scheduler.materializer", level="WARNING") as cm:
                out = await materializer._generate_fresh_text(
                    "discussion",
                    category="movies",
                    examples=[],
                    used_texts={used_a, used_b, used_c},
                    scheduled_date="2099-01-01",
                    scheduled_time="18:00",
                )

        self.assertIsNone(out)
        self.assertEqual(idx["i"], 3, "should attempt all three before giving up")
        self.assertTrue(
            any("all 3 candidates rejected" in m for m in cm.output),
            msg=f"missing all-fail summary log; saw: {cm.output}",
        )
        # The summary must name the slot so logs are debuggable.
        self.assertTrue(any("2099-01-01" in m and "18:00" in m for m in cm.output))


class QualityGateEmptyResponsesCountAsCandidates(unittest.IsolatedAsyncioTestCase):
    async def test_empty_responses_consume_attempts(self):
        responses = ["", "", _wrap(_GOOD_TEXTS[0])]
        idx = {"i": 0}

        async def deliver(_prompt: str) -> str:
            r = responses[idx["i"]]
            idx["i"] += 1
            return r

        with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=deliver)):
            out = await materializer._generate_fresh_text(
                "morning",
                category=None,
                examples=[],
                used_texts=set(),
                scheduled_date="2099-01-01",
                scheduled_time="08:00",
            )

        self.assertEqual(out, _GOOD_TEXTS[0])
        self.assertEqual(idx["i"], 3)


class HermesLearnedFragmentsReachRuntimeGate(unittest.TestCase):
    def test_scheduler_feedback_fragments_are_hard_rejected(self):
        cases = (
            "בוקר טוב 🌞 רביעי — היום הזה עוד לא הוחלט. מה הדבר הכי שווה שאתם מכניסים אליו?",
            "רביעי בלילה — הגענו לאמצע השבוע. מה שיניתם בו ממה שתכננתם ביום ראשון?",
            "שישי בבוקר — הפעם יש לכם רשות מלאה לעשות בדיוק מה שבא לכם. מה זה?",
            "ערב שישי בבית, בלי תוכניות גדולות — ניצחון או ויתור?",
            "שבת בלילה — מה הרגע שהפסקתם לעשות דברים והחלטתם שממנו זה הדבר?",
            "☕ שני בבוקר — קפה ראשון בשקט, או רצים כבר שתתחיל הרעש?",
            "🌙 שני בלילה — נסגרים מוקדם או מנצלים את הדממה? 🎧☕",
            "🧩 מה הדבר הכי חמוד שראיתם השבוע שגרם לכם לעצור הכל ולצחוק?",
            "🌙 חמישי בערב — מה אתם גונבים לעצמכם מהלילה הזה?",
            "☀️ בוקר ראשון — משהו שלא בתכנון אבל עלה על הסדר?",
            "🌙 סוגרים את השבת — מה אתם שומרים מהיום הזה?",
            "🌙 יום שני בערב — הרגע שבין עוד לא נגמר לשוב, נמאס — מה הוא נראה אצלכם הלילה?",
            "🎨 מה היה הדבר הכי שני במחלוקת ששמעתם על יצירה — שדווקא גרם לכם ללכת לבדוק אותה בעצמכם?",
        )
        for text in cases:
            with self.subTest(text=text):
                self.assertIsNotNone(freshness_rejection(text))


if __name__ == "__main__":
    unittest.main()
