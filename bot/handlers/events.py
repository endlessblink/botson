"""Events and activities handler."""

import json
import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, ConversationHandler,
    MessageHandler, filters,
)

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings, is_feature_enabled
from ..utils.helpers import is_admin, get_display_name
from ..utils.levels import check_level_up

logger = logging.getLogger(__name__)

# Conversation states for event creation
TITLE, DESCRIPTION, DATE, TIME, LOCATION, CONFIRM = range(6)


async def create_event_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start event creation flow. /event command."""
    if not update.effective_user or not update.message:
        return ConversationHandler.END

    if not is_feature_enabled("events", update.effective_chat.id):
        await update.message.reply_text("הפיצ'ר הזה לא פעיל כרגע")
        return ConversationHandler.END

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים ליצור אירועים")
        return ConversationHandler.END

    await update.message.reply_text("🎉 בואו ניצור אירוע!\n\nמה שם האירוע?")
    return TITLE


async def event_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive event title."""
    context.user_data["event_title"] = update.message.text
    await update.message.reply_text("מעולה! תיאור קצר של האירוע:")
    return DESCRIPTION


async def event_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive event description."""
    context.user_data["event_desc"] = update.message.text
    await update.message.reply_text("מתי האירוע? (פורמט: DD/MM/YYYY)")
    return DATE


async def event_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive event date."""
    text = update.message.text.strip()
    try:
        parts = text.split("/")
        # Convert DD/MM/YYYY to YYYY-MM-DD
        iso_date = f"{parts[2]}-{parts[1].zfill(2)}-{parts[0].zfill(2)}"
        context.user_data["event_date"] = iso_date
    except (IndexError, ValueError):
        await update.message.reply_text("פורמט לא תקין. נסו שוב: DD/MM/YYYY")
        return DATE

    await update.message.reply_text("באיזו שעה? (פורמט: HH:MM, או שלחו 'דלג')")
    return TIME


async def event_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive event time."""
    text = update.message.text.strip()
    if text in ("דלג", "skip", "-"):
        context.user_data["event_time"] = None
    else:
        context.user_data["event_time"] = text

    await update.message.reply_text("איפה האירוע? (מיקום/קישור, או שלחו 'דלג')")
    return LOCATION


async def event_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive event location."""
    text = update.message.text.strip()
    if text in ("דלג", "skip", "-"):
        context.user_data["event_location"] = None
    else:
        context.user_data["event_location"] = text

    # Show confirmation
    title = context.user_data["event_title"]
    desc = context.user_data["event_desc"]
    date_str = context.user_data["event_date"]
    time_str = context.user_data.get("event_time") or "לא צוין"
    loc = context.user_data.get("event_location") or "לא צוין"

    summary = (
        f"📋 סיכום האירוע:\n\n"
        f"📌 {title}\n"
        f"📝 {desc}\n"
        f"📅 {date_str}\n"
        f"🕐 {time_str}\n"
        f"📍 {loc}\n\n"
        f"לפרסם? (כן/לא)"
    )
    await update.message.reply_text(summary)
    return CONFIRM


async def event_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and publish event."""
    if update.message.text.strip() not in ("כן", "yes", "כ"):
        await update.message.reply_text("האירוע בוטל.")
        context.user_data.clear()
        return ConversationHandler.END

    db: Database = context.bot_data["db"]
    event_id = await db.create_event(
        title=context.user_data["event_title"],
        description=context.user_data["event_desc"],
        event_date=context.user_data["event_date"],
        event_time=context.user_data.get("event_time"),
        location=context.user_data.get("event_location"),
        created_by=update.effective_user.id,
    )

    # Build announcement message
    announcement = _format_event_message(
        event_id,
        context.user_data["event_title"],
        context.user_data["event_desc"],
        context.user_data["event_date"],
        context.user_data.get("event_time"),
        context.user_data.get("event_location"),
        [], [],
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ מגיע/ה!", callback_data=f"rsvp_yes_{event_id}"),
            InlineKeyboardButton("🤔 אולי", callback_data=f"rsvp_maybe_{event_id}"),
        ]
    ])

    # Post to general channel
    settings = get_settings()
    general_topic = settings.get("topics", {}).get("general")
    kwargs = {"chat_id": GROUP_ID, "text": announcement, "reply_markup": keyboard}
    if general_topic:
        kwargs["message_thread_id"] = general_topic

    try:
        sent = await context.bot.send_message(**kwargs)
        await db.update_event(event_id, message_id=sent.message_id)
        await update.message.reply_text(f"✅ האירוע פורסם! (ID: {event_id})")
    except Exception as e:
        logger.error("Failed to publish event: %s", e)
        await update.message.reply_text("שגיאה בפרסום האירוע")

    context.user_data.clear()
    return ConversationHandler.END


async def cancel_event_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel event creation."""
    context.user_data.clear()
    await update.message.reply_text("יצירת האירוע בוטלה.")
    return ConversationHandler.END


