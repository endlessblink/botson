# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Welcome handler for new members joining the group."""

import asyncio
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.config import get_settings, is_feature_enabled
from ..utils.copy import load_copy
from ..utils.helpers import get_display_name, is_bot_user

logger = logging.getLogger(__name__)

# Pending joins for batching
_pending_joins: list[dict] = []
_batch_task: asyncio.Task | None = None


async def _flush_pending(context: ContextTypes.DEFAULT_TYPE, chat_id: int, topic_id: int | None):
    """Send batched welcome message for accumulated joins."""
    global _pending_joins, _batch_task

    if not _pending_joins:
        return

    joins = list(_pending_joins)
    _pending_joins = []
    _batch_task = None

    # Register members in DB
    db: Database = context.bot_data["db"]
    for join in joins:
        await db.upsert_member(join["user_id"], join["username"], join["name"])
        await db.upsert_chat_member(chat_id, join["user_id"], join["username"], join["name"])
        await db.record_member_activity(chat_id, join["user_id"], "join", str(join["user_id"]))

    if topic_id is not None:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=topic_id,
                text=load_copy("welcome", "public_batch"),
            )
        except Exception:
            logger.exception("Could not post batched public welcome in topic %s", topic_id)

    for join in joins:
        name = join["name"]
        try:
            await context.bot.send_message(
                chat_id=join["user_id"], text=load_copy("welcome", "dm_single", name=name)
            )
            await context.bot.send_message(chat_id=join["user_id"], text=load_copy("welcome", "rules"))
            logger.info("Sent welcome DM to: %s (ID: %d)", name, join["user_id"])
            await db.log_activity("welcome", f"שלח הודעת ברוכים הבאים ל-{name}", join["user_id"])
        except Exception as e:
            logger.warning("Could not DM user %d: %s", join["user_id"], e)


async def handle_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new members joining the group."""
    global _batch_task

    if not update.message or not update.message.new_chat_members:
        return

    if not is_feature_enabled("welcome", update.effective_chat.id):
        return
    settings = get_settings()
    batch_window = settings.get("welcome", {}).get("batch_window_seconds", 30)
    topic_id = settings.get("topics", {}).get("welcome")

    for member in update.message.new_chat_members:
        if is_bot_user(member):
            logger.debug("Skipping bot: %s", member.username)
            continue

        name = get_display_name(member)
        _pending_joins.append({
            "user_id": member.id,
            "username": member.username,
            "name": name,
            "joined_at": datetime.now(),
        })
        logger.info("New member queued: %s (ID: %d)", name, member.id)

    # Cancel existing batch timer and start a new one
    if _batch_task and not _batch_task.done():
        _batch_task.cancel()

    chat_id = update.effective_chat.id

    async def _delayed_flush():
        await asyncio.sleep(batch_window)
        await _flush_pending(context, chat_id, topic_id)

    _batch_task = asyncio.create_task(_delayed_flush())


def register(app):
    """Register welcome handlers."""
    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, handle_new_members)
    )
