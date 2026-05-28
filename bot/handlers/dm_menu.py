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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.error import Forbidden
from telegram.ext import (
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..database.db import Database
from ..utils.config import GROUP_ID, deep_link, get_settings
from ..utils.copy import load_copy
from ..utils.helpers import get_display_name
from ..utils.levels import check_level_up
from .trivia_interest import record_trivia_interest, refresh_warmup_group_button

logger = logging.getLogger(__name__)
_IL_TZ = ZoneInfo("Asia/Jerusalem")

# Preference activity types shown as toggles, and how dispatched message_types
# map to them for opt-in notifications. Game warm-ups are intentionally not
# mapped here: users get personal reminders only after signing up for a game.
PREFERENCE_TYPES = ("events",)
EVENT_POINTS = 3  # parity with handle_rsvp in events.py


def _msg_type_to_activity(message_type: str) -> str | None:
    """Map a dispatched scheduled_messages.message_type → preference type."""
    if message_type == "event":
        return "events"
    return None


def _reminder_lead_options() -> list[int]:
    """Lead-time choices (minutes) offered in the menu. 0 = at kickoff."""
    r = (get_settings() or {}).get("reminder") or {}
    return [int(x) for x in (r.get("game_lead_options") or [0, 5, 10, 30, 60])]


def _lead_label(minutes: int) -> str:
    """Button/display label for a lead time (0 → 'at start')."""
    if minutes <= 0:
        return load_copy("dm_menu", "reminder_lead_start_btn")
    return load_copy("dm_menu", "reminder_lead_btn", minutes=minutes)


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


def game_deep_link_button(scheduled_msg_id: int) -> InlineKeyboardButton | None:
    """Open the private chat on a specific game subscription screen."""
    url = deep_link(f"game_{int(scheduled_msg_id)}")
    if not url:
        return None
    return InlineKeyboardButton(load_copy("dm_menu", "open_game_in_dm"), url=url)


def _game_label_from_payload(payload: dict) -> str:
    return (
        str(payload.get("activity_label") or "").strip()
        or str(payload.get("theme_label") or "").strip()
        or load_copy("dm_menu", "label_game_default")
    )


async def _get_warmup_payload(db: Database, scheduled_msg_id: int) -> tuple[dict | None, dict]:
    async with db._db.execute(
        "SELECT * FROM scheduled_messages WHERE id=? AND message_type='trivia_warmup_rsvp'",
        (scheduled_msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None, {}
    try:
        payload = json.loads(row["poll_options"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return dict(row), payload


async def _warmup_sibling_ids(db: Database, scheduled_msg_id: int) -> list[int]:
    """All non-cancelled warm-up rows sharing this row's warmup_marker.

    Duplicate warm-up rows are possible after retries/manual approvals. Signup,
    signoff, and display state should follow the game marker, not whichever
    duplicated public message id the user happened to click.
    """
    row, payload = await _get_warmup_payload(db, scheduled_msg_id)
    if not row:
        return []
    marker = str(payload.get("warmup_marker") or "").strip()
    if not marker:
        return [int(scheduled_msg_id)]
    async with db._db.execute(
        """SELECT id, poll_options
           FROM scheduled_messages
           WHERE message_type = 'trivia_warmup_rsvp'
             AND status != 'cancelled'
           ORDER BY id ASC"""
    ) as cur:
        rows = await cur.fetchall()
    ids: list[int] = []
    for r in rows:
        try:
            rp = json.loads(r["poll_options"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if str(rp.get("warmup_marker") or "").strip() == marker:
            ids.append(int(r["id"]))
    return ids or [int(scheduled_msg_id)]


async def _has_game_signup(db: Database, scheduled_msg_id: int, user_id: int) -> bool:
    for wid in await _warmup_sibling_ids(db, scheduled_msg_id):
        if await db.has_trivia_interest_response(wid, user_id):
            return True
    return False


async def _remove_game_signup(db: Database, scheduled_msg_id: int, user_id: int) -> None:
    for wid in await _warmup_sibling_ids(db, scheduled_msg_id):
        await db.remove_trivia_interest_response(wid, user_id)


def _game_toggle_markup(scheduled_msg_id: int, subscribed: bool) -> InlineKeyboardMarkup:
    label = (
        load_copy("dm_menu", "reminder_unsubscribe_btn")
        if subscribed else load_copy("dm_menu", "notify_btn_signup")
    )
    prefix = "dmmenu_gunsub" if subscribed else "dmmenu_gsub"
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data=f"{prefix}_{scheduled_msg_id}")]])


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


async def _build_upcoming(db: Database, user_id: int):
    """Build the (text, markup) for the upcoming list, reflecting whether
    `user_id` is already signed up to each game (so the button can toggle)."""
    now = _il_now()
    events = await db.get_upcoming_events(limit=5)
    # By DATE only (not the warm-up's post time): a warm-up posts ~an hour
    # before kickoff, so filtering on its scheduled_time wrongly drops the game
    # from the menu the moment the warm-up posts. We keep it until the real
    # kickoff (game row time) passes — filtered in Python below.
    warmups = await db.get_active_warmups(now.date().isoformat())
    # marker → game row, so we can show the real kickoff (the game's own time),
    # not the warm-up's post time. Same authoritative source as the reminder job.
    games_by_marker: dict[str, dict] = {}
    for game in await db.get_upcoming_games(now.date().isoformat()):
        try:
            gp = json.loads(game.get("poll_options") or "{}")
        except (json.JSONDecodeError, TypeError):
            gp = {}
        gm = str(gp.get("warmup_marker") or "").strip()
        if gm:
            games_by_marker[gm] = game

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

    shown_games = 0
    seen_markers: set[str] = set()
    for wu in warmups:
        if shown_games >= 5:
            break
        try:
            payload = json.loads(wu.get("poll_options") or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        # Resolve the real kickoff: linked game row (authoritative) → warm-up's
        # stored game_time → warm-up's own time. Drop items already started.
        marker = str(payload.get("warmup_marker") or "").strip()
        group_key = marker or f"row:{wu['id']}"
        if group_key in seen_markers:
            continue
        seen_markers.add(group_key)
        game = games_by_marker.get(marker) if marker else None
        if game:
            kdate, ktime = game.get("scheduled_date"), str(game.get("scheduled_time") or "")
        elif payload.get("game_time"):
            kdate, ktime = wu.get("scheduled_date"), str(payload.get("game_time"))
        else:
            kdate, ktime = wu.get("scheduled_date"), str(wu.get("scheduled_time") or "")
        try:
            kickoff = datetime.fromisoformat(f"{kdate} {ktime[:5]}").replace(tzinfo=_IL_TZ)
        except (ValueError, TypeError):
            kickoff = None
        if kickoff is not None and kickoff <= now:
            continue  # game already started / passed → don't list it

        label = _game_label_from_payload(payload)
        when = _fmt_when(kdate, ktime)
        lines.append(load_copy(
            "dm_menu", "item_line",
            icon=load_copy("dm_menu", "icon_game"), label=label, when=when,
        ))
        # Toggle button: shows the signed-up state and lets the user sign off.
        wid = int(wu["id"])
        if await _has_game_signup(db, wid, user_id):
            rows.append([InlineKeyboardButton(
                load_copy("dm_menu", "btn_signed_up"), callback_data=f"dmmenu_troff_{wid}",
            )])
        else:
            rows.append([InlineKeyboardButton(
                load_copy("dm_menu", "btn_signup_interest"), callback_data=f"dmmenu_tr_{wid}",
            )])
        shown_games += 1

    if lines:
        return load_copy("dm_menu", "upcoming_title") + "\n\n" + "\n".join(lines), InlineKeyboardMarkup(rows)
    return load_copy("dm_menu", "upcoming_empty"), None


async def show_upcoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List upcoming events + game warm-ups with sign-up/sign-off buttons."""
    user = update.effective_user
    if not user:
        return
    db: Database = context.bot_data["db"]
    text, markup = await _build_upcoming(db, user.id)
    await _send_inline(update, text, markup)


def _prefs_markup(prefs: dict[str, bool], leads: set[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for ptype in PREFERENCE_TYPES:
        state = load_copy("dm_menu", "pref_on") if prefs.get(ptype) else load_copy("dm_menu", "pref_off")
        label = f"{state} {load_copy('dm_menu', f'pref_{ptype}')}"
        rows.append([InlineKeyboardButton(label, callback_data=f"dmmenu_pref_{ptype}")])

    # Pre-game reminder lead times — MULTI-select: each option is an independent
    # on/off toggle (✅ when selected). "כבוי" clears them all. dmmenu_lead_<m>
    # toggles a minute; dmmenu_leadclear clears all.
    mark = load_copy("dm_menu", "pref_on")
    options = _reminder_lead_options()
    lead_buttons = [InlineKeyboardButton(
        (f"{mark} " if not leads else "") + load_copy("dm_menu", "reminder_off_btn"),
        callback_data="dmmenu_leadclear",
    )]
    for m in options:
        lead_buttons.append(InlineKeyboardButton(
            (f"{mark} " if m in leads else "") + _lead_label(m),
            callback_data=f"dmmenu_lead_{m}",
        ))
    # Lay out the lead buttons up to 3 per row so the keyboard stays readable.
    for i in range(0, len(lead_buttons), 3):
        rows.append(lead_buttons[i:i + 3])
    return InlineKeyboardMarkup(rows)


async def show_prefs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render the activity-type toggles + the multi-select reminder times."""
    user = update.effective_user
    if not user:
        return
    db: Database = context.bot_data["db"]
    prefs = await db.get_user_preferences(user.id)
    leads = await db.get_reminder_leads(user.id)
    text = (
        load_copy("dm_menu", "prefs_title") + "\n\n" + load_copy("dm_menu", "prefs_help")
        + "\n\n" + load_copy("dm_menu", "reminder_section")
        + "\n" + load_copy("dm_menu", "reminder_help")
    )
    await _send_inline(update, text, _prefs_markup(prefs, leads))


async def _rerender_prefs_markup(query, db: Database, user_id: int):
    prefs = await db.get_user_preferences(user_id)
    leads = await db.get_reminder_leads(user_id)
    try:
        await query.edit_message_reply_markup(reply_markup=_prefs_markup(prefs, leads))
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("dm_menu: failed to update prefs markup: %s", e)


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
    await _rerender_prefs_markup(query, db, user.id)


async def toggle_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle one pre-game reminder lead time on/off (multi-select)."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    try:
        minutes = int(query.data.rsplit("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer()
        return
    db: Database = context.bot_data["db"]
    await db.toggle_reminder_lead(user.id, minutes)
    await query.answer(load_copy("dm_menu", "reminder_saved"))
    await _rerender_prefs_markup(query, db, user.id)


async def clear_leads(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear all pre-game reminder lead times (turn reminders off)."""
    query = update.callback_query
    user = update.effective_user
    if not query or not user:
        return
    db: Database = context.bot_data["db"]
    await db.clear_reminder_leads(user.id)
    await query.answer(load_copy("dm_menu", "reminder_saved"))
    await _rerender_prefs_markup(query, db, user.id)


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


async def _rerender_upcoming(query, db: Database, user_id: int):
    """Re-draw the upcoming list in place so a sign-up/off button flips state."""
    text, markup = await _build_upcoming(db, user_id)
    try:
        await query.edit_message_text(text=text, reply_markup=markup)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("dm_menu: failed to re-render upcoming: %s", e)


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
    if await _has_game_signup(db, scheduled_msg_id, user.id):
        await query.answer(load_copy("dm_menu", "signup_done"))
        await _rerender_upcoming(query, db, user.id)
        await refresh_warmup_group_button(context.bot, db, scheduled_msg_id)
        return
    result = await record_trivia_interest(db, context.bot, scheduled_msg_id, user)
    if result is None:
        await query.answer(load_copy("dm_menu", "signup_unavailable"), show_alert=True)
        return
    if result.get("closed"):
        await query.answer(load_copy("dm_menu", "signup_closed"), show_alert=True)
        return
    await query.answer(load_copy("dm_menu", "signup_done"))
    await _rerender_upcoming(query, db, user.id)
    # Keep the group warm-up's live count in sync with this DM action.
    await refresh_warmup_group_button(context.bot, db, scheduled_msg_id)


async def _handle_trivia_signoff(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove the user's sign-up (toggle off) and re-render the list."""
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
    await _remove_game_signup(db, scheduled_msg_id, user.id)
    await query.answer(load_copy("dm_menu", "signoff_done"))
    await _rerender_upcoming(query, db, user.id)
    # Keep the group warm-up's live count in sync with this DM action.
    await refresh_warmup_group_button(context.bot, db, scheduled_msg_id)


async def show_game_subscription(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    scheduled_msg_id: int,
    *,
    subscribe: bool = False,
):
    """Private deep-link landing page for one game reminder subscription."""
    if not _is_dm(update):
        if update.message:
            await update.message.reply_text(load_copy("dm_menu", "dm_only_notice"))
        return
    user = update.effective_user
    if not user or not update.message:
        return
    db: Database = context.bot_data["db"]
    row, payload = await _get_warmup_payload(db, scheduled_msg_id)
    if not row:
        await update.message.reply_text(load_copy("dm_menu", "signup_unavailable"))
        return
    label = _game_label_from_payload(payload)
    if subscribe and not await _has_game_signup(db, scheduled_msg_id, user.id):
        result = await record_trivia_interest(db, context.bot, scheduled_msg_id, user)
        if result is None:
            await update.message.reply_text(load_copy("dm_menu", "signup_unavailable"))
            return
        if result.get("closed"):
            await update.message.reply_text(load_copy("dm_menu", "signup_closed"))
            return
        await refresh_warmup_group_button(context.bot, db, scheduled_msg_id)
    subscribed = await _has_game_signup(db, scheduled_msg_id, user.id)
    text = (
        load_copy("dm_menu", "signup_done_dm", label=label)
        if subscribed else load_copy("dm_menu", "game_dm_prompt", label=label)
    )
    await update.message.reply_text(
        text,
        reply_markup=_game_toggle_markup(scheduled_msg_id, subscribed),
    )


async def _handle_game_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    row, payload = await _get_warmup_payload(db, scheduled_msg_id)
    if not row:
        await query.answer(load_copy("dm_menu", "signup_unavailable"), show_alert=True)
        return
    label = _game_label_from_payload(payload)
    if not await _has_game_signup(db, scheduled_msg_id, user.id):
        result = await record_trivia_interest(db, context.bot, scheduled_msg_id, user)
        if result is None:
            await query.answer(load_copy("dm_menu", "signup_unavailable"), show_alert=True)
            return
        if result.get("closed"):
            await query.answer(load_copy("dm_menu", "signup_closed"), show_alert=True)
            return
    await query.answer(load_copy("dm_menu", "signup_done"))
    try:
        await query.edit_message_text(
            text=load_copy("dm_menu", "signup_done_dm", label=label),
            reply_markup=_game_toggle_markup(scheduled_msg_id, True),
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("dm_menu: failed to update game subscribe screen: %s", e)
    await refresh_warmup_group_button(context.bot, db, scheduled_msg_id)


async def _handle_game_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    await _remove_game_signup(db, scheduled_msg_id, user.id)
    await query.answer(load_copy("dm_menu", "signoff_done"))
    try:
        await query.edit_message_text(
            text=load_copy("dm_menu", "signoff_done_dm"),
            reply_markup=_game_toggle_markup(scheduled_msg_id, False),
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("dm_menu: failed to update game unsubscribe screen: %s", e)
    await refresh_warmup_group_button(context.bot, db, scheduled_msg_id)


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
    elif data == "dmmenu_leadclear":
        await clear_leads(update, context)
    elif data.startswith("dmmenu_lead_"):
        await toggle_lead(update, context)
    elif data.startswith("dmmenu_evy_"):
        await _handle_event_rsvp(update, context, "yes")
    elif data.startswith("dmmenu_evm_"):
        await _handle_event_rsvp(update, context, "maybe")
    elif data.startswith("dmmenu_troff_"):
        await _handle_trivia_signoff(update, context)
    elif data.startswith("dmmenu_gunsub_"):
        await _handle_game_unsubscribe(update, context)
    elif data.startswith("dmmenu_gsub_"):
        await _handle_game_subscribe(update, context)
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
    # Stop here so no later-group handler (antispam's catch-all in group 0,
    # levels' message tracker, etc.) also processes this menu-button text.
    raise ApplicationHandlerStop


async def send_due_game_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Per-minute job: DM each signed-up user a personal reminder before their
    game's kickoff, at EACH lead time they selected (multi-select).

    Per-lead dedupe (game_reminder_sent) means each selected lead fires once.
    Robust to a late/missed tick: any leads already past at first check are
    collapsed into a single immediate reminder (so a late sign-up with several
    leads doesn't get a burst). Users with no selected leads get nothing;
    Forbidden (blocked bot) is swallowed.
    """
    db: Database = context.bot_data["db"]
    now = _il_now()
    today = now.date().isoformat()

    # Sign-up lists live on warm-up rows. Duplicate warm-up rows can share a
    # marker after retries/manual edits, so keep all siblings and aggregate
    # responders by user instead of letting a later empty row hide real RSVPs.
    warmups_by_marker: dict[str, list[dict]] = {}
    for wu in await db.get_active_warmups(today):
        try:
            wp = json.loads(wu.get("poll_options") or "{}")
        except (json.JSONDecodeError, TypeError):
            wp = {}
        marker = str(wp.get("warmup_marker") or "").strip()
        if marker:
            warmups_by_marker.setdefault(marker, []).append({"row": wu, "payload": wp})

    # Iterate the actual game rows — their scheduled time is the real kickoff.
    for game in await db.get_upcoming_games(today):
        try:
            kickoff = datetime.fromisoformat(
                f"{game['scheduled_date']} {str(game['scheduled_time'])[:5]}"
            ).replace(tzinfo=_IL_TZ)
        except (ValueError, TypeError, KeyError):
            continue
        if kickoff <= now:
            continue  # already started / past

        try:
            gp = json.loads(game.get("poll_options") or "{}")
        except (json.JSONDecodeError, TypeError):
            gp = {}
        marker = str(gp.get("warmup_marker") or "").strip()
        warmups = warmups_by_marker.get(marker) if marker else None
        if not warmups:
            continue  # no warm-up → no sign-up list to remind

        responders_by_user: dict[int, tuple[int, dict]] = {}
        for warmup in warmups:
            warmup_id = int(warmup["row"]["id"])
            for response in await db.get_trivia_interest_responses(warmup_id):
                uid = int(response["user_id"])
                current = responders_by_user.get(uid)
                if current is None or warmup_id < current[0]:
                    responders_by_user[uid] = (warmup_id, response)
        responders = list(responders_by_user.values())
        if not responders:
            continue
        wp = warmups[0]["payload"]
        label = (
            str(wp.get("activity_label") or gp.get("activity_label") or "").strip()
            or str(wp.get("theme_label") or gp.get("theme_label") or "").strip()
            or load_copy("dm_menu", "label_game_default")
        )

        for warmup_id, r in responders:
            uid = int(r["user_id"])
            leads = await db.get_reminder_leads(uid)
            if not leads:
                continue  # this user wants no reminders
            # Leads whose moment has arrived (kickoff - lead <= now) and not yet
            # sent. On a normal timeline one becomes due per window; on a late
            # sign-up several may be due at once → collapse to one DM.
            due = [
                L for L in leads
                if now >= kickoff - timedelta(minutes=L)
                and not await db.was_game_reminded(warmup_id, uid, L)
            ]
            if not due:
                continue
            minutes_left = max(0, int((kickoff - now).total_seconds() // 60))
            if minutes_left < 2:
                body = load_copy("dm_menu", "reminder_dm_now", label=label)
            else:
                body = load_copy("dm_menu", "reminder_dm", label=label, minutes=minutes_left)
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=body,
                    reply_markup=_game_toggle_markup(warmup_id, True),
                )
            except Forbidden:
                for L in due:
                    await db.mark_game_reminded(warmup_id, uid, L)
                continue
            except Exception as e:  # noqa: BLE001
                logger.warning("dm_menu: reminder failed for user %s: %s", uid, e)
                continue
            for L in due:
                await db.mark_game_reminded(warmup_id, uid, L)


def register(app):
    app.add_handler(CommandHandler("menu", show_menu))
    app.add_handler(CallbackQueryHandler(route_menu, pattern=r"^dmmenu_"))
    # Persistent reply-keyboard taps arrive as plain text. Register in a
    # negative group so this runs BEFORE antispam's group-0 catch-all
    # (MessageHandler(filters.ALL & ~COMMAND)) — otherwise antispam consumes the
    # tap and the menu never opens. Match only the two exact labels in private
    # chat so no other DM message is touched.
    up, prefs = kb_labels()
    app.add_handler(MessageHandler(
        filters.ChatType.PRIVATE & filters.Text([up, prefs]), handle_menu_text,
    ), group=-1)
