"""Trivia game handler with scoring."""

import json
import logging
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

from ..database.db import Database
from ..utils.config import GROUP_ID, GOALS_TOPIC_ID, get_settings, is_feature_enabled
from ..utils.helpers import is_admin, get_display_name
from ..utils.levels import check_level_up

logger = logging.getLogger(__name__)

# Active trivia state
_active_trivia: dict | None = None
# Track who already answered the current question
_answered_users: set[int] = set()


def _load_questions() -> list[dict]:
    """Load trivia questions from YAML."""
    from ..utils.config import load_yaml
    try:
        data = load_yaml("trivia.yaml")
        return data.get("questions", [])
    except Exception as e:
        logger.error("Failed to load trivia.yaml: %s", e)
        return []


async def trivia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start a trivia question. /trivia command (admin only to start)."""
    global _active_trivia, _answered_users

    if not update.effective_user or not update.message:
        return

    if not is_feature_enabled("trivia", update.effective_chat.id):
        await update.message.reply_text("הפיצ'ר הזה לא פעיל כרגע")
        return

    if _active_trivia:
        await update.message.reply_text("⏳ כבר יש שאלה פעילה! חכו שהיא תסתיים.")
        return

    questions = _load_questions()
    if not questions:
        await update.message.reply_text("אין שאלות טריוויה זמינות")
        return

    q = random.choice(questions)
    _answered_users = set()

    # Build answer buttons
    buttons = []
    for i, option in enumerate(q["options"]):
        buttons.append([InlineKeyboardButton(option, callback_data=f"trivia_{i}")])

    keyboard = InlineKeyboardMarkup(buttons)

    category = q.get("category", "כללי")
    text = f"🧠 טריוויה! ({category})\n\n{q['text']}"

    sent = await update.message.reply_text(text, reply_markup=keyboard)

    _active_trivia = {
        "question": q,
        "message_id": sent.message_id,
        "chat_id": sent.chat_id,
        "correct_count": 0,
        "wrong_count": 0,
    }


async def send_scheduled_trivia(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: post a trivia question automatically."""
    global _active_trivia, _answered_users

    if not is_feature_enabled("trivia"):
        return

    if _active_trivia:
        return  # Don't overlap

    questions = _load_questions()
    if not questions:
        return

    q = random.choice(questions)
    _answered_users = set()

    settings = get_settings()
    general_topic = settings.get("topics", {}).get("general")

    buttons = []
    for i, option in enumerate(q["options"]):
        buttons.append([InlineKeyboardButton(option, callback_data=f"trivia_{i}")])

    keyboard = InlineKeyboardMarkup(buttons)

    category = q.get("category", "כללי")
    text = f"🧠 זמן טריוויה! ({category})\n\n{q['text']}"

    kwargs = {"chat_id": GROUP_ID, "text": text, "reply_markup": keyboard}
    if general_topic:
        kwargs["message_thread_id"] = general_topic

    db = context.bot_data["db"]
    try:
        sent = await context.bot.send_message(**kwargs)
        _active_trivia = {
            "question": q,
            "message_id": sent.message_id,
            "chat_id": sent.chat_id,
            "correct_count": 0,
            "wrong_count": 0,
        }
        logger.info("Sent scheduled trivia question")
        await db.log_activity("trivia", "שלח שאלת טריוויה")
    except Exception as e:
        logger.error("Failed to send trivia: %s", e)


