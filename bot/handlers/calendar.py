"""Content calendar — checks for due messages and sends them automatically."""

import json
import logging
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from ..database.db import Database
from ..utils.config import get_settings

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _next_matching_day(current_date: date, days: list[int]) -> date:
    """Find the next date that matches one of the Hebrew days.
    Hebrew: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
    """
    for i in range(1, 8):
        next_d = current_date + timedelta(days=i)
        hebrew_day = (next_d.weekday() + 1) % 7
        if hebrew_day in days:
            return next_d
    return current_date + timedelta(days=7)  # fallback


async def check_and_send_due_messages(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute. Checks for due messages and sends them."""
    now = datetime.now(_IL_TZ)
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    db: Database = context.bot_data["db"]
    due = await db.get_due_messages(current_date, current_time)

    if not due:
        return

    import os
    from telegram import Bot
    bot_token = os.getenv("BOT_TOKEN", "")
    main_group = int(os.getenv("GROUP_ID", "0"))
    test_group = int(os.getenv("TEST_GROUP_ID", "0"))

    for msg in due:
        target = msg.get("target_group", "main")
        if target == "test":
            group_id = test_group
        elif target == "both":
            group_id = main_group  # Send to main first, test handled separately
        else:
            group_id = main_group

        if not group_id:
            await db.mark_message_failed(msg["id"], f"No group ID for target '{target}'")
            continue

        kwargs = {"chat_id": group_id, "text": msg["text"]}
        if msg.get("channel_topic_id"):
            kwargs["message_thread_id"] = msg["channel_topic_id"]

        try:
            bot = Bot(bot_token)
            sent = await bot.send_message(**kwargs)

            # Auto-pin if requested
            if msg.get("auto_pin"):
                try:
                    await bot.pin_chat_message(
                        chat_id=group_id,
                        message_id=sent.message_id,
                        disable_notification=True,
                    )
                except Exception as e:
                    logger.warning("Failed to pin message %d: %s", sent.message_id, e)

            await db.mark_message_sent(msg["id"], sent.message_id)
            await db.log_activity(
                msg.get("message_type", "custom"),
                f"שלח: {msg['text'][:50]}",
                target_channel=str(msg.get("channel_topic_id") or "general"),
            )
            logger.info("Sent scheduled message %d: %s", msg["id"], msg["text"][:40])

            # Handle recurrence — create next occurrence
            recurrence = msg.get("recurrence")
            if recurrence:
                today = date.fromisoformat(current_date)
                if recurrence == "daily":
                    next_date = today + timedelta(days=1)
                elif recurrence == "weekdays":
                    next_d = today + timedelta(days=1)
                    while next_d.weekday() >= 5:  # Skip Sat/Sun
                        next_d += timedelta(days=1)
                    next_date = next_d
                elif recurrence == "weekly" and msg.get("recurrence_days"):
                    days = json.loads(msg["recurrence_days"])
                    next_date = _next_matching_day(today, days)
                else:
                    next_date = None

                if next_date:
                    await db.create_scheduled_message(
                        text=msg["text"],
                        message_type=msg.get("message_type", "custom"),
                        channel_topic_id=msg.get("channel_topic_id"),
                        target_group=msg.get("target_group", "main"),
                        scheduled_date=next_date.isoformat(),
                        scheduled_time=msg.get("scheduled_time", "09:00"),
                        recurrence=recurrence,
                        recurrence_days=msg.get("recurrence_days"),
                        auto_pin=bool(msg.get("auto_pin")),
                        created_by="recurrence",
                    )
                    logger.info("Created next occurrence for %d on %s", msg["id"], next_date)

        except Exception as e:
            await db.mark_message_failed(msg["id"], str(e))
            logger.error("Failed to send scheduled message %d: %s", msg["id"], e)
