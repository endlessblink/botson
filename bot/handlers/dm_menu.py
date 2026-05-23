# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml copy.dm_menu.*). Inline literals are allowed only as explicit
# `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Private DM menu — the user-facing control surface.

A community member opens this in a 1:1 chat with the bot, via the
`t.me/<bot>?start=menu` deep link (surfaced as a button on group posts) or
`/menu`. Telegram does not let a bot DM a user first, so the deep link is the
only entry point.

The menu does two things:
  1. Lists upcoming sign-up-able games (trivia / Emoji Night warm-ups) and
     events, each with a sign-up button. Sign-up reuses the existing persistence
     layer (`db.update_event_rsvp`, `record_trivia_interest`) — no new RSVP store.
  2. Lets the user opt into DM heads-ups per activity type (games / events).
     `notify_opted_in_users` (called from calendar dispatch) DMs opted-in users
     when a matching activity publishes.
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import Forbidden
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..database.db import Database
from ..utils.config import GROUP_ID, deep_link
from ..utils.copy import load_copy
from ..utils.helpers import get_display_name
from ..utils.levels import check_level_up
from .trivia_interest import record_trivia_interest

logger = logging.getLogger(__name__)
_IL_TZ = ZoneInfo("Asia/Jerusalem")

# Preference activity types shown as toggles, and how dispatched message_types
# map to them for opt-in notifications. Kept deliberately small (games +
# events) to match what the bot can honestly act on.
PREFERENCE_TYPES = ("games", "events")
EVENT_POINTS = 3  # parity with handle_rsvp in events.py


def _msg_type_to_activity(message_type: str) -> str | None:
    """Map a dispatched scheduled_messages.message_type → preference type."""
    if message_type == "event":
        return "events"
    if message_type == "trivia_warmup_rsvp":
        return "games"
    return None


def _il_now() -> datetime:
    return datetime.now(_IL_TZ)


def _fmt_when(date_str: str, time_str: str | None) -> str:
    """Human 'DD/MM HH:MM' from ISO date + HH:MM(:SS) time; date-only if no time."""
    out = ""
    try:
        d = datetime.fromisoformat(str(date_str)).date()
        out = f"{d.day:02d}/{d.month:02d}"
    except (ValueError, TypeError):
        out = str(date_str or "")
    t = str(time_str or "").strip()
    if t:
        out = f"{out} {t[:5]}".strip()
    return out


def _is_dm(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type == "private")


def deep_link_button() -> InlineKeyboardButton | None:
    """An "open the personal DM menu" URL button, or None when no BOT_USERNAME.

    Surfaced on group posts so members can find the DM entry point — a bot
    cannot DM a user first, so this deep link is the only way in.
    """
    url = deep_link("menu")
    if not url:
        return None
    return InlineKeyboardButton(load_copy("dm_menu", "open_in_dm"), url=url)


# ── Menu rendering ───────────────────────────────────────────


def kb_labels() -> tuple[str, str]:
    """The two persistent reply-keyboard labels (read from config)."""
    return load_copy("dm_menu", "kb_upcoming"), load_copy("dm_menu", "kb_prefs")


def persistent_kb() -> ReplyKeyboardMarkup:
    """Always-visible reply keyboard pinned above the text box.

    The two top-level options live here so users never need to remember /menu —
    a tap sends the button's exact text, matched by handle_menu_text.
    """
    up, prefs = kb_labels()
    return ReplyKeyboardMarkup(
        [[up], [prefs]], resize_keyboard=True, is_persistent=True,
    )


async def _send_inline(update: Update, text: str, markup: InlineKeyboardMarkup | None):
    """Render a sub-screen: edit in place when from a button callback, send a
    fresh message when triggered from a reply-keyboard tap (a Message)."""
    query = update.callback_query
    if query:
        await query.answer()
        try:
            await query.edit_message_text(text=text, reply_markup=markup)
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning("dm_menu: failed to edit sub-screen: %s", e)
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup)


