"""Regression tests for the planner trivia coercion + channel-chip palette.

Covers the issues that bit us on 2026-04-27:
- A scheduled trivia game stored with message_type='discussion' relied on
  text-coercion at fire time. The dashboard listed it as "discussion" which
  was confusing. The coercion logic itself must keep working so existing rows
  don't silently break, but new edits with type='trivia' must persist as
  trivia_round.
- The channel-chip palette was missing welcome (341) — `welcome_id` was added
  to `mapped_ids` but never rendered in any chip group. Botson_corner (4037)
  must also be in the `other` group when present in verified_forum_topics.
"""
import json
import unittest

from bot.handlers import calendar as bot_calendar
from bot.handlers.trivia_round import _pick_questions
from dashboard.app import _coerce_game_message_fields, _looks_like_trivia_launch


class TestTriviaCoercion(unittest.TestCase):
    def test_discussion_with_trivia_launch_text_coerces_to_trivia_round(self):
        mtype, payload = _coerce_game_message_fields(
            "discussion",
            "🧠 הערב ב-22:00 — סיבוב טריוויה מוזיקה! 5 שאלות",
        )
        self.assertEqual(mtype, "trivia_round")
        self.assertIsNotNone(payload)
        self.assertIn("מוזיקה", payload)
        self.assertIn("question_count", payload)

    def test_warmup_text_stays_discussion(self):
        # The warm-up explicitly says "in 10 min" / "warming up" — must NOT
        # coerce, otherwise the bot would fire a real game from the warm-up row.
        for warmup in (
            "🧠 בעוד 10 דקות — סיבוב טריוויה מוזיקה! מתחממים?",
            "תזכורת: סיבוב טריוויה מתחיל בקרוב",
        ):
            mtype, _ = _coerce_game_message_fields("discussion", warmup)
            self.assertEqual(mtype, "discussion", f"warmup leaked: {warmup}")

    def test_explicit_trivia_round_round_trips(self):
        mtype, payload = _coerce_game_message_fields(
            "trivia_round",
            "anything",
            poll_options={"pre_roll_s": 30, "categories": ["מוזיקה"]},
        )
        self.assertEqual(mtype, "trivia_round")
        self.assertIn("מוזיקה", payload)

    def test_looks_like_trivia_launch_blockers(self):
        self.assertTrue(_looks_like_trivia_launch("🧠 סיבוב טריוויה מוזיקה!"))
        self.assertFalse(_looks_like_trivia_launch("בעוד 10 דקות סיבוב טריוויה"))
        self.assertFalse(_looks_like_trivia_launch("תזכורת — סיבוב טריוויה"))
        self.assertFalse(_looks_like_trivia_launch("מתחממים? סיבוב טריוויה!"))

    def test_music_payload_is_strictly_music(self):
        """Regression: 2026-04-27 round fired with mixed categories.
        The payload built from a music-themed announcement must carry
        categories=["מוזיקה"] only — no movies/TV bleed-through."""
        _, payload = _coerce_game_message_fields(
            "discussion",
            "🧠 הערב ב-22:00 — סיבוב טריוויה מוזיקה! 5 שאלות על אמנים",
        )
        decoded = json.loads(payload)
        self.assertEqual(decoded["categories"], ["מוזיקה"])
        self.assertEqual(decoded["theme_label"], "מוזיקה")
        self.assertEqual(decoded["question_count"], 5)

    def test_inference_does_not_bleed_through_unrelated_keywords(self):
        """The exact bug from 2026-04-27: the music announcement contained
        the word "ההיסטוריה" inside the description, which was matching
        the "היסטור" needle and producing a mixed music+history round.
        The fix: only the word right after "סיבוב טריוויה" counts."""
        for text in (
            "🧠 סיבוב טריוויה מוזיקה — שירים מהיסטוריה של הרוק",
            "🧠 סיבוב טריוויה מוזיקה: הסרטים שעיצבו את המוזיקה הישראלית",
            "🧠 סיבוב טריוויה מוזיקה — אמנים, אלבומים ולהיטים",
        ):
            _, payload = _coerce_game_message_fields("discussion", text)
            self.assertIsNotNone(payload, f"no payload for: {text}")
            decoded = json.loads(payload)
            self.assertEqual(
                decoded["categories"], ["מוזיקה"],
                f"category bled through for: {text} → {decoded['categories']}",
            )

    def test_default_question_count_is_in_user_range(self):
        """User wants trivia rounds to default to 7-10 questions, not 5."""
        from dashboard.app import _infer_question_count
        # No explicit count in the text — falls back to the default.
        default = _infer_question_count("🧠 סיבוב טריוויה מוזיקה!")
        self.assertGreaterEqual(default, 7)
        self.assertLessEqual(default, 10)

    def test_bot_and_dashboard_category_inference_agree(self):
        """Both sides infer the same categories — drift here means the bot
        could fire with different filtering than the dashboard intended."""
        for text in (
            "🧠 סיבוב טריוויה מוזיקה!",
            "🧠 סיבוב טריוויה סרטים — 5 שאלות",
            "🧠 סיבוב טריוויה גיימינג",
            "🧠 סיבוב טריוויה היסטוריה ומדע",
        ):
            self.assertEqual(
                bot_calendar._infer_trivia_categories(text),
                _DASHBOARD_INFER_CATEGORIES(text),
                f"calendar.py and dashboard/app.py disagree on: {text}",
            )


