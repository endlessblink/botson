"""Content calendar — checks for due messages and sends them automatically."""

import json
import logging
import os
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from ..database.db import Database
from .emoji_puzzle import send_scheduled_emoji_message
from ..utils.config import should_skip_scheduled_message
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _media_dir() -> Path:
    return Path(os.getenv("MEDIA_DIR", "./media")).resolve()


async def send_message_with_optional_cover(bot, *, db: Database, chat_id: int, text: str,
                                            message_thread_id: int | None = None,
                                            cover_path: str | None = None,
                                            bypass_verification: bool = False):
    """Send a message, as a photo with caption if cover_path is set.

    cover_path is stored relative to MEDIA_DIR (e.g. "covers/foo.png").
    Returns the sent Message object. Raises UnverifiedTopicError if the target
    fails the topic_guard check (unless bypass_verification=True).
    """
    if cover_path:
        full = _media_dir() / cover_path
        if full.exists():
            with full.open("rb") as f:
                return await safe_send(
                    bot,
                    db,
                    "send_photo",
                    chat_id=chat_id,
                    photo=f,
                    caption=text,
                    message_thread_id=message_thread_id,
                    bypass_verification=bypass_verification,
                )
        logger.warning("cover_path %s not found at %s — falling back to text", cover_path, full)
    return await safe_send(
        bot,
        db,
        "send_message",
        chat_id=chat_id,
        text=text,
        message_thread_id=message_thread_id,
        bypass_verification=bypass_verification,
    )


def _parse_poll_options(raw) -> list[str]:
    """Decode poll_options from DB (JSON string or list) into a clean list."""
    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return [str(o).strip() for o in items if str(o).strip()]


def _build_poll_markup(options: list[str]):
    """Build an InlineKeyboardMarkup with one button per option.

    Buttons are labelled "🗓️ {option}" with callback_data "poll_{index}" so the
    existing `polls.handle_poll_vote` handler auto-discovers labels and tracks
    voter names in the button text.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = [
        [InlineKeyboardButton(f"🗓️ {opt}", callback_data=f"poll_{i}")]
        for i, opt in enumerate(options)
    ]
    return InlineKeyboardMarkup(rows)


async def send_poll_message(bot, *, db: Database, chat_id: int, question: str,
                             options: list[str],
                             message_thread_id: int | None = None,
                             duration_hours: int | None = None,
                             cover_path: str | None = None,
                             bypass_verification: bool = False):
    """Send an inline-button poll. Returns the sent Message. Raises
    UnverifiedTopicError for guarded targets."""
    markup = _build_poll_markup(options)
    if cover_path:
        full = _media_dir() / cover_path
        if full.exists():
            with full.open("rb") as f:
                return await safe_send(
                    bot,
                    db,
                    "send_photo",
                    chat_id=chat_id,
                    photo=f,
                    caption=question,
                    reply_markup=markup,
                    message_thread_id=message_thread_id,
                    bypass_verification=bypass_verification,
                )
        logger.warning("poll cover_path %s not found — sending without photo", cover_path)
    return await safe_send(
        bot,
        db,
        "send_message",
        chat_id=chat_id,
        text=question,
        reply_markup=markup,
        message_thread_id=message_thread_id,
        bypass_verification=bypass_verification,
    )


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
        if should_skip_scheduled_message(msg.get("scheduled_date", ""), msg.get("created_by")):
            logger.info(
                "Skipping auto scheduled message %s on blackout date %s",
                msg.get("id"),
                msg.get("scheduled_date"),
            )
            await db.delete_scheduled_message(msg["id"])
            continue

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

        try:
            bot = Bot(bot_token)
            msg["_resolved_chat_id"] = group_id
            if msg.get("message_type", "").startswith("emoji_puzzle_"):
                sent = await send_scheduled_emoji_message(bot, db, msg)
            else:
                poll_options = _parse_poll_options(msg.get("poll_options"))
                if msg.get("message_type") == "poll" and len(poll_options) >= 2:
                    sent = await send_poll_message(
                        bot,
                        db=db,
                        chat_id=group_id,
                        question=msg["text"],
                        options=poll_options,
                        message_thread_id=msg.get("channel_topic_id"),
                        duration_hours=msg.get("poll_duration"),
                        cover_path=msg.get("cover_path"),
                    )
                else:
                    if msg.get("message_type") == "poll":
                        logger.warning(
                            "Scheduled poll %d has no valid options — sending as text",
                            msg["id"],
                        )
                    sent = await send_message_with_optional_cover(
                        bot,
                        db=db,
                        chat_id=group_id,
                        text=msg["text"],
                        message_thread_id=msg.get("channel_topic_id"),
                        cover_path=msg.get("cover_path"),
                    )

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
                        cover_path=msg.get("cover_path"),
                        poll_options=msg.get("poll_options"),
                        poll_duration=msg.get("poll_duration"),
                    )
                    logger.info("Created next occurrence for %d on %s", msg["id"], next_date)

        except Exception as e:
            await db.mark_message_failed(msg["id"], str(e))
            logger.error("Failed to send scheduled message %d: %s", msg["id"], e)
