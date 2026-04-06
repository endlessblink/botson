"""Daily goals & achievements handler with prompt rotation and streaks."""

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GOALS_TOPIC_ID, GROUP_ID, get_settings, is_feature_enabled
from ..utils.levels import check_level_up

logger = logging.getLogger(__name__)


async def send_morning_prompt(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send morning prompt to goals channel."""
    if not is_feature_enabled("morning_prompt") and not is_feature_enabled("goals"):
        return
    settings = get_settings()

    db: Database = context.bot_data["db"]
    prompt = await db.get_random_prompt("morning")

    if not GOALS_TOPIC_ID:
        logger.warning("GOALS_TOPIC_ID not set, skipping morning prompt")
        return

    kwargs = {"chat_id": GROUP_ID, "text": prompt}
    if GOALS_TOPIC_ID:
        kwargs["message_thread_id"] = GOALS_TOPIC_ID

    try:
        await context.bot.send_message(**kwargs)
        logger.info("Sent morning prompt")
        await db.log_activity("goals", "שלח הודעת בוקר", target_channel="goals")
    except Exception as e:
        logger.error("Failed to send morning prompt: %s", e)


async def send_evening_prompt(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send evening prompt to goals channel."""
    if not is_feature_enabled("evening_prompt") and not is_feature_enabled("goals"):
        return
    settings = get_settings()

    db: Database = context.bot_data["db"]
    prompt = await db.get_random_prompt("evening")

    if not GOALS_TOPIC_ID:
        logger.warning("GOALS_TOPIC_ID not set, skipping evening prompt")
        return

    kwargs = {"chat_id": GROUP_ID, "text": prompt}
    if GOALS_TOPIC_ID:
        kwargs["message_thread_id"] = GOALS_TOPIC_ID

    try:
        await context.bot.send_message(**kwargs)
        logger.info("Sent evening prompt")
        await db.log_activity("goals", "שלח הודעת ערב", target_channel="goals")
    except Exception as e:
        logger.error("Failed to send evening prompt: %s", e)


async def track_goals_participation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when a member posts in the goals channel for streak tracking."""
    if not update.message or not update.effective_user:
        return

    if not is_feature_enabled("goals", update.effective_chat.id):
        return

    # Only track messages in the goals topic
    thread_id = getattr(update.message, "message_thread_id", None)
    if thread_id != GOALS_TOPIC_ID:
        return

    user = update.effective_user
    db: Database = context.bot_data["db"]

    await db.upsert_member(user.id, user.username, user.first_name or "")
    await db.update_streak(user.id)
    old_points = await db.add_points(user.id, 2)
    await db.log_activity("points", f"+2 נקודות ל-{user.first_name or ''} (הישגים)", user.id, "goals")
    new_level = check_level_up(old_points, old_points + 2)
    if new_level:
        name = user.first_name or ""
        mention = f"[{name}](tg://user?id={user.id})"
        text = f"🎉 מזל טוב {mention}! עלה/תה לרמה {new_level['level']} — {new_level['emoji']} {new_level['tag']}!"
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="Markdown")
        except Exception:
            pass

    streak = await db.get_streak(user.id)
    current = streak["current"]

    # Celebrate milestones
    if current in (7, 14, 30, 60, 100):
        milestone_msg = f"🔥 {user.first_name} הגיע/ה לרצף של {current} ימים! כל הכבוד! 🎉"
        try:
            await update.message.reply_text(milestone_msg)
        except Exception as e:
            logger.error("Failed to send milestone message: %s", e)


async def streak_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's current streak. /streak command."""
    if not update.effective_user:
        return

    db: Database = context.bot_data["db"]
    streak = await db.get_streak(update.effective_user.id)

    if streak["current"] == 0:
        text = "עדיין אין לך רצף! שתפ/י מטרה או הישג בערוץ הישגים ומטרות כדי להתחיל 🌟"
    else:
        text = (
            f"🔥 הרצף שלך: {streak['current']} ימים\n"
            f"🏆 הרצף הארוך ביותר: {streak['longest']} ימים"
        )

    await update.message.reply_text(text)


def register(app):
    """Register goals handlers."""
    app.add_handler(CommandHandler("streak", streak_command))
    # Track participation in goals channel
    if GOALS_TOPIC_ID:
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=GROUP_ID),
                track_goals_participation,
            ),
            group=2,  # Lower priority than other handlers
        )
