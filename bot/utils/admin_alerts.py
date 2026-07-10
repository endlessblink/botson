"""Direct-message alerts to the bot's admins.

Used for conditions the operator must act on but which are invisible in the
group — e.g. a scheduled game that will not be published because its content
pool could not be refilled. Delivery is best-effort: a failed DM must never
abort the dispatch loop that raised the alert.
"""

from __future__ import annotations

import logging

from telegram import Bot

from .config import ADMIN_IDS

logger = logging.getLogger(__name__)


async def notify_admins(bot: Bot, message: str) -> int:
    """DM `message` to every configured admin. Returns the delivered count.

    Admin ids are positive chat ids, so these bypass the forum-topic send
    guard by construction — no `safe_send` wrapper is needed or wanted here.
    """
    if not ADMIN_IDS:
        logger.warning("admin_alerts: no ADMIN_IDS configured — alert dropped: %s", message)
        return 0
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=message)
            delivered += 1
        except Exception as exc:
            logger.error("admin_alerts: failed to notify admin %d: %s", admin_id, exc)
    return delivered
