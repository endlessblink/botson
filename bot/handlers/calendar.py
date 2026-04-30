"""Content calendar — checks for due messages and sends them automatically."""

import json
import logging
import os
import re
from types import SimpleNamespace
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from ..database.db import Database
from .emoji_puzzle import send_scheduled_emoji_message, start_emoji_night
from .trivia_round import start_scheduled_trivia_round
from .free_games import send_free_games
from .facts import send_scheduled_fact
from .levels import send_weekly_leaderboard
from .roundup import send_weekly_roundup
from ..utils.config import should_skip_scheduled_message
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _require_message_id(value, activity: str) -> int:
    message_id = int(value or 0)
    if message_id <= 0:
        raise RuntimeError(f"{activity} did not return a Telegram message_id")
    return message_id


_TRIVIA_CATEGORY_NEEDLES = (
    ("מוזיק", "מוזיקה"),
    ("סרט", "סרטים"),
    ("סדרה", "סרטים"),
    ("גיימ", "גיימינג"),
    ("ישראל", "ישראל"),
    ("מדע", "מדע"),
    ("היסטור", "היסטוריה"),
    ("גאוגר", "גאוגרפיה"),
)


def _infer_trivia_categories(text: str) -> list[str]:
    """Return the round's explicit theme as a single-element category list.

    History: an earlier version walked the entire text and took every
    matching keyword, which polluted music rounds when the announcement said
    things like "המוזיקה לאורך ההיסטוריה" — the round then mixed history
    questions in. Now we only look at the word right after "סיבוב טריוויה",
    which is what the user actually typed as the theme.
    """
    lowered = (text or "").lower()
    if not lowered:
        return []
    anchor = "סיבוב טריוויה"
    idx = lowered.find(anchor)
    window = lowered[idx + len(anchor):] if idx != -1 else lowered
    for needle, category in _TRIVIA_CATEGORY_NEEDLES:
        if needle in window:
            return [category]
    return []


def _infer_question_count(text: str, default: int = 10) -> int:
    match = re.search(r"(\d{1,2})\s*(?:שאל|חיד)", text or "")
    if not match:
        return default
    return max(1, min(20, int(match.group(1))))


def _looks_like_trivia_launch(text: str) -> bool:
    compact = (text or "").lower()
    if not ("סיבוב טריוויה" in compact or compact.startswith("🧠 טריוויה") or "trivia round" in compact):
        return False
    if "בעוד" in compact or "תזכורת" in compact or "מתחממים" in compact:
        return False
    return True


async def _coerce_due_game_row(db: Database, msg: dict, target: str) -> dict:
    """Treat natural-language scheduled game rows as executable game launches."""
    message_type = msg.get("message_type") or "custom"
    if message_type in {"trivia_round", "emoji_puzzle"}:
        return msg
    if message_type not in {"discussion", "custom", "trivia"}:
        return msg

    text = msg.get("text") or ""
    compact = text.lower()
    coerced = dict(msg)
    if _looks_like_trivia_launch(text):
        original_topic = coerced.get("channel_topic_id")
        categories = _infer_trivia_categories(text)
        payload = {
            "pre_roll_s": 30,
            "theme_label": categories[0] if categories else "כללי",
            "categories": categories,
            "question_count": _infer_question_count(text),
        }
        routing = await db.get_handler_routing("trivia_round")
        play_topic_id = routing["play_topic_id"] if routing and routing.get("play_topic_id") is not None else original_topic
        if target == "test":
            coerced["channel_topic_id"] = None
        else:
            coerced["channel_topic_id"] = play_topic_id
            if original_topic and original_topic != play_topic_id:
                payload["teaser_topic_id"] = int(original_topic)
        coerced["message_type"] = "trivia_round"
        coerced["poll_options"] = json.dumps(payload, ensure_ascii=False)
    elif "emoji night" in compact or "חידת אימוג" in compact or "חידות אימוג" in compact:
        routing = await db.get_handler_routing("emoji_puzzle")
        if target == "test":
            coerced["channel_topic_id"] = None
        elif routing and routing.get("play_topic_id") is not None:
            coerced["channel_topic_id"] = routing["play_topic_id"]
        coerced["message_type"] = "emoji_puzzle"
    return coerced


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


