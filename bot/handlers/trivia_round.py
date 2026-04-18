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

logger = logging.getLogger(__name__)

# Scoring constants (per user spec)
POINTS_CORRECT = 12
POINTS_FIRST_PLACE_BONUS = 20
QUESTION_COUNT = 5
QUESTION_TIMEOUT_S = 30

# Preferred categories for a round (from the announcement spec)
PREFERRED_CATEGORIES = {
    "גיאוגרפיה", "מדע", "סרטים", "היסטוריה", "ספורט", "גיימינג",
}

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


def _pick_questions(n: int) -> list[dict]:
    """Pick n questions, preferring the categories in PREFERRED_CATEGORIES."""
    pool = _load_questions()
    if not pool:
        return []
    preferred = [q for q in pool if q.get("category") in PREFERRED_CATEGORIES]
    rest = [q for q in pool if q.get("category") not in PREFERRED_CATEGORIES]
    random.shuffle(preferred)
    random.shuffle(rest)
    picked: list[dict] = []
    seen_texts: set[str] = set()
    for q in preferred + rest:
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


def _format_announcement(pre_roll_s: int) -> str:
    if pre_roll_s >= 60:
        minutes = max(1, pre_roll_s // 60)
        when = f"עוד {minutes} דקות"
    else:
        when = f"עוד {pre_roll_s} שניות"
    return (
        "🧠 טריוויה מתחילה!\n\n"
        f"{QUESTION_COUNT} שאלות. {QUESTION_TIMEOUT_S} שניות לכל אחת. "
        f"תשובה נכונה = {POINTS_CORRECT} נק׳. המקום הראשון = +{POINTS_FIRST_PLACE_BONUS} בונוס.\n\n"
        "קטגוריות: גיאוגרפיה, מדע, סרטים, היסטוריה, ספורט, גיימינג.\n\n"
        f"השאלה הראשונה יורדת {when} — תישארו בצ׳אט."
    )


async def _post_question(bot, db: Database, chat_id: int, thread_id: int | None,
                          q_index: int, q: dict) -> int:
    """Post question q_index and return message_id."""
    category = q.get("category", "כללי")
    text = (
        f"🧠 שאלה {q_index + 1}/{QUESTION_COUNT} · {category}\n\n"
        f"{q['text']}\n\n"
        f"⏱ {QUESTION_TIMEOUT_S} שניות"
    )
    markup = _build_answer_markup(q_index, q["options"])
    kwargs = {"chat_id": chat_id, "text": text, "reply_markup": markup}
    if thread_id is not None:
        kwargs["message_thread_id"] = thread_id
    msg = await bot.send_message(**kwargs)
    return msg.message_id


async def _reveal_question(bot, chat_id: int, message_id: int, q: dict,
                             q_index: int, round_state: dict) -> None:
    correct_idx = q["correct"]
    correct_text = q["options"][correct_idx]
    answers = round_state.get("answers_this_q", {})
    n_correct = sum(1 for v in answers.values() if v == correct_idx)
    n_wrong = len(answers) - n_correct
    text = (
        f"🧠 שאלה {q_index + 1}/{QUESTION_COUNT} · נסגרה\n\n"
        f"{q['text']}\n\n"
        f"✅ התשובה: {correct_text}\n"
        f"📊 {n_correct} נכון · {n_wrong} לא נכון"
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
    if not scores:
        return "🧠 סוף הטריוויה!\n\nאף אחד לא ענה נכון הפעם. בפעם הבאה 💪"
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1]["correct"], kv[1]["name"]))
    medals = ["🥇", "🥈", "🥉"]
    lines = ["🧠 סוף הטריוויה! תוצאות:\n"]
    for i, (uid, s) in enumerate(ranked):
        medal = medals[i] if i < 3 else f"{i+1}."
        bonus = f" (+{POINTS_FIRST_PLACE_BONUS} בונוס 🏆)" if uid in bonus_winners else ""
        lines.append(f"{medal} {s['name']} — {s['correct']}/{QUESTION_COUNT} נכון · {s['points']} נק׳{bonus}")
    if bonus_winners:
        winners_names = ", ".join(round_state["scores"][u]["name"] for u in bonus_winners)
        lines.append(f"\n🏆 מקום ראשון: {winners_names} (+{POINTS_FIRST_PLACE_BONUS} בונוס)")
    return "\n".join(lines)


