"""End-to-end tests for the trivia-round launch path.

These cover the full chain `get_due_messages` → `_coerce_due_game_row` →
dispatch loop → `start_scheduled_trivia_round` → `safe_send`, against a real
SQLite database and the live `config/trivia.yaml` pool. Telegram is mocked
at the `context.bot` boundary; the question-posting background task
(`_continue_round_after_announcement`) is patched to a no-op so the tests
don't hang on `pre_roll_s` waits.

Existing dispatch tests (`tests/test_calendar_scheduled_games.py`) use a
`FakeScheduledDb` stub and most of them also stub `start_scheduled_trivia_round`
itself — so they don't catch regressions in the SQL filter, the coercion
step, or the handler entry path. This file fills that gap.

Run with: .venv/bin/python -m unittest tests.test_scheduler_e2e_trivia_launch
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

# Set env vars BEFORE importing dashboard/handlers so module-level reads
# pick them up. The dispatch loop reads TEST_GROUP_ID/GROUP_ID/BOT_TOKEN
# inside check_and_send_due_messages (calendar.py:295-297), but Bot(...)
# format-validation can also surface here.
os.environ.setdefault("TEST_GROUP_ID", "-1003747545764")
os.environ.setdefault("GROUP_ID", "-1003873409631")
os.environ.setdefault("BOT_TOKEN", "12345:fake_test_token_passes_format_check")

# Israel time matches `_IL_TZ` used by the dispatch loop.
try:
    from zoneinfo import ZoneInfo
    _IL_TZ = ZoneInfo("Asia/Jerusalem")
except Exception:  # pragma: no cover
    _IL_TZ = None

from bot.database.db import Database  # noqa: E402
from bot.handlers import calendar as calendar_handler  # noqa: E402
from bot.handlers import trivia_interest as interest_handler  # noqa: E402
from bot.handlers import trivia_round as trivia_handler  # noqa: E402


TEST_GROUP_ID = int(os.environ["TEST_GROUP_ID"])


def _now_il_struct():
    """`(date_iso, hh_mm_iso, datetime)` from Israel time, matching the dispatch loop."""
    now = datetime.now(_IL_TZ) if _IL_TZ else datetime.now()
    return now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), now


def _hhmm_seconds_ago(seconds: int) -> tuple[str, str]:
    """Return `(date_iso, HH:MM)` for `seconds` ago in IL time."""
    now = datetime.now(_IL_TZ) if _IL_TZ else datetime.now()
    earlier = now - timedelta(seconds=seconds)
    return earlier.strftime("%Y-%m-%d"), earlier.strftime("%H:%M")


def _make_context(db: Database) -> SimpleNamespace:
    """Build a fake `ContextTypes.DEFAULT_TYPE` shaped object for the dispatch loop."""
    bot = AsyncMock()
    # safe_send returns whatever bot.<method_name>(...) returns.
    # Trivia handler reads `int(getattr(announcement, "message_id", 0) or 0)`.
    bot.send_message = AsyncMock(
        return_value=SimpleNamespace(
            message_id=900001,
            chat=SimpleNamespace(id=TEST_GROUP_ID),
        )
    )
    bot.pin_chat_message = AsyncMock(return_value=None)
    bot.set_message_reaction = AsyncMock(return_value=None)
    return SimpleNamespace(bot=bot, bot_data={"db": db})


class TriviaLaunchE2EBase(unittest.IsolatedAsyncioTestCase):
    """Common fixture: temp SQLite DB, patched background task, _active_rounds reset.

    Subclasses define the actual scenarios. We patch at the test boundary
    rather than the suite level so a single bad test can't poison the rest.
    """

    async def asyncSetUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(self._tmp.name)
        await self.db.init()
        # Background-task no-op — prevents pre_roll_s waits from hanging the test.
        self._continue_patch = patch.object(
            trivia_handler, "_continue_round_after_announcement",
            new=AsyncMock(return_value=None),
        )
        self._continue_patch.start()
        # Top-up no-op — tests don't want to spawn LLM/CLI calls when the
        # pool is intentionally short (T5).
        self._topup_patch = patch.object(
            trivia_handler, "_top_up_scheduled_questions_if_possible",
            new=AsyncMock(return_value=None),
        )
        self._topup_patch.start()
        # Patch the freshly-constructed Bot inside the dispatch loop so
        # non-trivia rows (text-typed discussion, polls, events) don't hit
        # real Telegram. Tests that care about this path inspect the
        # AsyncMock instance. The dispatch loop does `from telegram import
        # Bot` lazily (calendar.py:294-295), so the patch target is
        # `telegram.Bot` rather than `calendar.Bot`.
        self._bot_factory_instance = AsyncMock()
        self._bot_factory_instance.send_message = AsyncMock(
            return_value=SimpleNamespace(message_id=800001)
        )
        self._bot_factory_instance.pin_chat_message = AsyncMock(return_value=None)
        self._bot_class_patch = patch(
            "telegram.Bot",
            new=lambda token: self._bot_factory_instance,
        )
        self._bot_class_patch.start()

    async def asyncTearDown(self) -> None:
        # Clear active-rounds guard so the next test can launch in the same chat.
        trivia_handler._active_rounds.clear()
        self._continue_patch.stop()
        self._topup_patch.stop()
        self._bot_class_patch.stop()
        await self.db.close()
        try:
            os.unlink(self._tmp.name)
        except OSError:
            pass

    async def _row(self, msg_id: int) -> dict:
        """Reload a scheduled_messages row by id."""
        async with self.db._db.execute(
            "SELECT * FROM scheduled_messages WHERE id = ?", (msg_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}


class TriviaLaunchE2ETests(TriviaLaunchE2EBase):

    async def test_due_trivia_round_launches_announcement_and_marks_sent(self):
        """T1 (happy path): a due trivia_round row fires the announcement and flips to sent."""
        date_iso, time_iso = _hhmm_seconds_ago(60)  # well within current minute
        msg_id = await self.db.create_scheduled_message(
            text="🧠 סיבוב טריוויה ישראל! 3 שאלות",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            poll_options=json.dumps({
                "pre_roll_s": 5,
                "theme_label": "ישראל",
                "categories": ["ישראל"],
                "question_count": 3,
            }, ensure_ascii=False),
            status="scheduled",
        )

        ctx = _make_context(self.db)
        await calendar_handler.check_and_send_due_messages(ctx)

        # Telegram was called for the announcement.
        ctx.bot.send_message.assert_awaited()
        announce_args = ctx.bot.send_message.await_args
        self.assertEqual(announce_args.kwargs.get("chat_id"), TEST_GROUP_ID)
        self.assertIn("ישראל", announce_args.kwargs.get("text", ""))

        # Row flipped to sent with a real message_id.
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")
        self.assertEqual(row["sent_message_id"], 900001)

        # Background task was created (patched to no-op but spy on the call).
        trivia_handler._continue_round_after_announcement.assert_awaited_once()

    async def test_due_discussion_row_with_trivia_launch_text_is_coerced_and_fires(self):
        """T2 (coercion): a `discussion`-typed row with trivia-launch text becomes a trivia_round at fire time."""
        date_iso, time_iso = _hhmm_seconds_ago(60)
        msg_id = await self.db.create_scheduled_message(
            text="🧠 הערב — סיבוב טריוויה ישראל! 3 שאלות",
            message_type="discussion",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            status="scheduled",
        )

        ctx = _make_context(self.db)
        await calendar_handler.check_and_send_due_messages(ctx)

        # Trivia handler ran (announcement on context.bot, NOT on the
        # text-path Bot factory).
        ctx.bot.send_message.assert_awaited()
        self._bot_factory_instance.send_message.assert_not_called()

        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")

    async def test_warmup_text_does_not_coerce_to_launch(self):
        """T3 (warm-up boundary): "בעוד … מתחממים" stays a discussion, sent as text."""
        date_iso, time_iso = _hhmm_seconds_ago(60)
        msg_id = await self.db.create_scheduled_message(
            text="🧠 בעוד 30 דקות מתחממים לסיבוב טריוויה ישראל הערב!",
            message_type="discussion",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            status="scheduled",
        )

        ctx = _make_context(self.db)
        await calendar_handler.check_and_send_due_messages(ctx)

        # Trivia path NOT invoked; text-Bot path WAS invoked.
        ctx.bot.send_message.assert_not_called()
        self._bot_factory_instance.send_message.assert_called()

        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")

    async def test_malformed_poll_options_falls_back_to_defaults(self):
        """T4 (malformed): bad JSON in poll_options → empty payload → handler uses defaults and fires a general round.

        `_parse_scheduled_payload` (trivia_round.py:600-608) silently returns
        `{}` on JSON decode failure rather than raising; the handler then uses
        default values for theme/categories/count. This is the actual behavior
        — the test locks it in so a future "raise on bad JSON" change is
        intentional, not accidental. The bot must NOT crash on malformed input.
        """
        date_iso, time_iso = _hhmm_seconds_ago(60)
        msg_id = await self.db.create_scheduled_message(
            text="🧠 סיבוב טריוויה",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            poll_options="{not valid json",
            status="scheduled",
        )

        ctx = _make_context(self.db)
        # Must NOT raise.
        await calendar_handler.check_and_send_due_messages(ctx)

        # Default general round fires successfully.
        ctx.bot.send_message.assert_awaited()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")

    async def test_insufficient_question_pool_marks_failed_before_announcement(self):
        """T5 (empty pool): a category not in trivia.yaml → row failed, no send."""
        date_iso, time_iso = _hhmm_seconds_ago(60)
        msg_id = await self.db.create_scheduled_message(
            text="🧠 סיבוב טריוויה",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            poll_options=json.dumps({
                "pre_roll_s": 5,
                "theme_label": "ניסוי",
                "categories": ["___nonexistent___"],
                "question_count": 5,
            }, ensure_ascii=False),
            status="scheduled",
        )

        ctx = _make_context(self.db)
        await calendar_handler.check_and_send_due_messages(ctx)

        ctx.bot.send_message.assert_not_called()
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "failed")
        # Top-up was attempted (and short-circuited by our patch).
        trivia_handler._top_up_scheduled_questions_if_possible.assert_awaited()

    async def test_idempotent_dispatch_does_not_double_fire(self):
        """T6 (idempotency): two scheduler ticks in a row → handler runs once."""
        date_iso, time_iso = _hhmm_seconds_ago(60)
        msg_id = await self.db.create_scheduled_message(
            text="🧠 סיבוב טריוויה ישראל",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            poll_options=json.dumps({
                "pre_roll_s": 5,
                "theme_label": "ישראל",
                "categories": ["ישראל"],
                "question_count": 3,
            }, ensure_ascii=False),
            status="scheduled",
        )

        ctx = _make_context(self.db)
        # The first tick fires; the second tick must skip (status='sent' filters it out).
        await calendar_handler.check_and_send_due_messages(ctx)
        await calendar_handler.check_and_send_due_messages(ctx)

        # Exactly one announcement send; mark_message_sent ran once.
        self.assertEqual(ctx.bot.send_message.await_count, 1)
        row = await self._row(msg_id)
        self.assertEqual(row["status"], "sent")

    async def test_warmup_and_game_in_same_tick_fire_in_order(self):
        """T7 (multi-row): warm-up discussion + trivia_round in same tick fire in scheduled-time order."""
        date_iso_3s, time_iso_3s = _hhmm_seconds_ago(180)
        date_iso_1s, time_iso_1s = _hhmm_seconds_ago(60)
        # Warm-up discussion with the "מתחממים" trigger word — stays text, doesn't coerce.
        warmup_id = await self.db.create_scheduled_message(
            text="🧠 בעוד חצי שעה מתחממים לטריוויה ישראל!",
            message_type="discussion",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso_3s,
            scheduled_time=time_iso_3s,
            status="scheduled",
        )
        game_id = await self.db.create_scheduled_message(
            text="🧠 סיבוב טריוויה ישראל",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso_1s,
            scheduled_time=time_iso_1s,
            poll_options=json.dumps({
                "pre_roll_s": 5,
                "theme_label": "ישראל",
                "categories": ["ישראל"],
                "question_count": 3,
            }, ensure_ascii=False),
            status="scheduled",
        )

        ctx = _make_context(self.db)
        await calendar_handler.check_and_send_due_messages(ctx)

        # Warm-up went via text-Bot; trivia went via context.bot.
        self._bot_factory_instance.send_message.assert_called()
        ctx.bot.send_message.assert_awaited()

        warmup = await self._row(warmup_id)
        game = await self._row(game_id)
        self.assertEqual(warmup["status"], "sent")
        self.assertEqual(game["status"], "sent")

    async def test_warmup_rsvp_users_seed_scheduled_trivia_ready_gate(self):
        """Regression: RSVP threshold met in Sherlock's Den must count at game launch.

        The production failure on 2026-05-22 happened because the warm-up RSVP
        reached the threshold, but scheduled trivia then demanded a second
        in-game ready click and cancelled at pre-roll. This test covers the DB
        chain: sent warmup row + stored RSVP responses + due game row sharing
        the warmup_marker.
        """
        date_iso, time_iso = _hhmm_seconds_ago(60)
        marker = f"warmup-rsvp:trivia:{date_iso}:19:00"
        warmup_id = await self.db.create_scheduled_message(
            text="warmup",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=341,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            poll_options=json.dumps({
                "min_ready_players": 2,
                "game_time": "23:59",
                "theme_label": "ישראל",
                "warmup_marker": marker,
            }, ensure_ascii=False),
            status="scheduled",
        )
        await self.db.mark_message_sent(warmup_id, 700001)
        await self.db.add_trivia_interest_response(warmup_id, 101, "Lotem")
        await self.db.add_trivia_interest_response(warmup_id, 202, "Refeli")

        game_id = await self.db.create_scheduled_message(
            text="",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            poll_options=json.dumps({
                "pre_roll_s": 5,
                "theme_label": "ישראל",
                "categories": ["ישראל"],
                "question_count": 3,
                "min_ready_players": 2,
                "warmup_marker": marker,
            }, ensure_ascii=False),
            status="scheduled",
        )

        ctx = _make_context(self.db)
        await calendar_handler.check_and_send_due_messages(ctx)

        row = await self._row(game_id)
        self.assertEqual(row["status"], "sent")
        round_state = trivia_handler._continue_round_after_announcement.await_args.kwargs["round_state"]
        self.assertEqual(round_state["ready_users"], {101: "Lotem", 202: "Refeli"})

    async def test_duplicate_warmup_announcements_do_not_undercount_rsvp(self):
        """Regression: the 2026-05-22 'never fired' class.

        Populate keys the warm-up marker by date+time, so committing the same
        slot twice creates two sent ``trivia_warmup_rsvp`` rows sharing one
        marker. Real RSVPs land on whichever row was posted (here the older,
        lower-id row); the newer sibling is empty. The dispatch RSVP gate used
        to resolve a single ``ORDER BY id DESC`` row, count the empty sibling
        (0 < threshold), mark the game ``skipped`` and never fire it. The gate
        must aggregate across BOTH rows and launch.
        """
        date_iso, time_iso = _hhmm_seconds_ago(60)
        marker = f"warmup-rsvp:trivia:{date_iso}:19:00"

        async def _make_warmup(sent_message_id: int) -> int:
            wid = await self.db.create_scheduled_message(
                text="warmup",
                message_type="trivia_warmup_rsvp",
                channel_topic_id=341,
                target_group="test",
                scheduled_date=date_iso,
                scheduled_time=time_iso,
                poll_options=json.dumps({
                    "min_ready_players": 2,
                    "game_time": "23:59",
                    "theme_label": "ישראל",
                    "warmup_marker": marker,
                }, ensure_ascii=False),
                status="scheduled",
            )
            await self.db.mark_message_sent(wid, sent_message_id)
            return wid

        # Older row carries the real RSVPs; the duplicate sent later is empty.
        warmup_real = await _make_warmup(700101)
        await self.db.add_trivia_interest_response(warmup_real, 101, "Lotem")
        await self.db.add_trivia_interest_response(warmup_real, 202, "Refeli")
        await _make_warmup(700102)  # duplicate, no RSVPs, higher id

        game_id = await self.db.create_scheduled_message(
            text="",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=time_iso,
            poll_options=json.dumps({
                "pre_roll_s": 5,
                "theme_label": "ישראל",
                "categories": ["ישראל"],
                "question_count": 3,
                "min_ready_players": 2,
                "warmup_marker": marker,
            }, ensure_ascii=False),
            status="scheduled",
        )

        ctx = _make_context(self.db)
        await calendar_handler.check_and_send_due_messages(ctx)

        row = await self._row(game_id)
        self.assertEqual(row["status"], "sent")  # must NOT be 'skipped'
        round_state = trivia_handler._continue_round_after_announcement.await_args.kwargs["round_state"]
        self.assertEqual(round_state["ready_users"], {101: "Lotem", 202: "Refeli"})

    async def test_ready_gate_does_not_cancel_when_seeded_from_warmup_rsvp(self):
        """Regression: a seeded warmup-ready list satisfies the pre-roll gate."""
        self._continue_patch.stop()
        q = {"text": "q", "options": ["a", "b", "c", "d"], "correct": 0}
        state = trivia_handler._create_round_state(
            [q], 1, min_ready_players=2, ready_users={101: "Lotem", 202: "Refeli"},
        )
        bot = AsyncMock()
        db = self.db
        with patch.object(trivia_handler, "safe_send", new=AsyncMock(return_value=SimpleNamespace(message_id=99))) as safe_send, \
             patch.object(trivia_handler, "_post_question", new=AsyncMock(return_value=100)) as post_question, \
             patch.object(trivia_handler, "_update_question_timer", new=AsyncMock()), \
             patch.object(trivia_handler, "_reveal_question", new=AsyncMock()), \
             patch.object(trivia_handler.asyncio, "sleep", new=AsyncMock()):
            await trivia_handler._continue_round_after_announcement(
                bot, db, TEST_GROUP_ID, 4037, pre_roll_s=1, round_state=state,
            )

        post_question.assert_awaited_once()
        self.assertFalse(any("לא מתחילה" in call.kwargs.get("text", "") for call in safe_send.await_args_list))

    async def test_late_warmup_rsvp_after_game_time_is_rejected_and_not_stored(self):
        """Regression: users cannot retroactively join after game_time."""
        date_iso, _time_iso, now = _now_il_struct()
        game_time = (now - timedelta(minutes=1)).strftime("%H:%M")
        warmup_id = await self.db.create_scheduled_message(
            text="warmup",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=341,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(hours=1)).strftime("%H:%M"),
            poll_options=json.dumps({
                "min_ready_players": 2,
                "game_time": game_time,
                "theme_label": "ישראל",
                "warmup_marker": "late-rsvp-marker",
            }, ensure_ascii=False),
            status="scheduled",
        )
        await self.db.mark_message_sent(warmup_id, 700002)
        query = SimpleNamespace(
            data=f"trivint_{warmup_id}",
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(id=303, username="late", first_name="Late", last_name="User", full_name="Late User"),
        )
        ctx = _make_context(self.db)

        await interest_handler.handle_trivia_interest(update, ctx)

        query.answer.assert_awaited_once()
        self.assertTrue(query.answer.await_args.kwargs.get("show_alert"))
        responses = await self.db.get_trivia_interest_responses(warmup_id)
        self.assertEqual(responses, [])

    async def test_warmup_rsvp_button_and_confirmation_include_names(self):
        """Regression: operator-visible RSVP state includes actual participant names."""
        await self.db.set_handler_routing("trivia_warmup", 341, [])
        await self.db.set_handler_routing("trivia_round", 4037, [])
        date_iso, _time_iso, now = _now_il_struct()
        warmup_id = await self.db.create_scheduled_message(
            text="warmup",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=341,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(minutes=10)).strftime("%H:%M"),
            poll_options=json.dumps({
                "min_ready_players": 2,
                "game_time": "23:59",
                "theme_label": "ישראל",
                "activity_label": "הטריוויה על ישראל",
                "warmup_marker": "names-marker",
            }, ensure_ascii=False),
            status="scheduled",
        )
        await self.db.mark_message_sent(warmup_id, 700003)

        async def click(user_id: int, name: str):
            query = SimpleNamespace(
                data=f"trivint_{warmup_id}",
                answer=AsyncMock(),
                edit_message_reply_markup=AsyncMock(),
            )
            update = SimpleNamespace(
                callback_query=query,
                effective_user=SimpleNamespace(id=user_id, username=name.lower(), first_name=name, last_name=None, full_name=name),
            )
            await interest_handler.handle_trivia_interest(update, _make_context(self.db))
            return query

        with patch.object(interest_handler, "safe_send", new=AsyncMock(return_value=SimpleNamespace(message_id=700004))) as send:
            first_query = await click(101, "Lotem")
            second_query = await click(202, "Refeli")

        first_markup = first_query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
        second_markup = second_query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
        self.assertIn("Lotem", first_markup.inline_keyboard[0][0].text)
        self.assertIn("Lotem", second_markup.inline_keyboard[0][0].text)
        self.assertIn("Refeli", second_markup.inline_keyboard[0][0].text)
        confirmation_text = send.await_args.kwargs["text"]
        self.assertIn("Lotem", confirmation_text)
        self.assertIn("Refeli", confirmation_text)
        self.assertEqual(send.await_args.kwargs["message_thread_id"], 4037)

    async def test_threshold_confirmation_aggregates_duplicate_warmup_rows_by_marker(self):
        """Duplicate warm-ups with one marker must not split the confirmation count."""
        await self.db.set_handler_routing("trivia_round", 4037, [])
        date_iso, _time_iso, now = _now_il_struct()
        marker = "warmup-rsvp:trivia:2099-01-01:21:00"
        payload = {
            "min_ready_players": 2,
            "game_time": "23:59",
            "theme_label": "ישראל",
            "activity_label": "הטריוויה על ישראל",
            "warmup_marker": marker,
        }
        first_warmup = await self.db.create_scheduled_message(
            text="warmup A",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(minutes=10)).strftime("%H:%M"),
            poll_options=json.dumps(payload, ensure_ascii=False),
            status="scheduled",
        )
        second_warmup = await self.db.create_scheduled_message(
            text="warmup B",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(minutes=9)).strftime("%H:%M"),
            poll_options=json.dumps(payload, ensure_ascii=False),
            status="scheduled",
        )
        await self.db.mark_message_sent(first_warmup, 700010)
        await self.db.mark_message_sent(second_warmup, 700011)
        await self.db.add_trivia_interest_response(first_warmup, 101, "Lotem")

        user = SimpleNamespace(
            id=202,
            username="refeli",
            first_name="Refeli",
            last_name=None,
            full_name="Refeli",
        )
        with patch.object(
            interest_handler,
            "safe_send",
            new=AsyncMock(return_value=SimpleNamespace(message_id=700012)),
        ) as send:
            result = await interest_handler.record_trivia_interest(
                self.db, _make_context(self.db).bot, second_warmup, user,
            )

        self.assertEqual(result["count"], 2)
        self.assertIn("Lotem", result["names"])
        self.assertIn("Refeli", result["names"])
        send.assert_awaited_once()
        confirmation_text = send.await_args.kwargs["text"]
        self.assertIn("2 אנשים בפנים", confirmation_text)
        self.assertIn("Lotem", confirmation_text)
        self.assertIn("Refeli", confirmation_text)
        self.assertEqual(send.await_args.kwargs["message_thread_id"], 4037)

    async def test_warmup_button_count_aggregates_duplicate_warmup_rows_by_marker(self):
        """The visible group button must show the same marker-wide count as quorum."""
        date_iso, _time_iso, now = _now_il_struct()
        marker = "warmup-rsvp:trivia:2099-01-02:21:00"
        payload = {
            "min_ready_players": 3,
            "game_time": "23:59",
            "theme_label": "ישראל",
            "warmup_marker": marker,
        }
        first_warmup = await self.db.create_scheduled_message(
            text="warmup A",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(minutes=10)).strftime("%H:%M"),
            poll_options=json.dumps(payload, ensure_ascii=False),
            status="scheduled",
        )
        second_warmup = await self.db.create_scheduled_message(
            text="warmup B",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(minutes=9)).strftime("%H:%M"),
            poll_options=json.dumps(payload, ensure_ascii=False),
            status="scheduled",
        )
        await self.db.mark_message_sent(first_warmup, 700020)
        await self.db.mark_message_sent(second_warmup, 700021)
        await self.db.add_trivia_interest_response(first_warmup, 101, "Lotem")

        query = SimpleNamespace(
            data=f"trivint_{second_warmup}",
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=202,
                username="refeli",
                first_name="Refeli",
                last_name=None,
                full_name="Refeli",
            ),
        )

        await interest_handler.handle_trivia_interest(update, _make_context(self.db))

        markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
        button_text = markup.inline_keyboard[0][0].text
        self.assertIn("(2)", button_text)
        self.assertIn("Lotem", button_text)
        self.assertIn("Refeli", button_text)

    async def test_warmup_button_signoff_removes_duplicate_marker_sibling_signup(self):
        """Clicking any duplicate warm-up button toggles the marker-wide signup off."""
        date_iso, _time_iso, now = _now_il_struct()
        marker = "warmup-rsvp:trivia:2099-01-03:21:00"
        payload = {
            "min_ready_players": 2,
            "game_time": "23:59",
            "theme_label": "ישראל",
            "warmup_marker": marker,
        }
        first_warmup = await self.db.create_scheduled_message(
            text="warmup A",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(minutes=10)).strftime("%H:%M"),
            poll_options=json.dumps(payload, ensure_ascii=False),
            status="scheduled",
        )
        second_warmup = await self.db.create_scheduled_message(
            text="warmup B",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=4037,
            target_group="test",
            scheduled_date=date_iso,
            scheduled_time=(now - timedelta(minutes=9)).strftime("%H:%M"),
            poll_options=json.dumps(payload, ensure_ascii=False),
            status="scheduled",
        )
        await self.db.mark_message_sent(first_warmup, 700030)
        await self.db.mark_message_sent(second_warmup, 700031)
        await self.db.add_trivia_interest_response(first_warmup, 101, "Lotem")

        query = SimpleNamespace(
            data=f"trivint_{second_warmup}",
            answer=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_user=SimpleNamespace(
                id=101,
                username="lotem",
                first_name="Lotem",
                last_name=None,
                full_name="Lotem",
            ),
        )

        await interest_handler.handle_trivia_interest(update, _make_context(self.db))

        self.assertFalse(await self.db.has_trivia_interest_response(first_warmup, 101))
        self.assertFalse(await self.db.has_trivia_interest_response(second_warmup, 101))
        markup = query.edit_message_reply_markup.await_args.kwargs["reply_markup"]
        self.assertIn("(0)", markup.inline_keyboard[0][0].text)


if __name__ == "__main__":
    unittest.main()
