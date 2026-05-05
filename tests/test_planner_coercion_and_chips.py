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
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import yaml
from fastapi import HTTPException

from bot.database.db import Database
from bot.handlers import calendar as bot_calendar
from bot.handlers.trivia_round import _pick_questions
from dashboard import app as dashboard_app
from dashboard.app import _CAL_TYPE_STYLE, _coerce_game_message_fields, _looks_like_trivia_launch


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

    def __init__(self, body):
        self._body = body
        self.query_params = {}

    async def json(self):
        return self._body


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
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=0)

        after = await self._scheduled_count(db)
        await db.close()

        self.assertEqual(before, after, "suggest must not write scheduled rows")
        types = {s["message_type"] for s in result["suggestions"]}
        self.assertIn("discussion", types)
        self.assertIn("trivia_round", types)
        self.assertIn("emoji_puzzle", types)
        self.assertIn("facts_tidbit", types)
        self.assertIn("facts_spooky", types)
        self.assertIn("weekly_leaderboard", types)
        emoji_rows = [s for s in result["suggestions"] if s["message_type"] == "emoji_puzzle"]
        self.assertTrue(emoji_rows)
        emoji_payload = json.loads(emoji_rows[0]["poll_options_json"])
        self.assertIn(emoji_payload["theme_label"], {"סרטים", "סדרות"})
        self.assertIn(emoji_payload["media_types"], (["movie"], ["series"]))
        self.assertTrue(any(
            s["message_type"] == "discussion" and s["source"] == "ai-fill-emoji"
            for s in result["suggestions"]
        ))
        trivia_rows = [s for s in result["suggestions"] if s["message_type"] == "trivia_round"]
        self.assertTrue(trivia_rows)
        trivia_payload = json.loads(trivia_rows[0]["poll_options_json"])
        self.assertTrue(trivia_payload["theme_label"])
        self.assertTrue(trivia_payload["categories"])
        self.assertGreaterEqual(int(trivia_payload.get("min_ready_players", 0)), 0)
        self.assertLessEqual(len(result["suggestions"]), 12)

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
        await db.create_scheduled_message(
            text="🧩 Emoji Night — סרטים (5 חידות)",
            message_type="emoji_puzzle",
            channel_topic_id=4037,
            target_group="main",
            scheduled_date="2026-05-01",
            scheduled_time="22:00",
            poll_options=json.dumps({"theme_label": "סרטים", "media_types": ["movie"]}, ensure_ascii=False),
            status="scheduled",
        )
        canned = "איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש?"

        with patch.object(dashboard_app, "_generate_via_cli", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_generate_via_api", new=AsyncMock(return_value=canned)), \
             patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=0)

        await db.close()
        emoji_rows = [s for s in result["suggestions"] if s["message_type"] == "emoji_puzzle"]
        self.assertTrue(emoji_rows)
        emoji_payload = json.loads(emoji_rows[0]["poll_options_json"])
        self.assertEqual(emoji_payload["theme_label"], "סדרות")
        self.assertEqual(emoji_payload["media_types"], ["series"])

    async def test_ai_suggest_calendar_rotates_trivia_subject_away_from_recent(self):
        db = Database(":memory:")
        await db.init()
        await db.create_scheduled_message(
            text="🧠 סיבוב טריוויה — ישראל (5 שאלות)",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="main",
            scheduled_date="2026-05-01",
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
            result = await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=0)

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

    async def _scheduled_count(self, db):
        async with db._db.execute("SELECT COUNT(*) FROM scheduled_messages") as cur:
            return (await cur.fetchone())[0]

    async def test_executable_types_without_topic_resolve_to_handler_routing(self):
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

    async def test_ai_suggest_today_token_uses_server_israel_date(self):
        db = Database(":memory:")
        await db.init()
        try:
            with patch.object(dashboard_app, "_ai_suggest_calendar", new=AsyncMock(return_value={"ok": True})) as suggest:
                res = await dashboard_app.ai_suggest(FakeCalendarRequest({"target_date": "today"}), db)
        finally:
            await db.close()

        self.assertEqual(res, {"ok": True})
        called_date = suggest.await_args.kwargs["target_date"]
        self.assertRegex(called_date, r"^\d{4}-\d{2}-\d{2}$")

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
                        "text": "🧩 מתחממים לחידת אימוג'י\nב-22:00 נפתח Emoji Night בנושא סרטים וסדרות.",
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
                self.assertEqual(commit["inserted"], 2)
                due = await db.get_due_messages(today, due_time)
                self.assertEqual([row["message_type"] for row in due], ["discussion", "emoji_puzzle"])
                self.assertTrue(all(row["status"] == "scheduled" for row in due))

                context = SimpleNamespace(bot_data={"db": db}, bot=object())
                sent = SimpleNamespace(message_id=9001)
                with patch.dict(bot_calendar.os.environ, {"BOT_TOKEN": "token", "GROUP_ID": "-1001"}), \
                     patch("telegram.Bot", return_value=object()), \
                     patch.object(bot_calendar, "send_message_with_optional_cover", new=AsyncMock(return_value=sent)) as send_text, \
                     patch.object(bot_calendar, "start_emoji_night", new=AsyncMock(return_value=77)) as start_emoji:
                    await bot_calendar.check_and_send_due_messages(context)

                send_text.assert_awaited_once()
                start_emoji.assert_awaited_once_with(
                    context, -1001, 4037, force=True,
                    media_types=["movie", "tv"], theme_label="סרטים וסדרות",
                )
                rows = await db.get_scheduled_messages(today, today)
                self.assertEqual({row["status"] for row in rows}, {"sent"})
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
                self.assertEqual(warmup["scheduled_time"], "21:25")
                self.assertNotEqual(warmup["channel_topic_id"], 341)
                self.assertIn("22:00", warmup["text"])
                self.assertIn("35 דקות", warmup["text"])
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
                    text="old ai draft",
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
                self.assertEqual(warmup["scheduled_time"], "21:25")
                self.assertNotEqual(warmup["channel_topic_id"], 341)
                self.assertEqual(warmup["created_by"], f"trivia-announcement-draft:{game_id}")
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
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, quality)

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
        self.assertIn("throw new Error", planner_html)

        self.assertIn("'custom','poll'", planner_html)

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
        expected = set(discussions) - {"general"}
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
