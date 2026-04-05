"""Weekly roundup handler."""

import logging
from datetime import datetime, timedelta

from telegram.ext import ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings
from ..utils.levels import get_level

logger = logging.getLogger(__name__)


async def send_weekly_roundup(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send weekly roundup to general channel every Friday."""
    settings = get_settings()
    if not settings.get("features", {}).get("roundup", False):
        return

    db: Database = context.bot_data["db"]
    general_topic = settings.get("topics", {}).get("general")

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