async def handle_trivia_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle trivia answer button clicks."""
    global _active_trivia

    query = update.callback_query
    if not query or not query.data or not _active_trivia:
        if query:
            await query.answer("אין שאלה פעילה כרגע")
        return

    user = update.effective_user
    if not user:
        return

    # Check if user already answered
    if user.id in _answered_users:
        await query.answer("כבר ענית על השאלה הזו! 😊")
        return

    _answered_users.add(user.id)

    answer_idx = int(query.data.replace("trivia_", ""))
    correct_idx = _active_trivia["question"]["correct"]
    correct = answer_idx == correct_idx

    db: Database = context.bot_data["db"]
    await db.upsert_member(user.id, user.username, get_display_name(user))
    await db.add_trivia_score(user.id, correct)

    if correct:
        _active_trivia["correct_count"] += 1
        old_points = await db.add_points(user.id, 5)
        new_level = check_level_up(old_points, old_points + 5)
        if new_level:
            name = get_display_name(user)
            mention = f"[{name}](tg://user?id={user.id})"
            trivia_chat_id = _active_trivia["chat_id"] if _active_trivia else GROUP_ID
            try:
                await context.bot.send_message(
                    chat_id=trivia_chat_id,
                    text=f"🎉 מזל טוב {mention}! עלה/תה לרמה {new_level['level']} — {new_level['emoji']} {new_level['tag']}!",
                    parse_mode="Markdown",
                )
            except Exception:
                pass
        await query.answer("✅ תשובה נכונה! +10 נקודות")
    else:
        _active_trivia["wrong_count"] += 1
        correct_text = _active_trivia["question"]["options"][correct_idx]
        await query.answer(f"❌ לא נכון! התשובה: {correct_text}")

    # After enough answers or timeout, reveal the answer
    total = _active_trivia["correct_count"] + _active_trivia["wrong_count"]
    if total >= 10:  # Auto-close after 10 answers
        await _close_trivia(context)


async def _close_trivia(context: ContextTypes.DEFAULT_TYPE):
    """Close the active trivia question and show results."""
    global _active_trivia

    if not _active_trivia:
        return

    q = _active_trivia["question"]
    correct_text = q["options"][q["correct"]]

    result = (
        f"🧠 סוף הסיבוב!\n\n"
        f"השאלה: {q['text']}\n"
        f"✅ התשובה הנכונה: {correct_text}\n\n"
        f"📊 {_active_trivia['correct_count']} ענו נכון | {_active_trivia['wrong_count']} טעו"
    )

    try:
        await context.bot.edit_message_text(
            chat_id=_active_trivia["chat_id"],
            message_id=_active_trivia["message_id"],
            text=result,
        )
    except Exception as e:
        logger.error("Failed to close trivia: %s", e)

    _active_trivia = None


async def trivia_score_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's trivia score. /triviascore command."""
    if not update.effective_user or not update.message:
        return

    db: Database = context.bot_data["db"]
    score = await db.get_trivia_score(update.effective_user.id)

    if score["total"] == 0:
        text = "עדיין לא ענית על שאלות טריוויה! 🧠"
    else:
        pct = round(score["correct"] / score["total"] * 100) if score["total"] > 0 else 0
        text = (
            f"🧠 הניקוד שלך בטריוויה:\n\n"
            f"⭐ {score['score']} נקודות\n"
            f"✅ {score['correct']}/{score['total']} תשובות נכונות ({pct}%)"
        )

    await update.message.reply_text(text)


async def trivia_leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show trivia leaderboard. /triviatop command."""
    if not update.message:
        return

    db: Database = context.bot_data["db"]
    leaders = await db.get_trivia_leaderboard(10)

    if not leaders:
        await update.message.reply_text("אין עדיין ניקוד בטריוויה! התחילו לשחק עם /trivia")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🧠 טבלת טריוויה:", ""]
    for i, m in enumerate(leaders):
        medal = medals[i] if i < 3 else f" {i + 1}."
        pct = round(m["correct_answers"] / m["total_answers"] * 100) if m["total_answers"] > 0 else 0
        lines.append(f"{medal} {m['display_name']} — {m['total_score']} נקודות ({pct}% נכון)")

    await update.message.reply_text("\n".join(lines))


async def end_trivia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Force-end current trivia. /endtrivia (admin only)."""
    if not update.effective_user:
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים לסיים טריוויה")
        return

    if not _active_trivia:
        await update.message.reply_text("אין שאלת טריוויה פעילה")
        return

    await _close_trivia(context)
    await update.message.reply_text("✅ הטריוויה הסתיימה")


def register(app):
    """Register trivia handlers."""
    app.add_handler(CommandHandler("trivia", trivia_command))
    app.add_handler(CommandHandler("triviascore", trivia_score_command))
    app.add_handler(CommandHandler("triviatop", trivia_leaderboard_command))
    app.add_handler(CommandHandler("endtrivia", end_trivia_command))
    app.add_handler(CallbackQueryHandler(handle_trivia_answer, pattern=r"^trivia_\d+$"))
