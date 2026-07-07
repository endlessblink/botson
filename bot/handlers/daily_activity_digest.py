# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Daily announcement-channel digest of today's bot activities."""

from __future__ import annotations

import logging
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings, is_auto_blocked_on
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")

_DIGEST_TYPES = {
    "trivia_round": "🧠 טריוויה",
    "emoji_puzzle": "🧩 חידת אימוג'י",
    "facts_tidbit": "🔎 עובדה מעניינת",
    "facts_spooky": "🕯️ סיפור מסתורי",
    "free_games": "🎮 משחקים חינם",
    "weekly_roundup": "📊 סיכום שבועי",
    "weekly_leaderboard": "🏆 טבלת רמות",
}

_DETAIL_CACHE: dict[str, dict] = {}
_REMINDER_INTEREST: dict[str, dict] = {}
_RLM = "\u200f"
_LRM = "\u200e"


def _clean_title(row: dict) -> str:
    raw = str(row.get("text") or "").strip()
    # splitlines()[0] crashes on empty/whitespace text (empty list). Guard it so
    # a row with no text falls back to the message-type label instead of raising
    # and taking down the whole daily digest job.
    text = raw.splitlines()[0] if raw else ""
    mtype = str(row.get("message_type") or "")
    fallback = _DIGEST_TYPES.get(mtype, mtype)
    text = text or fallback
    return text[:80].rstrip() + ("…" if len(text) > 80 else "")


def _button_label(row: dict) -> str:
    mtype = str(row.get("message_type") or "")
    label = _DIGEST_TYPES.get(mtype, mtype)
    time_s = str(row.get("scheduled_time") or "").strip()[:5]
    return f"{_RLM}{label} · {_LRM}{time_s}{_LRM}".strip()


def _activity_label(row: dict) -> str:
    return _DIGEST_TYPES.get(str(row.get("message_type") or ""), str(row.get("message_type") or ""))


def _activity_time(row: dict) -> str:
    return str(row.get("scheduled_time") or "").strip()[:5]


