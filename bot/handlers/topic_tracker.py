# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Silently tracks forum topics from incoming messages."""

import logging
from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings
from ..utils.helpers import get_display_name

logger = logging.getLogger(__name__)


async def capture_recent_community_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store only ordinary text from the configured main group for short context read-back."""
    msg = update.message
    user = getattr(update, "effective_user", None)
    if not msg or not user or msg.chat_id != GROUP_ID or getattr(user, "is_bot", False):
        return
    text = (msg.text or msg.caption or "").strip()
    if not text or text.startswith("/"):
        return

    retention_hours = int(
        (get_settings().get("bot") or {}).get("community_context_recent_hours", 24)
    )
    db: Database = context.bot_data["db"]
    try:
        await db.record_recent_community_message(
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            thread_id=msg.message_thread_id,
            sender_name=get_display_name(user),
            text=text,
            occurred_at=msg.date,
            retention_hours=retention_hours,
        )
    except Exception:
        logger.exception("topic_tracker: failed to retain recent community context")


def _auto_category_key(thread_id: int) -> str:
    return f"topic_{int(thread_id)}"


async def track_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track forum topic from any message that has a thread ID."""
    if not update.message:
        return

    msg = update.message
    await capture_recent_community_message(update, context)
    thread_id = msg.message_thread_id
    if not thread_id:
        return

    db: Database = context.bot_data["db"]

    if getattr(msg, "forum_topic_closed", None):
        await db.delete_topic(thread_id)
        logger.info("topic_tracker: removed closed forum topic thread=%s", thread_id)
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
    elif getattr(msg, "forum_topic_edited", None) and getattr(msg.forum_topic_edited, "name", None):
        topic_name = msg.forum_topic_edited.name
    elif msg.reply_to_message and msg.reply_to_message.forum_topic_created:
        topic_name = msg.reply_to_message.forum_topic_created.name

    if not topic_name:
        return

    await db.upsert_forum_topic(thread_id, topic_name)
    existing = None
    if hasattr(db, "get_verified_forum_topic_by_id"):
        existing = await db.get_verified_forum_topic_by_id(thread_id)
    await db.upsert_verified_forum_topic(
        thread_id,
        topic_name,
        (existing or {}).get("category_key") or _auto_category_key(thread_id),
        (existing or {}).get("verification_source") or "auto forum_topic_created",
    )


def register(app):
    """Register the topic tracker handler at lowest priority."""
    app.add_handler(
        MessageHandler(filters.ALL, track_topic),
        group=99,  # Very low priority, runs after everything else
    )
