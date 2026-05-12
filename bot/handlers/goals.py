# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Daily goals & achievements handler with prompt rotation, streaks, and auto-pin."""

import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GOALS_TOPIC_ID as _ENV_GOALS_TOPIC_ID, GROUP_ID, get_settings, is_feature_enabled
from ..utils.copy import load_copy


def _get_goals_topic_id():
    """Get goals topic ID from env or settings.yaml fallback."""
    if _ENV_GOALS_TOPIC_ID:
        return _ENV_GOALS_TOPIC_ID
    settings = get_settings()
    return settings.get("topics", {}).get("goals")
from ..utils.levels import check_level_up
from ..utils.scoring import get_points

logger = logging.getLogger(__name__)


async def track_goals_participation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track when a member posts in the goals channel for streak tracking."""
    if not update.message or not update.effective_user:
        return

    if not is_feature_enabled("goals", update.effective_chat.id):
        return

    # Only track messages in the goals topic
    thread_id = getattr(update.message, "message_thread_id", None)
    goals_topic = _get_goals_topic_id()
    if thread_id != goals_topic:
        return

    user = update.effective_user
    db: Database = context.bot_data["db"]

    await db.upsert_member(user.id, user.username, user.first_name or "")
    await db.update_streak(user.id)

    # Check if this is a reply to a bot prompt — bonus points
    is_prompt_reply = (
        update.message.reply_to_message
        and update.message.reply_to_message.from_user
        and update.message.reply_to_message.from_user.is_bot
    )

    points = get_points("prompt_reply") if is_prompt_reply else get_points("goals_post")
    label = load_copy("goals", "prompt_reply_label" if is_prompt_reply else "goals_post_label", default="goals")

    old_points = await db.add_points(user.id, points)
    await db.log_activity("points", f"+{points} points for {user.first_name or ''} ({label})", user.id, "goals")
    new_level = check_level_up(old_points, old_points + points)
    if new_level:
        name = user.first_name or ""
        mention = f"[{name}](tg://user?id={user.id})"
        text = load_copy(
            "goals", "level_up", default="Level up: {mention} {level} {emoji} {tag}",
            mention=mention,
            level=new_level["level"],
            emoji=new_level["emoji"],
            tag=new_level["tag"],
        )
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=text, parse_mode="Markdown")
        except Exception:
            pass

    streak = await db.get_streak(user.id)
    current = streak["current"]

    # Celebrate milestones
    milestones = (get_settings().get("gamification") or {}).get("streak_milestones") or []
    if current in {int(x) for x in milestones}:
        milestone_msg = load_copy(
            "goals", "milestone", default="Streak milestone: {name} {days}",
            name=user.first_name or "", days=current,
        )
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
        text = load_copy("goals", "streak_empty", default="No streak yet")
    else:
        text = load_copy(
            "goals", "streak_status", default="Current streak: {current}\nBest streak: {longest}",
            current=streak["current"], longest=streak["longest"],
        )

    await update.message.reply_text(text)


def register(app):
    """Register goals handlers."""
    app.add_handler(CommandHandler("streak", streak_command))
    # Track participation in goals channel
    if _get_goals_topic_id():
        app.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Chat(chat_id=GROUP_ID),
                track_goals_participation,
            ),
            group=2,  # Lower priority than other handlers
        )
