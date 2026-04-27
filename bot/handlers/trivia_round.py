"""Trivia round — 5 questions, 30s timer each, +12 correct, +20 first-place bonus.

Separate from the single-question `trivia.py` handler. Uses a distinct callback
prefix (`trivround_`) so clicks don't collide with the legacy trivia state.
"""

import asyncio
import json
import logging
import os
import random
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from ..database.db import Database
from ..utils.config import GROUP_ID, TEST_GROUP_ID, get_settings, is_feature_enabled
from ..utils.helpers import is_admin, get_display_name
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)

# Scoring constants (per user spec)
POINTS_CORRECT = 12
POINTS_FIRST_PLACE_BONUS = 20
QUESTION_COUNT = 8
QUESTION_TIMEOUT_S = 15

# Round theme — filter the question pool.
PREFERRED_CATEGORIES = {"סרטים", "טלוויזיה"}
THEME_LABEL = "סרטים וטלוויזיה"

# Trigger file polled every 10s so dashboard can kick off a round without HTTP.
TRIGGER_FILE = Path(__file__).resolve().parents[2] / "data" / "trivia_round_trigger.json"
STOP_FILE = Path(__file__).resolve().parents[2] / "data" / "trivia_round_stop"

# Per-chat active round state: {chat_id: {q_index, msg_id, scores, ...}}
_active_rounds: dict[int, dict] = {}


def _load_questions() -> list[dict]:
    from ..utils.config import load_yaml
    try:
        data = load_yaml("trivia.yaml")
        return data.get("questions", []) or []
    except Exception as e:
        logger.error("trivia_round: failed to load trivia.yaml: %s", e)
        return []


def _pick_questions(n: int, preferred_categories: set[str] | None = None) -> list[dict]:
    """Pick n questions matching the supplied categories.

    When the caller passed explicit categories (preferred_categories is not None)
    we NEVER silently fall back to unrelated questions — doing so caused tech
    rounds to play film questions on 2026-04-22. Callers that want the old
    broad-pool behaviour should pass preferred_categories=None.
    """
    pool = _load_questions()
    if not pool:
        return []
    strict = preferred_categories is not None

    if not strict:
        # General round — pull n random questions from the entire pool, no
        # category bias. This is what plays when the announcement is just
        # "סיבוב טריוויה" with no theme word.
        copy = list(pool)
        random.shuffle(copy)
        seen_texts: set[str] = set()
        picked: list[dict] = []
        for q in copy:
            txt = q.get("text") or ""
            if txt in seen_texts:
                continue
            picked.append(q)
            seen_texts.add(txt)
            if len(picked) >= n:
                break
        return picked

    # Strict mode — themed round. Only questions tagged with the announced
    # category are eligible. If the pool has fewer than n, return what we
    # have; never top up with off-theme questions.
    def _matches(q: dict) -> bool:
        cat = str(q.get("category") or "").strip().lower()
        return any(cat == str(c).strip().lower() for c in preferred_categories)

    matching = [q for q in pool if _matches(q)]
    random.shuffle(matching)

    picked: list[dict] = []
    seen_texts: set[str] = set()
    for q in matching:
        if q.get("text") in seen_texts:
            continue
        picked.append(q)
        seen_texts.add(q.get("text", ""))
        if len(picked) >= n:
            break
    return picked


def _build_answer_markup(q_index: int, options: list[str]) -> InlineKeyboardMarkup:
    rows = []
    for i, opt in enumerate(options):
        rows.append([InlineKeyboardButton(opt, callback_data=f"trivround_{q_index}_{i}")])
    return InlineKeyboardMarkup(rows)


