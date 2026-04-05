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
    if not thread_id:
        return

    # Try to get topic name from forum_topic_created service message
    topic_name = None
    if msg.forum_topic_created:
        topic_name = msg.forum_topic_created.name
    elif msg.reply_to_message and msg.reply_to_message.forum_topic_created:
        topic_name = msg.reply_to_message.forum_topic_created.name

    # If we can't get the name, use a placeholder that will be updated later
    if not topic_name:
        # Check if we already know this topic
        db: Database = context.bot_data["db"]
        existing = await db.get_forum_topics()
        known_ids = {t["topic_id"] for t in existing}
        if thread_id in known_ids:
            return  # Already tracked, no new name to update
        topic_name = f"Topic {thread_id}"

    db: Database = context.bot_data["db"]
    await db.upsert_forum_topic(thread_id, topic_name)


def register(app):
    """Register the topic tracker handler at lowest priority."""
    app.add_handler(
        MessageHandler(filters.ALL, track_topic),
        group=99,  # Very low priority, runs after everything else
    )
