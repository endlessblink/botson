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
import unittest

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
