"""Trivia warm-up interest-check handler.

Handles the "🙋 אני בפנים!" inline button on trivia_warmup_rsvp messages sent to
topic 341 (מצטרפים חדשים ועדכונים). Tracks responses in trivia_interest_responses
and fires a confirmation message to the same topic when the threshold is met.
"""

import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID
from ..utils.helpers import get_display_name
from ..utils.topic_guard import safe_send

logger = logging.getLogger(__name__)

_WARMUP_TOPIC_ID = 341


async def handle_trivia_interest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a click on the אני בפנים button of a trivia warm-up message."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    try:
        scheduled_msg_id = int(query.data.split("_")[1])
    except (IndexError, ValueError):
        return

    user = update.effective_user
    if not user:
        return

    db: Database = context.bot_data["db"]
    display_name = get_display_name(user)

    await db.upsert_member(user.id, user.username, display_name)
    count, already_responded = await db.add_trivia_interest_response(
        scheduled_msg_id, user.id, display_name
    )

    # Update button to show live count
    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton(f"🙋 אני בפנים! ({count})", callback_data=query.data),
    ]])
    try:
        await query.edit_message_reply_markup(reply_markup=markup)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("trivia_interest: failed to update button: %s", e)

    if already_responded:
        return

    # Check threshold and fire confirmation exactly once when it's first crossed
    async with db._db.execute(
        "SELECT poll_options FROM scheduled_messages WHERE id=?",
        (scheduled_msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return

    try:
        payload = json.loads(row["poll_options"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}

    threshold = int(payload.get("min_ready_players") or 0)
    if threshold <= 0 or count != threshold:
        return

    game_time = str(payload.get("game_time") or "")
    theme = str(payload.get("theme_label") or "כללי").strip() or "כללי"

    time_part = f" ב-{game_time}" if game_time else ""
    confirmation = (
        f"✅ הגענו למינימום! {count} אנשים בפנים —\n"
        f"הטריוויה על {theme} תתקיים היום{time_part}.\n"
        f"כולם מוזמנים! 🎮"
    )
    try:
        await safe_send(
            context.bot,
            db,
            "send_message",
            chat_id=GROUP_ID,
            text=confirmation,
            message_thread_id=_WARMUP_TOPIC_ID,
        )
        logger.info(
            "trivia_interest: threshold %d reached for msg %d — confirmation sent to topic %d",
            threshold, scheduled_msg_id, _WARMUP_TOPIC_ID,
        )
    except Exception as e:
        logger.error("trivia_interest: failed to send confirmation: %s", e)


def register(app):
    app.add_handler(CallbackQueryHandler(handle_trivia_interest, pattern=r"^trivint_\d+$"))
