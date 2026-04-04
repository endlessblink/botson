"""Weekly roundup handler."""

import logging
from datetime import datetime, timedelta

from telegram.ext import ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings

logger = logging.getLogger(__name__)


async def send_weekly_roundup(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send weekly roundup to general channel every Friday."""
    db: Database = context.bot_data["db"]
    settings = get_settings()
    general_topic = settings.get("topics", {}).get("general")

    week_ago = datetime.now() - timedelta(days=7)

    # Gather stats
    new_members = await db.get_member_count_since(week_ago)
    karma_leaders = await db.get_weekly_karma_leaders(3)
    top_streaks = await db.get_top_streaks(3)

    # Build message
    lines = ["📊 סיכום שבועי — אלהוריים וזה", ""]

    # New members
    if new_members > 0:
        lines.append(f"👋 {new_members} חברים חדשים הצטרפו השבוע!")
    else:
        lines.append("👋 לא הצטרפו חברים חדשים השבוע")

    lines.append("")

    # Top karma earners
    if karma_leaders:
        lines.append("⭐ מובילי קארמה השבוע:")
        medals = ["🥇", "🥈", "🥉"]
        for i, leader in enumerate(karma_leaders):
            medal = medals[i] if i < 3 else f" {i+1}."
            lines.append(f"  {medal} {leader['display_name']} — {leader['weekly_karma']} נקודות")
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
    except Exception as e:
        logger.error("Failed to send weekly roundup: %s", e)