def _format_announcement(pre_roll_s: int, *, theme_label: str, question_count: int) -> str:
    if pre_roll_s >= 60:
        minutes = max(1, pre_roll_s // 60)
        when = f"עוד {minutes} דקות"
    else:
        when = f"עוד {pre_roll_s} שניות"
    theme_emoji = "🇮🇱" if "ישראל" in theme_label else "🎬"
    return (
        f"{theme_emoji} טריוויה: {theme_label}!\n\n"
        f"{question_count} שאלות מהירות · {QUESTION_TIMEOUT_S} שניות לכל אחת.\n"
        f"תשובה נכונה = {POINTS_CORRECT} נק׳ · מקום ראשון = +{POINTS_FIRST_PLACE_BONUS} בונוס 🏆\n\n"
        f"השאלה הראשונה יורדת {when} — תתחממו 🍿"
    )


_TIMER_BAR_BLOCKS = 10


def _timer_bar(remaining_s: int) -> str:
    """Return a 10-block progress bar for remaining time (0..QUESTION_TIMEOUT_S)."""
    filled = max(0, min(_TIMER_BAR_BLOCKS, round(remaining_s / QUESTION_TIMEOUT_S * _TIMER_BAR_BLOCKS)))
    return "▓" * filled + "░" * (_TIMER_BAR_BLOCKS - filled)


def _question_text(q: dict, q_index: int, remaining_s: int) -> str:
    category = q.get("category", "כללי")
    bar = _timer_bar(remaining_s)
    if remaining_s >= QUESTION_TIMEOUT_S:
        timer = f"⏱ {QUESTION_TIMEOUT_S} שניות {bar}"
    elif remaining_s <= 5:
        timer = f"⏱ נשארו {remaining_s} שניות ⚠️ {bar}"
    else:
        timer = f"⏱ נשארו {remaining_s} שניות {bar}"
    return (
        f"🧠 שאלה {q_index + 1}/{q.get('_round_question_count', QUESTION_COUNT)} · {category}\n\n"
        f"{q['text']}\n\n"
        f"{timer}"
    )


def _leaderboard_snapshot(scores: dict, *, limit: int = 5) -> str:
    if not scores:
        return "עדיין אין מובילים"
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1]["correct"], -kv[1]["points"], kv[1]["name"]))
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for idx, (_uid, row) in enumerate(ranked[:limit]):
        medal = medals[idx] if idx < len(medals) else f"{idx+1}."
        lines.append(f"{medal} {row['name']} — {row['points']} נק׳")
    if len(ranked) > limit:
        lines.append(f"… ועוד {len(ranked) - limit} משתתפים")
    return "\n".join(lines)


async def _post_question(bot, db: Database, chat_id: int, thread_id: int | None,
                          q_index: int, q: dict) -> int:
    """Post question q_index and return message_id."""
    text = _question_text(q, q_index, QUESTION_TIMEOUT_S)
    markup = _build_answer_markup(q_index, q["options"])
    msg = await safe_send(
        bot,
        db,
        "send_message",
        chat_id=chat_id,
        text=text,
        reply_markup=markup,
        message_thread_id=thread_id,
    )
    return msg.message_id


async def _update_question_timer(bot, chat_id: int, message_id: int, q: dict,
                                   q_index: int, remaining_s: int) -> None:
    """Edit the question message to refresh the timer bar. Keep buttons attached."""
    text = _question_text(q, q_index, remaining_s)
    markup = _build_answer_markup(q_index, q["options"])
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=markup,
        )
    except Exception as e:
        # Swallow "message is not modified" / flood-control — non-fatal.
        logger.debug("trivia_round: timer edit skipped: %s", e)


async def _reveal_question(bot, chat_id: int, message_id: int, q: dict,
                              q_index: int, round_state: dict) -> None:
    correct_idx = q["correct"]
    correct_text = q["options"][correct_idx]
    answers = round_state.get("answers_this_q", {})
    n_correct = sum(1 for v in answers.values() if v == correct_idx)
    n_wrong = len(answers) - n_correct
    question_count = round_state.get("question_count", QUESTION_COUNT)
    questions_left = max(0, question_count - (q_index + 1))
    leaders = _leaderboard_snapshot(round_state.get("scores", {}))
    text = (
        f"🧠 שאלה {q_index + 1}/{question_count} · נסגרה\n\n"
        f"{q['text']}\n\n"
        f"✅ התשובה: {correct_text}\n"
        f"📊 {n_correct} נכון · {n_wrong} לא נכון\n\n"
        f"🏁 נשארו עוד {questions_left} שאלות\n"
        f"{leaders}"
    )
    try:
        # Strip keyboard so users can't click after reveal (otherwise late clicks
        # on a visible-correct-answer would score — PTB preserves reply_markup
        # unless you pass reply_markup=None explicitly).
        await bot.edit_message_text(
            chat_id=chat_id, message_id=message_id, text=text, reply_markup=None,
        )
    except Exception as e:
        logger.warning("trivia_round: reveal edit failed: %s", e)


