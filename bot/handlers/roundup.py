"""Weekly roundup handler."""

import logging
from datetime import datetime, timedelta

from telegram.ext import ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings, is_auto_blocked_on, is_feature_enabled
from ..utils.levels import get_level

logger = logging.getLogger(__name__)


async def send_weekly_roundup(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send weekly roundup to general channel every Friday."""
    if not is_feature_enabled("roundup"):
        return
    if is_auto_blocked_on(datetime.now().date()):
        logger.info("roundup: blackout date, skipping automatic post")
        return

    settings = get_settings()
    db: Database = context.bot_data["db"]
    general_topic = await db.get_verified_topic_id("general")
    if general_topic is None:
        logger.warning("roundup: no verified topic mapping for category 'general'; skipping send")
        return

    week_ago = datetime.now() - timedelta(days=7)

    # Gather stats
    new_members = await db.get_member_count_since(week_ago)
    karma_leaders = await db.get_weekly_leaders(3)
    top_streaks = await db.get_top_streaks(3)

    # Build message
    lines = ["📊 סיכום שבועי — אלהוריים וזה", ""]

    # New members
    if new_members > 0:
        lines.append(f"👋 {new_members} חברים חדשים הצטרפו השבוע!")
    else:
        lines.append("👋 לא הצטרפו חברים חדשים השבוע")

    lines.append("")

    # Top level earners
    if karma_leaders:
        lines.append("🏅 מובילי רמות השבוע:")
        medals = ["🥇", "🥈", "🥉"]
        for i, leader in enumerate(karma_leaders):
            medal = medals[i] if i < 3 else f" {i+1}."
            lvl = get_level(leader.get("karma_points", 0))
            lines.append(f"  {medal} {lvl['emoji']} {leader['display_name']} — {lvl['tag']} (רמה {lvl['level']})")
        lines.append("")

    # Achievement streaks
    if top_streaks:
        lines.append("🔥 רצפים בהישגים ומטרות:")
        for s in top_streaks:
            if s["current_streak"] > 0:
                lines.append(f"  💪 {s['display_name']} — {s['current_streak']} ימים רצוף!")
        lines.append("")

    lines.append("שבוע טוב לכולם! 🎉")

    text = "\n".join(lines)

    kwargs = {"chat_id": GROUP_ID, "text": text}
    if general_topic:
        kwargs["message_thread_id"] = general_topic

    try:
        await context.bot.send_message(**kwargs)
        logger.info("Sent weekly roundup")
        await db.log_activity("roundup", "שלח סיכום שבועי")
    except Exception as e:
        logger.error("Failed to send weekly roundup: %s", e)
