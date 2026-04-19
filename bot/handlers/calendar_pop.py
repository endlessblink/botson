"""Calendar popup demo handler.

Renders an inline-keyboard month grid where tapping a day fires a native
Telegram popup alert (`answerCallbackQuery` with show_alert=True) listing the
activity scheduled for that date — pulled live from `scheduled_messages` and
the `events` table.

Demo / option-3 prototype. Wired up so the user can decide whether the popup
pattern feels right before we build a full /calendar command with
edit-in-place day views.

Callback data format: `cal_pop_YYYY-MM-DD` (and `cal_pop_noop` for label/empty
cells). Pattern is namespaced so it can't collide with other handlers.
"""

from __future__ import annotations

import json
import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

from ..database.db import Database

CALENDAR_URL = "https://telegram-mini-app.in-theflow.com/calendar"

logger = logging.getLogger(__name__)

TYPE_META = {
    "morning":    ("🌞", "בוקר"),
    "evening":    ("🌙", "ערב"),
    "discussion": ("💬", "שיחה"),
    "weekly":     ("📊", "סיכום שבועי"),
}
HEB_DOW = ["א", "ב", "ג", "ד", "ה", "ו", "ש"]  # Sun..Sat


async def _build_popup_text(db: Database, day_iso: str) -> str:
    """Build the popup body for a given YYYY-MM-DD. Capped at ~190 chars."""
    try:
        d = date.fromisoformat(day_iso)
    except ValueError:
        return "תאריך לא חוקי"

    dow = HEB_DOW[(d.weekday() + 1) % 7]
    header = f"{d.strftime('%d/%m/%Y')} · יום {dow}"

    # Manual events (rare)
    event_lines: list[str] = []
    async with db._db.execute(
        "SELECT title, event_time, location, rsvp_yes FROM events "
        "WHERE event_date = ? AND active = 1 ORDER BY event_time",
        (day_iso,),
    ) as cursor:
        for r in await cursor.fetchall():
            title, t, loc, rsvp_json = r["title"], r["event_time"], r["location"], r["rsvp_yes"]
            bits = ["🎉"]
            if t:
                bits.append(str(t)[:5])
            bits.append(str(title))
            if loc:
                bits.append(f"@ {loc}")
            try:
                yes = len(json.loads(rsvp_json or "[]"))
                if yes:
                    bits.append(f"✅{yes}")
            except Exception:
                pass
            event_lines.append(" ".join(bits))

    # Scheduled bot prompts
    sched_lines: list[str] = []
    async with db._db.execute(
        "SELECT scheduled_time, message_type FROM scheduled_messages "
        "WHERE scheduled_date = ? AND status = 'scheduled' "
        "ORDER BY scheduled_time",
        (day_iso,),
    ) as cursor:
        for r in await cursor.fetchall():
            time_str = r["scheduled_time"] or ""
            mtype = r["message_type"]
            emoji, label = TYPE_META.get(mtype, ("📌", mtype))
            sched_lines.append(f"{emoji} {time_str[:5]} {label}")

    body_lines = event_lines + sched_lines
    if not body_lines:
        return f"{header}\n\nאין פעילות מתוכננת"

    text = header + "\n\n" + "\n".join(body_lines)

    # Telegram alert hard-caps at 200 chars
    if len(text) > 195:
        text = text[:192] + "…"
    return text


async def handle_pop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a native popup with the day's activity."""
    query = update.callback_query
    if not query or not query.data:
        return

    data = query.data
    if data == "cal_pop_noop":
        await query.answer()
        return

    if not data.startswith("cal_pop_"):
        await query.answer()
        return

    day_iso = data[len("cal_pop_"):]
    db: Database = context.bot_data.get("db")
    if db is None:
        await query.answer("שגיאת DB", show_alert=True)
        return

    try:
        text = await _build_popup_text(db, day_iso)
    except Exception as e:
        logger.exception("calendar_pop: failed to build popup for %s", day_iso)
        await query.answer(f"שגיאה: {e}", show_alert=True)
        return

    await query.answer(text=text, show_alert=True)


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """`/calendar` — reply with a button that opens the calendar Mini App.

    In groups: silent no-op. The calendar button is pinned in the welcome
    topic, so we don't need to clutter group chats with repeat replies.
    In DMs: replies with the button so users get a private one-tap link.
    """
    if not update.message:
        return
    if update.effective_chat and update.effective_chat.type != "private":
        return  # group chats use the pinned button instead
    text = (
        "📅 לוח אירועים אינטראקטיבי\n\n"
        "לחיצה על הכפתור פותחת את הלוח עם כל הפעילות בקבוצה. "
        "ניווט בין חודשים, ולחיצה על תאריך מציגה את כל הפרטים."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 פתח את הלוח", url=CALENDAR_URL),
    ]])
    await context.bot.send_message(
        chat_id=update.effective_chat.id, text=text, reply_markup=kb,
    )


def register(app):
    """Register the popup callback handler + /calendar command.

    `/calendar` is registered both as a CommandHandler (normal path) AND as a
    regex MessageHandler. The regex variant catches the case where an admin
    sends the command "as the group" (anonymous), which strips the
    BOT_COMMAND entity and makes CommandHandler ignore it.
    """
    app.add_handler(CallbackQueryHandler(handle_pop_callback, pattern=r"^cal_pop_"))
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(MessageHandler(
        filters.Regex(r"^/calendar(@\w+)?(\s|$)"),
        calendar_command,
    ))
    logger.info("calendar_pop handler registered (incl. /calendar command + regex fallback)")