def _build_final_text(round_state: dict, bonus_winners: list[int]) -> str:
    scores = round_state["scores"]
    question_count = round_state.get("question_count", QUESTION_COUNT)
    if not scores:
        return "🧠 סוף הטריוויה!\n\nאף אחד לא ענה נכון הפעם. בפעם הבאה 💪"
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1]["correct"], -kv[1]["points"], kv[1]["name"]))
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"🧠 סוף הטריוויה! תוצאות:\n\n👥 השתתפו {len(ranked)} שחקנים"]
    for i, (uid, s) in enumerate(ranked[:10]):
        medal = medals[i] if i < 3 else f"{i+1}."
        bonus = f" (+{POINTS_FIRST_PLACE_BONUS} בונוס 🏆)" if uid in bonus_winners else ""
        lines.append(f"{medal} {s['name']} — {s['correct']}/{question_count} נכון · {s['points']} נק׳{bonus}")
    if len(ranked) > 10:
        lines.append(f"\n… ועוד {len(ranked) - 10} משתתפים בדירוג")
    if bonus_winners:
        winners_names = ", ".join(round_state["scores"][u]["name"] for u in bonus_winners)
        lines.append(f"\n🏆 מקום ראשון: {winners_names} (+{POINTS_FIRST_PLACE_BONUS} בונוס)")
    lines.append("\nתודה ששיחקתם — נתראה בסיבוב הבא 🇮🇱")
    return "\n".join(lines)


