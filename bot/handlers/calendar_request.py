"""Members ask for the month's event calendar picture in Telegram.

How a member asks:
- in the group (main or test): tag the bot or reply to it with a schedule word
  ("@thebotstonbot לו״ז", "@thebotstonbot מה קורה החודש?"), or send /calendar;
- in a private chat with the bot: any message with a schedule word, or /calendar.
"Next month" words ("חודש הבא") ask for next month's picture.

In the main group the picture goes to the events topic from bot_message_routing
(`events_publish`); every send goes through safe_send. The rules (once every N
days per group, "it is right there", private daily cap, operator bypass) live in
bot/utils/calendar_share.py and are shared with WhatsApp.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.calendar_share import (
    asks_for_calendar, decide, month_for, picture, record, send_telegram_picture, share_cfg,
    telegram_caption, telegram_events_topic, telegram_message_link, text_with_link, upcoming_events, wants_next_month,
)
from ..utils.config import ADMIN_IDS, GROUP_ID, TEST_GROUP_ID
from ..utils.copy import load_copy
from ..utils.topic_guard import UnverifiedTopicError, safe_send
from .community_replies import _is_addressed_to_bot

logger = logging.getLogger(__name__)

_MEMBER_STATUSES = ("creator", "administrator", "member", "restricted")


async def _is_group_member(bot, user_id: int) -> bool | None:
    """True / False from Telegram's member list; None when it could not be read (never guess)."""
    failed = False
    for group_id in (GROUP_ID, TEST_GROUP_ID):
        if not group_id:
            continue
        try:
            member = await bot.get_chat_member(group_id, user_id)
        except Exception:
            logger.warning("calendar_request: could not read membership in %s", group_id, exc_info=True)
            failed = True
            continue
        status = str(getattr(member, "status", ""))
        if status in _MEMBER_STATUSES and (status != "restricted" or getattr(member, "is_member", True)):
            return True
    return None if failed else False


async def _answer(context, db, message, text: str, entities=None, reply_to: int | None = None) -> None:
    """A short text answer in the asker's chat and topic."""
    extra = {"entities": entities} if entities else {}
    try:
        await safe_send(context.bot, db, "send_message", chat_id=message.chat_id, text=text,
                        message_thread_id=message.message_thread_id,
                        reply_to_message_id=reply_to or message.message_id, **extra)
    except UnverifiedTopicError as e:
        logger.warning("calendar_request: guard refused the answer: %s", e)
    except Exception:
        if reply_to:  # the earlier calendar may be gone: answer the asker instead
            await _answer(context, db, message, text, entities)
        else:
            logger.exception("calendar_request: answer failed")


async def serve(update: Update, context: ContextTypes.DEFAULT_TYPE, *, next_month: bool) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    db: Database = context.bot_data["db"]
    private = chat.type == "private"
    operator = user.id in ADMIN_IDS
    ack = str(share_cfg().get("ack_reaction") or "")
    if ack:
        try:
            await message.set_reaction(ack)
        except Exception:
            logger.debug("calendar_request: could not react", exc_info=True)

    if private and not operator:
        member = await _is_group_member(context.bot, user.id)
        if member is None:
            await _answer(context, db, message, load_copy("calendar_share", "failed"))
            return
        if not member:
            await _answer(context, db, message, load_copy("calendar_share", "members_only"))
            return

    month = month_for(next_month)
    if private:
        thread = None
    elif chat.id == GROUP_ID:
        thread = await telegram_events_topic(db)
        if thread is None:
            logger.warning("calendar_request: no routing for events_publish; not posting")
            await _answer(context, db, message, load_copy("calendar_share", "failed"))
            return
    else:
        thread = message.message_thread_id  # test group: where it was asked

    try:
        d = await decide(db, platform="telegram", chat_id=str(chat.id), private=private, operator=operator,
                         user_ids=[str(user.id)], month=month)
        if d["action"] == "limit":
            await _answer(context, db, message, d["text"])
            return
        if d["action"] == "already":
            if d.get("thread_id") == message.message_thread_id:
                await _answer(context, db, message, d["text"], reply_to=int(d["message_id"]))
            else:
                text, entities = text_with_link(d["text"], load_copy("calendar_share", "link_words"),
                                                telegram_message_link(chat.id, d["message_id"]))
                await _answer(context, db, message, text, entities)
            logger.info("calendar_request: already posted in %s, pointed user %s to it", chat.id, user.id)
            return
        png = await picture(db, month)
        same_place = private or thread == message.message_thread_id
        text, entities = telegram_caption(month, await upcoming_events(db))
        sent = await send_telegram_picture(context.bot, db, chat_id=chat.id, thread_id=thread, png=png, text=text,
                                           entities=entities, reply_to=message.message_id if same_place else None)
        await record(db, platform="telegram", chat_id=str(chat.id), private=private, user_id=str(user.id),
                     operator=operator, message_id=sent.message_id, thread_id=thread, month=month, version=d["version"])
        logger.info("calendar_request: %s calendar sent to %s (thread %s) for user %s",
                    month, chat.id, thread, user.id)
        if not same_place:
            text, entities = text_with_link(load_copy("calendar_share", "posted_elsewhere"),
                                            load_copy("calendar_share", "link_words"),
                                            telegram_message_link(chat.id, sent.message_id))
            await _answer(context, db, message, text, entities)
    except UnverifiedTopicError as e:
        logger.warning("calendar_request: guard refused the calendar: %s", e)
        await _answer(context, db, message, load_copy("calendar_share", "failed"))
    except Exception:
        logger.exception("calendar_request: failed")
        await _answer(context, db, message, load_copy("calendar_share", "failed"))


async def handle_calendar_words(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat or user.is_bot:
        return
    if chat.type != "private" and (chat.id not in (GROUP_ID, TEST_GROUP_ID) or not _is_addressed_to_bot(
            message, int(context.bot.id), getattr(context.bot, "username", None))):
        return
    if not asks_for_calendar(message.text):
        return
    await serve(update, context, next_month=wants_next_month(message.text))


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or not chat or user.is_bot or not share_cfg().get("enabled"):
        return
    if chat.type != "private" and chat.id not in (GROUP_ID, TEST_GROUP_ID):
        return
    await serve(update, context, next_month=wants_next_month(message.text))


def register(app) -> None:
    app.add_handler(CommandHandler("calendar", calendar_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_calendar_words), group=6)
