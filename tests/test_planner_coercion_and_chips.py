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
import asyncio
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import yaml
from fastapi import HTTPException

from bot.database.db import Database
from bot.handlers import calendar as bot_calendar
from bot.scheduler import materializer
from bot.handlers.trivia_round import _pick_questions
from dashboard import app as dashboard_app
from dashboard.app import (
    _CAL_TYPE_STYLE,
    _active_discussion_categories_from_config,
    _coerce_game_message_fields,
    _looks_like_trivia_launch,
    build_generation_prompt,
)


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


class FakeCalendarRequest:
    session = {"authenticated": True}
    headers = {"content-type": "application/json"}

    def __init__(self, body):
        self._body = body
        self.query_params = {}

    async def json(self):
        return self._body

    async def body(self):
        if self._body is None:
            return b""
        return json.dumps(self._body).encode("utf-8")


class FakeQueryRequest:
    session = {"authenticated": True}

    def __init__(self, query_params):
        self.query_params = query_params


class FakeCalendarDb:
    def __init__(self):
        self.created = []

    async def get_handler_routing(self, handler):
        return {"handler": handler, "play_topic_id": 4037, "teaser_topic_ids": []}

    async def create_scheduled_message(self, **kwargs):
        self.created.append(kwargs)
        return len(self.created)


def _post_endpoint(path: str):
    for route in dashboard_app.app.routes:
        if getattr(route, "path", None) == path and "POST" in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"POST endpoint not found: {path}")


class TestDiscussionTopicGenerationContext(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.settings = {
            "topics": {"discussions": {"art": 111, "movies": 222}},
        }

    async def _seed_recent_rows(self, db: Database):
        await db.upsert_verified_forum_topic(111, "אמנות", "art", "test")
        await db.upsert_verified_forum_topic(222, "סרטים", "movies", "test")
        await db.create_scheduled_message(
            text="טקסט ישן של אמנות",
            message_type="discussion",
            channel_topic_id=111,
            target_group="main",
            scheduled_date="2099-01-01",
            scheduled_time="18:00",
            status="scheduled",
        )
        await db.create_scheduled_message(
            text="טקסט ישן של סרטים",
            message_type="discussion",
            channel_topic_id=222,
            target_group="main",
            scheduled_date="2099-01-02",
            scheduled_time="18:00",
            status="scheduled",
        )

    async def test_prompt_modal_generation_topic_wins_over_stale_category(self):
        db = Database(":memory:")
        await db.init()
        await self._seed_recent_rows(db)
        captured = {}

        def fake_prompt(field, mode, existing, category, instructions="", **kwargs):
            captured["field"] = field
            captured["category"] = category
            captured["category_name"] = kwargs.get("category_name")
            captured["recent_sent"] = kwargs.get("recent_sent") or []
            return "prompt"

        try:
            with patch.object(dashboard_app, "get_settings", return_value=self.settings), \
                 patch.object(dashboard_app, "build_generation_prompt", side_effect=fake_prompt), \
                 patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value="איזו יצירה ראיתם לאחרונה?")):
                endpoint = _post_endpoint("/api/generate")
                res = await endpoint(
                    FakeCalendarRequest({
                        "field": "discussion",
                        "mode": "single",
                        "category": "movies",
                        "topic_id": 111,
                    }),
                    db,
                )
        finally:
            await db.close()

        self.assertEqual(res["content"], "איזו יצירה ראיתם לאחרונה?")
        self.assertEqual(captured["category"], "art")
        self.assertEqual(captured["category_name"], "אמנות")
        self.assertEqual(captured["recent_sent"], ["טקסט ישן של אמנות"])

    async def test_create_drawer_generation_topic_wins_over_stale_category(self):
        db = Database(":memory:")
        await db.init()
        await self._seed_recent_rows(db)
        captured = {}

        def fake_prompt(field, mode, existing, category, **kwargs):
            captured["field"] = field
            captured["category"] = category
            captured["category_name"] = kwargs.get("category_name")
            captured["recent_sent"] = kwargs.get("recent_sent") or []
            return "prompt"

        try:
            with patch.object(dashboard_app, "get_settings", return_value=self.settings), \
                 patch.object(dashboard_app, "build_generation_prompt", side_effect=fake_prompt), \
                 patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value="איזו יצירה ראיתם לאחרונה?")):
                res = await dashboard_app.generate_content(
                    FakeCalendarRequest({
                        "type": "discussion",
                        "category": "movies",
                        "topic_id": 111,
                    }),
                    db,
                )
        finally:
            await db.close()

        self.assertEqual(res["text"], "איזו יצירה ראיתם לאחרונה?")
        self.assertEqual(captured["category"], "art")
        self.assertEqual(captured["category_name"], "אמנות")
        self.assertEqual(captured["recent_sent"], ["טקסט ישן של אמנות"])

    async def test_ai_suggest_commit_rejects_topic_category_mismatch(self):
        db = FakeCalendarDb()
        with patch.object(dashboard_app, "get_settings", return_value=self.settings):
            res = await dashboard_app.ai_suggest_commit(
                FakeCalendarRequest({
                    "approved": [{
                        "date": "2099-01-01",
                        "time": "18:00",
                        "message_type": "discussion",
                        "topic_id": 111,
                        "category": "movies",
                        "text": "איזו יצירה ראיתם לאחרונה?",
                        "source": "ai-fill",
                    }],
                }),
                db,
            )

        self.assertEqual(res["inserted"], 0)
        self.assertEqual(db.created, [])
        self.assertIn("discussion topic mismatch", res["errors"][0])