async def handle_rsvp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle RSVP button clicks."""
    query = update.callback_query
    if not query or not query.data:
        return

    await query.answer()
    data = query.data
    user = update.effective_user

    if data.startswith("rsvp_yes_"):
        event_id = int(data.replace("rsvp_yes_", ""))
        status = "yes"
    elif data.startswith("rsvp_maybe_"):
        event_id = int(data.replace("rsvp_maybe_", ""))
        status = "maybe"
    else:
        return

    db: Database = context.bot_data["db"]
    await db.upsert_member(user.id, user.username, get_display_name(user))
    await db.update_event_rsvp(event_id, user.id, status)

    if status == "yes":
        old_points = await db.add_points(user.id, 3)
        await db.log_activity("points", f"+3 נקודות ל-{get_display_name(user)} (אישור הגעה)", user.id)
        new_level = check_level_up(old_points, old_points + 3)
        if new_level:
            name = get_display_name(user)
            mention = f"[{name}](tg://user?id={user.id})"
            try:
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    text=f"🎉 מזל טוב {mention}! עלה/תה לרמה {new_level['level']} — {new_level['emoji']} {new_level['tag']}!",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    # Update the message with new RSVP counts.
    # Only edit the inline keyboard — NOT the text/caption — because:
    #   1. Photo+caption messages (events with covers) can't use edit_message_text;
    #      Telegram returns "Bad Request: there is no text in the message to edit".
    #      edit_message_reply_markup works for both text and photo messages.
    #   2. The button labels themselves carry the counts (✅ מגיע/ה (N) / 🤔 אולי (M)),
    #      so the user gets immediate visual confirmation of the new state.
    #   3. Avoids overwriting the original event-card text (which the dashboard
    #      authored) with the bot's own reformatted version.
    event = await db.get_event(event_id)
    if not event:
        return

    yes_list = json.loads(event["rsvp_yes"])
    maybe_list = json.loads(event["rsvp_maybe"])

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ מגיע/ה ({len(yes_list)})", callback_data=f"rsvp_yes_{event_id}"),
            InlineKeyboardButton(f"🤔 אולי ({len(maybe_list)})", callback_data=f"rsvp_maybe_{event_id}"),
        ]
    ])

    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except Exception as e:
        logger.error("Failed to update RSVP markup: %s", e)


async def events_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show upcoming events. /events command."""
    if not update.message:
        return

    db: Database = context.bot_data["db"]
    events = await db.get_upcoming_events(5)

    if not events:
        await update.message.reply_text("אין אירועים קרובים כרגע 📅")
        return

    lines = ["📅 אירועים קרובים:", ""]
    for e in events:
        time_str = f" | 🕐 {e['event_time']}" if e.get("event_time") else ""
        loc_str = f" | 📍 {e['location']}" if e.get("location") else ""
        yes_count = len(json.loads(e["rsvp_yes"]))
        lines.append(f"📌 **{e['title']}** — {e['event_date']}{time_str}{loc_str}")
        lines.append(f"   {e['description']}")
        lines.append(f"   ✅ {yes_count} מגיעים")
        lines.append("")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def send_event_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: remind about events happening tomorrow."""
    if not is_feature_enabled("events"):
        return

    from datetime import date, timedelta
    tomorrow = (date.today() + timedelta(days=1)).isoformat()

    db: Database = context.bot_data["db"]
    async with db._db.execute(
        "SELECT * FROM events WHERE active = 1 AND event_date = ?", (tomorrow,)
    ) as cursor:
        rows = await cursor.fetchall()
        events = [dict(r) for r in rows]

    if not events:
        return

    settings = get_settings()
    general_topic = settings.get("topics", {}).get("general")

    for event in events:
        yes_count = len(json.loads(event["rsvp_yes"]))
        text = (
            f"⏰ תזכורת! מחר יש אירוע:\n\n"
            f"📌 {event['title']}\n"
            f"📝 {event['description']}\n"
        )
        if event.get("event_time"):
            text += f"🕐 {event['event_time']}\n"
        if event.get("location"):
            text += f"📍 {event['location']}\n"
        text += f"\n✅ {yes_count} מגיעים"

        kwargs = {"chat_id": GROUP_ID, "text": text}
        if general_topic:
            kwargs["message_thread_id"] = general_topic

        try:
            await context.bot.send_message(**kwargs)
        except Exception as e:
            logger.error("Failed to send event reminder: %s", e)


def _format_event_message(event_id, title, description, event_date,
                          event_time, location, yes_list, maybe_list):
    """Format an event announcement message."""
    lines = [
        f"🎉 אירוע חדש!",
        f"",
        f"📌 {title}",
        f"📝 {description}",
        f"📅 {event_date}",
    ]
    if event_time:
        lines.append(f"🕐 {event_time}")
    if location:
        lines.append(f"📍 {location}")
    lines.append("")
    lines.append(f"✅ מגיעים: {len(yes_list)} | 🤔 אולי: {len(maybe_list)}")
    return "\n".join(lines)


def register(app):
    """Register event handlers."""
    # Conversation handler for event creation
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("event", create_event_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_title)],
            DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_description)],
            DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_date)],
            TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_time)],
            LOCATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_location)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, event_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel_event_creation)],
    )
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("events", events_command))
    app.add_handler(CallbackQueryHandler(handle_rsvp, pattern=r"^rsvp_"))
