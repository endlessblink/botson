# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Trivia warm-up interest-check handler.

Handles the "🙋 אני בפנים!" inline button on trivia_warmup_rsvp messages.
Tracks responses in trivia_interest_responses and fires a confirmation message
to the topic defined by the trivia_warmup routing row when the threshold is met.
"""

import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID
from ..utils.helpers import get_display_name
from ..utils.topic_guard import safe_send

logger = logging.getLogger(__name__)
_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _format_interest_names(responses: list[dict], *, limit: int = 5) -> str:
    names = [str(row.get("display_name") or "").strip() for row in responses]
    names = [name for name in names if name]
    if not names:
        return ""
    shown = names[:limit]
    suffix = f" +{len(names) - limit}" if len(names) > limit else ""
    return ", ".join(shown) + suffix


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


async def handle_trivia_interest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a click on the אני בפנים button of a trivia warm-up message."""
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
    display_name = get_display_name(user)

    async with db._db.execute(
        "SELECT scheduled_date, status, poll_options FROM scheduled_messages WHERE id=?",
        (scheduled_msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        await query.answer("ההרשמה כבר לא זמינה", show_alert=True)  # noqa: hardcoded-content (temporary fallback; copy extraction follow-up)
        return

    try:
        payload = json.loads(row["poll_options"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}

    if row["status"] != "sent" or _rsvp_closed(row, payload):
        await query.answer("ההרשמה למשחק הזה כבר נסגרה", show_alert=True)  # noqa: hardcoded-content (temporary fallback; copy extraction follow-up)
        return

    await query.answer()

    await db.upsert_member(user.id, user.username, display_name)
    count, already_responded = await db.add_trivia_interest_response(
        scheduled_msg_id, user.id, display_name
    )
    responses = await db.get_trivia_interest_responses(scheduled_msg_id)
    names = _format_interest_names(responses, limit=3)
    button_label = f"🙋 בפנים ({count})"
    if names:
        button_label = f"{button_label}: {names}"

    # Update button to show live count
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(button_label[:60], callback_data=query.data),
    ]])
    try:
        await query.edit_message_reply_markup(reply_markup=markup)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("trivia_interest: failed to update button: %s", e)

    if already_responded:
        return

    # Check threshold and fire confirmation exactly once when it's first crossed
    threshold = int(payload.get("min_ready_players") or 0)
    if threshold <= 0 or count != threshold:
        return

    game_time = str(payload.get("game_time") or "")
    from ..utils.copy import default_theme_label
    theme = str(payload.get("theme_label") or "").strip() or default_theme_label()
    activity_label = str(payload.get("activity_label") or f"הטריוויה על {theme}").strip()

    time_part = f" ב-{game_time}" if game_time else ""
    confirmation = (
        f"✅ הגענו למינימום! {count} אנשים בפנים —\n"
        f"נרשמו: {names}\n"
        f"{activity_label} תתקיים היום{time_part}.\n"
        f"כולם מוזמנים! 🎮"
    )
    try:
        routing = await db.get_handler_routing("trivia_warmup")
    except Exception:
        routing = None
    warmup_topic_id = (routing or {}).get("play_topic_id")
    if not warmup_topic_id:
        logger.warning("trivia_interest: no trivia_warmup routing — skipping confirmation")
        return

    try:
        await safe_send(
            context.bot,
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


def register(app):
    app.add_handler(CallbackQueryHandler(handle_trivia_interest, pattern=r"^trivint_\d+$"))
