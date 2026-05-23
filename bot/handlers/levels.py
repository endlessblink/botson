# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Levels system — validation-based progression with level-up announcements.

Points are earned only through validated actions (bot prompt replies, trivia,
events, streaks) — not raw message activity. See config/settings.yaml gamification section.
"""

from datetime import date
import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings, is_auto_blocked_on, is_feature_enabled
from ..utils.topic_guard import UnverifiedTopicError, safe_send
from ..utils.helpers import is_admin, is_bot_user, get_display_name
from ..utils.levels import get_level, get_progress, check_level_up, make_progress_bar

logger = logging.getLogger(__name__)


async def _announce_level_up(context: ContextTypes.DEFAULT_TYPE, user_id: int, user_name: str,
                             new_level: dict, chat_id: int | None = None):
    """Post level-up announcement to the group, tagging the user."""
    mention = f"[{user_name}](tg://user?id={user_id})"
    text = f"🎉 מזל טוב {mention}! עלה/תה לרמה {new_level['level']} — {new_level['emoji']} {new_level['tag']}!"
    announce_id = chat_id or GROUP_ID
    try:
        await context.bot.send_message(chat_id=announce_id, text=text, parse_mode="Markdown")
    except Exception as e:
        logger.error("Failed to announce level-up: %s", e)


async def award_and_check_level(db: Database, context: ContextTypes.DEFAULT_TYPE,
                                 user_id: int, user_name: str, points: int = 1,
                                 chat_id: int | None = None):
    """Award points and check for level-up. Announces if leveled up."""
    old_points = await db.add_points(user_id, points)
    new_points = old_points + points
    new_level = check_level_up(old_points, new_points)
    if new_level:
        announce_id = chat_id or GROUP_ID
        await _announce_level_up(context, user_id, user_name, new_level, announce_id)
        await db.log_activity("level_up", f"{user_name} עלה/תה לרמה {new_level['level']}", user_id)


async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track members from messages (no points for raw messages — validation-based only)."""
    if not update.message or not update.effective_user:
        return

    user = update.effective_user
    if is_bot_user(user):
        return

    db: Database = context.bot_data["db"]

    # Engagement capture: a reply to one of the bot's scheduled posts is the
    # highest-signal outcome for prompts (discussion/morning/evening). Recorded
    # independently of the levels feature toggle so the signal is never lost.
    await _maybe_record_prompt_reply(update, db)

    if not is_feature_enabled("levels", update.effective_chat.id):
        return

    await db.upsert_member(user.id, user.username, get_display_name(user))


async def _maybe_record_prompt_reply(update: Update, db: Database) -> None:
    """If this message replies to one of the bot's tracked posts, record it.

    Matching on the bot's stored sent_message_id (not just "is a reply") means
    we only count genuine replies to content the bot scheduled — forum topic
    roots and replies to other users never match.
    """
    if not update.message or not update.effective_user:
        return
    reply_to = update.message.reply_to_message
    if not reply_to:
        return
    try:
        sched = await db.get_scheduled_by_sent_message_id(reply_to.message_id)
        if sched:
            await db.record_prompt_reply(int(sched["id"]), update.effective_user.id)
    except Exception as e:  # noqa: BLE001 — engagement capture must never break message handling
        logger.warning("prompt-reply capture failed: %s", e)


async def level_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's level and progress. /level command."""
    if not update.effective_user or not update.message:
        return

    db: Database = context.bot_data["db"]
    points = await db.get_points(update.effective_user.id)
    progress = get_progress(points)
    current = progress["current"]
    bar = make_progress_bar(progress["progress"])

    if progress["next"]:
        text = (
            f"{current['emoji']} {current['tag']} (רמה {current['level']})\n"
            f"{bar} {progress['points_current']}/{progress['points_needed']} לרמה הבאה"
        )
    else:
        text = f"{current['emoji']} {current['tag']} (רמה {current['level']}) — רמה מקסימלית! 🏆"

    await update.message.reply_text(text)


async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show top 10 by level. /leaderboard."""
    if not update.message:
        return

    db: Database = context.bot_data["db"]
    leaders = await db.get_leaderboard(10)

    if not leaders:
        await update.message.reply_text("אין עדיין נתוני רמות! התחילו להיות פעילים 🌱")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 טבלת הרמות הכללית:", ""]
    for i, m in enumerate(leaders):
        if m["karma_points"] == 0:
            continue
        medal = medals[i] if i < 3 else f" {i + 1}."
        lvl = get_level(m["karma_points"])
        lines.append(f"{medal} {lvl['emoji']} {m['display_name']} — {lvl['tag']} (רמה {lvl['level']})")

    if len(lines) <= 2:
        await update.message.reply_text("אין עדיין נתוני רמות!")
        return

    await update.message.reply_text("\n".join(lines))


async def reset_levels_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset all points/levels. Admin only. /resetlevels."""
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים לאפס רמות")
        return

    db: Database = context.bot_data["db"]
    await db.reset_points()
    await update.message.reply_text("✅ כל הרמות אופסו! עונה חדשה התחילה 🎉")
    logger.info("Levels reset by admin %d", update.effective_user.id)


async def send_weekly_leaderboard(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: post weekly level leaderboard to general channel."""
    if is_auto_blocked_on(date.today()):
        logger.info("levels: blackout date, skipping weekly leaderboard")
        return {"skipped": "blackout date"}

    db: Database = context.bot_data["db"]
    leaders = await db.get_weekly_leaders(10)

    if not leaders:
        return {"skipped": "no weekly leaders"}

    routing = await db.get_handler_routing("weekly_leaderboard")
    if not routing or routing["play_topic_id"] is None:
        logger.warning("levels: no routing configured for 'weekly_leaderboard'; skipping")
        return None
    play_id = routing["play_topic_id"]

    medals = ["🥇", "🥈", "🥉"]
    lines = ["🏆 מובילי השבוע ברמות:", ""]
    for i, m in enumerate(leaders):
        medal = medals[i] if i < 3 else f" {i + 1}."
        lvl = get_level(m.get("karma_points", 0))
        lines.append(f"{medal} {lvl['emoji']} {m['display_name']} — {lvl['tag']} (+{m.get('weekly_stars', 0)} נקודות השבוע)")

    try:
        msg = await safe_send(
            context.bot,
            db,
            "send_message",
            chat_id=GROUP_ID,
            text="\n".join(lines),
            message_thread_id=play_id,
        )
        logger.info("Sent weekly leaderboard")
        return getattr(msg, "message_id", None)
    except UnverifiedTopicError as e:
        logger.warning("levels: guard refused send: %s", e)
    except Exception as e:
        logger.error("Failed to send weekly leaderboard: %s", e)
    return None


def register(app):
    """Register level handlers."""
    app.add_handler(CommandHandler("level", level_command))
    app.add_handler(CommandHandler("leaderboard", leaderboard_command))
    app.add_handler(CommandHandler("resetlevels", reset_levels_command))
    # Track members (no points for raw messages — validation-based scoring)
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, track_member),
        group=3,
    )