async def _run_round(bot, db: Database, chat_id: int, thread_id: int | None,
                      pre_roll_s: int) -> None:
    """Drive a single round start → finish. Safe to cancel via STOP_FILE."""
    if chat_id in _active_rounds:
        logger.info("trivia_round: round already active in chat %s", chat_id)
        return

    questions = _pick_questions(QUESTION_COUNT)
    if len(questions) < QUESTION_COUNT:
        logger.error("trivia_round: not enough questions (%d)", len(questions))
        return

    round_state: dict = {
        "questions": questions,
        "q_index": -1,
        "msg_id": None,
        "scores": {},  # user_id → {name, correct, points}
        "answers_this_q": {},  # user_id → answer_idx (for current question only)
        "aborted": False,
    }
    _active_rounds[chat_id] = round_state

    try:
        # Announcement
        ann_kwargs = {"chat_id": chat_id, "text": _format_announcement(pre_roll_s)}
        if thread_id is not None:
            ann_kwargs["message_thread_id"] = thread_id
        try:
            await bot.send_message(**ann_kwargs)
        except Exception as e:
            logger.error("trivia_round: announcement failed: %s", e)
            return

        # Pre-roll sleep (check abort)
        slept = 0
        while slept < pre_roll_s:
            if _should_abort(chat_id):
                return
            await asyncio.sleep(min(2, pre_roll_s - slept))
            slept += 2

        # Questions loop
        for q_index, q in enumerate(questions):
            if _should_abort(chat_id):
                return
            round_state["q_index"] = q_index
            round_state["answers_this_q"] = {}
            msg_id = await _post_question(bot, db, chat_id, thread_id, q_index, q)
            round_state["msg_id"] = msg_id

            # Wait for timeout (abortable)
            slept = 0
            while slept < QUESTION_TIMEOUT_S:
                if _should_abort(chat_id):
                    return
                await asyncio.sleep(1)
                slept += 1

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
        final_kwargs = {"chat_id": chat_id, "text": _build_final_text(round_state, bonus_winners)}
        if thread_id is not None:
            final_kwargs["message_thread_id"] = thread_id
        try:
            await bot.send_message(**final_kwargs)
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


async def trigger_watcher(context: ContextTypes.DEFAULT_TYPE):
    """Runs every ~10s. Looks for data/trivia_round_trigger.json, launches a round.

    Trigger JSON schema:
        {"chat_id": -1003747545764, "thread_id": null, "pre_roll_s": 30}
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
    pre_roll_s = int(payload.get("pre_roll_s", 30))

    db: Database = context.bot_data.get("db")
    if not db:
        logger.warning("trivia_round: no db in bot_data")
        return

    logger.info("trivia_round: trigger launching round chat=%s pre_roll=%ss", chat_id, pre_roll_s)
    asyncio.create_task(_run_round(context.bot, db, chat_id, thread_id, pre_roll_s))


def register(app):
    """Register round handlers."""
    app.add_handler(CommandHandler("triviaround", trivia_round_command))
    app.add_handler(CommandHandler("endtriviaround", end_trivia_round_command))
    app.add_handler(CallbackQueryHandler(handle_round_answer, pattern=r"^trivround_\d+_\d+$"))
    # Trigger-file watcher — lets the dashboard kick off a round.
    if app.job_queue:
        app.job_queue.run_repeating(trigger_watcher, interval=10, first=10, name="trivia_round_trigger")
    logger.info("trivia_round: handlers registered")
