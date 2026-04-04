"""Stars / recognition system handler."""

import logging
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings
from ..utils.helpers import is_admin, is_bot_user, get_display_name

logger = logging.getLogger(__name__)

# In-memory daily activity count: {user_id: {date_str: count}}
_daily_counts: dict[int, dict[str, int]] = {}

MAX_DAILY_STARS = 10


async def award_activity_stars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Award 1 star per message, up to MAX_DAILY_STARS per user per day."""
    msg = update.message
    if not msg or not update.effective_user:
        return

    user = update.effective_user

    # Skip bots
    if is_bot_user(user):
        return

    today = date.today().isoformat()
    user_counts = _daily_counts.setdefault(user.id, {})
    today_count = user_counts.get(today, 0)

    if today_count >= MAX_DAILY_STARS:
        return

    db: Database = context.bot_data["db"]
    await db.upsert_member(user.id, user.username, get_display_name(user))
    await db.add_stars(user.id, 1)
    user_counts[today] = today_count + 1

    logger.debug(
        "Stars: +1 for %s (today: %d)",
        get_display_name(user),
        user_counts[today],
    )


async def stars_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show star points. /stars or /stars @user."""
    if not update.effective_user or not update.message:
        return

    db: Database = context.bot_data["db"]

    # Check if querying another user
    if context.args and context.args[0].startswith("@"):
        username = context.args[0].lstrip("@")
        member = None
        leaders = await db.get_stars_leaderboard(100)
        for m in leaders:
            if m.get("display_name", "").lower() == username.lower():
                member = m
                break
        if member:
            text = f"⭐ ל-{member['display_name']} יש {member['karma_points']} כוכבים"
        else:
            text = f"לא מצאתי את המשתמש @{username}"
    else:
        stars = await db.get_stars(update.effective_user.id)
        text = f"⭐ יש לך {stars} כוכבים"

    await update.message.reply_text(text)


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top 10 stars earners. /leaderboard."""
    if not update.message:
        return

    db: Database = context.bot_data["db"]
    leaders = await db.get_stars_leaderboard(10)

    if not leaders:
        await update.message.reply_text("אין עדיין כוכבים! היו פעילים בקבוצה כדי לצבור כוכבים ⭐")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 טבלת כוכבים:", ""]
    for i, m in enumerate(leaders):
        if m["karma_points"] == 0:
            continue
        medal = medals[i] if i < 3 else f" {i + 1}."
        lines.append(f"{medal} {m['display_name']} — {m['karma_points']} כוכבים")

    if len(lines) <= 2:
        await update.message.reply_text("אין עדיין כוכבים!")
        return

    await update.message.reply_text("\n".join(lines))


async def reset_stars_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset all stars. Admin only. /resetstars."""
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים לאפס כוכבים")
        return

    db: Database = context.bot_data["db"]
    await db.reset_stars()
    await update.message.reply_text("✅ כל הכוכבים אופסו! עונה חדשה התחילה 🎉")
    logger.info("Stars reset by admin %d", update.effective_user.id)


async def send_weekly_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: post weekly stars leaderboard to general channel."""
    db: Database = context.bot_data["db"]
    leaders = await db.get_weekly_stars_leaders(10)

    if not leaders:
        return

    settings = get_settings()
    general_topic = settings.get("topics", {}).get("general")

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 טבלת כוכבים שבועית:", ""]
    for i, m in enumerate(leaders):
        medal = medals[i] if i < 3 else f" {i + 1}."
        lines.append(f"{medal} {m['display_name']} — {m['weekly_stars']} כוכבים השבוע")

    kwargs = {"chat_id": GROUP_ID, "text": "\n".join(lines)}
    if general_topic:
        kwargs["message_thread_id"] = general_topic

    try:
        await context.bot.send_message(**kwargs)
        logger.info("Sent weekly stars leaderboard")
    except Exception as e:
        logger.error("Failed to send weekly leaderboard: %s", e)


def register(app):
    """Register stars handlers."""
    app.add_handler(CommandHandler("stars", stars_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("resetstars", reset_stars_command))
    # Activity stars — run on all non-command text messages at lower priority
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, award_activity_stars),
        group=3,
    )
