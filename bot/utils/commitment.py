"""Check if a scheduled_messages row exists for the current minute+type.

Used by random prompt handlers (morning/evening/discussion) to defer to the
calendar checker when the user has committed a specific row for this day.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")


async def has_committed_row(db, message_type: str) -> bool:
    """Return True if a scheduled_messages row exists for today at this exact HH:MM
    with the given message_type and status='scheduled'.

    When True, the calendar checker (bot/handlers/calendar.py) will send the row,
    so the random handler should skip to avoid duplicate sends.
    """
    now = datetime.now(_IL_TZ)
    today_iso = now.date().isoformat()
    current_hh_mm = now.strftime("%H:%M")
    try:
        rows = await db.get_scheduled_messages(today_iso, today_iso)
    except Exception as e:
        logger.warning("[commitment] failed to query scheduled_messages: %s", e)
        return False
    for row in rows:
        if row.get("status") != "scheduled":
            continue
        if row.get("message_type") != message_type:
            continue
        row_time = (row.get("scheduled_time") or "")[:5]
        if row_time == current_hh_mm:
            logger.info(
                "[commitment] found committed %s row id=%s for %s %s — "
                "random handler will skip, calendar checker will send",
                message_type, row.get("id"), today_iso, current_hh_mm,
            )
            return True
    return False
