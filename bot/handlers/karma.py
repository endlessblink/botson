"""Karma / recognition system handler."""

import logging
import re

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings
from ..utils.helpers import is_admin, is_bot_user, get_display_name

logger = logging.getLogger(__name__)

# Karma trigger patterns
KARMA_TRIGGERS = re.compile(r"^(\+1|תודה|👏)$", re.UNICODE)


async def detect_karma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detect karma triggers in reply messages."""
    msg = update.message
    if not msg or not msg.reply_to_message or not msg.text:
        return
    if not update.effective_user:
        return

    # Check if message matches karma triggers
    settings = get_settings()
    triggers = settings.get("karma", {}).get("triggers", ["+1", "תודה", "👏"])
    text = msg.text.strip()

    if text not in triggers and not KARMA_TRIGGERS.match(text):
        return

    giver = update.effective_user
    receiver = msg.reply_to_message.from_user

    if not receiver:
        return

    # Anti-abuse: no self-karma
    if giver.id == receiver.id:
        return

    # Anti-abuse: no karma to bots
    if is_bot_user(receiver):
        return

    db: Database = context.bot_data["db"]

    # Anti-abuse: max karma per day
    max_per_day = settings.get("karma", {}).get("max_per_day", 5)
    given_today = await db.get_karma_given_today(giver.id)
    if given_today >= max_per_day:
        return

    # Ensure both users exist in DB
    await db.upsert_member(giver.id, giver.username, get_display_name(giver))
    await db.upsert_member(receiver.id, receiver.username, get_display_name(receiver))

    # Add karma
    await db.add_karma(giver.id, receiver.id, msg.reply_to_message.message_id)

    # React with confirmation emoji (silent)
    try:
        await msg.set_reaction("⭐")
    except Exception:
        # Fallback: some bot API versions may not support reactions
        try:
            await msg.reply_text("⭐", quote=False)
        except Exception as e:
            logger.error("Failed to confirm karma: %s", e)

    logger.info(
        "Karma: %s → %s (total: %d)",
        get_display_name(giver),
        get_display_name(receiver),
        await db.get_karma(receiver.id),
    )


async def karma_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show karma points. /karma or /karma @user."""
    if not update.effective_user or not update.message:
        return

    db: Database = context.bot_data["db"]

    # Check if querying another user
    if context.args and context.args[0].startswith("@"):
        username = context.args[0].lstrip("@")
        # Search by username in members table
        member = None
        leaders = await db.get_leaderboard(100)
        for m in leaders:
            if m.get("display_name", "").lower() == username.lower():
                member = m
                break
        if member:
            text = f"⭐ ל-{member['display_name']} יש {member['karma_points']} נקודות קארמה"
        else:
            text = f"לא מצאתי את המשתמש @{username}"
    else:
        karma = await db.get_karma(update.effective_user.id)
        text = f"⭐ יש לך {karma} נקודות קארמה"

    await update.message.reply_text(text)


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top 10 karma earners. /leaderboard."""
    if not update.message:
        return

    db: Database = context.bot_data["db"]
    leaders = await db.get_leaderboard(10)

    if not leaders:
        await update.message.reply_text("אין עדיין נקודות קארמה! התחילו להעריך אחד את השני עם +1, תודה או 👏")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 טבלת קארמה:", ""]
    for i, m in enumerate(leaders):
        if m["karma_points"] == 0:
            continue
        medal = medals[i] if i < 3 else f" {i + 1}."
        lines.append(f"{medal} {m['display_name']} — {m['karma_points']} נקודות")

    if len(lines) <= 2:
        await update.message.reply_text("אין עדיין נקודות קארמה!")
        return

    await update.message.reply_text("\n".join(lines))


async def reset_karma_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset all karma. Admin only. /resetkarma."""
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים לאפס קארמה")
        return

    db: Database = context.bot_data["db"]
    await db.reset_karma()
    await update.message.reply_text("✅ כל נקודות הקארמה אופסו! עונה חדשה התחילה 🎉")
    logger.info("Karma reset by admin %d", update.effective_user.id)


async def send_weekly_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: post weekly karma leaderboard to general channel."""
    db: Database = context.bot_data["db"]
    leaders = await db.get_weekly_karma_leaders(10)

    if not leaders:
        return

    settings = get_settings()
    general_topic = settings.get("topics", {}).get("general")

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 טבלת קארמה שבועית:", ""]
    for i, m in enumerate(leaders):
        medal = medals[i] if i < 3 else f" {i + 1}."
        lines.append(f"{medal} {m['display_name']} — {m['weekly_karma']} נקודות השבוע")

    kwargs = {"chat_id": GROUP_ID, "text": "\n".join(lines)}
    if general_topic:
        kwargs["message_thread_id"] = general_topic

    try:
        await context.bot.send_message(**kwargs)
        logger.info("Sent weekly karma leaderboard")
    except Exception as e:
        logger.error("Failed to send weekly leaderboard: %s", e)


def register(app):
    """Register karma handlers."""
    app.add_handler(CommandHandler("karma", karma_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("resetkarma", reset_karma_command))
    # Karma detection — run on all text messages at lower priority
    app.add_handler(
        MessageHandler(filters.TEXT & filters.REPLY, detect_karma),
        group=3,
    )