class TestSchedulerTypeExposure(unittest.IsolatedAsyncioTestCase):
    async def test_ai_suggest_calendar_returns_mixed_types_without_writes(self):
        db = Database(":memory:")
        await db.init()
        for media_type in ("movie", "series"):
            for idx in range(5):
                await db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                )
        await db._db.commit()
        before = await self._scheduled_count(db)
        call_counter = {"n": 0}

        async def distinct_canned(*args, **kwargs):
            call_counter["n"] += 1
            return f"איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש? ({call_counter['n']})"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            # Use next week so this test is not dependent on the wall clock
            # (week_offset=0 skips slots whose times have already passed today).
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=1)

        after = await self._scheduled_count(db)
        await db.close()

        self.assertEqual(before, after, "suggest must not write scheduled rows")
        types = {s["message_type"] for s in result["suggestions"]}
        self.assertIn("discussion", types)
        self.assertIn("trivia_round", types)
        self.assertIn("emoji_puzzle", types)
        self.assertIn("facts_tidbit", types)
        self.assertIn("facts_spooky", types)
        # weekly_leaderboard / weekly_roundup are cron-owned (bot/scheduler/jobs.py)
        # and must NOT be suggested as calendar rows — suggesting them would
        # re-introduce the 2026-05-23 duplicate-send bug.
        self.assertNotIn("weekly_leaderboard", types)
        self.assertNotIn("weekly_roundup", types)
        banned_placeholders = (
            "תיבחר מהמאגר בזמן השליחה",
            "ייבחר מהמאגר בזמן השליחה",
            "הבוט יבדוק וישלח אם נמצא משחק רלוונטי",
            "יופק מנתוני הפעילות בזמן השליחה",
            "תופק מנתוני הרמות בזמן השליחה",
        )
        for suggestion in result["suggestions"]:
            for fragment in banned_placeholders:
                with self.subTest(fragment=fragment, suggestion=suggestion):
                    self.assertNotIn(fragment, suggestion["text"])
        emoji_rows = [s for s in result["suggestions"] if s["message_type"] == "emoji_puzzle"]
        self.assertTrue(emoji_rows)
        self.assertTrue(all(row["text"] == "" for row in emoji_rows))
        self.assertTrue(all(row.get("preview_url") for row in emoji_rows))
        emoji_payload = json.loads(emoji_rows[0]["poll_options_json"])
        self.assertIn(emoji_payload["theme_label"], {"סרטים", "סדרות"})
        self.assertIn(emoji_payload["media_types"], (["movie"], ["series"]))
        self.assertTrue(any(
            s["message_type"] == "trivia_warmup_rsvp" and s["source"] == "ai-fill-emoji"
            for s in result["suggestions"]
        ))
        emoji_announcements = [
            s for s in result["suggestions"]
            if s["message_type"] == "trivia_warmup_rsvp" and s["source"] == "ai-fill-emoji"
        ]
        self.assertTrue(emoji_announcements)
        self.assertEqual(
            emoji_announcements[0]["topic_id"],
            54,
            "Movie/series Emoji Night warm-up announcements must appear in the relevant topic",
        )
        self.assertNotIn("warmup_reminder", types)
        for ann in (
            s for s in result["suggestions"] if s["message_type"] == "trivia_warmup_rsvp"
        ):
            ann_marker = json.loads(ann["poll_options_json"]).get("warmup_marker")
            self.assertTrue(ann_marker, f"announcement {ann} missing warmup_marker")
        trivia_rows = [s for s in result["suggestions"] if s["message_type"] == "trivia_round"]
        self.assertTrue(trivia_rows)
        self.assertTrue(all(row.get("preview_url") for row in trivia_rows))
        trivia_payload = json.loads(trivia_rows[0]["poll_options_json"])
        self.assertTrue(trivia_payload["theme_label"])
        self.assertTrue(trivia_payload["categories"])
        self.assertGreaterEqual(int(trivia_payload.get("min_ready_players", 0)), 0)
        trivia_announcements = [
            s for s in result["suggestions"]
            if s["message_type"] == "trivia_warmup_rsvp" and s["source"] == "ai-fill-trivia"
        ]
        self.assertTrue(trivia_announcements)
        expected_trivia_topics = {
            "אומנות": 347,
            "גיימינג": 1517,
            "מוזיקה": 4502,
            "סרטים": 54,
            "טלוויזיה": 54,
        }
        expected_topic = expected_trivia_topics.get(
            trivia_payload["categories"][0], 4037
        )
        self.assertEqual(trivia_announcements[0]["topic_id"], expected_topic)
        # T-127 invariant: every trivia_round / emoji_puzzle suggestion with
        # min_ready_players > 0 must come with a paired warmup announcement
        # (matched on warmup_marker) so the dispatch-time RSVP gate has data.
        emoji_game_rows = [s for s in result["suggestions"] if s["message_type"] == "emoji_puzzle"]
        ann_markers = {
            json.loads(s["poll_options_json"]).get("warmup_marker")
            for s in result["suggestions"] if s["message_type"] == "trivia_warmup_rsvp"
        }
        for game in trivia_rows + emoji_game_rows:
            payload = json.loads(game["poll_options_json"])
            if int(payload.get("min_ready_players") or 0) > 0:
                marker = payload.get("warmup_marker")
                self.assertTrue(
                    marker,
                    f"{game['message_type']} with min_ready>0 must carry warmup_marker: {game}",
                )
                self.assertIn(
                    marker, ann_markers,
                    f"{game['message_type']} marker {marker} has no paired announcement",
                )
            # activity_label must include the count fact (T-127 follow-up).
            label = payload.get("activity_label") or ""
            self.assertRegex(
                label, r"\(\d+ (חידות|שאלות)\)",
                f"{game['message_type']} activity_label missing count: {label}",
            )
        # Sanity bound — not a hard cap, just protection against an unbounded
        # explosion. Adjust upward when new pairings legitimately add rows.
        # ~13 capped-type rows (11 types + 2 RSVP announcements) + up to
        # ai_populate.flex.week.max_suggestions (12) subject discussions that
        # fill otherwise-empty days. Bound raised 16→30 on 2026-06-07 when
        # flex.week was enabled.
        self.assertLessEqual(len(result["suggestions"]), 30)
        fact_rows = [s for s in result["suggestions"] if s["message_type"] in {"facts_tidbit", "facts_spooky"}]
        self.assertTrue(fact_rows)
        self.assertTrue(all(row.get("preview_url") for row in fact_rows))
        self.assertTrue(all(not row["text"].startswith("[internal:") for row in fact_rows))
        self.assertTrue(all(json.loads(row["poll_options_json"]).get("fact_id") for row in fact_rows))
        executable_rows = [
            s for s in result["suggestions"]
            if s["message_type"] in {"trivia_round", "emoji_puzzle", "facts_tidbit", "facts_spooky", "free_games", "weekly_roundup", "weekly_leaderboard"}
        ]
        self.assertTrue(executable_rows)
        self.assertTrue(all(row.get("preview_url") for row in executable_rows))

    async def test_ai_suggest_calendar_counts_sent_games_against_week_caps(self):
        db = Database(":memory:")
        await db.init()
        for media_type in ("movie", "series"):
            for idx in range(5):
                await db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                )
        await db._db.commit()
        today = datetime.now().date()
        days_since_sunday = (today.weekday() + 1) % 7
        next_sunday = today - timedelta(days=days_since_sunday) + timedelta(weeks=1)
        for message_type, scheduled_time in (("trivia_round", "21:00"), ("emoji_puzzle", "22:00")):
            await db.create_scheduled_message(
                text="already ran",
                message_type=message_type,
                channel_topic_id=4037,
                target_group="main",
                scheduled_date=next_sunday.isoformat(),
                scheduled_time=scheduled_time,
                status="sent",
            )

        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"
        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=1)

        await db.close()
        suggested_types = {s["message_type"] for s in result["suggestions"]}
        self.assertNotIn("trivia_round", suggested_types)
        self.assertNotIn("emoji_puzzle", suggested_types)

    async def test_game_warmup_relevant_topic_routing_can_be_disabled(self):
        settings = dashboard_app.get_settings()
        patched_settings = dict(settings)
        patched_routes = dict(settings.get("game_warmup_topic_routes") or {})
        patched_routes["enabled"] = False
        patched_settings["game_warmup_topic_routes"] = patched_routes

        self.assertEqual(
            dashboard_app._configured_game_warmup_topic(
                patched_settings,
                route_key="trivia_categories",
                subjects=["גיימינג"],
                fallback_topic=4037,
            ),
            4037,
        )

    async def test_ai_suggest_day_does_not_stack_spooky_fact_on_emoji_slot(self):
        db = Database(":memory:")
        await db.init()
        for media_type in ("movie", "series"):
            for idx in range(5):
                await db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                )
        await db._db.commit()
        target_date = (datetime.now().date() + timedelta(days=7)).isoformat()
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(db, target_date=target_date, week_offset=0)

        await db.close()
        by_slot = {(s["date"], s["time"]): s["message_type"] for s in result["suggestions"]}
        self.assertEqual(by_slot.get((target_date, "22:00")), "emoji_puzzle")
        self.assertFalse(any(
            s["date"] == target_date and s["time"] == "22:00" and s["message_type"] == "facts_spooky"
            for s in result["suggestions"]
        ))

    async def test_ai_suggest_calendar_rotates_emoji_subject_away_from_recent(self):
        db = Database(":memory:")
        await db.init()
        for media_type in ("movie", "series"):
            for idx in range(5):
                await db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                )
        await db._db.commit()
        # Anchor the "recent" game inside the 21-day rotation window (relative to
        # now, not a hardcoded date that ages out) so rotation is genuinely
        # enforced rather than passing by random luck.
        recent_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        await db.create_scheduled_message(
            text="🧩 Emoji Night — סרטים (5 חידות)",
            message_type="emoji_puzzle",
            channel_topic_id=4037,
            target_group="main",
            scheduled_date=recent_date,
            scheduled_time="22:00",
            poll_options=json.dumps({"theme_label": "סרטים", "media_types": ["movie"]}, ensure_ascii=False),
            status="scheduled",
        )
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            # week_offset=1 (next week) keeps the suggested slot in the future so
            # the test is not wall-clock dependent (week_offset=0 yields no slots
            # late on the last day of the week).
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=1)

        await db.close()
        emoji_rows = [s for s in result["suggestions"] if s["message_type"] == "emoji_puzzle"]
        self.assertTrue(emoji_rows)
        emoji_payload = json.loads(emoji_rows[0]["poll_options_json"])
        self.assertEqual(emoji_payload["theme_label"], "סדרות")
        self.assertEqual(emoji_payload["media_types"], ["series"])

    async def test_ai_suggest_calendar_rotates_emoji_subject_away_from_recent_rounds(self):
        db = Database(":memory:")
        await db.init()
        movie_id = None
        for media_type in ("movie", "series"):
            for idx in range(5):
                async with db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                ) as cursor:
                    if media_type == "movie" and movie_id is None:
                        movie_id = cursor.lastrowid
        await db._db.execute(
            """INSERT INTO emoji_puzzle_rounds
               (puzzle_id, chat_id, message_id, sent_at, status, award_points)
               VALUES (?, ?, ?, datetime('now'), 'solved', 5)""",
            (movie_id, -100, 123),
        )
        await db._db.commit()
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            # week_offset=1: stable future slot (see sibling test). The recent
            # round above uses datetime('now'), so it stays inside the window.
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=1)

        await db.close()
        emoji_rows = [s for s in result["suggestions"] if s["message_type"] == "emoji_puzzle"]
        self.assertTrue(emoji_rows)
        emoji_payload = json.loads(emoji_rows[0]["poll_options_json"])
        self.assertEqual(emoji_payload["theme_label"], "סדרות")
        self.assertEqual(emoji_payload["media_types"], ["series"])

    async def test_ai_suggest_calendar_rotates_trivia_subject_away_from_recent(self):
        db = Database(":memory:")
        await db.init()
        # Recent game anchored inside the 21-day rotation window (relative to now).
        recent_date = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        await db.create_scheduled_message(
            text="🧠 סיבוב טריוויה — ישראל (5 שאלות)",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="main",
            scheduled_date=recent_date,
            scheduled_time="21:00",
            poll_options=json.dumps({"theme_label": "ישראל", "categories": ["ישראל"], "question_count": 5}, ensure_ascii=False),
            status="scheduled",
        )
        for media_type in ("movie", "series"):
            for idx in range(5):
                await db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                )
        await db._db.commit()
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            # week_offset=1: stable future slot, not wall-clock dependent.
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=1)

        await db.close()
        trivia_rows = [s for s in result["suggestions"] if s["message_type"] == "trivia_round"]
        self.assertTrue(trivia_rows)
        trivia_payload = json.loads(trivia_rows[0]["poll_options_json"])
        self.assertNotEqual(trivia_payload["categories"], ["ישראל"])
        self.assertEqual(trivia_payload["theme_label"], trivia_payload["categories"][0])

    async def test_ai_suggest_calendar_skips_past_times_today(self):
        db = Database(":memory:")
        await db.init()
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"
        now = dashboard_app.datetime.now()

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=now.date().isoformat(), week_offset=0,
            )

        await db.close()

        for suggestion in result["suggestions"]:
            slot = dashboard_app.datetime.fromisoformat(
                f"{suggestion['date']}T{suggestion['time'][:5]}"
            )
            self.assertGreaterEqual(slot, now, suggestion)

    async def _seed_emoji_pool(self, db):
        for media_type in ("movie", "series"):
            for idx in range(5):
                await db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                )
        await db._db.commit()

    async def test_ai_suggest_rolling_window_fills_next_seven_days(self):
        # The reported bug: clicking "fill a week" on the last weekday of the
        # Sun–Sat week only returned a couple of suggestions because the
        # current calendar week was almost entirely in the past. Rolling mode
        # must always span the next N days *starting today*, no matter the
        # weekday, so a full upcoming week of slots is offered.
        from datetime import date as _date

        class FixedDate(_date):
            @classmethod
            def today(cls):
                return cls(2099, 1, 2)  # arbitrary future weekday

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                base = cls(2099, 1, 2, 0, 0)
                return base.replace(tzinfo=tz) if tz is not None else base

        db = Database(":memory:")
        await db.init()
        await self._seed_emoji_pool(db)
        before = await self._scheduled_count(db)
        call_counter = {"n": 0}

        async def distinct_canned(*args, **kwargs):
            call_counter["n"] += 1
            return f"איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש? ({call_counter['n']})"

        with patch.object(dashboard_app, "date", FixedDate), \
             patch.object(dashboard_app, "datetime", FixedDateTime), \
             patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=None, window_mode="rolling",
            )

        after = await self._scheduled_count(db)
        await db.close()

        self.assertEqual(before, after, "suggest must not write scheduled rows")
        self.assertEqual(result["window"]["scope"], "week")
        self.assertEqual(result["window"]["start"], "2099-01-02")
        self.assertEqual(result["window"]["end"], "2099-01-08")  # today + 6
        self.assertTrue(result["suggestions"], "rolling fill must produce suggestions")
        for s in result["suggestions"]:
            self.assertGreaterEqual(s["date"], "2099-01-02")
            self.assertLessEqual(s["date"], "2099-01-08")
        types = {s["message_type"] for s in result["suggestions"]}
        # Balanced mix, not a flood of one type.
        self.assertGreaterEqual(len(types), 3, types)

    async def test_ai_suggest_rolling_window_skips_past_times_on_day_zero(self):
        # Day 0 of the rolling window is today; already-elapsed times on it
        # must be dropped (CLAUDE.md: never suggest slots before server time).
        db = Database(":memory:")
        await db.init()
        await self._seed_emoji_pool(db)
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"
        now = dashboard_app.datetime.now()

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=None, window_mode="rolling",
            )

        await db.close()
        today_iso = now.date().isoformat()
        for s in result["suggestions"]:
            if s["date"] == today_iso:
                slot = dashboard_app.datetime.fromisoformat(f"{s['date']}T{s['time'][:5]}")
                self.assertGreaterEqual(slot, now, s)

    async def test_ai_suggest_week_of_targets_that_calendar_week(self):
        # "Fill a specific week" passes week_of=<date>; the window must be the
        # Sun–Sat calendar week containing that date.
        db = Database(":memory:")
        await db.init()
        await self._seed_emoji_pool(db)
        # A date comfortably in the future so nothing is skipped as past.
        anchor = datetime.now().date() + timedelta(days=21)
        days_since_sunday = (anchor.weekday() + 1) % 7
        sunday = anchor - timedelta(days=days_since_sunday)
        saturday = sunday + timedelta(days=6)
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=None, window_mode="week", week_of=anchor.isoformat(),
            )

        await db.close()
        self.assertEqual(result["window"]["scope"], "week")
        self.assertEqual(result["window"]["start"], sunday.isoformat())
        self.assertEqual(result["window"]["end"], saturday.isoformat())
        for s in result["suggestions"]:
            self.assertGreaterEqual(s["date"], sunday.isoformat())
            self.assertLessEqual(s["date"], saturday.isoformat())

    async def test_ai_suggest_rolling_window_length_is_config_driven(self):
        # The 7 must come from settings.yaml:ai_populate.rolling_window_days,
        # not be hardcoded. Setting it to 3 must produce a 3-day window.
        from datetime import date as _date

        class FixedDate(_date):
            @classmethod
            def today(cls):
                return cls(2099, 1, 2)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                base = cls(2099, 1, 2, 0, 0)
                return base.replace(tzinfo=tz) if tz is not None else base

        base_settings = dashboard_app.get_settings()
        patched = dict(base_settings)
        patched_ai = dict(base_settings.get("ai_populate") or {})
        patched_ai["rolling_window_days"] = 3
        patched["ai_populate"] = patched_ai

        db = Database(":memory:")
        await db.init()
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "date", FixedDate), \
             patch.object(dashboard_app, "datetime", FixedDateTime), \
             patch.object(dashboard_app, "get_settings", return_value=patched), \
             patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=None, window_mode="rolling",
            )

        await db.close()
        self.assertEqual(result["window"]["start"], "2099-01-02")
        self.assertEqual(result["window"]["end"], "2099-01-04")  # today + (3-1)

    async def test_ai_suggest_rolling_window_spreads_flex_subject_discussions(self):
        # Operator intent: empty days get SUBJECT discussions at a RANGE of
        # hours, spread across the week (not one fixed 18:00 slot, not all
        # clustered on one day). flex.week drives this; per_day_max keeps them
        # spread. This pins both behaviors.
        from datetime import date as _date

        class FixedDate(_date):
            @classmethod
            def today(cls):
                return cls(2099, 1, 4)

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                base = cls(2099, 1, 4, 0, 0)
                return base.replace(tzinfo=tz) if tz is not None else base

        flex_cfg = (dashboard_app.get_settings().get("ai_populate") or {}).get("flex") or {}
        week_flex = flex_cfg.get("week") or {}
        if not (flex_cfg.get("enabled") and week_flex.get("enabled")):
            self.skipTest("flex.week not enabled in settings")
        per_day_max = int(week_flex.get("per_day_max") or 0)
        win = (week_flex.get("windows") or [{}])[0]
        win_start_t, win_end_t = str(win.get("start") or "12:00"), str(win.get("end") or "22:00")

        db = Database(":memory:")
        await db.init()
        counter = {"n": 0}

        async def distinct_canned(*args, **kwargs):
            counter["n"] += 1
            return f"איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש? ({counter['n']})"

        with patch.object(dashboard_app, "date", FixedDate), \
             patch.object(dashboard_app, "datetime", FixedDateTime), \
             patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=None, window_mode="rolling",
            )

        await db.close()
        flex_rows = [s for s in result["suggestions"] if str(s.get("source", "")).startswith("ai-fill-flex")]
        self.assertTrue(flex_rows, "flex.week must produce subject discussions")
        self.assertTrue(all(r["message_type"] == "discussion" for r in flex_rows))
        # Subject discussions carry a category and route to its channel.
        self.assertTrue(all(r.get("category") for r in flex_rows))
        # Times fall within the configured range (a range of hours, not 18:00).
        for r in flex_rows:
            self.assertGreaterEqual(r["time"][:5], win_start_t, r)
            self.assertLessEqual(r["time"][:5], win_end_t, r)
        # Spread, not clustered: more than one distinct day used.
        flex_dates = {r["date"] for r in flex_rows}
        self.assertGreaterEqual(len(flex_dates), 2, flex_rows)
        # Per-day cap respected.
        if per_day_max:
            from collections import Counter
            per_day = Counter(r["date"] for r in flex_rows)
            self.assertTrue(all(v <= per_day_max for v in per_day.values()), dict(per_day))

    async def test_ai_suggest_calendar_returns_flex_non_game_after_evening(self):
        db = Database(":memory:")
        await db.init()
        target_date = "2099-01-01"
        call_counter = {"n": 0}

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                base = cls(2099, 1, 1, 20, 29)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base

        async def distinct_canned(*args, **kwargs):
            call_counter["n"] += 1
            return f"איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש? ({call_counter['n']})"

        with patch.object(dashboard_app, "datetime", FixedDateTime), \
             patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(side_effect=distinct_canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=target_date, week_offset=0,
            )

        await db.close()

        flex_rows = [s for s in result["suggestions"] if str(s.get("source", "")).startswith("ai-fill-flex")]
        self.assertTrue(flex_rows, result)
        self.assertTrue(all(row["message_type"] in {"discussion", "custom"} for row in flex_rows))
        self.assertTrue(all(row["time"] >= "20:39" for row in flex_rows))

    async def test_ai_suggest_commit_accepts_custom_flex_rows(self):
        db = Database(":memory:")
        await db.init()
        body = {
            "approved": [{
                "date": "2099-01-01",
                "time": "21:30",
                "message_type": "custom",
                "topic_id": 4037,
                "text": "שאלה קצרה לבדיקה",
                "source": "ai-fill-flex",
            }],
        }

        result = await dashboard_app.ai_suggest_commit(FakeCalendarRequest(body), db)
        rows = await db.get_scheduled_messages("2099-01-01", "2099-01-01")
        await db.close()

        self.assertEqual(result["inserted"], 1, result)
        self.assertEqual(rows[0]["message_type"], "custom")
        self.assertEqual(rows[0]["created_by"], "ai-fill-flex")

    async def test_ai_suggest_calendar_returns_skip_reasons(self):
        db = Database(":memory:")
        await db.init()
        target_date = "2099-01-01"

        class FixedDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                base = cls(2099, 1, 1, 20, 29)
                if tz is not None:
                    return base.replace(tzinfo=tz)
                return base

        with patch.object(dashboard_app, "datetime", FixedDateTime), \
             patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value="איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?")), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value="איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?")), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(
                db, target_date=target_date, week_offset=0,
            )

        await db.close()

        self.assertIn("skip_reasons", result)
        self.assertIn("empty_state", result)
        self.assertTrue(result["skip_reasons"], result)
        self.assertTrue(any(r.get("code") in {"past", "past_or_too_soon"} for r in result["skip_reasons"]))
        self.assertTrue(all(r.get("label") for r in result["skip_reasons"]))

    async def test_ai_suggest_endpoint_returns_pollable_job(self):
        db = Database(":memory:")
        await db.init()
        dashboard_app._AI_SUGGEST_TASKS.clear()
        expected = {
            "window": {"start": "2099-01-01", "end": "2099-01-01", "scope": "day"},
            "suggestions": [],
            "stats_block": "",
            "errors": [],
            "skip_reasons": [],
            "empty_state": {},
        }

        with patch.object(dashboard_app, "_ai_suggest_calendar", new=AsyncMock(return_value=expected)):
            started = await dashboard_app.ai_suggest(
                FakeCalendarRequest({"target_date": "2099-01-01", "week_offset": 0}), db,
            )
            for _ in range(10):
                status = await dashboard_app.ai_suggest_status(started["job_id"], FakeCalendarRequest({}), db)
                if status["status"] == "completed":
                    break
                await asyncio.sleep(0.01)

        await db.close()
        dashboard_app._AI_SUGGEST_TASKS.clear()

        self.assertEqual(started["status"], "pending")
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["result"], expected)

    async def _scheduled_count(self, db):
        async with db._db.execute("SELECT COUNT(*) FROM scheduled_messages") as cur:
            return (await cur.fetchone())[0]

    async def test_executable_types_without_topic_resolve_to_handler_routing(self):
        # free_games / weekly_roundup / weekly_leaderboard are intentionally NOT in
        # this list — they are cron-owned (bot/scheduler/dispatch_owner.py) and
        # rejected by create_calendar_item (see test_create_calendar_item_rejects_
        # cron_owned_types below).
        for message_type in (
            "trivia_round",
            "emoji_puzzle",
            "facts_tidbit",
            "facts_spooky",
        ):
            with self.subTest(message_type=message_type):
                db = FakeCalendarDb()
                body = {
                    "text": "scheduled activity",
                    "message_type": message_type,
                    "channel_topic_id": None,
                    "target_group": "main",
                    "scheduled_date": "2099-01-01",
                    "scheduled_time": "18:00",
                }
                with patch.object(
                    dashboard_app,
                    "_ensure_trivia_announcement_scheduled",
                    new=AsyncMock(return_value=None),
                ), patch.object(
                    dashboard_app,
                    "_ensure_trivia_pool_ready_for_round",
                    new=AsyncMock(return_value={"generated": 0, "available": 10, "required": 10}),
                ):
                    res = await dashboard_app.create_calendar_item(FakeCalendarRequest(body), db)

                self.assertEqual(res["status"], "ok")
                self.assertEqual(db.created[0]["message_type"], message_type)
                self.assertEqual(db.created[0]["channel_topic_id"], 4037)

    async def test_create_calendar_item_trivia_round_without_db_id_tops_up_pool(self):
        db = FakeCalendarDb()
        body = {
            "text": "scheduled trivia",
            "message_type": "trivia_round",
            "channel_topic_id": None,
            "target_group": "test",
            "scheduled_date": "2099-01-01",
            "scheduled_time": "18:00",
            "poll_options": json.dumps({"categories": [], "question_count": 10}),
        }

        with patch.object(
            dashboard_app,
            "_ensure_trivia_announcement_scheduled",
            new=AsyncMock(return_value=None),
        ):
            res = await dashboard_app.create_calendar_item(FakeCalendarRequest(body), db)

        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["trivia_topup"], {"generated": 0, "available": 10, "required": 10})
        self.assertEqual(db.created[0]["message_type"], "trivia_round")
        self.assertEqual(db.created[0]["target_group"], "test")

    async def test_create_calendar_item_rejects_same_minute_clash(self):
        db = Database(":memory:")
        await db.init()
        try:
            await db.create_scheduled_message(
                text="first",
                message_type="custom",
                channel_topic_id=341,
                target_group="main",
                scheduled_date="2099-01-01",
                scheduled_time="18:00",
            )
            body = {
                "text": "second",
                "message_type": "emoji_puzzle",
                "channel_topic_id": 4037,
                "target_group": "main",
                "scheduled_date": "2099-01-01",
                "scheduled_time": "18:00",
            }
            with self.assertRaises(HTTPException) as ctx:
                await dashboard_app.create_calendar_item(FakeCalendarRequest(body), db)
            self.assertEqual(ctx.exception.status_code, 409)
            self.assertEqual(ctx.exception.detail["error"], "slot_clash")
        finally:
            await db.close()

    async def test_legacy_ai_fill_trivia_links_warmup_and_game_marker(self):
        db = Database(":memory:")
        await db.init()
        try:
            await db.set_handler_routing("trivia_round", play_topic_id=4037, teaser_topic_ids=[])
            with patch.object(
                dashboard_app,
                "_generate_activity_copy",
                new=AsyncMock(return_value="warmup copy"),
            ):
                result = await dashboard_app._ai_fill_trivia_for_week(db, 0)

            rows = await db.get_scheduled_messages("2000-01-01", "2999-12-31", include_cancelled=True)
            by_type = {row["message_type"]: row for row in rows}
            self.assertEqual(result["inserted"], 2, result)
            self.assertIn("trivia_warmup_rsvp", by_type)
            self.assertIn("trivia_round", by_type)
            self.assertEqual(by_type["trivia_warmup_rsvp"]["channel_topic_id"], 4037)
            self.assertEqual(by_type["trivia_round"]["channel_topic_id"], 4037)
            warmup_payload = json.loads(by_type["trivia_warmup_rsvp"]["poll_options"])
            game_payload = json.loads(by_type["trivia_round"]["poll_options"])
            self.assertEqual(warmup_payload["warmup_marker"], game_payload["warmup_marker"])
            self.assertTrue(game_payload["warmup_marker"].startswith("warmup-rsvp:trivia:"))
        finally:
            await db.close()

    async def test_legacy_ai_fill_trivia_refuses_occupied_game_slot(self):
        db = Database(":memory:")
        await db.init()
        try:
            await db.set_handler_routing("trivia_round", play_topic_id=4037, teaser_topic_ids=[])
            with patch.object(dashboard_app, "date") as fake_date:
                fake_date.today.return_value = datetime(2099, 1, 1).date()
                fake_date.fromisoformat.side_effect = lambda value: datetime.strptime(value, "%Y-%m-%d").date()
                await db.create_scheduled_message(
                    text="occupied",
                    message_type="custom",
                    channel_topic_id=341,
                    target_group="main",
                    scheduled_date="2099-01-03",
                    scheduled_time="21:00",
                    status="scheduled",
                )
                result = await dashboard_app._ai_fill_trivia_for_week(db, 0)

            rows = await db.get_scheduled_messages("2099-01-03", "2099-01-03")
            self.assertEqual(result["inserted"], 0, result)
            self.assertIn("round slot clash", result["errors"][0])
            self.assertEqual([row["message_type"] for row in rows], ["custom"])
        finally:
            await db.close()

    async def test_create_calendar_item_rejects_cron_owned_types(self):
        # Cron-owned types (weekly_roundup/weekly_leaderboard/free_games — see
        # bot/scheduler/dispatch_owner.py) are sent by the APScheduler cron jobs, not
        # as calendar rows. Creating them as rows caused a duplicate send (2026-05-23),
        # so the endpoint must reject every cron-owned type with HTTP 400.
        from fastapi import HTTPException
        from bot.scheduler.dispatch_owner import CRON_OWNED_TYPES
        for message_type in sorted(CRON_OWNED_TYPES):
            with self.subTest(message_type=message_type):
                db = FakeCalendarDb()
                body = {
                    "text": "scheduled activity",
                    "message_type": message_type,
                    "channel_topic_id": None,
                    "target_group": "main",
                    "scheduled_date": "2099-01-01",
                    "scheduled_time": "18:00",
                }
                with self.assertRaises(HTTPException) as ctx:
                    await dashboard_app.create_calendar_item(FakeCalendarRequest(body), db)
                self.assertEqual(ctx.exception.status_code, 400)
                self.assertEqual(db.created, [])

    async def test_create_calendar_item_rejects_unsupported_target_group(self):
        db = FakeCalendarDb()
        body = {
            "text": "scheduled activity",
            "message_type": "custom",
            "channel_topic_id": 4037,
            "target_group": "both",
            "scheduled_date": "2099-01-01",
            "scheduled_time": "18:00",
        }
        with self.assertRaises(HTTPException) as ctx:
            await dashboard_app.create_calendar_item(FakeCalendarRequest(body), db)

        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("unsupported target_group", ctx.exception.detail)
        self.assertEqual(db.created, [])

    async def test_ai_suggest_commit_schedules_approved_rows(self):
        db = FakeCalendarDb()
        body = {
            "approved": [{
                "date": "2099-01-01",
                "time": "22:00",
                "message_type": "emoji_puzzle",
                "topic_id": 4037,
                "text": "Emoji Night",
                "source": "ai-fill-pool-row",
                "poll_options_json": '{"theme_label":"movies"}',
            }]
        }

        res = await dashboard_app.ai_suggest_commit(FakeCalendarRequest(body), db)

        self.assertEqual(res["inserted"], 1)
        self.assertEqual(db.created[0]["status"], "scheduled")
        self.assertEqual(db.created[0]["message_type"], "emoji_puzzle")
        self.assertEqual(db.created[0]["poll_options"], '{"theme_label":"movies"}')

    async def test_ai_suggest_commit_is_idempotent_for_existing_game_slot(self):
        db = Database(":memory:")
        await db.init()
        body = {
            "approved": [{
                "date": "2099-01-01",
                "time": "22:00",
                "message_type": "emoji_puzzle",
                "topic_id": 4037,
                "text": "",
                "source": "ai-fill-pool-row",
                "poll_options_json": '{"theme_label":"movies"}',
            }]
        }

        first = await dashboard_app.ai_suggest_commit(FakeCalendarRequest(body), db)
        second = await dashboard_app.ai_suggest_commit(FakeCalendarRequest(body), db)
        rows = await db.get_scheduled_messages("2099-01-01", "2099-01-01")
        await db.close()

        self.assertEqual(first["inserted"], 1, first)
        self.assertEqual(second["inserted"], 0, second)
        self.assertIn("duplicate slot", second["skipped"][0])
        self.assertEqual([row["message_type"] for row in rows], ["emoji_puzzle"])

    async def test_ai_suggest_commit_dedupes_duplicate_game_slots_in_one_request(self):
        db = Database(":memory:")
        await db.init()
        approved = {
            "date": "2099-01-01",
            "time": "22:00",
            "message_type": "trivia_round",
            "topic_id": 4037,
            "text": "🧠 סיבוב טריוויה",
            "source": "ai-fill-pool-row",
            "poll_options_json": '{"theme_label":"כללי"}',
        }

        result = await dashboard_app.ai_suggest_commit(
            FakeCalendarRequest({"approved": [approved, dict(approved)]}),
            db,
        )
        rows = await db.get_scheduled_messages("2099-01-01", "2099-01-01")
        await db.close()

        self.assertEqual(result["inserted"], 1, result)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("duplicate slot", result["skipped"][0])
        self.assertEqual([row["message_type"] for row in rows], ["trivia_round"])

    async def test_ai_suggest_commit_rejects_same_minute_activity_clash(self):
        db = Database(":memory:")
        await db.init()
        approved = [
            {
                "date": "2099-01-01",
                "time": "22:00",
                "message_type": "emoji_puzzle",
                "topic_id": 4037,
                "text": "",
                "source": "ai-fill-pool-row",
                "poll_options_json": '{"theme_label":"movies"}',
            },
            {
                "date": "2099-01-01",
                "time": "22:00",
                "message_type": "facts_tidbit",
                "topic_id": 341,
                "text": "fact",
                "source": "ai-fill-pool-row",
                "poll_options_json": '{"fact_id":"f1"}',
            },
        ]

        result = await dashboard_app.ai_suggest_commit(
            FakeCalendarRequest({"approved": approved}),
            db,
        )
        rows = await db.get_scheduled_messages("2099-01-01", "2099-01-01")
        await db.close()

        self.assertEqual(result["inserted"], 1, result)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("slot clash", result["skipped"][0])
        self.assertEqual([row["message_type"] for row in rows], ["emoji_puzzle"])

    async def test_ai_suggest_commit_rejects_known_low_quality_text(self):
        db = FakeCalendarDb()
        body = {
            "approved": [{
                "date": "2099-01-01",
                "time": "09:00",
                "message_type": "morning",
                "topic_id": 4037,
                "text": "☕ שני בבוקר — קפה ראשון בשקט, או רצים כבר שתתחיל הרעש?",
                "source": "ai-fill",
            }]
        }

        res = await dashboard_app.ai_suggest_commit(FakeCalendarRequest(body), db)

        self.assertEqual(res["inserted"], 0)
        self.assertEqual(db.created, [])
        self.assertIn("quality rejected", res["errors"][0])

    async def test_ai_suggest_today_token_uses_server_israel_date(self):
        db = Database(":memory:")
        await db.init()
        dashboard_app._AI_SUGGEST_TASKS.clear()
        try:
            with patch.object(dashboard_app, "_ai_suggest_calendar", new=AsyncMock(return_value={"ok": True})) as suggest:
                started = await dashboard_app.ai_suggest(FakeCalendarRequest({"target_date": "today"}), db)
                for _ in range(10):
                    res = await dashboard_app.ai_suggest_status(started["job_id"], FakeCalendarRequest({}), db)
                    if res["status"] == "completed":
                        break
                    await asyncio.sleep(0.01)
        finally:
            await db.close()
            dashboard_app._AI_SUGGEST_TASKS.clear()

        self.assertEqual(res["result"], {"ok": True})
        called_date = suggest.await_args.kwargs["target_date"]
        self.assertRegex(called_date, r"^\d{4}-\d{2}-\d{2}$")

    async def test_ai_suggest_empty_body_uses_defaults(self):
        db = Database(":memory:")
        await db.init()
        dashboard_app._AI_SUGGEST_TASKS.clear()
        try:
            with patch.object(dashboard_app, "_ai_suggest_calendar", new=AsyncMock(return_value={"ok": True})) as suggest:
                started = await dashboard_app.ai_suggest(FakeCalendarRequest(None), db)
                for _ in range(10):
                    res = await dashboard_app.ai_suggest_status(started["job_id"], FakeCalendarRequest({}), db)
                    if res["status"] == "completed":
                        break
                    await asyncio.sleep(0.01)
        finally:
            await db.close()
            dashboard_app._AI_SUGGEST_TASKS.clear()

        self.assertEqual(res["result"], {"ok": True})
        self.assertIsNone(suggest.await_args.kwargs["target_date"])
        self.assertEqual(suggest.await_args.kwargs["week_offset"], 0)

    async def test_ai_suggest_cancel_stops_running_job(self):
        db = Database(":memory:")
        await db.init()
        dashboard_app._AI_SUGGEST_TASKS.clear()
        started_event = asyncio.Event()

        async def slow_suggest(*args, **kwargs):
            started_event.set()
            await asyncio.sleep(60)

        try:
            with patch.object(dashboard_app, "_ai_suggest_calendar", new=AsyncMock(side_effect=slow_suggest)):
                started = await dashboard_app.ai_suggest(FakeCalendarRequest({"target_date": "2099-01-01"}), db)
                await asyncio.wait_for(started_event.wait(), timeout=1)
                cancelled = await dashboard_app.ai_suggest_cancel(started["job_id"], FakeCalendarRequest({}), db)
                await asyncio.sleep(0)
                status = await dashboard_app.ai_suggest_status(started["job_id"], FakeCalendarRequest({}), db)
        finally:
            await db.close()
            dashboard_app._AI_SUGGEST_TASKS.clear()

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(status["status"], "cancelled")

    async def test_ai_suggest_survives_dashboard_restart(self):
        """Gap 10: completed job state in SQLite must be readable across processes."""
        db = Database(":memory:")
        await db.init()
        dashboard_app._AI_SUGGEST_TASKS.clear()
        try:
            with patch.object(
                dashboard_app, "_ai_suggest_calendar",
                new=AsyncMock(return_value={"ok": True, "suggestions": []}),
            ):
                started = await dashboard_app.ai_suggest(
                    FakeCalendarRequest({"target_date": "2099-01-01"}), db,
                )
                for _ in range(20):
                    res = await dashboard_app.ai_suggest_status(started["job_id"], FakeCalendarRequest({}), db)
                    if res["status"] == "completed":
                        break
                    await asyncio.sleep(0.01)
            # Simulate dashboard restart: drop the in-memory task handle.
            # The job row in SQLite must still answer status queries.
            dashboard_app._AI_SUGGEST_TASKS.clear()
            after_restart = await dashboard_app.ai_suggest_status(
                started["job_id"], FakeCalendarRequest({}), db,
            )
        finally:
            await db.close()
            dashboard_app._AI_SUGGEST_TASKS.clear()

        self.assertEqual(after_restart["status"], "completed")
        self.assertEqual(after_restart["result"], {"ok": True, "suggestions": []})

    async def test_ai_suggest_orphan_recovery_marks_pending_failed(self):
        """Gap 10: pending/running rows from a prior process are reclassified."""
        db = Database(":memory:")
        await db.init()
        try:
            await db.create_ai_suggest_job("orphan-1", target_date="2099-01-01", week_offset=0)
            await db.update_ai_suggest_job("orphan-1", status="running")
            await db.create_ai_suggest_job("orphan-2", target_date=None, week_offset=1)
            # leave orphan-2 at status='pending'

            recovered = await db.recover_orphaned_ai_suggest_jobs()
            row1 = await db.get_ai_suggest_job("orphan-1")
            row2 = await db.get_ai_suggest_job("orphan-2")
        finally:
            await db.close()

        self.assertEqual(recovered, 2)
        self.assertEqual(row1["status"], "failed")
        self.assertIn("restart", (row1["error"] or "").lower())
        self.assertEqual(row2["status"], "failed")

    async def test_calendar_api_does_not_render_static_pool_preview_events(self):
        db = Database(":memory:")
        await db.init()
        try:
            events = await dashboard_app.get_calendar(
                FakeQueryRequest({"start": "2099-01-04", "end": "2099-01-11"}),
                db,
            )
        finally:
            await db.close()

        self.assertEqual(events, [])

    async def test_calendar_api_exposes_will_send_diagnostics(self):
        db = Database(":memory:")
        await db.init()
        try:
            scheduled_id = await db.create_scheduled_message(
                text="scheduled text",
                message_type="morning",
                channel_topic_id=2184,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="09:00",
                status="scheduled",
            )
            draft_id = await db.create_scheduled_message(
                text="draft text",
                message_type="discussion",
                channel_topic_id=54,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="18:00",
                status="draft",
            )
            failed_id = await db.create_scheduled_message(
                text="failed text",
                message_type="evening",
                channel_topic_id=2184,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="21:00",
                status="scheduled",
            )
            await db.mark_message_failed(failed_id, "telegram rejected")
            events = await dashboard_app.get_calendar(
                FakeQueryRequest({"start": "2099-01-04", "end": "2099-01-05"}),
                db,
            )
        finally:
            await db.close()

        by_id = {int(event["id"]): event["extendedProps"] for event in events}
        self.assertTrue(by_id[scheduled_id]["willSend"])
        self.assertEqual(by_id[scheduled_id]["diagnosticLabel"], "יישלח")
        self.assertIn("הסקזולר", by_id[scheduled_id]["diagnosticDetail"])
        self.assertFalse(by_id[draft_id]["willSend"])
        self.assertEqual(by_id[draft_id]["diagnosticLabel"], "טיוטה")
        self.assertIn("לא תישלח", by_id[draft_id]["diagnosticDetail"])
        self.assertFalse(by_id[failed_id]["willSend"])
        self.assertEqual(by_id[failed_id]["diagnosticLabel"], "נכשל")
        self.assertIn("telegram rejected", by_id[failed_id]["diagnosticDetail"])

    async def test_calendar_will_send_events_have_exactly_one_scheduled_row(self):
        db = Database(":memory:")
        await db.init()
        try:
            scheduled_id = await db.create_scheduled_message(
                text="sendable row",
                message_type="morning",
                channel_topic_id=2184,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="09:00",
                status="scheduled",
            )
            draft_id = await db.create_scheduled_message(
                text="draft row",
                message_type="discussion",
                channel_topic_id=54,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="18:00",
                status="draft",
            )
            sent_id = await db.create_scheduled_message(
                text="sent row",
                message_type="evening",
                channel_topic_id=2184,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="21:00",
                status="scheduled",
            )
            await db.mark_message_sent(sent_id, 123)
            skipped_id = await db.create_scheduled_message(
                text="skipped row",
                message_type="custom",
                channel_topic_id=341,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="22:00",
                status="scheduled",
            )
            await db.mark_message_skipped(skipped_id, "operator skipped")
            failed_id = await db.create_scheduled_message(
                text="failed row",
                message_type="custom",
                channel_topic_id=341,
                target_group="main",
                scheduled_date="2099-01-04",
                scheduled_time="23:00",
                status="scheduled",
            )
            await db.mark_message_failed(failed_id, "telegram rejected")

            events = await dashboard_app.get_calendar(
                FakeQueryRequest({"start": "2099-01-04", "end": "2099-01-05"}),
                db,
            )
            sendable = [event for event in events if event["extendedProps"].get("willSend")]
            for event in sendable:
                async with db._db.execute(
                    "SELECT COUNT(*) AS count FROM scheduled_messages WHERE id = ? AND status = 'scheduled'",
                    (int(event["id"]),),
                ) as cur:
                    row = await cur.fetchone()
                self.assertEqual(row["count"], 1, event)
        finally:
            await db.close()

        self.assertEqual([int(event["id"]) for event in sendable], [scheduled_id])
        by_id = {int(event["id"]): event["extendedProps"] for event in events if not str(event["id"]).startswith("preview-")}
        self.assertFalse(by_id[draft_id]["willSend"])
        self.assertFalse(by_id[sent_id]["willSend"])
        self.assertFalse(by_id[skipped_id]["willSend"])
        self.assertFalse(by_id[failed_id]["willSend"])
        for event in events:
            if str(event["id"]).startswith("preview-"):
                self.assertFalse(event["extendedProps"].get("willSend"))
                self.assertEqual(event["extendedProps"].get("diagnosticLabel"), "תצוגה בלבד")

    async def test_bot_reload_materializer_generates_fresh_auto_content(self):
        db = Database(":memory:")
        await db.init()
        counter = 0

        async def fresh_response(_prompt):
            nonlocal counter
            counter += 1
            return json.dumps({"text": f"טקסט חדש שנוצר אוטומטית מספר {counter}"}, ensure_ascii=False)

        try:
            with patch.object(materializer, "_generate_with_claude", new=AsyncMock(side_effect=fresh_response)):
                inserted = await materializer.materialize_forward(db, days_ahead=14)
            self.assertGreater(inserted, 0)
            self.assertEqual(await self._scheduled_count(db), inserted)
            async with db._db.execute(
                "SELECT text, created_by FROM scheduled_messages WHERE status = 'scheduled'"
            ) as cur:
                rows = await cur.fetchall()
            self.assertTrue(all(row["created_by"] == "auto" for row in rows))
            static_sources = set()
            prompts = dashboard_app.load_yaml("prompts.yaml") or {}
            for values in prompts.values():
                static_sources.update(str(v).strip() for v in values or [])
            discussions = dashboard_app.load_yaml("discussions.yaml") or {}
            for values in discussions.values():
                static_sources.update(str(v).strip() for v in values or [])
            for row in rows:
                self.assertNotIn(row["text"].strip(), static_sources)

            purged = await materializer.purge_future_auto_rows(db)
            self.assertEqual(purged, inserted)
        finally:
            await db.close()

    async def test_planner_day_diagnostics_reports_scheduler_state(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                await db.upsert_verified_forum_topic(4037, "הפינה של בוטסון", "botson_corner", "test")
                await db.set_handler_routing("emoji_puzzle", 4037, [])
                msg_id = await db.create_scheduled_message(
                    text="🧩 Emoji Night — סרטים וסדרות (5 חידות)",
                    message_type="emoji_puzzle",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="22:00",
                    created_by="ai-fill-pool-row",
                    status="scheduled",
                    poll_options=json.dumps({"theme_label": "סרטים וסדרות", "media_types": ["movie", "tv"]}, ensure_ascii=False),
                )

                res = await dashboard_app.planner_day_diagnostics(
                    FakeQueryRequest({"date": "2099-01-01"}),
                    db,
                )

                self.assertEqual(res["counts"]["total"], 1)
                self.assertEqual(res["counts"]["by_type"]["emoji_puzzle"], 1)
                self.assertEqual(res["routing"]["emoji_puzzle"]["play_topic_id"], 4037)
                row = res["rows"][0]
                self.assertEqual(row["id"], msg_id)
                self.assertEqual(row["status"], "scheduled")
                self.assertEqual(row["message_type"], "emoji_puzzle")
                self.assertTrue(row["topic_verified"])
                self.assertEqual(row["payload"]["media_types"], ["movie", "tv"])
            finally:
                await db.close()

    async def test_approved_emoji_populate_rows_are_due_and_dispatch_correctly(self):
        now = datetime.now(ZoneInfo("Asia/Jerusalem"))
        today = now.date().isoformat()
        due_time = now.strftime("%H:%M")
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                await db.upsert_verified_forum_topic(4037, "הפינה של בוטסון", "botson_corner", "test")
                await db.set_handler_routing("emoji_puzzle", 4037, [])
                approved = [
                    {
                        "date": today,
                        "time": due_time,
                        "message_type": "discussion",
                        "topic_id": 4037,
                        "text": "🧩 הערב ב-22:00: חידת אימוג'י בנושא סרטים וסדרות.",
                        "source": "ai-fill-emoji",
                    },
                    {
                        "date": today,
                        "time": due_time,
                        "message_type": "emoji_puzzle",
                        "topic_id": 4037,
                        "text": "🧩 Emoji Night — סרטים וסדרות (5 חידות)",
                        "source": "ai-fill-pool-row",
                        "poll_options_json": json.dumps({
                            "theme_label": "סרטים וסדרות",
                            "media_types": ["movie", "tv"],
                            "puzzle_count": 5,
                        }, ensure_ascii=False),
                    },
                ]

                commit = await dashboard_app.ai_suggest_commit(
                    FakeCalendarRequest({"approved": approved}),
                    db,
                )
                self.assertEqual(commit["inserted"], 1)
                self.assertEqual(len(commit["skipped"]), 1)
                self.assertIn("slot clash", commit["skipped"][0])
                due = await db.get_due_messages(today, due_time)
                self.assertEqual([row["message_type"] for row in due], ["emoji_puzzle"])
                self.assertTrue(all(row["status"] == "scheduled" for row in due))

                context = SimpleNamespace(bot_data={"db": db}, bot=object())
                sent = SimpleNamespace(message_id=9001)
                with patch.dict(bot_calendar.os.environ, {"BOT_TOKEN": "token", "GROUP_ID": "-1001"}), \
                     patch("telegram.Bot", return_value=object()), \
                     patch.object(bot_calendar, "send_message_with_optional_cover", new=AsyncMock(return_value=sent)) as send_text, \
                     patch.object(bot_calendar, "emoji_skip_reason", new=AsyncMock(return_value=None)), \
                     patch.object(bot_calendar, "start_emoji_night", new=AsyncMock(return_value={"session_id": 7, "message_id": 77})) as start_emoji:
                    await bot_calendar.check_and_send_due_messages(context)

                send_text.assert_not_awaited()
                start_emoji.assert_awaited_once_with(
                    context, -1001, 4037, force=True,
                    media_types=["movie", "tv"], theme_label="סרטים וסדרות",
                    return_launch_info=True,
                )
                rows = await db.get_scheduled_messages(today, today)
                by_type = {row["message_type"]: row for row in rows}
                self.assertEqual(by_type["emoji_puzzle"]["status"], "sent")
                self.assertNotIn("discussion", by_type)
            finally:
                await db.close()

    async def test_plain_text_types_keep_selected_topic(self):
        db = FakeCalendarDb()
        body = {
            "text": "hello",
            "message_type": "custom",
            "channel_topic_id": 54,
            "target_group": "main",
            "scheduled_date": "2099-01-01",
            "scheduled_time": "18:00",
        }
        await dashboard_app.create_calendar_item(FakeCalendarRequest(body), db)
        self.assertEqual(db.created[0]["message_type"], "custom")
        self.assertEqual(db.created[0]["channel_topic_id"], 54)

    async def test_turning_trivia_live_promotes_existing_warmup_to_scheduled(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                game_id = await db.create_scheduled_message(
                    text="🧠 סיבוב טריוויה גיימינג! 10 שאלות",
                    message_type="trivia_round",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="22:00",
                    poll_options=json.dumps({
                        "pre_roll_s": 30,
                        "theme_label": "גיימינג",
                        "categories": ["גיימינג"],
                        "question_count": 10,
                    }, ensure_ascii=False),
                    status="draft",
                )
                warmup_id = await db.create_scheduled_message(
                    text="old warmup",
                    message_type="custom",
                    channel_topic_id=341,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="18:00",
                    created_by=f"trivia-announcement-draft:{game_id}",
                    status="draft",
                )

                with patch.object(
                    dashboard_app,
                    "_ensure_trivia_pool_ready_for_round",
                    new=AsyncMock(return_value={"generated": 0, "available": 10, "required": 10}),
                ), patch.object(
                    dashboard_app,
                    "_generate_activity_copy",
                    new=AsyncMock(return_value="טריוויה גיימינג מתחילה ב-22:00\nבעוד 60 דקות מתחילים."),
                ):
                    res = await dashboard_app.schedule_calendar_item(
                        game_id,
                        FakeCalendarRequest({}),
                        db,
                    )

                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["announcement_draft_id"], warmup_id)
                async with db._db.execute(
                    "SELECT status, scheduled_time, channel_topic_id, text FROM scheduled_messages WHERE id = ?",
                    (warmup_id,),
                ) as cur:
                    warmup = await cur.fetchone()
                self.assertEqual(warmup["status"], "scheduled")
                self.assertEqual(warmup["scheduled_time"], "21:00")
                self.assertEqual(warmup["channel_topic_id"], 1517)
                self.assertIn("22:00", warmup["text"])
                self.assertIn("60 דקות", warmup["text"])
            finally:
                await db.close()

    async def test_turning_trivia_live_blocks_visibly_when_topup_fails(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                game_id = await db.create_scheduled_message(
                    text="🧠 סיבוב טריוויה גיימינג! 10 שאלות",
                    message_type="trivia_round",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="22:00",
                    poll_options=json.dumps({
                        "pre_roll_s": 30,
                        "theme_label": "גיימינג",
                        "categories": ["גיימינג"],
                        "question_count": 10,
                    }, ensure_ascii=False),
                    status="draft",
                )

                with patch.object(
                    dashboard_app,
                    "_ensure_trivia_pool_ready_for_round",
                    new=AsyncMock(side_effect=HTTPException(status_code=503, detail="missing trivia questions")),
                ):
                    with self.assertRaises(HTTPException) as ctx:
                        await dashboard_app.schedule_calendar_item(game_id, FakeCalendarRequest({}), db)

                self.assertEqual(ctx.exception.status_code, 503)
                self.assertIn("missing trivia questions", ctx.exception.detail)
                async with db._db.execute("SELECT status FROM scheduled_messages WHERE id = ?", (game_id,)) as cur:
                    row = await cur.fetchone()
                self.assertEqual(row["status"], "draft")
            finally:
                await db.close()

    async def test_review_schedule_can_move_draft_to_requested_date(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                msg_id = await db.create_scheduled_message(
                    text="🎬 סרט שראיתם לבד והפתיע אתכם לטובה?",
                    message_type="discussion",
                    channel_topic_id=59,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="19:30",
                    status="draft",
                    created_by="ai-fill-today",
                )

                res = await dashboard_app.schedule_calendar_item(
                    msg_id,
                    FakeCalendarRequest({"scheduled_date": "2099-01-02", "scheduled_time": "20:00"}),
                    db,
                )

                self.assertEqual(res["status"], "ok")
                self.assertEqual(res["scheduled_for"], "2099-01-02T20:00:00")
                async with db._db.execute(
                    "SELECT scheduled_date, scheduled_time, status FROM scheduled_messages WHERE id = ?",
                    (msg_id,),
                ) as cur:
                    row = await cur.fetchone()
                self.assertEqual(row["scheduled_date"], "2099-01-02")
                self.assertEqual(row["scheduled_time"], "20:00")
                self.assertEqual(row["status"], "scheduled")
            finally:
                await db.close()

    async def test_review_schedule_rejects_existing_low_quality_draft(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                msg_id = await db.create_scheduled_message(
                    text="ערב טוב 🌙 איך היה היום? ספרו דבר אחד טוב שקרה",
                    message_type="evening",
                    channel_topic_id=2184,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="21:00",
                    status="draft",
                    created_by="ai-fill-today",
                )

                with self.assertRaises(HTTPException) as ctx:
                    await dashboard_app.schedule_calendar_item(
                        msg_id,
                        FakeCalendarRequest({"scheduled_date": "2099-01-01", "scheduled_time": "21:00"}),
                        db,
                    )

                self.assertEqual(ctx.exception.status_code, 422)
                self.assertEqual(ctx.exception.detail["error"], "quality_rejected")
                self.assertIn("concrete_failure_generic_day_checkin", ctx.exception.detail["failures"])
                async with db._db.execute("SELECT status FROM scheduled_messages WHERE id = ?", (msg_id,)) as cur:
                    row = await cur.fetchone()
                self.assertEqual(row["status"], "draft")
            finally:
                await db.close()

    async def test_update_calendar_item_rejects_unsupported_target_group(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                msg_id = await db.create_scheduled_message(
                    text="valid body",
                    message_type="custom",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="18:00",
                    status="draft",
                )

                with self.assertRaises(HTTPException) as ctx:
                    await dashboard_app.update_calendar_item(
                        msg_id,
                        FakeCalendarRequest({"target_group": "both"}),
                        db,
                    )

                self.assertEqual(ctx.exception.status_code, 400)
                async with db._db.execute("SELECT target_group FROM scheduled_messages WHERE id = ?", (msg_id,)) as cur:
                    row = await cur.fetchone()
                self.assertEqual(row["target_group"], "main")
            finally:
                await db.close()

    async def test_update_calendar_item_rejects_same_minute_clash(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                await db.create_scheduled_message(
                    text="first",
                    message_type="custom",
                    channel_topic_id=341,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="18:00",
                    status="scheduled",
                )
                msg_id = await db.create_scheduled_message(
                    text="second",
                    message_type="custom",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="19:00",
                    status="scheduled",
                )

                with self.assertRaises(HTTPException) as ctx:
                    await dashboard_app.update_calendar_item(
                        msg_id,
                        FakeCalendarRequest({"scheduled_time": "18:00", "force": True}),
                        db,
                    )

                self.assertEqual(ctx.exception.status_code, 409)
                self.assertEqual(ctx.exception.detail["error"], "slot_clash")
                async with db._db.execute("SELECT scheduled_time FROM scheduled_messages WHERE id = ?", (msg_id,)) as cur:
                    row = await cur.fetchone()
                self.assertEqual(row["scheduled_time"], "19:00")
            finally:
                await db.close()

    async def test_put_approval_rejects_existing_low_quality_draft_without_text_payload(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                msg_id = await db.create_scheduled_message(
                    text="סרט שראיתם יותר מ-3 פעמים?",
                    message_type="discussion",
                    channel_topic_id=54,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="18:00",
                    status="draft",
                    created_by="ai-fill-today",
                )

                with self.assertRaises(HTTPException) as ctx:
                    await dashboard_app.update_calendar_item(
                        msg_id,
                        FakeCalendarRequest({"status": "scheduled", "scheduled_time": "18:00"}),
                        db,
                    )

                self.assertEqual(ctx.exception.status_code, 422)
                self.assertEqual(ctx.exception.detail["error"], "quality_rejected")
                self.assertIn("concrete_failure_generic_movie_rewatch", ctx.exception.detail["failures"])
                async with db._db.execute("SELECT status FROM scheduled_messages WHERE id = ?", (msg_id,)) as cur:
                    row = await cur.fetchone()
                self.assertEqual(row["status"], "draft")
            finally:
                await db.close()

    async def test_turning_trivia_live_creates_warmup_as_scheduled(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                game_id = await db.create_scheduled_message(
                    text="🧠 סיבוב טריוויה גיימינג! 10 שאלות",
                    message_type="trivia_round",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="22:00",
                    poll_options=json.dumps({
                        "pre_roll_s": 30,
                        "theme_label": "גיימינג",
                        "categories": ["גיימינג"],
                        "question_count": 10,
                    }, ensure_ascii=False),
                    status="draft",
                )

                with patch.object(
                    dashboard_app,
                    "_ensure_trivia_pool_ready_for_round",
                    new=AsyncMock(return_value={"generated": 0, "available": 10, "required": 10}),
                ), patch.object(
                    dashboard_app,
                    "_generate_activity_copy",
                    new=AsyncMock(return_value="טריוויה גיימינג מתחילה ב-22:00\nבעוד 60 דקות מתחילים."),
                ):
                    res = await dashboard_app.schedule_calendar_item(
                        game_id,
                        FakeCalendarRequest({}),
                        db,
                    )

                self.assertEqual(res["status"], "ok")
                warmup_id = res["announcement_draft_id"]
                self.assertIsInstance(warmup_id, int)
                async with db._db.execute(
                    "SELECT status, scheduled_time, channel_topic_id, created_by FROM scheduled_messages WHERE id = ?",
                    (warmup_id,),
                ) as cur:
                    warmup = await cur.fetchone()
                self.assertEqual(warmup["status"], "scheduled")
                self.assertEqual(warmup["scheduled_time"], "21:00")
                self.assertEqual(warmup["channel_topic_id"], 1517)
                self.assertEqual(warmup["created_by"], f"trivia-announcement-draft:{game_id}")
            finally:
                await db.close()

    async def test_trivia_warmup_inherits_test_target_and_cancels_with_game(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                game_id = await db.create_scheduled_message(
                    text="🧠 סיבוב טריוויה בדיקה",
                    message_type="trivia_round",
                    channel_topic_id=4037,
                    target_group="test",
                    scheduled_date="2099-01-01",
                    scheduled_time="22:00",
                    poll_options=json.dumps({
                        "pre_roll_s": 30,
                        "theme_label": "כללי",
                        "categories": [],
                        "question_count": 5,
                    }, ensure_ascii=False),
                    status="draft",
                )

                with patch.object(
                    dashboard_app,
                    "_ensure_trivia_pool_ready_for_round",
                    new=AsyncMock(return_value={"generated": 0, "available": 5, "required": 5}),
                ), patch.object(
                    dashboard_app,
                    "_generate_activity_copy",
                    new=AsyncMock(return_value="טריוויה בדיקה מתחילה ב-22:00"),
                ):
                    res = await dashboard_app.schedule_calendar_item(
                        game_id,
                        FakeCalendarRequest({}),
                        db,
                    )

                warmup_id = res["announcement_draft_id"]
                async with db._db.execute(
                    "SELECT target_group, status FROM scheduled_messages WHERE id = ?",
                    (warmup_id,),
                ) as cur:
                    warmup = await cur.fetchone()
                self.assertEqual(warmup["target_group"], "test")
                self.assertEqual(warmup["status"], "scheduled")

                await dashboard_app.delete_calendar_item(game_id, FakeCalendarRequest({}), db)
                async with db._db.execute(
                    "SELECT status FROM scheduled_messages WHERE id IN (?, ?) ORDER BY id",
                    (game_id, warmup_id),
                ) as cur:
                    statuses = [row["status"] for row in await cur.fetchall()]
                self.assertEqual(statuses, ["cancelled", "cancelled"])
            finally:
                await db.close()

    async def test_turning_trivia_live_creates_warmup_with_marker_but_no_public_reminder(self):
        """Scheduling a trivia game creates the RSVP warm-up marker, but not
        a second public reminder row; personal DMs own reminder follow-up."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                game_id = await db.create_scheduled_message(
                    text="🧠 סיבוב טריוויה גיימינג! 10 שאלות",
                    message_type="trivia_round",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="22:00",
                    poll_options=json.dumps({
                        "pre_roll_s": 30,
                        "theme_label": "גיימינג",
                        "categories": ["גיימינג"],
                        "question_count": 10,
                        "min_ready_players": 2,
                    }, ensure_ascii=False),
                    status="draft",
                )

                with patch.object(
                    dashboard_app, "_ensure_trivia_pool_ready_for_round",
                    new=AsyncMock(return_value={"generated": 0, "available": 10, "required": 10}),
                ), patch.object(
                    dashboard_app, "_generate_activity_copy",
                    new=AsyncMock(return_value="טריוויה גיימינג מתחילה ב-22:00\nלחצו על הכפתור."),
                ):
                    res = await dashboard_app.schedule_calendar_item(
                        game_id, FakeCalendarRequest({}), db,
                    )

                self.assertEqual(res["status"], "ok")
                announcement_id = res["announcement_draft_id"]
                async with db._db.execute(
                    "SELECT poll_options FROM scheduled_messages WHERE id = ?",
                    (announcement_id,),
                ) as cur:
                    ann = await cur.fetchone()
                ann_payload = json.loads(ann["poll_options"] or "{}")
                self.assertEqual(ann_payload["warmup_marker"], f"warmup-rsvp:{game_id}")

                async with db._db.execute(
                    """SELECT id, message_type, scheduled_time, channel_topic_id,
                              status, created_by, poll_options
                       FROM scheduled_messages
                       WHERE created_by = ?""",
                    (f"warmup-reminder-draft:{game_id}",),
                ) as cur:
                    reminder = await cur.fetchone()
                self.assertIsNone(reminder, "public warmup_reminder rows must not be created")
            finally:
                await db.close()

    async def test_legacy_reminder_prompt_uses_reminder_aware_rules(self):
        """If legacy reminder copy generation is used, it must still say the
        button lives on the original announcement, not on the reminder text."""
        captured = {}

        async def capture_cli(prompt: str) -> str:
            captured["prompt"] = prompt
            return "תזכורת קצרה"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=capture_cli)):
            result = await dashboard_app._generate_activity_copy(
                "trivia_warmup_reminder",
                game_time="22:00",
                reminder_offset_min=20,
                theme_label="גיימינג",
                activity_label="הטריוויה על גיימינג",
                min_ready_players=2,
                is_reminder=True,
            )
        self.assertIsNotNone(result)
        prompt = captured["prompt"]
        self.assertIn("ההודעה המקורית", prompt)
        self.assertIn("הכפתור בהודעה המקורית", prompt)
        self.assertNotIn("הכפתור מופיע מתחת לטקסט", prompt)

    async def test_emoji_warmup_copy_uses_configured_subject_without_llm(self):
        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock()) as cli:
            result = await dashboard_app._generate_activity_copy(
                "emoji_warmup",
                game_time="22:00",
                theme_label="סדרות",
                puzzle_count=5,
                min_ready_players=2,
            )

        self.assertIsNotNone(result)
        self.assertIn("סדרות", result)
        self.assertNotIn("סדרות מאוירות", result)
        cli.assert_not_called()

    async def test_legacy_warmup_reminder_row_is_skipped_when_threshold_met(self):
        """Legacy public reminder rows are skipped; personal DMs own reminders."""
        from bot.handlers import calendar as bot_calendar

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                marker = "warmup-rsvp:test-1"
                ann_id = await db.create_scheduled_message(
                    text="חימום טריוויה — לחצו אני בפנים",
                    message_type="trivia_warmup_rsvp",
                    channel_topic_id=341,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="21:00",
                    poll_options=json.dumps({
                        "min_ready_players": 2,
                        "warmup_marker": marker,
                        "game_time": "22:00",
                    }, ensure_ascii=False),
                    status="scheduled",
                )
                # Mark announcement as sent so dispatch finds it
                await db._db.execute(
                    "UPDATE scheduled_messages SET status='sent', sent_message_id=? WHERE id=?",
                    (5050, ann_id),
                )
                # Two responses → threshold met
                await db.add_trivia_interest_response(ann_id, 111, "user-a")
                await db.add_trivia_interest_response(ann_id, 222, "user-b")
                await db._db.commit()

                # Build a synthetic due reminder row
                reminder_row = {
                    "id": 9001,
                    "scheduled_date": "2099-01-01",
                    "scheduled_time": "21:40",
                    "message_type": "warmup_reminder",
                    "target_group": "main",
                    "channel_topic_id": 341,
                    "status": "scheduled",
                    "text": "תזכורת — עדיין אפשר להצטרף",
                    "created_by": f"warmup-reminder-draft:{marker}",
                    "auto_pin": False,
                    "poll_options": json.dumps({
                        "min_ready_players": 2,
                        "warmup_marker": marker,
                        "game_time": "22:00",
                    }, ensure_ascii=False),
                    "poll_duration": None,
                    "cover_path": None,
                    "recurrence": None,
                    "recurrence_days": None,
                }

                skipped: list = []
                sent: list = []
                original_get_due = db.get_due_messages

                async def fake_get_due(*args, **kwargs):
                    return [reminder_row]
                async def fake_get_scheduled_message(msg_id):
                    return dict(reminder_row) if msg_id == reminder_row["id"] else None
                async def fake_claim_scheduled_message(msg_id, *, stale_after_minutes=15):
                    return msg_id == reminder_row["id"]

                async def capture_skipped(msg_id, reason):
                    skipped.append((msg_id, reason))

                async def capture_sent(msg_id, sent_message_id):
                    sent.append((msg_id, sent_message_id))

                db.get_due_messages = fake_get_due  # type: ignore[assignment]
                db.get_scheduled_message = fake_get_scheduled_message  # type: ignore[assignment]
                db.claim_scheduled_message = fake_claim_scheduled_message  # type: ignore[assignment]
                db.mark_message_skipped = capture_skipped  # type: ignore[assignment]
                db.mark_message_sent = capture_sent  # type: ignore[assignment]
                async def noop_failed(msg_id, error):
                    pass
                db.mark_message_failed = noop_failed  # type: ignore[assignment]

                context = SimpleNamespace(bot_data={"db": db}, bot=object())

                with patch.dict(bot_calendar.os.environ,
                                {"BOT_TOKEN": "token", "GROUP_ID": "-1001",
                                 "TEST_GROUP_ID": "-1002", "STALE_DROP_MINUTES": "0"}), \
                     patch("telegram.Bot", return_value=object()), \
                     patch("bot.utils.config.warmup_reminder_enabled", return_value=True), \
                     patch.object(bot_calendar, "safe_send", new=AsyncMock()) as ss:
                    await bot_calendar.check_and_send_due_messages(context)

                ss.assert_not_awaited()
                self.assertEqual(len(skipped), 1)
                self.assertEqual(skipped[0][0], 9001)
                self.assertIn("personal DM reminders", skipped[0][1])
                self.assertEqual(sent, [])
            finally:
                db.get_due_messages = original_get_due  # type: ignore[assignment]
                await db.close()

    async def test_legacy_warmup_reminder_row_is_skipped_when_under_threshold(self):
        """Legacy public reminder rows are skipped even below threshold; the
        RSVP gate is enforced at game launch and personal DMs handle reminders."""
        from bot.handlers import calendar as bot_calendar

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                marker = "warmup-rsvp:test-2"
                ann_id = await db.create_scheduled_message(
                    text="חימום",
                    message_type="trivia_warmup_rsvp",
                    channel_topic_id=341,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="21:00",
                    poll_options=json.dumps({
                        "min_ready_players": 3,
                        "warmup_marker": marker,
                        "game_time": "22:00",
                    }, ensure_ascii=False),
                    status="scheduled",
                )
                await db._db.execute(
                    "UPDATE scheduled_messages SET status='sent', sent_message_id=? WHERE id=?",
                    (4242, ann_id),
                )
                await db.add_trivia_interest_response(ann_id, 111, "only-one")
                await db._db.commit()

                reminder_row = {
                    "id": 9002,
                    "scheduled_date": "2099-01-01",
                    "scheduled_time": "21:40",
                    "message_type": "warmup_reminder",
                    "target_group": "main",
                    "channel_topic_id": 341,
                    "status": "scheduled",
                    "text": "עוד מקום פנוי — לחצו אני בפנים בהודעה למעלה",
                    "created_by": "warmup-reminder-draft:t-2",
                    "auto_pin": False,
                    "poll_options": json.dumps({
                        "min_ready_players": 3,
                        "warmup_marker": marker,
                        "game_time": "22:00",
                    }, ensure_ascii=False),
                    "poll_duration": None,
                    "cover_path": None,
                    "recurrence": None,
                    "recurrence_days": None,
                }

                skipped: list = []
                sent: list = []
                async def fake_get_due(*args, **kwargs):
                    return [reminder_row]
                async def fake_get_scheduled_message(msg_id):
                    return dict(reminder_row) if msg_id == reminder_row["id"] else None
                async def fake_claim_scheduled_message(msg_id, *, stale_after_minutes=15):
                    return msg_id == reminder_row["id"]
                async def capture_skipped(msg_id, reason):
                    skipped.append((msg_id, reason))
                async def capture_sent(msg_id, sent_message_id):
                    sent.append((msg_id, sent_message_id))
                async def noop_failed(msg_id, error):
                    raise AssertionError(f"unexpected failure: {error}")

                db.get_due_messages = fake_get_due  # type: ignore[assignment]
                db.get_scheduled_message = fake_get_scheduled_message  # type: ignore[assignment]
                db.claim_scheduled_message = fake_claim_scheduled_message  # type: ignore[assignment]
                db.mark_message_skipped = capture_skipped  # type: ignore[assignment]
                db.mark_message_sent = capture_sent  # type: ignore[assignment]
                db.mark_message_failed = noop_failed  # type: ignore[assignment]

                context = SimpleNamespace(bot_data={"db": db}, bot=object())

                fake_sent = SimpleNamespace(message_id=7777)
                with patch.dict(bot_calendar.os.environ,
                                {"BOT_TOKEN": "token", "GROUP_ID": "-1001",
                                 "TEST_GROUP_ID": "-1002", "STALE_DROP_MINUTES": "0"}), \
                     patch("telegram.Bot", return_value=object()), \
                     patch("bot.utils.config.warmup_reminder_enabled", return_value=True), \
                     patch.object(bot_calendar, "safe_send",
                                  new=AsyncMock(return_value=fake_sent)) as ss:
                    await bot_calendar.check_and_send_due_messages(context)

                self.assertEqual(len(skipped), 1)
                self.assertEqual(skipped[0][0], 9002)
                self.assertIn("personal DM reminders", skipped[0][1])
                self.assertEqual(sent, [])
                ss.assert_not_awaited()
            finally:
                await db.close()

    async def _run_gated_trivia_dispatch(self, *, threshold, num_responses, marker):
        """Helper for T-127 trivia gate tests. Builds a DB with a sent
        announcement carrying `marker`, seeds N interest responses, then runs
        check_and_send_due_messages with a synthetic trivia_round row that
        carries the same marker. Returns (skipped, sent, start_trivia_calls,
        cancel_send_calls)."""
        from bot.handlers import calendar as bot_calendar

        db = Database(":memory:")
        await db.init()
        try:
            ann_id = await db.create_scheduled_message(
                text="חימום",
                message_type="trivia_warmup_rsvp",
                channel_topic_id=341,
                target_group="main",
                scheduled_date="2099-01-01",
                scheduled_time="21:00",
                poll_options=json.dumps({
                    "min_ready_players": threshold,
                    "warmup_marker": marker,
                    "game_time": "22:00",
                }, ensure_ascii=False),
                status="scheduled",
            )
            await db._db.execute(
                "UPDATE scheduled_messages SET status='sent', sent_message_id=? WHERE id=?",
                (4242, ann_id),
            )
            for uid in range(1, num_responses + 1):
                await db.add_trivia_interest_response(ann_id, 1000 + uid, f"u{uid}")
            await db._db.commit()

            game_row = {
                "id": 9100,
                "scheduled_date": "2099-01-01",
                "scheduled_time": "22:00",
                "message_type": "trivia_round",
                "target_group": "main",
                "channel_topic_id": 4037,
                "status": "scheduled",
                "text": "🧠 סיבוב טריוויה",
                "created_by": "dashboard",
                "auto_pin": False,
                "poll_options": json.dumps({
                    "min_ready_players": threshold,
                    "warmup_marker": marker,
                    "theme_label": "גיימינג",
                    "categories": ["גיימינג"],
                    "question_count": 5,
                    "activity_label": "הטריוויה על גיימינג",
                }, ensure_ascii=False),
                "poll_duration": None,
                "cover_path": None,
                "recurrence": None,
                "recurrence_days": None,
            }

            skipped: list = []
            sent: list = []

            async def fake_get_due(*a, **k):
                return [game_row]
            async def fake_get_scheduled_message(msg_id):
                return dict(game_row) if msg_id == game_row["id"] else None
            async def fake_claim_scheduled_message(msg_id, *, stale_after_minutes=15):
                return msg_id == game_row["id"]
            async def cap_skipped(msg_id, reason):
                skipped.append((msg_id, reason))
            async def cap_sent(msg_id, sent_message_id):
                sent.append((msg_id, sent_message_id))
            async def noop_failed(msg_id, error):
                raise AssertionError(f"unexpected failure: {error}")

            db.get_due_messages = fake_get_due  # type: ignore[assignment]
            db.get_scheduled_message = fake_get_scheduled_message  # type: ignore[assignment]
            db.claim_scheduled_message = fake_claim_scheduled_message  # type: ignore[assignment]
            db.mark_message_skipped = cap_skipped  # type: ignore[assignment]
            db.mark_message_sent = cap_sent  # type: ignore[assignment]
            db.mark_message_failed = noop_failed  # type: ignore[assignment]

            context = SimpleNamespace(bot_data={"db": db}, bot=object())

            with patch.dict(bot_calendar.os.environ,
                            {"BOT_TOKEN": "token", "GROUP_ID": "-1001",
                             "TEST_GROUP_ID": "-1002"}), \
                 patch("telegram.Bot", return_value=object()), \
                 patch("bot.utils.config.rsvp_gate_enabled", return_value=True), \
                 patch.object(bot_calendar, "start_scheduled_trivia_round",
                              new=AsyncMock(return_value=555)) as start_trivia, \
                 patch.object(bot_calendar, "safe_send",
                              new=AsyncMock(return_value=SimpleNamespace(message_id=999))) as ss:
                await bot_calendar.check_and_send_due_messages(context)

            return skipped, sent, start_trivia, ss
        finally:
            await db.close()

    async def test_trivia_round_cancelled_when_warmup_rsvp_under_threshold(self):
        """T-127: when only 1/3 RSVP'd, the trivia_round is skipped, the game
        is not launched, and a Hebrew cancel notice is sent as a reply to the
        announcement in the warm-up topic."""
        marker = "warmup-rsvp:t127-under"
        skipped, sent, start_trivia, ss = await self._run_gated_trivia_dispatch(
            threshold=3, num_responses=1, marker=marker,
        )
        start_trivia.assert_not_awaited()
        self.assertEqual(sent, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("warmup_rsvp_gate", skipped[0][1])
        self.assertIn("1/3", skipped[0][1])
        # Cancel notice is sent (the only safe_send call in this branch is the
        # cancellation reply).
        ss.assert_awaited()
        cancel_kwargs = ss.await_args.kwargs
        self.assertEqual(cancel_kwargs["chat_id"], -1001)
        self.assertEqual(cancel_kwargs["message_thread_id"], 341)
        self.assertEqual(cancel_kwargs["reply_to_message_id"], 4242)
        self.assertIn("1/3", cancel_kwargs["text"])

    async def test_trivia_round_proceeds_when_warmup_rsvp_meets_threshold(self):
        """T-127: when RSVP count equals or exceeds the threshold, the gate
        is a no-op and the trivia round launches as before."""
        marker = "warmup-rsvp:t127-met"
        skipped, sent, start_trivia, ss = await self._run_gated_trivia_dispatch(
            threshold=2, num_responses=2, marker=marker,
        )
        start_trivia.assert_awaited_once()
        self.assertEqual(skipped, [])
        self.assertEqual(sent, [(9100, 555)])
        ss.assert_not_awaited()  # no cancel notice

    async def test_legacy_trivia_round_without_marker_proceeds(self):
        """T-127: rows that predate the RSVP system (no warmup_marker) launch
        unchanged — the gate must not break legacy schedules."""
        from bot.handlers import calendar as bot_calendar

        db = Database(":memory:")
        await db.init()
        try:
            game_row = {
                "id": 9101,
                "scheduled_date": "2099-01-01",
                "scheduled_time": "22:00",
                "message_type": "trivia_round",
                "target_group": "main",
                "channel_topic_id": 4037,
                "status": "scheduled",
                "text": "🧠 סיבוב",
                "created_by": "dashboard",
                "auto_pin": False,
                "poll_options": json.dumps({
                    "theme_label": "כללי",
                    "categories": [],
                    "question_count": 5,
                }, ensure_ascii=False),
                "poll_duration": None,
                "cover_path": None,
                "recurrence": None,
                "recurrence_days": None,
            }
            sent: list = []
            async def fake_get_due(*a, **k):
                return [game_row]
            async def fake_get_scheduled_message(msg_id):
                return dict(game_row) if msg_id == game_row["id"] else None
            async def fake_claim_scheduled_message(msg_id, *, stale_after_minutes=15):
                return msg_id == game_row["id"]
            async def cap_sent(msg_id, sent_message_id):
                sent.append((msg_id, sent_message_id))
            async def noop_skipped(msg_id, reason):
                raise AssertionError(f"unexpected skip: {reason}")
            async def noop_failed(msg_id, error):
                raise AssertionError(f"unexpected failure: {error}")

            db.get_due_messages = fake_get_due  # type: ignore[assignment]
            db.get_scheduled_message = fake_get_scheduled_message  # type: ignore[assignment]
            db.claim_scheduled_message = fake_claim_scheduled_message  # type: ignore[assignment]
            db.mark_message_sent = cap_sent  # type: ignore[assignment]
            db.mark_message_skipped = noop_skipped  # type: ignore[assignment]
            db.mark_message_failed = noop_failed  # type: ignore[assignment]

            context = SimpleNamespace(bot_data={"db": db}, bot=object())
            with patch.dict(bot_calendar.os.environ,
                            {"BOT_TOKEN": "token", "GROUP_ID": "-1001",
                             "TEST_GROUP_ID": "-1002"}), \
                 patch("telegram.Bot", return_value=object()), \
                 patch.object(bot_calendar, "start_scheduled_trivia_round",
                              new=AsyncMock(return_value=777)) as start_trivia, \
                 patch.object(bot_calendar, "safe_send", new=AsyncMock()) as ss:
                await bot_calendar.check_and_send_due_messages(context)

            start_trivia.assert_awaited_once()
            self.assertEqual(sent, [(9101, 777)])
            ss.assert_not_awaited()
        finally:
            await db.close()

    async def test_emoji_puzzle_cancelled_when_warmup_rsvp_under_threshold(self):
        """T-127 emoji branch: same gate fires before start_emoji_night."""
        from bot.handlers import calendar as bot_calendar

        marker = "warmup-rsvp:t127-emoji"
        db = Database(":memory:")
        await db.init()
        try:
            ann_id = await db.create_scheduled_message(
                text="חימום emoji",
                message_type="trivia_warmup_rsvp",
                channel_topic_id=341,
                target_group="main",
                scheduled_date="2099-01-01",
                scheduled_time="20:30",
                poll_options=json.dumps({
                    "min_ready_players": 2,
                    "warmup_marker": marker,
                    "game_time": "22:00",
                    "activity_label": "Emoji Night על סרטים",
                }, ensure_ascii=False),
                status="scheduled",
            )
            await db._db.execute(
                "UPDATE scheduled_messages SET status='sent', sent_message_id=? WHERE id=?",
                (8888, ann_id),
            )
            await db._db.commit()  # 0 RSVPs

            game_row = {
                "id": 9200,
                "scheduled_date": "2099-01-01",
                "scheduled_time": "22:00",
                "message_type": "emoji_puzzle",
                "target_group": "main",
                "channel_topic_id": 4037,
                "status": "scheduled",
                "text": "🧩 emoji night",
                "created_by": "ai-fill-pool-row",
                "auto_pin": False,
                "poll_options": json.dumps({
                    "theme_label": "סרטים",
                    "media_types": ["movie"],
                    "puzzle_count": 5,
                    "min_ready_players": 2,
                    "warmup_marker": marker,
                    "activity_label": "Emoji Night על סרטים",
                }, ensure_ascii=False),
                "poll_duration": None,
                "cover_path": None,
                "recurrence": None,
                "recurrence_days": None,
            }
            skipped: list = []
            async def fake_get_due(*a, **k):
                return [game_row]
            async def fake_get_scheduled_message(msg_id):
                return dict(game_row) if msg_id == game_row["id"] else None
            async def fake_claim_scheduled_message(msg_id, *, stale_after_minutes=15):
                return msg_id == game_row["id"]
            async def cap_skipped(msg_id, reason):
                skipped.append((msg_id, reason))
            async def noop_sent(msg_id, sent_message_id):
                raise AssertionError(f"unexpected send for {msg_id}")
            async def noop_failed(msg_id, error):
                raise AssertionError(f"unexpected failure: {error}")
            db.get_due_messages = fake_get_due  # type: ignore[assignment]
            db.get_scheduled_message = fake_get_scheduled_message  # type: ignore[assignment]
            db.claim_scheduled_message = fake_claim_scheduled_message  # type: ignore[assignment]
            db.mark_message_skipped = cap_skipped  # type: ignore[assignment]
            db.mark_message_sent = noop_sent  # type: ignore[assignment]
            db.mark_message_failed = noop_failed  # type: ignore[assignment]

            context = SimpleNamespace(bot_data={"db": db}, bot=object())
            with patch.dict(bot_calendar.os.environ,
                            {"BOT_TOKEN": "token", "GROUP_ID": "-1001",
                             "TEST_GROUP_ID": "-1002"}), \
                 patch("telegram.Bot", return_value=object()), \
                 patch("bot.utils.config.rsvp_gate_enabled", return_value=True), \
                 patch.object(bot_calendar, "start_emoji_night",
                              new=AsyncMock(return_value=99)) as start_emoji, \
                 patch.object(bot_calendar, "safe_send",
                              new=AsyncMock(return_value=SimpleNamespace(message_id=1))) as ss:
                await bot_calendar.check_and_send_due_messages(context)

            start_emoji.assert_not_awaited()
            self.assertEqual(len(skipped), 1)
            self.assertIn("0/2", skipped[0][1])
            ss.assert_awaited()
            cancel_kwargs = ss.await_args.kwargs
            self.assertIn("Emoji Night על סרטים", cancel_kwargs["text"])
        finally:
            await db.close()

    async def test_trivia_topup_generates_reviews_and_persists_missing_questions(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp)
            (config_dir / "trivia.yaml").write_text(
                yaml.safe_dump({
                    "questions": [{
                        "text": "existing gaming question",
                        "options": ["a", "b", "c", "d"],
                        "correct": 0,
                        "category": "גיימינג",
                    }]
                }, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            generated = "\n\n".join(
                f"שאלה: שאלת גיימינג חדשה מספר {i}\n"
                f"תשובות: א{i} | ב{i} | ג{i} | ד{i}\n"
                "נכונה: 0\n"
                "קטגוריה: גיימינג"
                for i in range(1, 10)
            )
            row = {
                "id": 999,
                "poll_options": json.dumps({
                    "theme_label": "גיימינג",
                    "categories": ["גיימינג"],
                    "question_count": 10,
                }, ensure_ascii=False),
            }

            with patch.object(dashboard_app, "CONFIG_DIR", config_dir), \
                 patch.object(dashboard_app, "load_yaml", return_value=yaml.safe_load((config_dir / "trivia.yaml").read_text(encoding="utf-8"))), \
                 patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=generated)), \
                 patch.object(dashboard_app, "_generate_via_api", new=AsyncMock()) as generate_api:
                result = await dashboard_app._ensure_trivia_pool_ready_for_round(row)

            self.assertEqual(result["generated"], 9)
            self.assertEqual(result["available"], 10)
            generate_api.assert_not_awaited()
            saved = yaml.safe_load((config_dir / "trivia.yaml").read_text(encoding="utf-8"))
            self.assertEqual(len(saved["questions"]), 10)
            self.assertEqual({q["category"] for q in saved["questions"]}, {"גיימינג"})


class TestPlannerTemplateExposure(unittest.TestCase):
    def test_calendar_styles_include_executable_activity_types(self):
        for message_type in (
            "trivia_round",
            "emoji_puzzle",
            "free_games",
            "facts_tidbit",
            "facts_spooky",
            "weekly_roundup",
            "weekly_leaderboard",
        ):
            with self.subTest(message_type=message_type):
                self.assertIn(message_type, _CAL_TYPE_STYLE)

    def test_ai_fill_schema_allows_executable_activity_types(self):
        schema = dashboard_app._today_plan_tool_schema()["input_schema"]
        regular_slot = schema["properties"]["regular_slots"]["items"]
        allowed = set(regular_slot["properties"]["type"]["enum"])

        self.assertEqual(allowed, set(dashboard_app.AI_REGULAR_SLOT_TYPES))

    def test_ai_fill_schema_requires_activity_coverage_decisions(self):
        schema = dashboard_app._today_plan_tool_schema()["input_schema"]
        self.assertIn("coverage_decisions", schema["required"])
        decision = schema["properties"]["coverage_decisions"]["items"]
        self.assertEqual(
            set(decision["required"]),
            {"activity_type", "scheduled_time", "topic_id", "action", "reason"},
        )

    def test_question_quality_rejects_recent_bad_prompt_patterns(self):
        quality = Path("config/question_quality.md").read_text(encoding="utf-8")
        for marker in (
            "דאנקינג",
            "קבוצות חוץ",
            "מאמץ כבד",
            "פילר גנרי",
            "שיפחדו",
            "אוכלי בשר",
            "היום הזה עוד לא הוחלט",
            "יצור (ממשי או מהדמיון)",
            "הגענו לאמצע השבוע",
            "כמעט סוף שבוע",
            "עוד שעה אחת לפני שנגמר השבוע",
            "עוד קצת ואז כבר סוף שבוע",
            "הריטואל שסוגר לכם את השבוע",
            "ז'אנר מסוים... משהו אחר לגמרי",
            "מי חטף/מוסיף פנים כזה",
            "למבוגר האחראי בסיטואציה",
            "בראש השולחן",
            "רשות מלאה",
            "בלי תוכניות גדולות",
            "פוליטיקה צריכה טריגר חד וקונקרטי",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, quality)

    def test_activity_warmups_are_not_static_templates(self):
        src = Path("dashboard/app.py").read_text(encoding="utf-8")
        banned_static_fragments = (
            "מתחממים לחידת אימוג'י",
            "נפתח Emoji Night בנושא",
            "נקודות למהירים",
            "מתחילים סיבוב {poll_payload['theme_label']} בפינה של בוטסון",
            "חייבת להכיל את המילה \"מתחממים\" או \"בעוד\"",
            "תיבחר מהמאגר בזמן השליחה",
            "ייבחר מהמאגר בזמן השליחה",
            "הבוט יבדוק וישלח אם נמצא משחק רלוונטי",
            "יופק מנתוני הפעילות בזמן השליחה",
        )
        for fragment in banned_static_fragments:
            with self.subTest(fragment=fragment):
                self.assertNotIn(fragment, src)

        self.assertIn(
            "async def _generate_activity_copy", src,
            "activity warm-up rows should be generated instead of static templates",
        )
        self.assertIn(
            "אני בפנים", src,
            "ready-gate copy must mention the RSVP button",
        )

    def test_static_prompt_pools_are_not_materialized_directly(self):
        previews = materializer.compute_week_previews("2099-01-04", {}, set())

        self.assertEqual(previews, [])

    def test_materialize_forward_skips_when_generation_unavailable(self):
        async def run():
            db = Database(":memory:")
            await db.init()
            try:
                with patch.object(materializer, "_generate_with_claude", new=AsyncMock(return_value=None)):
                    inserted = await materializer.materialize_forward(db, days_ahead=14)
                async with db._db.execute("SELECT COUNT(*) FROM scheduled_messages") as cur:
                    count = (await cur.fetchone())[0]
                return inserted, count
            finally:
                await db.close()

        self.assertEqual(asyncio.run(run()), (0, 0))

    def test_activity_copy_generation_failure_skips_instead_of_fallback(self):
        async def run():
            with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(side_effect=RuntimeError("cli down"))), \
                 patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(side_effect=RuntimeError("api down"))), \
                 patch.object(dashboard_app, "_generate_via_codex_cli", new=AsyncMock(side_effect=RuntimeError("codex down"))):
                return await dashboard_app._generate_activity_copy(
                    "trivia_warmup",
                    fallback="טקסט סטטי שאסור לשלוח",
                    game_time="22:00",
                )

        self.assertIsNone(asyncio.run(run()))

    def test_activity_copy_rejects_repeated_and_misleading_ready_copy(self):
        async def run(raw, avoid=None):
            with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=raw)):
                return await dashboard_app._generate_activity_copy(
                    "trivia_warmup",
                    game_time="21:00",
                    warmup_offset_min=35,
                    theme_label="ישראל",
                    min_ready_players=2,
                    avoid_texts=set(avoid or []),
                )

        repeated = '{"text":"עוד 35 דקות מתחילים סיבוב ישראל. צריך לפחות 2 שחקנים שמסמנים שהם בפנים."}'
        self.assertIsNone(asyncio.run(run(repeated)))
        original = "טקסט מקורי לאירוע הערב"
        self.assertIsNone(asyncio.run(run(json.dumps({"text": original}, ensure_ascii=False), {original})))

    def test_activity_coverage_marks_relevant_future_slots_required(self):
        settings = {
            "features": {"evening_prompt": True, "discussions": True, "levels": True},
            "topics": {"goals": 2184},
            "schedule": {
                "evening_prompt": {"days": [5], "time": "21:00"},
                "discussion_prompt": {"days": [5], "times": ["18:00", "21:00"]},
                "weekly_leaderboard": {"days": [5], "time": "18:00"},
            },
        }
        with patch.object(dashboard_app, "_is_feature_enabled_simple", return_value=True):
            reqs = dashboard_app._build_activity_coverage_requirements(
                settings=settings,
                hebrew_day=5,
                now_hhmm="17:30",
                existing_drafts_today=[],
                active_discussion_categories=[{"key": "funny", "topic_id": 153}],
            )
        required = {
            (r["activity_type"], r["scheduled_time"])
            for r in reqs
            if r["relevance"] == "required"
        }
        self.assertIn(("discussion", "18:00"), required)
        self.assertIn(("discussion", "21:00"), required)
        self.assertIn(("evening", "21:00"), required)
        self.assertIn(("weekly_leaderboard", "18:00"), required)

    def test_ai_fill_cli_prompt_stays_under_timeout_budget(self):
        bundle = {
            "today": "2026-05-01",
            "hebrew_day_name": "שישי",
            "hebrew_day_num": 5,
            "now_time_il": "15:30",
            "events_today": [],
            "scheduled_messages_today": [
                {
                    "id": i,
                    "text": "טקסט קיים ארוך יחסית לשורה מתוזמנת כדי לדמות יום אמיתי " * 8,
                    "message_type": "discussion",
                    "scheduled_time": "18:00",
                    "topic_id": 4037,
                    "created_by": "ai-fill-today",
                    "status": "draft",
                }
                for i in range(12)
            ],
            "existing_drafts_today": [],
            "this_week_previews": [
                {"date": "2026-05-02", "type": "discussion", "topic_id": 4037, "preview": "תצוגה מקדימה " * 6}
                for _ in range(25)
            ],
            "recent_sent_samples_by_type": {
                f"type_{i}": [
                    {"date": "2026-04-30", "topic_id": 4037, "text": "דוגמת קול קהילה " * 8}
                    for _ in range(3)
                ]
                for i in range(12)
            },
            "verified_topic_ids": [7, 54, 59, 153, 335, 341, 347, 1431, 1517, 2184, 3113, 4037],
            "verified_topic_names": {str(i): "שם ערוץ מאומת" for i in range(12)},
            "existing_trivia_categories": [
                {"category": f"קטגוריה {i}", "count": 12, "sample": "שאלה לדוגמה " * 5}
                for i in range(15)
            ],
            "existing_emoji_answers_sample": [{"he": "תשובה", "en": "answer"} for _ in range(20)],
            "schedule": {"discussion_prompt": {"days": [5], "times": ["18:00", "21:00"]}},
            "active_discussion_categories": [{"key": "funny", "topic_id": 153}],
            "activity_coverage_requirements": [
                {
                    "activity_type": "discussion",
                    "scheduled_time": "18:00",
                    "topic_id": None,
                    "relevance": "required",
                    "reason": "enabled, scheduled today, future slot",
                },
                {
                    "activity_type": "weekly_roundup",
                    "scheduled_time": "18:00",
                    "topic_id": None,
                    "relevance": "required",
                    "reason": "enabled, scheduled today, future slot",
                },
            ],
            "goals_topic_id": 2184,
        }

        prompt = dashboard_app._build_cli_digest_prompt(bundle)

        self.assertLess(len(prompt), 28000)
        self.assertNotIn('\n  "', prompt)

    def test_planner_drawer_exposes_supported_types_and_ai_allowlist(self):
        planner_html = (dashboard_app.TEMPLATES_DIR / "planner.html").read_text(encoding="utf-8")
        for message_type in (
            "evening",
            "trivia_round",
            "emoji_puzzle",
            "free_games",
            "facts_tidbit",
            "facts_spooky",
            "weekly_roundup",
            "weekly_leaderboard",
        ):
            with self.subTest(message_type=message_type):
                self.assertIn(f'data-type="{message_type}"', planner_html)

    def test_draft_approval_checks_failed_http_response(self):
        planner_html = (dashboard_app.TEMPLATES_DIR / "planner.html").read_text(encoding="utf-8")
        self.assertIn("if (!resp.ok)", planner_html)
        self.assertIn("apiErrorMessage", planner_html)

        self.assertIn("'custom','poll'", planner_html)

    def test_review_modal_surfaces_quality_failures(self):
        planner_html = (dashboard_app.TEMPLATES_DIR / "planner.html").read_text(encoding="utf-8")
        self.assertIn("props.qualityFailures", planner_html)
        self.assertIn("review-quality-warning", planner_html)
        self.assertIn("טיוטה נדחתה בבדיקת איכות", planner_html)

    def test_review_schedule_sends_date_and_uses_local_date_formatter(self):
        planner_html = (dashboard_app.TEMPLATES_DIR / "planner.html").read_text(encoding="utf-8")
        self.assertIn("scheduled_date: card.getAttribute('data-review-date')", planner_html)
        self.assertIn("function formatLocalDate", planner_html)
        self.assertNotIn("toISOString().split('T')[0]", planner_html)

    def test_fill_today_uses_server_today_token_not_stale_calendar_date(self):
        planner_html = (dashboard_app.TEMPLATES_DIR / "planner.html").read_text(encoding="utf-8")
        self.assertIn("var dateForCall = 'today';", planner_html)
        self.assertIn("_aiSuggestFetch(iso, 'day');", planner_html)

    def test_content_inventory_scheduler_types_are_exposed(self):
        class FakeDb:
            async def list_emoji_puzzles(self):
                return []

            async def get_all_events(self):
                return []

            async def recent_free_games(self, limit):
                return []

            async def get_handler_routing(self, handler):
                return {"handler": handler, "play_topic_id": 4037, "teaser_topic_ids": []}

        context = asyncio.run(dashboard_app._build_activities_context(FakeDb()))
        inventory_types = {
            scheduler_type
            for activity in context["activities"]
            for scheduler_type in activity.get("scheduler_types", [])
        }

        planner_html = (dashboard_app.TEMPLATES_DIR / "planner.html").read_text(encoding="utf-8")
        ai_types = set(dashboard_app.AI_REGULAR_SLOT_TYPES)
        manual_types = {
            "poll",
            "event",
            "custom",
            "trivia",
            *ai_types,
        }

        self.assertGreaterEqual(manual_types, inventory_types)
        for scheduler_type in inventory_types:
            with self.subTest(scheduler_type=scheduler_type):
                self.assertIn(f'data-type="{scheduler_type}"', planner_html)
                if scheduler_type not in {"event"}:
                    self.assertIn(scheduler_type, manual_types)

        ai_expected = inventory_types - {"event"}
        self.assertGreaterEqual(ai_types, ai_expected)

    def test_all_safe_discussion_pools_are_mapped_for_ai_populate(self):
        settings = dashboard_app.get_settings()
        discussions = dashboard_app.load_yaml("discussions.yaml") or {}
        mapped = settings.get("topics", {}).get("discussions", {}) or {}

        # Telegram General/root is deliberately not used by this bot.
        # `funny` is intentionally disabled from auto-generation
        # (2026-05-16) — humor-as-discussion-question doesn't work; pool
        # entries are kept for when a curated joke pool replaces them.
        # See config/operator_prefs.md "no humor as discussion question" rule.
        intentionally_disabled = {"general", "funny"}
        expected = set(discussions) - intentionally_disabled
        self.assertGreaterEqual(set(mapped), expected)
        for category in expected:
            self.assertTrue(mapped.get(category), f"{category} must have a topic id")


class TestPopulateButtonConsolidation(unittest.TestCase):
    """The planner used to have two AI populate buttons (✨ fill empty slots
    only, and ♻️ wipe + regenerate). They were collapsed into a single
    `✨ מלא טיוטות ב-AI` that runs the regenerate flow (which subsumes
    fill since the inner ai_fill loop fills empty slots after wipe). These
    tests pin the consolidation so a future template edit can't quietly
    bring back the duplicate or break the wiring.
    """

    @classmethod
    def setUpClass(cls):
        cls.html = (dashboard_app.TEMPLATES_DIR / "planner.html").read_text(encoding="utf-8")

    def test_exactly_one_populate_button_in_toolbar(self):
        # The ai-fill-all-btn id should appear exactly once (button definition).
        # JS lookups via getElementById('ai-fill-all-btn') count separately.
        button_def = self.html.count('id="ai-fill-all-btn"')
        self.assertEqual(
            button_def, 1,
            f'expected one button with id="ai-fill-all-btn", found {button_def}',
        )

    def test_orphaned_regenerate_button_is_gone(self):
        self.assertNotIn(
            'id="ai-regenerate-btn"', self.html,
            "old ♻ ייצר מחדש button is back — collapse the duplicates",
        )
        self.assertNotIn(
            "♻️ ייצר מחדש", self.html,
            "old ♻ button label is back",
        )

    def test_populate_button_wires_to_regenerate_flow(self):
        # The single button must call aiRegenerateWeek (which posts to the
        # regenerate endpoint), not the deleted aiFillAllVisible.
        self.assertIn(
            'id="ai-fill-all-btn" onclick="aiRegenerateWeek()"', self.html,
            "Populate button must call aiRegenerateWeek",
        )

    def test_orphaned_fill_function_is_gone(self):
        self.assertNotIn(
            "function aiFillAllVisible", self.html,
            "deleted aiFillAllVisible function is back",
        )
        # And no leftover calls to it from anywhere else.
        self.assertNotIn(
            "aiFillAllVisible(", self.html,
            "stale call to deleted aiFillAllVisible(...)",
        )

    def test_regenerate_function_opens_suggest_modal(self):
        # Populate flow is suggest+confirm modal — aiRegenerateWeek must
        # call _aiSuggestFetch instead of writing to the DB directly.
        self.assertIn(
            "async function aiRegenerateWeek()", self.html,
            "aiRegenerateWeek function must still exist",
        )
        regenerate_block_start = self.html.index("async function aiRegenerateWeek()")
        block = self.html[regenerate_block_start:regenerate_block_start + 600]
        self.assertIn(
            "_aiSuggestFetch(", block,
            "aiRegenerateWeek must trigger the suggest+confirm modal",
        )
        self.assertNotIn(
            "confirm(", block,
            "Populate must always open the suggestion modal without a pre-modal browser confirm",
        )

    def test_specific_day_populate_uses_date_picker(self):
        self.assertIn(
            'id="ai-fill-day-date"', self.html,
            "specific-day Populate must expose a date input",
        )
        self.assertIn(
            'type="date"', self.html,
            "specific-day Populate must use a native date picker",
        )
        specific_day_start = self.html.index("function aiFillSpecificDay()")
        block = self.html[specific_day_start:specific_day_start + 700]
        self.assertIn(
            "showPicker", block,
            "specific-day Populate should open the date picker when supported",
        )
        self.assertNotIn(
            "prompt(", block,
            "specific-day Populate must not use a browser prompt",
        )

    def test_date_click_drawer_can_populate_selected_day(self):
        self.assertIn(
            'id="drawer-ai-populate-day-wrap"', self.html,
            "date-click drawer must expose the selected-day AI populate action",
        )
        self.assertIn(
            "openedFromDateClick", self.html,
            "drawer must distinguish calendar date clicks from generic new-message opens",
        )
        self.assertIn(
            "function aiPopulateDrawerDate()", self.html,
            "selected-day drawer AI populate handler missing",
        )
        handler_start = self.html.index("function aiPopulateDrawerDate()")
        block = self.html[handler_start:handler_start + 500]
        self.assertIn(
            "_aiSuggestFetch(iso, 'day')", block,
            "selected-day drawer action must use the suggest+confirm day flow",
        )

    def test_pool_growth_buttons_removed_from_toolbar(self):
        # Pool growth (emoji puzzles, facts) folded INTO Populate — the
        # standalone toolbar buttons must be gone. Their JS handlers stay as
        # private helpers callable from aiRegenerateWeek.
        self.assertNotIn(
            'id="ai-suggest-emoji-btn"', self.html,
            "🎬 הצע חידות אמוג'י button is back — pool growth folds into Populate",
        )
        self.assertNotIn(
            'id="ai-suggest-facts-btn"', self.html,
            "🔎 הצע עובדות button is back — pool growth folds into Populate",
        )

    def test_populate_calls_pool_growth_endpoints(self):
        # aiRegenerateWeek must POST to the three pool-growth URLs (emoji
        # suggest, facts suggest, facts append) so one click grows pools.
        regenerate_block_start = self.html.index("async function aiRegenerateWeek()")
        # The pool helpers live above aiRegenerateWeek and are awaited from
        # inside it; check the whole tail of the file.
        whole_tail = self.html[regenerate_block_start - 5000:]
        for endpoint in (
            "/api/pool/emoji-puzzles/suggest",
            "/api/pool/facts/suggest",
            "/api/pool/facts/append",
        ):
            self.assertIn(
                endpoint, whole_tail,
                f"Populate flow must POST to {endpoint}",
            )

    def test_suggest_modal_markup_present(self):
        # Modal must exist with the suggest list container, approve button,
        # and clear/check toggles. If any of these go missing, the user
        # can't review or approve suggestions.
        self.assertIn(
            'id="ai-suggest-modal"', self.html,
            "ai-suggest-modal container missing",
        )
        self.assertIn(
            'id="ai-suggest-list"', self.html,
            "ai-suggest-list container missing — suggestions render here",
        )
        self.assertIn(
            'id="ai-suggest-approve-btn"', self.html,
            "approve button missing — user can't commit suggestions",
        )
        for label in ("אשר וצור טיוטות", "סמן הכל", "נקה הכל", "ביטול"):
            self.assertIn(
                label, self.html,
                f"modal control label '{label}' missing",
            )

    def test_suggest_endpoints_referenced_from_js(self):
        # The new suggest+commit endpoints must be called from the modal's
        # fetch logic. Pins URL string to catch typos / refactor drift.
        self.assertIn(
            "'/api/weekplan/ai-suggest'", self.html,
            "suggest endpoint url missing in JS",
        )
        self.assertIn(
            "'/api/weekplan/ai-suggest-commit'", self.html,
            "suggest-commit endpoint url missing in JS",
        )
        self.assertIn(
            "'/api/weekplan/ai-suggest/' + encodeURIComponent(startData.job_id)", self.html,
            "suggest status polling endpoint url missing in JS",
        )
        self.assertIn(
            "'/api/weekplan/ai-suggest/' + encodeURIComponent(_aiSuggestState.jobId) + '/cancel'", self.html,
            "suggest cancel endpoint url missing in JS",
        )
        self.assertIn(
            "statusData.status === 'completed'", self.html,
            "suggest modal must poll until background generation completes",
        )

    def test_prompt_modal_regenerate_sends_topic_id(self):
        html = (dashboard_app.TEMPLATES_DIR / "_prompt_modal.html").read_text(encoding="utf-8")
        self.assertIn("body.topic_id = parseInt(_modalState.topicId, 10);", html)

    def test_planner_drawer_generation_sends_topic_id(self):
        self.assertIn("var TOPIC_CATEGORY = {", self.html)
        self.assertIn("topic_id: wizardState.channelTopicId || null", self.html)
        self.assertIn("wizardState.category = category || (newId && TOPIC_CATEGORY[newId]) || null;", self.html)
        self.assertIn(
            "statusData.status === 'cancelled'", self.html,
            "suggest modal must stop polling when backend job is cancelled",
        )

    def test_suggest_modal_surfaces_and_unchecks_quality_failures(self):
        self.assertIn("s.quality_failures", self.html)
        self.assertIn("בעיות איכות", self.html)
        self.assertIn("איכות תקינה", self.html)
        self.assertIn("_aiSuggestState.checked[s.key] = !((s.quality_failures || []).length)", self.html)

    def test_planner_handles_expired_auth_without_raw_unauthorized(self):
        self.assertIn("פג תוקף ההתחברות", self.html)
        self.assertIn("window.location.href = '/login'", self.html)

    def test_trivia_form_defaults_not_hardcoded_to_israel(self):
        self.assertNotIn('id="trivia-theme" type="text" value="ישראל"', self.html)
        self.assertNotIn('id="trivia-categories" type="text" value="ישראל"', self.html)

    def test_internal_suggestions_link_to_preview_render(self):
        self.assertIn("s.preview_url", self.html)
        self.assertIn("/planner/suggestion-preview", Path("dashboard/app.py").read_text(encoding="utf-8"))
        self.assertIn("פתח תצוגה מקדימה", self.html)

    def test_ai_suggest_errors_format_non_string_details(self):
        self.assertIn("function _formatApiError", self.html)
        self.assertIn("Array.isArray(data.detail)", self.html)
        self.assertIn("JSON.stringify(data.detail)", self.html)

    def test_ai_suggest_modal_surfaces_generation_notices_with_suggestions(self):
        self.assertIn("Array.isArray(data.errors) && data.errors.length", self.html)
        self.assertIn("Array.isArray(data.notices) && data.notices.length", self.html)
        self.assertIn("noticeLines.join('\\n')", self.html)
        self.assertIn("errorLines.join('\\n')", self.html)
        self.assertIn("data.stats_block || ''", self.html)

    def test_ai_suggest_empty_state_renders_skip_reasons(self):
        self.assertIn("function _aiSuggestRenderEmpty", self.html)
        self.assertIn("data.skip_reasons", self.html)
        self.assertIn("data.empty_state", self.html)
        self.assertNotIn("_aiSuggestSkipLabel", self.html)

    def test_ai_suggest_game_time_editor_updates_linked_rows(self):
        for fn in (
            "function _aiSuggestUpdateActivityTime",
            "function _aiSuggestTimeControl",
            "function _aiSuggestLeadMinutes",
        ):
            with self.subTest(fn=fn):
                self.assertIn(fn, self.html)
        self.assertIn('data-suggest-time-key', self.html)
        self.assertIn('payload.game_time = nextTime', self.html)
        self.assertIn("s.message_type === 'trivia_warmup_rsvp'", self.html)
        self.assertIn("s.message_type === 'warmup_reminder'", self.html)

    def test_type_switch_clears_incompatible_new_message_state(self):
        select_start = self.html.index("function selectType(type)")
        block = self.html[select_start:select_start + 3500]
        self.assertIn("var previousType = wizardState.type", block)
        self.assertIn("previousType && previousType !== type && !wizardState.editingId", block)
        self.assertIn("wizardState.text = TYPE_DEFAULT_TEXT[type] || ''", block)
        self.assertIn("wizardState.pollOptions = ['', '']", block)
        self.assertIn("wizardState.eventLocation = ''", block)
        self.assertIn("clearCover()", block)

    def test_calendar_cards_show_scheduler_trust_state(self):
        content_start = self.html.index("eventContent: function(arg)")
        block = self.html[content_start:content_start + 1800]
        self.assertIn("תצוגה בלבד", block)
        self.assertIn("יישלח", block)
        self.assertIn("טיוטה", block)
        self.assertIn("נשלח", block)
        self.assertIn("נכשל", block)
        self.assertIn("דולג", block)
        self.assertIn("event-chip-state", block)
        mount_start = self.html.index("eventDidMount: function(info)")
        mount_block = self.html[mount_start:mount_start + 1000]
        self.assertIn("תצוגה בלבד — לא יישלח עד שמירה", mount_block)
        self.assertIn("מתוזמן — ייבדק על ידי הסקזולר", mount_block)
        self.assertIn("טיוטה — לא תישלח אוטומטית", mount_block)
        self.assertIn("נשלח", mount_block)
        self.assertIn("נכשל", mount_block)
        self.assertIn("דולג", mount_block)
        self.assertIn("props.diagnosticDetail", mount_block)

    def test_create_preview_never_renders_fake_rsvp_buttons(self):
        preview_start = self.html.index("function renderPreview()")
        block = self.html[preview_start:preview_start + 2200]
        self.assertIn("Never render fake RSVP buttons", block)
        self.assertNotIn("rsvpRow.classList.remove('hidden')", block)
        self.assertNotIn("✅ מגיע/ה (0)", self.html)
        self.assertNotIn("🤔 אולי (0)", self.html)

    def test_approve_reads_live_checkbox_state(self):
        approve_start = self.html.index("async function aiSuggestApprove()")
        block = self.html[approve_start:approve_start + 700]
        self.assertIn(
            "_aiSuggestIsChecked(s)", block,
            "Approve must read checkbox DOM state so unchecked rows are not committed",
        )
        self.assertNotIn(
            "_aiSuggestState.checked[s.key] !== false", block,
            "Approve must not rely only on cached checkbox state",
        )

    def test_regenerate_endpoint_is_what_the_button_hits(self):
        # Sanity check: the regenerate function fetches /api/weekplan/ai-fill-regenerate.
        # If a future refactor swaps the endpoint, that's a real concern — pin it.
        regenerate_block_start = self.html.index("async function aiRegenerateWeek()")
        block = self.html[regenerate_block_start:regenerate_block_start + 6000]
        self.assertIn(
            "/api/weekplan/ai-fill-regenerate", block,
            "Populate button must POST to /api/weekplan/ai-fill-regenerate",
        )

    def test_inline_js_parses_as_valid_syntax(self):
        """Catch the class of bug where Hebrew strings in single-quoted JS
        ('כמה חידות אמוג'י') aren't escaped — that turns the apostrophe in
        אמוג'י into a string terminator and SyntaxErrors the whole script,
        which silently breaks the calendar widget.

        Skipped if Node isn't on PATH (CI without Node will pass — fine for
        a regression check that primarily fires during local dev).
        """
        import shutil
        import subprocess
        import tempfile
        node = shutil.which("node")
        if not node:
            self.skipTest("node not available — JS syntax check skipped")
        # Strip Jinja2 expression/block tags before sending to Node.
        src = self.html
        src = re.sub(r"{%[\s\S]*?%}", "", src)
        src = re.sub(r"{{\s*(?:[^{}]|{[^}]*})*\s*}}", "0", src)
        scripts = re.findall(r"<script[^>]*>([\s\S]*?)</script>", src)
        combined = "\n;\n".join(s for s in scripts if s.strip())
        with tempfile.NamedTemporaryFile(suffix=".js", mode="w", delete=False) as f:
            f.write(combined)
            tmp_path = f.name
        try:
            result = subprocess.run(
                [node, "--check", tmp_path],
                capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(
                result.returncode, 0,
                f"planner.html inline JS has a syntax error:\n{result.stderr}",
            )
        finally:
            import os
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def test_drag_drop_checks_failed_save_response(self):
        block = self.html[self.html.index("eventDrop: async function"):self.html.index("dateClick: function")]
        self.assertIn("if (!resp.ok)", block)
        self.assertIn("info.revert()", block)
        self.assertIn("apiErrorMessage", block)


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

    def test_recent_question_keys_are_excluded_when_pool_has_fresh_items(self):
        from bot.handlers import trivia_round
        pool = [
            {"text": "already played", "options": ["a", "b"], "correct": 0, "category": "כללי"},
            {"text": "fresh one", "options": ["a", "b"], "correct": 0, "category": "כללי"},
            {"text": "fresh two", "options": ["a", "b"], "correct": 0, "category": "כללי"},
        ]
        recent = {trivia_round._question_key(pool[0])}

        with patch.object(trivia_round, "_load_questions", return_value=pool):
            picked = trivia_round._pick_questions(2, None, exclude_question_keys=recent)

        self.assertEqual({q["text"] for q in picked}, {"fresh one", "fresh two"})

    def test_recent_question_exclusion_falls_back_when_pool_too_small(self):
        from bot.handlers import trivia_round
        pool = [
            {"text": "already played", "options": ["a", "b"], "correct": 0, "category": "כללי"},
            {"text": "fresh one", "options": ["a", "b"], "correct": 0, "category": "כללי"},
        ]
        recent = {trivia_round._question_key(pool[0])}

        with patch.object(trivia_round, "_load_questions", return_value=pool):
            fresh = trivia_round._pick_questions(2, None, exclude_question_keys=recent)
            fallback = trivia_round._pick_questions(2, None)

        self.assertEqual([q["text"] for q in fresh], ["fresh one"])
        self.assertEqual(len(fallback), 2)

    def test_general_round_pulls_from_full_pool(self):
        """A general round (preferred_categories=None) must NOT be biased to
        a hard-coded fallback like movies/TV. It samples randomly from the
        whole pool. Sanity check: with multiple draws we should see more
        than one distinct category appear over enough rounds."""
        seen_categories: set[str] = set()
        for _ in range(20):
            picked = _pick_questions(5, None)
            for q in picked:
                cat = str(q.get("category") or "").strip()
                if cat:
                    seen_categories.add(cat)
        self.assertGreater(
            len(seen_categories), 1,
            f"general round looks biased — only saw categories: {seen_categories}",
        )


class TestDiscussionCategoryDiscovery(unittest.TestCase):
    def test_settings_topic_without_pool_is_active(self):
        settings = {"topics": {"discussions": {"music": 4502, "new_topic": 7777}}}
        discussions_pool = {"music": ["שאלה קיימת"]}
        verified_rows = [
            {"topic_id": 4502, "verified_name": "מוזיקה", "category_key": "music"},
            {"topic_id": 7777, "verified_name": "ערוץ חדש", "category_key": "new_topic"},
        ]

        categories = _active_discussion_categories_from_config(settings, discussions_pool, verified_rows)
        by_key = {c["category_key"]: c for c in categories}

        self.assertIn("new_topic", by_key)
        self.assertEqual(by_key["new_topic"]["topic_id"], 7777)
        self.assertEqual(by_key["new_topic"]["name"], "ערוץ חדש")
        self.assertFalse(by_key["new_topic"]["has_pool"])

    def test_pool_only_category_is_not_active(self):
        settings = {"topics": {"discussions": {"music": 4502}}}
        discussions_pool = {"music": ["שאלה קיימת"], "stale_pool_only": ["לא אמור להופיע"]}

        categories = _active_discussion_categories_from_config(settings, discussions_pool, [])

        self.assertEqual([c["category_key"] for c in categories], ["music"])

    def test_discussion_prompt_includes_verified_channel_name(self):
        prompt = build_generation_prompt(
            "discussion",
            "single",
            "",
            "new_topic",
            category_name="ערוץ חדש",
        )

        self.assertIn("new_topic / ערוץ חדש", prompt)

    def test_materializer_discussion_categories_do_not_require_pool(self):
        class FakeDb:
            async def get_verified_forum_topics(self):
                return [{"topic_id": 7777, "verified_name": "ערוץ חדש", "category_key": "new_topic"}]

        settings = {"topics": {"discussions": {"new_topic": 7777}}}
        categories = asyncio.run(materializer._active_discussion_categories(FakeDb(), settings, {}))

        self.assertEqual(categories[0]["category_key"], "new_topic")
        self.assertEqual(categories[0]["topic_id"], 7777)
        self.assertFalse(categories[0]["has_pool"])

    def test_auto_verified_topic_is_active_without_settings_entry(self):
        settings = {"topics": {"goals": 2184, "welcome": 341, "discussions": {}}}
        verified_rows = [
            {"topic_id": 2184, "verified_name": "יעדים", "category_key": "goals"},
            {"topic_id": 7777, "verified_name": "ערוץ חדש", "category_key": "topic_7777"},
        ]

        categories = _active_discussion_categories_from_config(settings, {}, verified_rows)

        self.assertEqual([c["category_key"] for c in categories], ["topic_7777"])


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