async def _create_event_row_from_scheduled(db: Database, msg: dict) -> int:
    """Create an `events` row for a scheduled message of type 'event'.

    The planner wizard stores the full card body in `text` (no separate
    title/description fields on scheduled_messages), so derive a title from
    the first non-empty line and keep the whole text as description for the
    dashboard events list. Location is already embedded in the text by the
    wizard, so we don't split it out here.
    """
    raw = (msg.get("text") or "").strip()
    first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    title = (first_line[:80] or "אירוע") if first_line else "אירוע"
    created_by = msg.get("created_by")
    try:
        created_by_int = int(created_by) if created_by is not None else 0
    except (TypeError, ValueError):
        created_by_int = 0
    return await db.create_event(
        title=title,
        description=raw,
        event_date=msg.get("scheduled_date") or "",
        event_time=msg.get("scheduled_time"),
        location=None,
        created_by=created_by_int,
        cover_path=msg.get("cover_path"),
        auto_pin=bool(msg.get("auto_pin")),
        topic_id=msg.get("channel_topic_id"),
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

    # Stale-drop guard: defense-in-depth against rows whose scheduled_time is
    # far in the past (bot was offline for hours, DB anomaly, etc.). Threshold
    # via settings.yaml (stale_drop_minutes, default 30). Marks row 'failed'
    # so it neither sends nor retries. Future-dated rows are unaffected.
    try:
        from bot.utils.config import get_settings as _get_settings
        stale_drop_minutes = int((_get_settings() or {}).get("stale_drop_minutes", 30) or 30)
    except Exception:
        stale_drop_minutes = 30

    for msg in due:
        if should_skip_scheduled_message(msg.get("scheduled_date", ""), msg.get("created_by")):
            logger.info(
                "Skipping auto scheduled message %s on blackout date %s",
                msg.get("id"),
                msg.get("scheduled_date"),
            )
            await db.delete_scheduled_message(msg["id"])
            continue

        # Stale-drop check — fires only for rows significantly behind now.
        if stale_drop_minutes > 0:
            try:
                sched_dt = datetime.strptime(
                    f"{msg.get('scheduled_date','')} {(msg.get('scheduled_time') or '00:00')[:5]}",
                    "%Y-%m-%d %H:%M",
                ).replace(tzinfo=_IL_TZ)
                delay_minutes = (now - sched_dt).total_seconds() / 60.0
            except (ValueError, TypeError):
                delay_minutes = 0
            if delay_minutes > stale_drop_minutes:
                logger.warning(
                    "Stale-drop: msg id=%s scheduled=%s %s was %.1f min late (threshold %d) — marking failed without send",
                    msg.get("id"), msg.get("scheduled_date"), msg.get("scheduled_time"),
                    delay_minutes, stale_drop_minutes,
                )
                await db.mark_message_failed(msg["id"], f"stale:{int(delay_minutes)}min")
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

        msg = await _coerce_due_game_row(db, msg, target)

        try:
            bot = Bot(bot_token)
            msg["_resolved_chat_id"] = group_id
            event_id_for_rsvp: int | None = None
            if msg.get("message_type") == "trivia_round":
                sent = SimpleNamespace(
                    message_id=_require_message_id(
                        await start_scheduled_trivia_round(context, msg),
                        "trivia_round",
                    )
                )
            elif msg.get("message_type") == "emoji_puzzle":
                session_id = await start_emoji_night(context, group_id, msg.get("channel_topic_id"), force=True)
                if session_id is None:
                    raise RuntimeError("Emoji Night did not start")
                sent = SimpleNamespace(message_id=session_id)
            elif msg.get("message_type") == "free_games":
                summary = await send_free_games(context, force=True)
                if not summary or int(summary.get("posted") or 0) <= 0:
                    raise RuntimeError(f"free_games did not post: {summary}")
                sent = SimpleNamespace(message_id=1)
            elif msg.get("message_type") in {"facts_tidbit", "facts_spooky"}:
                pool = msg.get("message_type", "").removeprefix("facts_")
                sent_ok = await send_scheduled_fact(
                    bot,
                    db,
                    pool=pool,
                    chat_id=group_id,
                    thread_id=msg.get("channel_topic_id"),
                )
                if not sent_ok:
                    raise RuntimeError(f"facts {pool} did not send")
                sent = SimpleNamespace(message_id=1)
            elif msg.get("message_type") == "weekly_roundup":
                sent = SimpleNamespace(
                    message_id=_require_message_id(
                        await send_weekly_roundup(context, force=True),
                        "weekly_roundup",
                    )
                )
            elif msg.get("message_type") == "weekly_leaderboard":
                sent = SimpleNamespace(
                    message_id=_require_message_id(
                        await send_weekly_leaderboard(context),
                        "weekly_leaderboard",
                    )
                )
            elif msg.get("message_type", "").startswith("emoji_puzzle_"):
                sent = await send_scheduled_emoji_message(bot, db, msg)
            elif msg.get("message_type") == "event":
                event_id_for_rsvp = await _create_event_row_from_scheduled(db, msg)
                sent = await send_message_with_optional_cover(
                    bot,
                    db=db,
                    chat_id=group_id,
                    text=msg["text"],
                    message_thread_id=msg.get("channel_topic_id"),
                    cover_path=msg.get("cover_path"),
                )
                # Attach RSVP buttons once the message exists so the bot's
                # rsvp_yes_/rsvp_maybe_ handler can update this message.
                try:
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    await bot.edit_message_reply_markup(
                        chat_id=group_id,
                        message_id=sent.message_id,
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("✅ מגיע/ה", callback_data=f"rsvp_yes_{event_id_for_rsvp}"),
                            InlineKeyboardButton("🤔 אולי", callback_data=f"rsvp_maybe_{event_id_for_rsvp}"),
                        ]]),
                    )
                except Exception as e:
                    logger.warning("[events] failed to attach RSVP buttons to %d: %s", msg["id"], e)
                # Persist message_id so the RSVP handler can edit this exact message.
                await db.update_event(event_id_for_rsvp, message_id=sent.message_id)
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
            if msg.get("auto_pin") and sent.message_id:
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