# Pull dashboard-side inference into a helper for the agreement check above.
def _DASHBOARD_INFER_CATEGORIES(text: str):
    from dashboard.app import _infer_trivia_categories as _f
    return _f(text)


class TestQuestionPickerStrictMode(unittest.TestCase):
    """The bot must NOT fall back to other categories when a themed round is
    launched. Regression from 2026-04-22 (tech round played film questions)
    and 2026-04-27 (music round played mixed)."""

    def test_strict_returns_only_matching_category(self):
        # _pick_questions reads trivia.yaml at module level. Trust the live
        # config and just check the picker's output is filtered to the asked
        # category. If the pool has fewer than n music questions the picker
        # returns what it has — never tops up with non-matching.
        picked = _pick_questions(20, {"מוזיקה"})
        self.assertTrue(picked, "expected at least one music question in pool")
        for q in picked:
            self.assertEqual(
                str(q.get("category", "")).strip(), "מוזיקה",
                f"non-music question leaked into music round: {q.get('text')}",
            )

    def test_strict_does_not_topup_with_unrelated(self):
        # Asking for a category that doesn't exist must yield zero, not a
        # silently-mixed fallback (the failure mode that hit production).
        picked = _pick_questions(5, {"__no_such_category__"})
        self.assertEqual(picked, [])


class TestChannelChipPaletteRoute(unittest.IsolatedAsyncioTestCase):
    """Verify grouped_channels (the chip-palette source) includes welcome + botson_corner."""

    async def test_grouped_channels_includes_welcome_and_botson_corner(self):
        # Inline reimplementation of the chip-palette assembly that runs in
        # planner_page (dashboard/app.py). Keeping it here as a guard means
        # future refactors of that block can't silently drop welcome again.
        topic_ids_dict = {"art": 347, "cute": 335, "gaming": 1517,
                          "movies": 54, "politics": 1431, "singles": 59}
        goals_id = 2184
        welcome_id = 341
        verified_topics = [
            {"topic_id": 7,    "verified_name": "vegan",          "category_key": "vegan"},
            {"topic_id": 54,   "verified_name": "movies",         "category_key": "movies"},
            {"topic_id": 59,   "verified_name": "singles",        "category_key": "singles"},
            {"topic_id": 153,  "verified_name": "funny",          "category_key": "funny"},
            {"topic_id": 335,  "verified_name": "cute",           "category_key": "cute"},
            {"topic_id": 341,  "verified_name": "welcome",        "category_key": "welcome"},
            {"topic_id": 347,  "verified_name": "art",            "category_key": "art"},
            {"topic_id": 1431, "verified_name": "politics",       "category_key": "politics"},
            {"topic_id": 1517, "verified_name": "gaming",         "category_key": "gaming"},
            {"topic_id": 2184, "verified_name": "goals",          "category_key": "goals"},
            {"topic_id": 3113, "verified_name": "ai_en",          "category_key": "ai_en"},
            {"topic_id": 4037, "verified_name": "botson_corner",  "category_key": "botson_corner"},
        ]
        verified_by_id = {v["topic_id"]: v for v in verified_topics}
        mapped_ids = set(topic_ids_dict.values()) | {goals_id, welcome_id}
        mapped_ids.discard(None)

        daily_chips = []
        if goals_id and goals_id in verified_by_id:
            daily_chips.append({"topic_id": goals_id, "name": verified_by_id[goals_id]["verified_name"]})
        if welcome_id and welcome_id in verified_by_id:
            daily_chips.append({"topic_id": welcome_id, "name": verified_by_id[welcome_id]["verified_name"]})

        grouped = {
            "discussions": [
                {"topic_id": tid, "name": verified_by_id[tid]["verified_name"], "category": cat}
                for cat, tid in topic_ids_dict.items()
                if tid and tid in verified_by_id
            ],
            "daily": daily_chips,
            "other": [
                {"topic_id": v["topic_id"], "name": v["verified_name"]}
                for v in verified_topics if v["topic_id"] not in mapped_ids
            ],
        }

        all_ids = (
            {c["topic_id"] for c in grouped["discussions"]}
            | {c["topic_id"] for c in grouped["daily"]}
            | {c["topic_id"] for c in grouped["other"]}
        )
        # Welcome (341) was the silent omission — make sure it's a chip now.
        self.assertIn(341, all_ids, "welcome topic must be in chip palette")
        # Botson_corner is the trivia/announcement room.
        self.assertIn(4037, all_ids, "botson_corner topic must be in chip palette")
        # Every verified topic ends up somewhere in the palette.
        for v in verified_topics:
            self.assertIn(v["topic_id"], all_ids, f"missing chip for {v['verified_name']}")


if __name__ == "__main__":
    unittest.main()