async def show_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render the top-level menu and pin the persistent keyboard. DM-only."""
    if not _is_dm(update):
        if update.message:
            await update.message.reply_text(load_copy("dm_menu", "dm_only_notice"))
        return

    user = update.effective_user
    db: Database = context.bot_data["db"]
    if user:
        await db.upsert_member(user.id, user.username, get_display_name(user))

    text = load_copy("dm_menu", "menu_title")
    # A reply keyboard can only ride on a fresh message, not an edit. The "back"
    # callback also routes here, so send a new message in both cases.
    if update.callback_query:
        await update.callback_query.answer()
    chat = update.effective_chat
    if chat:
        await context.bot.send_message(chat_id=chat.id, text=text, reply_markup=persistent_kb())


async def show_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List upcoming events + game warm-ups with sign-up buttons."""
    db: Database = context.bot_data["db"]
    now = _il_now()

    events = await db.get_upcoming_events(limit=5)
    warmups = await db.get_upcoming_scheduled_games(
        now.date().isoformat(), now.strftime("%H:%M"), limit=5
    )

    lines: list[str] = []
    rows: list[list[InlineKeyboardButton]] = []

    for ev in events:
        label = str(ev.get("title") or "").strip()
        when = _fmt_when(ev.get("event_date"), ev.get("event_time"))
        lines.append(load_copy(
            "dm_menu", "item_line",
            icon=load_copy("dm_menu", "icon_event"), label=label, when=when,
        ))
        rows.append([
            InlineKeyboardButton(load_copy("dm_menu", "btn_rsvp_yes"), callback_data=f"dmmenu_evy_{ev['id']}"),
            InlineKeyboardButton(load_copy("dm_menu", "btn_rsvp_maybe"), callback_data=f"dmmenu_evm_{ev['id']}"),
        ])

    for wu in warmups:
        try:
            payload = json.loads(wu.get("poll_options") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        label = (
            str(payload.get("activity_label") or "").strip()
            or str(payload.get("theme_label") or "").strip()
            or load_copy("dm_menu", "label_game_default")
        )
        when = _fmt_when(wu.get("scheduled_date"), wu.get("scheduled_time"))
        lines.append(load_copy(
            "dm_menu", "item_line",
            icon=load_copy("dm_menu", "icon_game"), label=label, when=when,
        ))
        # Sign up anytime: the warm-up row exists as soon as the game is
        # scheduled, so a user can RSVP from the DM before the warm-up is even
        # posted to the group. Early sign-ups are counted when the game runs
        # (same trivia_interest_responses row). record_trivia_interest skips
        # the group confirmation until the warm-up is actually live.
        rows.append([InlineKeyboardButton(
            load_copy("dm_menu", "btn_signup_interest"),
            callback_data=f"dmmenu_tr_{wu['id']}",
        )])

    if lines:
        text = load_copy("dm_menu", "upcoming_title") + "\n\n" + "\n".join(lines)
        markup = InlineKeyboardMarkup(rows)
    else:
        text = load_copy("dm_menu", "upcoming_empty")
        markup = None

    await _send_inline(update, text, markup)


def _prefs_markup(prefs: dict[str, bool]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ptype in PREFERENCE_TYPES:
        state = load_copy("dm_menu", "pref_on") if prefs.get(ptype) else load_copy("dm_menu", "pref_off")
        label = f"{state} {load_copy('dm_menu', f'pref_{ptype}')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"dmmenu_pref_{ptype}")])
    return InlineKeyboardMarkup(rows)


async def show_prefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render the per-activity notification toggles."""
    user = update.effective_user
    if not user:
        return
    db: Database = context.bot_data["db"]
    prefs = await db.get_user_preferences(user.id)
    text = load_copy("dm_menu", "prefs_title") + "\n\n" + load_copy("dm_menu", "prefs_help")
    await _send_inline(update, text, _prefs_markup(prefs))


async def toggle_pref(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Flip a single activity-type opt-in and re-render the toggle list."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    ptype = query.data[len("dmmenu_pref_"):]
    if ptype not in PREFERENCE_TYPES:
        await query.answer()
        return
    db: Database = context.bot_data["db"]
    new_state = not await db.get_activity_preference(user.id, ptype)
    await db.set_activity_preference(user.id, ptype, new_state)
    await query.answer(load_copy("dm_menu", "pref_saved"))
    prefs = await db.get_user_preferences(user.id)
    try:
        await query.edit_message_reply_markup(reply_markup=_prefs_markup(prefs))
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("dm_menu: failed to update prefs markup: %s", e)


# ── DM sign-up actions (reuse the persistence layer) ─────────


async def _handle_event_rsvp(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str):
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    try:
        event_id = int(query.data.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    db: Database = context.bot_data["db"]
    event = await db.get_event(event_id)
    if not event:
        await query.answer(load_copy("dm_menu", "signup_unavailable"), show_alert=True)
        return

    try:
        already_yes = user.id in json.loads(event.get("rsvp_yes") or "[]")
    except (json.JSONDecodeError, TypeError):
        already_yes = False

    await db.upsert_member(user.id, user.username, get_display_name(user))
    await db.update_event_rsvp(event_id, user.id, status)

    # Award points once, only on a first "yes" (parity with events.handle_rsvp,
    # without its every-click double-award).
    if status == "yes" and not already_yes:
        old_points = await db.add_points(user.id, EVENT_POINTS)
        await db.log_activity(
            "points",
            load_copy("dm_menu", "rsvp_log", points=EVENT_POINTS, name=get_display_name(user)),
            user.id,
        )
        new_level = check_level_up(old_points, old_points + EVENT_POINTS)
        if new_level:
            mention = f"[{get_display_name(user)}](tg://user?id={user.id})"
            try:
                await context.bot.send_message(
                    chat_id=GROUP_ID,
                    text=load_copy(
                        "goals", "level_up",
                        mention=mention, level=new_level["level"],
                        emoji=new_level["emoji"], tag=new_level["tag"],
                    ),
                    parse_mode="Markdown",
                )
            except Exception:
                pass

    await query.answer(load_copy("dm_menu", "signup_done"))


async def _handle_trivia_signup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    try:
        scheduled_msg_id = int(query.data.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    db: Database = context.bot_data["db"]
    result = await record_trivia_interest(db, context.bot, scheduled_msg_id, user)
    if result is None:
        await query.answer(load_copy("dm_menu", "signup_unavailable"), show_alert=True)
        return
    if result.get("closed"):
        await query.answer(load_copy("dm_menu", "signup_closed"), show_alert=True)
        return
    await query.answer(load_copy("dm_menu", "signup_done"))


# ── Callback router ──────────────────────────────────────────


async def route_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    data = query.data
    if data == "dmmenu_home":
        await show_menu(update, context)
    elif data == "dmmenu_upcoming":
        await show_upcoming(update, context)
    elif data == "dmmenu_prefs":
        await show_prefs(update, context)
    elif data.startswith("dmmenu_pref_"):
        await toggle_pref(update, context)
    elif data.startswith("dmmenu_evy_"):
        await _handle_event_rsvp(update, context, "yes")
    elif data.startswith("dmmenu_evm_"):
        await _handle_event_rsvp(update, context, "maybe")
    elif data.startswith("dmmenu_tr_"):
        await _handle_trivia_signup(update, context)
    elif data == "dmmenu_noop":
        await query.answer(load_copy("dm_menu", "signup_not_open_yet"))
    else:
        await query.answer()


# ── Opt-in DM notifications (called from calendar dispatch) ───


async def notify_opted_in_users(context, db: Database, msg: dict, event_id: int | None = None):
    """DM opted-in users a heads-up + sign-up button after an activity posts.

    Called from calendar dispatch right after a row is marked sent. Skips users
    already notified for this row (dedupe) and users who blocked the bot.
    """
    activity_type = _msg_type_to_activity(str(msg.get("message_type") or ""))
    if activity_type is None:
        return
    scheduled_msg_id = int(msg["id"])

    # Build the sign-up button per activity type, reusing the DM callbacks.
    if activity_type == "events":
        if event_id is None:
            return
        signup_cb = f"dmmenu_evy_{event_id}"
        label = str(msg.get("text") or "").splitlines()[0][:80] if msg.get("text") else ""
        body = load_copy("dm_menu", "notify_events", label=label)
    else:  # games
        signup_cb = f"dmmenu_tr_{scheduled_msg_id}"
        try:
            payload = json.loads(msg.get("poll_options") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        label = (
            str(payload.get("activity_label") or "").strip()
            or str(payload.get("theme_label") or "").strip()
            or load_copy("dm_menu", "label_game_default")
        )
        body = load_copy("dm_menu", "notify_games", label=label)

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(load_copy("dm_menu", "notify_btn_signup"), callback_data=signup_cb),
    ]])

    users = await db.list_opted_in_users(activity_type)
    sent = 0
    for user_id in users:
        if await db.was_notified(scheduled_msg_id, user_id):
            continue
        try:
            await context.bot.send_message(chat_id=user_id, text=body, reply_markup=markup)
        except Forbidden:
            # User has not started / blocked the bot — can't DM them.
            await db.mark_notified(scheduled_msg_id, user_id)
            continue
        except Exception as e:  # noqa: BLE001
            logger.warning("dm_menu: notify failed for user %s: %s", user_id, e)
            continue
        await db.mark_notified(scheduled_msg_id, user_id)
        sent += 1
    if sent:
        logger.info(
            "dm_menu: notified %d opted-in users about %s msg %d",
            sent, activity_type, scheduled_msg_id,
        )


async def handle_menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route taps on the persistent reply-keyboard buttons (which arrive as
    plain text messages) to the matching sub-screen."""
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    up, prefs = kb_labels()
    if text == up:
        await show_upcoming(update, context)
    elif text == prefs:
        await show_prefs(update, context)


def register(app):
    app.add_handler(CommandHandler("menu", show_menu))
    app.add_handler(CallbackQueryHandler(route_menu, pattern=r"^dmmenu_"))
    # Persistent reply-keyboard taps arrive as text — match the two exact
    # labels in private chat so we don't swallow any other DM message.
    up, prefs = kb_labels()
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Text([up, prefs]), handle_menu_text,
    ))
