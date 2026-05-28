# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Trivia warm-up interest-check handler.

Handles the "🙋 אני בפנים!" inline button on trivia_warmup_rsvp messages.
Tracks responses in trivia_interest_responses and fires a confirmation message
to the executable game's topic when the threshold is met.
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID
from ..utils.config import get_settings
from ..utils.helpers import get_display_name
from ..utils.topic_guard import safe_send

logger = logging.getLogger(__name__)
_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _default_reminder_lead_minutes() -> int | None:
    reminder = (get_settings() or {}).get("reminder") or {}
    raw = reminder.get("default_game_lead_minutes")
    if raw is None:
        options = reminder.get("game_lead_options") or []
        raw = options[0] if options else None
    try:
        return max(0, int(raw)) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _format_interest_names(responses: list[dict], *, limit: int = 5) -> str:
    names = [str(row.get("display_name") or "").strip() for row in responses]
    names = [name for name in names if name]
    if not names:
        return ""
    shown = names[:limit]
    suffix = f" +{len(names) - limit}" if len(names) > limit else ""
    return ", ".join(shown) + suffix


async def _warmup_sibling_ids(db: Database, scheduled_msg_id: int) -> list[int]:
    async with db._db.execute(
        "SELECT poll_options FROM scheduled_messages WHERE id=? AND message_type='trivia_warmup_rsvp'",
        (scheduled_msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return []
    try:
        payload = json.loads(row["poll_options"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    marker = str(payload.get("warmup_marker") or "").strip()
    if not marker:
        return [int(scheduled_msg_id)]

    async with db._db.execute(
        """SELECT id, poll_options
           FROM scheduled_messages
           WHERE message_type='trivia_warmup_rsvp'
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


async def _has_marker_interest_response(db: Database, scheduled_msg_id: int, user_id: int) -> bool:
    for wid in await _warmup_sibling_ids(db, scheduled_msg_id):
        if await db.has_trivia_interest_response(wid, user_id):
            return True
    return False


async def _remove_marker_interest_response(db: Database, scheduled_msg_id: int, user_id: int) -> None:
    for wid in await _warmup_sibling_ids(db, scheduled_msg_id):
        await db.remove_trivia_interest_response(wid, user_id)


async def _interest_summary_for_button(db: Database, scheduled_msg_id: int) -> tuple[int, str]:
    async with db._db.execute(
        "SELECT status, poll_options FROM scheduled_messages WHERE id=?",
        (scheduled_msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if row:
        try:
            payload = json.loads(row["poll_options"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        marker = str(payload.get("warmup_marker") or "").strip()
        if row["status"] == "sent" and marker:
            users = await db.get_warmup_rsvp_user_map(marker)
            names = _format_interest_names(
                [{"display_name": name} for name in users.values()],
                limit=3,
            )
            return len(users), names

    responses = await db.get_trivia_interest_responses(scheduled_msg_id)
    return len(responses), _format_interest_names(responses, limit=3)


def _rsvp_closed(row, payload: dict, *, now: datetime | None = None) -> bool:
    game_time = str(payload.get("game_time") or "").strip()
    scheduled_date = str(row["scheduled_date"] or "").strip()
    if not game_time or not scheduled_date:
        return False
    try:
        game_at = datetime.fromisoformat(f"{scheduled_date} {game_time[:5]}").replace(tzinfo=_IL_TZ)
    except ValueError:
        return False
    return (now or datetime.now(_IL_TZ)) >= game_at


async def record_trivia_interest(db: Database, bot, scheduled_msg_id: int, user) -> dict | None:
    """Record an interest RSVP for a warm-up and fire the threshold confirmation.

    Single source of truth for the warm-up "I'm in" write, shared by the
    in-group button (handle_trivia_interest) and the private DM menu
    (bot/handlers/dm_menu.py). Touches no message — the caller renders its own
    UI. The threshold confirmation send is independent of the click surface, so
    it works whether the RSVP came from the group button or a DM.

    Returns None when the warm-up no longer exists, ``{"closed": True}`` when
    RSVP is closed, else ``{"count", "already", "names"}``.
    """
    async with db._db.execute(
        "SELECT scheduled_date, status, poll_options FROM scheduled_messages WHERE id=?",
        (scheduled_msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return None

    try:
        payload = json.loads(row["poll_options"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}

    # Sign up anytime: accept RSVPs for a game that is still 'scheduled' (warm-up
    # not posted yet) as well as a live 'sent' warm-up. Only a past/closed game
    # is rejected. The DM menu lets users join early; the in-group button only
    # ever runs against a 'sent' row.
    if row["status"] not in ("scheduled", "sent") or _rsvp_closed(row, payload):
        return {"closed": True}

    marker = str(payload.get("warmup_marker") or "").strip()
    threshold = int(payload.get("min_ready_players") or 0)
    aggregate_before: dict[int, str] | None = None
    if row["status"] == "sent" and marker and threshold > 0:
        aggregate_before = await db.get_warmup_rsvp_user_map(marker)

    display_name = get_display_name(user)
    await db.upsert_member(user.id, user.username, display_name)
    count, already_responded = await db.add_trivia_interest_response(
        scheduled_msg_id, user.id, display_name
    )
    if not await db.get_reminder_leads(user.id):
        default_lead = _default_reminder_lead_minutes()
        if default_lead is not None:
            await db.toggle_reminder_lead(user.id, default_lead)
    responses = await db.get_trivia_interest_responses(scheduled_msg_id)
    aggregate_after: dict[int, str] | None = None
    if row["status"] == "sent" and marker:
        aggregate_after = await db.get_warmup_rsvp_user_map(marker)
    if aggregate_after is not None:
        count = len(aggregate_after)
        names = _format_interest_names(
            [{"display_name": name} for name in aggregate_after.values()],
            limit=3,
        )
    else:
        names = _format_interest_names(responses, limit=3)
    result = {"count": count, "already": already_responded, "names": names}

    if already_responded:
        return result

    # The "we hit minimum" confirmation is posted as a reply in the warm-up
    # topic — only meaningful once the warm-up is actually live. For early
    # (status='scheduled') DM sign-ups we just record the interest; the count
    # is honored later when the warm-up posts / the game's RSVP gate runs.
    if row["status"] != "sent":
        return result

    # Check threshold and fire confirmation exactly once when it's first crossed.
    # Use the same marker-wide, user-deduped RSVP set as the launch gate so
    # duplicate warm-up rows cannot split signups and suppress confirmation.
    before_count = len(aggregate_before) if aggregate_before is not None else count - 1
    if threshold <= 0 or before_count >= threshold or count < threshold:
        return result

    game_time = str(payload.get("game_time") or "")
    from ..utils.copy import default_theme_label
    theme = str(payload.get("theme_label") or "").strip() or default_theme_label()
    activity_label = str(payload.get("activity_label") or f"הטריוויה על {theme}").strip()  # noqa: hardcoded-content (existing fallback; activity_label is config-sourced)

    time_part = f" ב-{game_time}" if game_time else ""
    confirmation = (
        f"✅ הגענו למינימום! {count} אנשים בפנים —\n"
        f"נרשמו: {names}\n"
        f"{activity_label} תתקיים היום{time_part}.\n"
        f"כולם מוזמנים! 🎮"
    )  # noqa: hardcoded-content (pre-existing confirmation copy; extraction tracked separately)
    handler = "emoji_puzzle" if ":emoji:" in marker else "trivia_round"
    try:
        routing = await db.get_handler_routing(handler)
    except Exception:
        routing = None
    warmup_topic_id = (routing or {}).get("play_topic_id")
    if not warmup_topic_id:
        logger.warning("trivia_interest: no %s routing — skipping confirmation", handler)
        return result

    try:
        await safe_send(
            bot,
            db,
            "send_message",
            chat_id=GROUP_ID,
            text=confirmation,
            message_thread_id=warmup_topic_id,
        )
        logger.info(
            "trivia_interest: threshold %d reached for msg %d — confirmation sent to topic %d",
            threshold, scheduled_msg_id, warmup_topic_id,
        )
    except Exception as e:
        logger.error("trivia_interest: failed to send confirmation: %s", e)
    return result


def _interest_button(scheduled_msg_id: int, count: int, names: str) -> InlineKeyboardButton:
    """The live-count '🙋 בפנים (N): names' button for a warm-up."""
    label = f"🙋 בפנים ({count})"  # noqa: hardcoded-content (button label; extraction follow-up)
    if names:
        label = f"{label}: {names}"
    return InlineKeyboardButton(label[:60], callback_data=f"trivint_{scheduled_msg_id}")


def _warmup_markup(scheduled_msg_id: int, count: int, names: str) -> InlineKeyboardMarkup:
    """Count button + the 'open in DM' deep-link button (when configured)."""
    rows = [[_interest_button(scheduled_msg_id, count, names)]]
    # Local import avoids a circular import (dm_menu imports this module).
    from .dm_menu import game_deep_link_button
    dl = game_deep_link_button(scheduled_msg_id)
    if dl:
        rows.append([dl])
    return InlineKeyboardMarkup(rows)


async def refresh_warmup_group_button(bot, db: Database, scheduled_msg_id: int):
    """Update the group warm-up message's count button to match the table.

    Called after a DM sign-up/off so the group display and the DM stay in sync.
    No-op if the warm-up hasn't been posted to the group yet.
    """
    async with db._db.execute(
        "SELECT sent_message_id FROM scheduled_messages WHERE id=?",
        (scheduled_msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row or not row["sent_message_id"]:
        return
    count, names = await _interest_summary_for_button(db, scheduled_msg_id)
    try:
        await bot.edit_message_reply_markup(
            chat_id=GROUP_ID, message_id=row["sent_message_id"],
            reply_markup=_warmup_markup(scheduled_msg_id, count, names),
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("trivia_interest: failed to refresh group button: %s", e)


async def handle_trivia_interest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle the user's sign-up from the group warm-up's 🙋 button.

    Already signed up → leave; otherwise → join. Mirrors the DM menu toggle so
    both surfaces stay in sync (both read/write trivia_interest_responses).
    """
    query = update.callback_query
    if not query or not query.data:
        return

    try:
        scheduled_msg_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        return

    user = update.effective_user
    if not user:
        return

    db: Database = context.bot_data["db"]

    # Toggle off if already in.
    if await _has_marker_interest_response(db, scheduled_msg_id, user.id):
        await _remove_marker_interest_response(db, scheduled_msg_id, user.id)
        await query.answer("ביטלת את ההרשמה")  # noqa: hardcoded-content (temporary fallback; copy extraction follow-up)
        count, names = await _interest_summary_for_button(db, scheduled_msg_id)
        try:
            await query.edit_message_reply_markup(
                reply_markup=_warmup_markup(scheduled_msg_id, count, names)
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                logger.warning("trivia_interest: failed to update button: %s", e)
        return

    result = await record_trivia_interest(db, context.bot, scheduled_msg_id, user)
    if result is None:
        await query.answer("ההרשמה כבר לא זמינה", show_alert=True)  # noqa: hardcoded-content (temporary fallback; copy extraction follow-up)
        return
    if result.get("closed"):
        await query.answer("ההרשמה למשחק הזה כבר נסגרה", show_alert=True)  # noqa: hardcoded-content (temporary fallback; copy extraction follow-up)
        return

    await query.answer()
    try:
        await query.edit_message_reply_markup(
            reply_markup=_warmup_markup(scheduled_msg_id, int(result["count"]), str(result["names"]))
        )
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("trivia_interest: failed to update button: %s", e)


def register(app):
    app.add_handler(CallbackQueryHandler(handle_trivia_interest, pattern=r"^trivint_\d+$"))