async def _run_round(bot, db: Database, chat_id: int, thread_id: int | None,
                      pre_roll_s: int,
                      preferred_categories: set[str] | None = None,
                      theme_label: str | None = None,
                      question_count: int = QUESTION_COUNT,
                      teaser_topic_id: int | None = None,
                      teaser_text: str | None = None) -> None:
    """Drive a single round start → finish. Safe to cancel via STOP_FILE."""
    if chat_id in _active_rounds:
        logger.info("trivia_round: round already active in chat %s", chat_id)
        return

    # No fallback to a fixed legacy category. If the announcement specified a
    # theme, strict mode kicks in. Otherwise (preferred_categories=None) the
    # picker pulls from the entire pool — that's what "general" means.
    theme_label = theme_label or THEME_LABEL
    question_count = max(1, min(20, int(question_count or QUESTION_COUNT)))

    questions = _pick_questions(question_count, preferred_categories)
    for q in questions:
        q["_round_question_count"] = question_count
    if len(questions) < question_count:
        logger.error(
            "trivia_round: not enough questions matching categories=%s (found=%d, requested=%d). "
            "Check that the questions saved in trivia.yaml have exactly one of these category tags.",
            sorted(preferred_categories) if preferred_categories else ["(general — full pool)"],
            len(questions),
            question_count,
        )
        return

    round_state: dict = {
        "questions": questions,
        "question_count": question_count,
        "q_index": -1,
        "msg_id": None,
        "scores": {},  # user_id → {name, correct, points}
        "answers_this_q": {},  # user_id → answer_idx (for current question only)
        "aborted": False,
    }
    _active_rounds[chat_id] = round_state

    try:
        # Optional teaser in a theme-matched topic, fired BEFORE the main
        # announcement so the linked audience has time to jump over.
        if teaser_topic_id is not None and teaser_topic_id != thread_id:
            if teaser_text:
                teaser_body = teaser_text
            else:
                teaser_body = (
                    f"🧠 עוד רגע מתחיל סיבוב טריוויה ({theme_label}) בפינה של בוטסון — "
                    f"{question_count} שאלות. בואו לשחק!"
                )
            try:
                await safe_send(
                    bot,
                    db,
                    "send_message",
                    chat_id=chat_id,
                    text=teaser_body,
                    message_thread_id=teaser_topic_id,
                )
            except UnverifiedTopicError as e:
                logger.warning("trivia_round: teaser refused by guard: %s", e)
            except Exception as e:
                logger.warning("trivia_round: teaser send failed: %s", e)

        # Announcement
        try:
            await safe_send(
                bot,
                db,
                "send_message",
                chat_id=chat_id,
                text=_format_announcement(pre_roll_s, theme_label=theme_label, question_count=question_count),
                message_thread_id=thread_id,
            )
        except UnverifiedTopicError as e:
            logger.error("trivia_round: announcement refused by guard: %s", e)
            return
        except Exception as e:
            logger.error("trivia_round: announcement failed: %s", e)
            return

        # Pre-roll sleep until the last 5 seconds, then send a kickoff message.
        kickoff_gap = 5
        bulk_sleep = max(0, pre_roll_s - kickoff_gap)
        slept = 0
        while slept < bulk_sleep:
            if _should_abort(chat_id):
                return
            step = min(2, bulk_sleep - slept)
            if step <= 0:
                break
            await asyncio.sleep(step)
            slept += step

        # Kickoff message — makes it unambiguous that the round is starting now.
        try:
            await safe_send(
                bot,
                db,
                "send_message",
                chat_id=chat_id,
                text=f"🚀 מתחילים! השאלה הראשונה יורדת עוד {kickoff_gap} שניות — תתרכזו בכפתורים 👇",
                message_thread_id=thread_id,
            )
        except UnverifiedTopicError as e:
            logger.warning("trivia_round: kickoff refused by guard: %s", e)
        except Exception as e:
            logger.warning("trivia_round: kickoff send failed: %s", e)

        # Final countdown before Q1 (abortable).
        slept = 0
        while slept < kickoff_gap:
            if _should_abort(chat_id):
                return
            await asyncio.sleep(1)
            slept += 1

        # Timer-bar tick marks for each question — (elapsed_s, remaining_s).
        # For a 15s window: 5s → 10 left, 10s → 5 left, 13s → 2 left.
        tick_marks = [(5, 10), (10, 5), (13, 2)]

        # Questions loop
        for q_index, q in enumerate(questions):
            if _should_abort(chat_id):
                return
            round_state["q_index"] = q_index
            round_state["answers_this_q"] = {}
            msg_id = await _post_question(bot, db, chat_id, thread_id, q_index, q)
            round_state["msg_id"] = msg_id

            # Wait for timeout with progress-bar edits at the configured ticks.
            elapsed = 0
            for target_elapsed, remaining_s in tick_marks:
                while elapsed < target_elapsed:
                    if _should_abort(chat_id):
                        return
                    await asyncio.sleep(1)
                    elapsed += 1
                await _update_question_timer(
                    bot, chat_id, msg_id, q, q_index, remaining_s,
                )
            while elapsed < QUESTION_TIMEOUT_S:
                if _should_abort(chat_id):
                    return
                await asyncio.sleep(1)
                elapsed += 1

            await _reveal_question(bot, chat_id, msg_id, q, q_index, round_state)
            # Short pause between questions
            await asyncio.sleep(3)

        # Compute first-place winners (most correct; ties all get bonus)
        scores = round_state["scores"]
        bonus_winners: list[int] = []
        if scores:
            max_correct = max(s["correct"] for s in scores.values())
            if max_correct > 0:
                bonus_winners = [uid for uid, s in scores.items() if s["correct"] == max_correct]

        # Award bonus in DB
        for uid in bonus_winners:
            try:
                await db.add_points(uid, POINTS_FIRST_PLACE_BONUS)
                await db.log_activity(
                    "trivia_round",
                    f"+{POINTS_FIRST_PLACE_BONUS} בונוס מקום ראשון ל-{scores[uid]['name']}",
                    uid,
                )
                scores[uid]["points"] += POINTS_FIRST_PLACE_BONUS
            except Exception as e:
                logger.warning("trivia_round: bonus award failed for %s: %s", uid, e)

        # Final message
        try:
            await safe_send(
                bot,
                db,
                "send_message",
                chat_id=chat_id,
                text=_build_final_text(round_state, bonus_winners),
                message_thread_id=thread_id,
            )
        except UnverifiedTopicError as e:
            logger.warning("trivia_round: final refused by guard: %s", e)
        except Exception as e:
            logger.error("trivia_round: final message failed: %s", e)

        await db.log_activity("trivia_round", f"סיום סיבוב טריוויה ({len(scores)} משתתפים)")

    finally:
        _active_rounds.pop(chat_id, None)