def _mention_user(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    name = getattr(user, "full_name", None) or getattr(user, "first_name", None) or "מישהו"
    return f"<a href=\"tg://user?id={user.id}\">{name}</a>"


def _button_user_label(user) -> str:
    if getattr(user, "username", None):
        return f"@{user.username}"
    return getattr(user, "first_name", None) or getattr(user, "full_name", None) or "מישהו"


def _button_label_with_interest(key: str, row: dict) -> str:
    base = _button_label(row)
    entry = _REMINDER_INTEREST.get(key) or {}
    users = list((entry.get("button_users") or {}).values())
    if not users:
        return base
    names = ", ".join(users[:2])
    extra = f" +{len(users) - 2}" if len(users) > 2 else ""
    return f"✅ {base} · {names}{extra}"


def _daily_activity_keyboard_from_cache() -> InlineKeyboardMarkup | None:
    buttons = []
    for key, detail in _DETAIL_CACHE.items():
        row = detail.get("row") or {}
        buttons.append([InlineKeyboardButton(_button_label_with_interest(key, row), callback_data=key)])
    return InlineKeyboardMarkup(buttons) if buttons else None


def _reminder_delay_seconds(row: dict, *, lead_minutes: int = 15) -> float | None:
    date_s = str(row.get("scheduled_date") or datetime.now(_IL_TZ).date().isoformat())
    time_s = str(row.get("scheduled_time") or "").strip()[:5]
    try:
        activity_at = datetime.fromisoformat(f"{date_s}T{time_s}:00").replace(tzinfo=_IL_TZ)
    except ValueError:
        return None
    remind_at = activity_at - timedelta(minutes=lead_minutes)
    now = datetime.now(_IL_TZ)
    if remind_at <= now:
        remind_at = now + timedelta(seconds=10)
    return max(1.0, (remind_at - now).total_seconds())


def _game_warmup_marker(row: dict) -> str:
    if str(row.get("message_type") or "") not in {"trivia_round", "emoji_puzzle"}:
        return ""
    try:
        payload = json.loads(row.get("poll_options") or "{}")
    except (json.JSONDecodeError, TypeError):
        return ""
    return str(payload.get("warmup_marker") or "").strip()


async def _record_digest_game_rsvp(context: ContextTypes.DEFAULT_TYPE, row: dict, user) -> None:
    """Treat digest interest in a game as a real game RSVP.

    The daily digest UI says "tap activity to get a reminder", but for games it
    also shows interested users beside the activity. The launch gate must count
    those users, so persist them through the same RSVP path as the warm-up button
    and DM menu.
    """
    marker = _game_warmup_marker(row)
    if not marker or user is None:
        return
    db: Database | None = context.bot_data.get("db") if getattr(context, "bot_data", None) else None
    if db is None:
        return
    warmup = await db.get_warmup_announcement_for_marker(marker)
    if not warmup:
        logger.warning("daily_activity_digest: no warm-up row for marker %s", marker)
        return
    from .trivia_interest import record_trivia_interest, refresh_warmup_group_button
    result = await record_trivia_interest(db, context.bot, int(warmup["id"]), user)
    if result is None or result.get("closed"):
        logger.info(
            "daily_activity_digest: digest game RSVP ignored marker=%s warmup=%s result=%s",
            marker, warmup.get("id"), result,
        )
        return
    await refresh_warmup_group_button(context.bot, db, int(warmup["id"]))


def build_daily_activity_keyboard(rows: list[dict]) -> InlineKeyboardMarkup | None:
    items = [r for r in rows if r.get("status") == "scheduled" and r.get("message_type") in _DIGEST_TYPES]
    if not items:
        return None
    items.sort(key=lambda r: (str(r.get("scheduled_time") or ""), str(r.get("message_type") or "")))
    _DETAIL_CACHE.clear()
    buttons = []
    for idx, row in enumerate(items[:8]):
        key = f"daily_digest:{idx}"
        _DETAIL_CACHE[key] = {"title": _clean_title(row), "row": dict(row)}
        buttons.append([InlineKeyboardButton(_button_label(row), callback_data=key)])
    return InlineKeyboardMarkup(buttons)


def build_daily_activity_digest(rows: list[dict], *, day_label: str = "היום") -> str | None:
    items = [r for r in rows if r.get("status") == "scheduled" and r.get("message_type") in _DIGEST_TYPES]
    if not items:
        return None
    items.sort(key=lambda r: (str(r.get("scheduled_time") or ""), str(r.get("message_type") or "")))
    count = len(items)
    lines = [f"{_RLM}📅 היום בבוטסון", ""]
    lines.append(f"{_RLM}יש {count} פעילויות מתוכננות {day_label}.")
    lines.append("לחצו על פעילות כדי לקבל תזכורת מתויגת לפני שהיא מתחילה.")
    lines.append("")
    lines.append("חימומים יעלו בנפרד לפני אירועים גדולים.")
    return "\n".join(lines)


async def handle_daily_digest_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    key = query.data or ""
    detail = _DETAIL_CACHE.get(key)
    if not detail:
        await query.answer("הפעילות הזו כבר לא זמינה בתצוגה", show_alert=False)
        return
    user = update.effective_user
    if not user:
        await query.answer(detail["title"], show_alert=False)
        return
    entry = _REMINDER_INTEREST.setdefault(key, {"detail": detail, "users": {}, "job_scheduled": False})
    entry["users"][user.id] = _mention_user(user)
    entry.setdefault("button_users", {})[user.id] = _button_user_label(user)
    await _record_digest_game_rsvp(context, detail["row"], user)
    if not entry["job_scheduled"] and context.job_queue:
        delay = _reminder_delay_seconds(detail["row"])
        if delay is not None:
            chat_id = query.message.chat_id if query.message else GROUP_ID
            thread_id = getattr(query.message, "message_thread_id", None) if query.message else None
            context.job_queue.run_once(
                _send_activity_reminder,
                when=delay,
                data={"key": key, "chat_id": chat_id, "thread_id": thread_id},
                name=f"daily_digest_reminder_{key.replace(':', '_')}",
            )
            entry["job_scheduled"] = True
    try:
        await query.edit_message_reply_markup(reply_markup=_daily_activity_keyboard_from_cache())
    except Exception as e:
        logger.warning("daily_activity_digest: failed to refresh digest button: %s", e)
    await query.answer(f"סבבה, אזכיר לך לפני {_activity_label(detail['row'])}", show_alert=True)


async def _send_activity_reminder(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    key = data.get("key")
    entry = _REMINDER_INTEREST.pop(key, None)
    if not entry:
        return
    mentions = list((entry.get("users") or {}).values())
    if not mentions:
        return
    row = entry["detail"]["row"]
    label = _activity_label(row)
    time_s = _activity_time(row)
    people = " ".join(mentions)
    verb = "ביקשתם" if len(mentions) > 1 else "ביקשת"
    text = (
        f"{_RLM}⏰ תזכורת לבוטסון\n\n"
        f"{_RLM}{people}\n"
        f"{_RLM}{verb} תזכורת ל־{label}.\n"
        f"{_RLM}מתחיל ב־{_LRM}{time_s}{_LRM}."
    )
    try:
        db = context.bot_data.get("db")
        await safe_send(
            context.bot,
            db,
            "send_message",
            chat_id=int(data.get("chat_id") or GROUP_ID),
            text=text,
            parse_mode="HTML",
            message_thread_id=data.get("thread_id"),
        )
    except Exception as e:
        logger.warning("daily_activity_digest: reminder send failed: %s", e)


async def send_daily_activity_digest(context: ContextTypes.DEFAULT_TYPE, *, force: bool = False) -> int | None:
    """Send one daily digest to the welcome/updates topic, if there is content."""
    now = datetime.now(_IL_TZ)
    if not force and is_auto_blocked_on(now.date()):
        logger.info("daily_activity_digest: blackout date, skipping automatic post")
        return None

    settings = get_settings()
    cfg = (settings.get("schedule", {}) or {}).get("daily_activity_digest", {}) or {}
    if not force and cfg.get("enabled") is False:
        return None

    topic_id = (settings.get("topics", {}) or {}).get("welcome")
    if topic_id is None:
        logger.warning("daily_activity_digest: topics.welcome missing; skipping send")
        return None

    db: Database = context.bot_data["db"]
    today = now.date().isoformat()
    rows = await db.get_scheduled_messages(today, today)
    text = build_daily_activity_digest(rows)
    if not text:
        logger.info("daily_activity_digest: no bot activities today; skipping send")
        return None
    keyboard = build_daily_activity_keyboard(rows)

    try:
        msg = await safe_send(
            context.bot,
            db,
            "send_message",
            chat_id=GROUP_ID,
            text=text,
            reply_markup=keyboard,
            message_thread_id=int(topic_id),
        )
        msg_id = getattr(msg, "message_id", None)
        await db.log_activity("daily_activity_digest", today, target_channel=str(topic_id))
        return int(msg_id or 0) or None
    except UnverifiedTopicError as e:
        logger.warning("daily_activity_digest: guard refused send: %s", e)
    except Exception as e:
        logger.error("daily_activity_digest: send failed: %s", e)
    return None


def register(app):
    app.add_handler(CallbackQueryHandler(handle_daily_digest_button, pattern=r"^daily_digest:\d+$"))
