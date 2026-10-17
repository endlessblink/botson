"""Members create events: tag the bot, get a personal form link, the operator approves.

A Telegram member who tags the bot (or replies to it) asking to create an event
gets a reply with a button to a short form on the dashboard server. WhatsApp
members reach the same form through Botty. Each submission is DMed to the
admins with approve / reject buttons; approval publishes it to both groups
(see bot/utils/shared_events.py).
"""
from __future__ import annotations

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.config import ADMIN_IDS, GROUP_ID, TEST_GROUP_ID
from ..utils.copy import load_copy
from ..utils.helpers import get_display_name
from ..utils.shared_events import apply_mode_command, approve_request, asks_for_event, create_request_link, is_trusted, member_cfg, reject_request, trust_requester
from ..utils.topic_guard import UnverifiedTopicError, safe_send
from .community_replies import _is_addressed_to_bot

logger = logging.getLogger(__name__)


async def handle_event_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat or user.is_bot:
        return
    db: Database = context.bot_data["db"]
    if chat.type == "private":
        # A private message works for the operator and for trusted co-admins only.
        if user.id not in ADMIN_IDS and not await is_trusted(db, "telegram", str(user.id)):
            return
        reply = apply_mode_command(message.text)
        if reply:
            await message.reply_text(reply)
            return
    elif chat.id not in (GROUP_ID, TEST_GROUP_ID) or not _is_addressed_to_bot(
            message, int(context.bot.id), getattr(context.bot, "username", None)):
        return
    if not asks_for_event(message.text, private=chat.type == "private"):
        return
    ack = str(member_cfg().get("ack_reaction") or "")
    if ack:  # "got it" the moment the message arrives; the link follows
        try:
            await message.set_reaction(ack)
        except Exception:
            logger.debug("event_requests: could not react to the message", exc_info=True)
    try:
        link = await create_request_link(db, platform="telegram", requester_id=str(user.id),
                                         requester_name=get_display_name(user))
    except Exception:
        logger.exception("event_requests: could not create a form link")
        return
    markup = None
    if link["status"] == "ok":
        markup = InlineKeyboardMarkup([[InlineKeyboardButton(link["button"], url=link["url"])]])
    try:
        await safe_send(context.bot, db, "send_message", chat_id=chat.id, text=link["text"],
                        message_thread_id=message.message_thread_id, reply_to_message_id=message.message_id,
                        reply_markup=markup)
        logger.info("event_requests: form link sent to user %s (%s)", user.id, link["status"])
    except UnverifiedTopicError as e:
        logger.warning("event_requests: guard refused reply: %s", e)
    except Exception:
        logger.exception("event_requests: reply failed")


async def handle_decision(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data or not update.effective_user:
        return
    if update.effective_user.id not in ADMIN_IDS:
        await query.answer()
        return
    approve = query.data.startswith("evreq_ok_")
    parts = query.data.split("_")
    request_id = int(parts[2])
    choice = parts[3] if len(parts) > 3 else None  # real | test | trust
    db: Database = context.bot_data["db"]
    await query.answer()
    if approve:
        try:
            result = await approve_request(context.bot, db, request_id, "test" if choice == "test" else "real")
            if choice == "trust" and result["status"] in ("ok", "partial"):
                await trust_requester(db, request_id)
        except Exception as e:
            logger.exception("event_requests: approve failed for %s", request_id)
            result = {"status": "failed", "error": str(e)[:200]}
        if result["status"] == "ok":
            note = load_copy("member_events", "approved_note")
        elif result["status"] == "partial":
            note = load_copy("member_events", "approved_partial_note", reason=result.get("whatsapp_error", ""))
        elif result["status"] == "already_decided":
            note = load_copy("member_events", "already_decided")
        else:
            note = f"⚠️ {result.get('error', '')}"
    else:
        ok = await reject_request(db, request_id)
        note = load_copy("member_events", "rejected_note" if ok else "already_decided")
    try:
        await query.edit_message_text(f"{query.message.text}\n\n{note}", reply_markup=None)
    except Exception as e:
        if "not modified" not in str(e).lower():
            logger.warning("event_requests: could not update the admin message: %s", e)


def register(app) -> None:
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_event_request), group=7)
    app.add_handler(CallbackQueryHandler(handle_decision, pattern=r"^evreq_(ok|no)_\d+(_(real|test|trust))?$"))