def _should_abort(chat_id: int) -> bool:
    state = _active_rounds.get(chat_id)
    if state and state.get("aborted"):
        return True
    if STOP_FILE.exists():
        try:
            STOP_FILE.unlink()
        except Exception:
            pass
        if state:
            state["aborted"] = True
        return True
    return False


async def handle_round_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle trivround_{qindex}_{answer} clicks."""
    query = update.callback_query
    if not query or not query.data:
        return
    user = update.effective_user
    if not user:
        return

    try:
        _, qidx_s, ans_s = query.data.split("_")
        q_index = int(qidx_s)
        answer_idx = int(ans_s)
    except (ValueError, AttributeError):
        await query.answer()
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    state = _active_rounds.get(chat_id) if chat_id else None
    if not state or state.get("q_index") != q_index:
        await query.answer("השאלה הזו כבר נסגרה")
        return

    # One answer per question per user
    answers = state.setdefault("answers_this_q", {})
    if user.id in answers:
        await query.answer("כבר ענית על השאלה הזו")
        return
    answers[user.id] = answer_idx

    q = state["questions"][q_index]
    correct = answer_idx == q["correct"]
    name = get_display_name(user)

    # Ensure scoreboard entry
    entry = state["scores"].setdefault(user.id, {"name": name, "correct": 0, "points": 0})
    entry["name"] = name  # keep fresh

    db: Database = context.bot_data.get("db")
    if db:
        try:
            await db.upsert_member(user.id, user.username, name)
            await db.add_trivia_score(user.id, correct)
        except Exception as e:
            logger.warning("trivia_round: db update failed: %s", e)

    if correct:
        entry["correct"] += 1
        entry["points"] += POINTS_CORRECT
        if db:
            try:
                await db.add_points(user.id, POINTS_CORRECT)
                await db.log_activity("points", f"+{POINTS_CORRECT} נקודות ל-{name} (טריוויה)", user.id)
            except Exception as e:
                logger.warning("trivia_round: points add failed: %s", e)
        await query.answer(f"✅ נכון! +{POINTS_CORRECT} נקודות")
    else:
        await query.answer(f"❌ לא נכון. התשובה: {q['options'][q['correct']]}")


async def trivia_round_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/triviaround — start a round in current chat (admin only)."""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים להתחיל סיבוב טריוויה")
        return

    chat_id = update.effective_chat.id
    thread_id = update.message.message_thread_id
    if chat_id in _active_rounds:
        await update.message.reply_text("סיבוב טריוויה כבר פעיל כאן")
        return

    # Short pre-roll for on-demand starts
    asyncio.create_task(_run_round(context.bot, context.bot_data["db"], chat_id, thread_id, pre_roll_s=10))


