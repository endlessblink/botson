"""Phase B: track reactions on the bot's own scheduled messages.

Telegram emits ``message_reaction`` updates as a per-user delta when
someone reacts to a message in a group where individual reactors are
visible (the default in supergroups). For each delta we:

1. Find the ``scheduled_messages`` row whose ``sent_message_id`` matches
   the reacted-to Telegram message id. Reactions on messages the bot
   didn't send through the calendar are ignored.
2. Update the per-user reactor row in ``message_reactors``.
3. Recompute the aggregate counts in ``message_engagement``.

This gives the dashboard a "did anyone see / care about this post?"
signal without needing Telegram view counts (which the Bot API doesn't
expose for group/topic messages).

Privacy: this handler only stores reactor user_ids — no message content,
no display name, no reaction emoji content beyond the bot-internal
``reaction_type`` string used for future analytics.
"""

from __future__ import annotations

import logging
from typing import Iterable

from telegram import MessageReactionUpdated, Update
from telegram.ext import Application, ContextTypes, MessageReactionHandler

from ..database.db import Database

logger = logging.getLogger(__name__)


def _first_reaction_type(reactions: Iterable) -> str | None:
    """Pick the first reaction's emoji/custom-id as a stable label.

    PTB delivers a tuple of ``ReactionType`` (emoji or custom_emoji).
    Multi-emoji reactions from one user are rare — we record only the
    first for analytics. Empty tuple → ``None`` (the user removed all
    their reactions).
    """
    for r in reactions or ():
        emoji = getattr(r, "emoji", None)
        if emoji:
            return str(emoji)
        custom_id = getattr(r, "custom_emoji_id", None)
        if custom_id:
            return f"custom:{custom_id}"
    return None


async def handle_message_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload: MessageReactionUpdated | None = update.message_reaction
    if payload is None:
        return

    user = payload.user
    if user is None:
        # Anonymous channel-style reactor — no user_id, can't track per
        # reactor. The aggregate-only `message_reaction_count` update
        # (not subscribed to here) would be the right surface for those.
        return

    chat = payload.chat
    if chat is None:
        return

    db: Database = context.bot_data.get("db")  # type: ignore[assignment]
    if db is None:
        logger.warning("[reactions] db not available in bot_data; skipping update")
        return

    telegram_message_id = int(payload.message_id)
    # Forum topics surface as message_thread_id on regular updates but
    # MessageReactionUpdated doesn't carry the thread id directly. The
    # find-by-message-id query is keyed on sent_message_id alone — that's
    # globally unique within the bot's history, so passing None for the
    # thread id is fine.
    scheduled_id = await db.find_scheduled_id_by_telegram_message(
        telegram_message_id, channel_topic_id=None
    )
    if scheduled_id is None:
        # Reaction on a message we didn't send through the calendar (a
        # member's own message, or a manual bot send). Quietly ignore —
        # don't pollute logs with normal traffic.
        return

    new_type = _first_reaction_type(payload.new_reaction)
    try:
        result = await db.record_reaction_update(
            scheduled_id,
            telegram_message_id=telegram_message_id,
            channel_topic_id=None,
            user_id=int(user.id),
            new_reaction_type=new_type,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[reactions] failed to record update for scheduled_msg_id=%s: %s",
            scheduled_id,
            e,
        )
        return

    logger.info(
        "[reactions] scheduled_msg_id=%s now reactions=%s distinct=%s",
        scheduled_id,
        result["reactions"],
        result["distinct_reactors"],
    )


def register(app: Application) -> None:
    app.add_handler(MessageReactionHandler(handle_message_reaction))
