"""Silently tracks forum topics from incoming messages."""

import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from ..database.db import Database

logger = logging.getLogger(__name__)


async def track_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track forum topic from any message that has a thread ID."""
    if not update.message:
        return

    msg = update.message
    thread_id = msg.message_thread_id
    logger.debug("track_topic called: chat=%s thread=%s text=%s", msg.chat_id, thread_id, (msg.text or '')[:30])
    if not thread_id:
        return

    # Only record a topic if we have a real name from a forum_topic_created
    # service message. Previously the tracker stored "Topic {thread_id}"
    # placeholders for every unknown thread the bot saw — those accumulated
    # into dozens of unnamed chips in the scheduler's "אחר" bucket and were
    # never upgraded (forum_topic_created only fires on topic creation, not
    # on later messages). The dot-test workflow in the Settings page is the
    # canonical path for naming an existing topic; the tracker's job is just
    # to catch newly-created ones in real time.
    topic_name = None
    if msg.forum_topic_created:
        topic_name = msg.forum_topic_created.name
    elif msg.reply_to_message and msg.reply_to_message.forum_topic_created:
        topic_name = msg.reply_to_message.forum_topic_created.name

    if not topic_name:
        return

    db: Database = context.bot_data["db"]
    await db.upsert_forum_topic(thread_id, topic_name)


def register(app):
    """Register the topic tracker handler at lowest priority."""
    app.add_handler(
        MessageHandler(filters.ALL, track_topic),
        group=99,  # Very low priority, runs after everything else
    )