async def end_trivia_round_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/endtriviaround — abort active round in current chat (admin only)."""
    if not update.effective_user or not update.message:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים")
        return
    chat_id = update.effective_chat.id
    state = _active_rounds.get(chat_id)
    if not state:
        await update.message.reply_text("אין סיבוב פעיל")
        return
    state["aborted"] = True
    await update.message.reply_text("מפסיק את הסיבוב…")


def _parse_scheduled_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


async def start_scheduled_trivia_round(context: ContextTypes.DEFAULT_TYPE, msg: dict) -> None:
    """Launch a trivia round from a scheduled_messages row without sending plain text."""
    db: Database = context.bot_data.get("db")
    if not db:
        raise RuntimeError("trivia_round: no db in bot_data")

    chat_id = int(msg.get("_resolved_chat_id") or 0)
    if not chat_id:
        raise RuntimeError("trivia_round: scheduled row has no resolved chat_id")

    payload = _parse_scheduled_payload(msg.get("poll_options"))
    thread_id = msg.get("channel_topic_id")
    thread_id = int(thread_id) if thread_id else None
    teaser_topic_id = payload.get("teaser_topic_id")
    teaser_topic_id = int(teaser_topic_id) if teaser_topic_id else None
    teaser_text_raw = payload.get("teaser_text")
    teaser_text = str(teaser_text_raw).strip() if teaser_text_raw else None
    pre_roll_s = int(payload.get("pre_roll_s", 30) or 30)
    theme_label = str(payload.get("theme_label") or "").strip() or None
    question_count = int(payload.get("question_count") or QUESTION_COUNT)
    raw_categories = payload.get("categories") or []
    if isinstance(raw_categories, str):
        raw_categories = [raw_categories]
    preferred_categories = {
        str(cat).strip() for cat in raw_categories
        if str(cat).strip()
    } or None

    logger.info(
        "trivia_round: scheduled row %s launching chat=%s thread=%s pre_roll=%ss theme=%s categories=%s count=%s",
        msg.get("id"),
        chat_id,
        thread_id,
        pre_roll_s,
        theme_label or THEME_LABEL,
        sorted(preferred_categories or PREFERRED_CATEGORIES),
        question_count,
    )
    asyncio.create_task(
        _run_round(
            context.bot,
            db,
            chat_id,
            thread_id,
            pre_roll_s,
            preferred_categories=preferred_categories,
            theme_label=theme_label,
            question_count=question_count,
            teaser_topic_id=teaser_topic_id,
            teaser_text=teaser_text,
        )
    )


async def trigger_watcher(context: ContextTypes.DEFAULT_TYPE):
    """Runs every ~10s. Looks for data/trivia_round_trigger.json, launches a round.

    Trigger JSON schema:
        {
          "chat_id": -1003747545764,
          "thread_id": null,
          "pre_roll_s": 30,
          "theme_label": "ישראל",
          "categories": ["ישראל"],
          "question_count": 5
        }
    Dashboard writes this file; bot consumes and deletes it.
    """
    if not TRIGGER_FILE.exists():
        return
    try:
        raw = TRIGGER_FILE.read_text(encoding="utf-8").strip()
        TRIGGER_FILE.unlink()
    except Exception as e:
        logger.warning("trivia_round: trigger read failed: %s", e)
        return
    try:
        payload = json.loads(raw) if raw else {}
    except Exception as e:
        logger.warning("trivia_round: trigger json invalid: %s", e)
        return

    chat_id = int(payload.get("chat_id") or 0)
    if not chat_id:
        logger.warning("trivia_round: trigger missing chat_id")
        return
    thread_id = payload.get("thread_id")
    thread_id = int(thread_id) if thread_id else None
    teaser_topic_id = payload.get("teaser_topic_id")
    teaser_topic_id = int(teaser_topic_id) if teaser_topic_id else None
    teaser_text_raw = payload.get("teaser_text")
    teaser_text = str(teaser_text_raw).strip() if teaser_text_raw else None
    pre_roll_s = int(payload.get("pre_roll_s", 30))
    theme_label = str(payload.get("theme_label") or "").strip() or None
    question_count = int(payload.get("question_count") or QUESTION_COUNT)
    raw_categories = payload.get("categories") or []
    preferred_categories = {
        str(cat).strip() for cat in raw_categories
        if str(cat).strip()
    } or None

    db: Database = context.bot_data.get("db")
    if not db:
        logger.warning("trivia_round: no db in bot_data")
        return

    logger.info(
        "trivia_round: trigger launching round chat=%s pre_roll=%ss theme=%s categories=%s count=%s",
        chat_id,
        pre_roll_s,
        theme_label or THEME_LABEL,
        sorted(preferred_categories or PREFERRED_CATEGORIES),
        question_count,
    )
    asyncio.create_task(
        _run_round(
            context.bot,
            db,
            chat_id,
            thread_id,
            pre_roll_s,
            preferred_categories=preferred_categories,
            theme_label=theme_label,
            question_count=question_count,
            teaser_topic_id=teaser_topic_id,
            teaser_text=teaser_text,
        )
    )


def register(app):
    """Register round handlers."""
    app.add_handler(CommandHandler("triviaround", trivia_round_command))
    app.add_handler(CommandHandler("endtriviaround", end_trivia_round_command))
    app.add_handler(CallbackQueryHandler(handle_round_answer, pattern=r"^trivround_\d+_\d+$"))
    # Trigger-file watcher — lets the dashboard kick off a round.
    if app.job_queue:
        app.job_queue.run_repeating(trigger_watcher, interval=10, first=10, name="trivia_round_trigger")
    logger.info("trivia_round: handlers registered")
