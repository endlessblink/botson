"""Configurable weekly state-review prompt."""

import logging
from datetime import datetime

from telegram.ext import ContextTypes

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings, is_auto_blocked_on
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)


def _review_config() -> dict:
    return get_settings().get("weekly_state_review") or {}


def build_weekly_state_review(config: dict | None = None) -> str | None:
    config = config or _review_config()
    question = str(config.get("question") or "").strip()
    if not question:
        return None
    usernames = config.get("tag_usernames") or []
    if isinstance(usernames, str):
        usernames = usernames.replace("\n", ",").split(",")
    tags = [str(name).strip().lstrip("@") for name in usernames if str(name).strip()]
    if tags:
        question = f"{question}\n\n" + " ".join(f"@{name}" for name in tags)
    return question


async def send_weekly_state_review(context: ContextTypes.DEFAULT_TYPE, *, force: bool = False) -> int | None:
    config = _review_config()
    if not force and not bool(config.get("enabled", False)):
        return None
    now = datetime.now()
    if not force and is_auto_blocked_on(now.date()):
        logger.info("weekly_state_review: blackout date, skipping automatic post")
        return None
    try:
        topic_id = int(config.get("topic_id"))
    except (TypeError, ValueError):
        logger.warning("weekly_state_review: topic_id is not configured")
        return None
    text = build_weekly_state_review(config)
    if not text:
        logger.warning("weekly_state_review: question is not configured")
        return None
    db: Database = context.bot_data["db"]
    try:
        message = await safe_send(
            context.bot, db, "send_message", chat_id=GROUP_ID,
            text=text, message_thread_id=topic_id,
        )
        message_id = getattr(message, "message_id", None)
        await db.log_activity("weekly_state_review", now.date().isoformat(), target_channel=str(topic_id))
        return int(message_id or 0) or None
    except UnverifiedTopicError as error:
        logger.warning("weekly_state_review: guard refused send: %s", error)
    except Exception as error:  # noqa: BLE001
        logger.error("weekly_state_review: send failed: %s", error)
    return None
