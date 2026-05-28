"""Periodic Telegram forum-topic reconciliation via MTProto.

Bot API updates catch topic lifecycle events while the bot is online. This sync
fills the gap after downtime by listing the current forum topics and reconciling
them with `verified_forum_topics`.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Iterable

from ..utils.config import GROUP_ID

logger = logging.getLogger(__name__)
_FORUM_TOPIC_FETCH_LIMIT = 100


@dataclass(frozen=True)
class SyncedForumTopic:
    topic_id: int
    name: str


def _auto_category_key(topic_id: int) -> str:
    return f"topic_{int(topic_id)}"


def _topic_id(raw: Any) -> int | None:
    for attr in ("id", "top_message", "topic_id"):
        value = getattr(raw, attr, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _topic_name(raw: Any) -> str:
    return str(getattr(raw, "title", None) or getattr(raw, "name", None) or "").strip()


def _iter_response_topics(response: Any) -> Iterable[Any]:
    topics = getattr(response, "topics", None)
    if topics is not None:
        return topics
    return response if isinstance(response, list) else []


def _sync_interval_seconds() -> int:
    raw = os.getenv("BOTSON_TOPIC_SYNC_INTERVAL_SECONDS", "21600")
    try:
        return max(300, int(raw))
    except (TypeError, ValueError):
        return 21600


def _sync_first_seconds() -> int:
    raw = os.getenv("BOTSON_TOPIC_SYNC_FIRST_SECONDS", "10")
    try:
        return max(10, int(raw))
    except (TypeError, ValueError):
        return 10


def topic_sync_configured() -> bool:
    return bool(os.getenv("TELEGRAM_API_ID") and os.getenv("TELEGRAM_API_HASH"))


async def fetch_forum_topics(chat_id: int = GROUP_ID) -> list[SyncedForumTopic]:
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required for topic sync")

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
        from telethon.tl.functions.messages import GetForumTopicsRequest
    except ImportError as e:
        raise RuntimeError("telethon is required for topic sync") from e

    session_string = os.getenv("TELEGRAM_SESSION_STRING", "").strip()
    if not session_string:
        raise RuntimeError("TELEGRAM_SESSION_STRING must contain an authorized user session for forum topic sync")
    session = StringSession(session_string)
    client = TelegramClient(session, int(api_id), api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("TELEGRAM_SESSION_STRING must contain an authorized user session for forum topic sync")
        channel = await client.get_input_entity(int(chat_id))
        response = await client(GetForumTopicsRequest(
            peer=channel,
            q="",
            offset_date=0,
            offset_id=0,
            offset_topic=0,
            limit=_FORUM_TOPIC_FETCH_LIMIT,
        ))
    finally:
        await client.disconnect()
    topics: list[SyncedForumTopic] = []
    for raw in _iter_response_topics(response):
        topic_id = _topic_id(raw)
        name = _topic_name(raw)
        if topic_id and name:
            topics.append(SyncedForumTopic(topic_id=topic_id, name=name))
    return topics


async def reconcile_forum_topics(
    db: Any,
    topics: list[SyncedForumTopic],
    *,
    remove_missing: bool = True,
) -> dict[str, int]:
    """Upsert current topics and remove verified topics absent from the live list."""
    live_by_id = {int(topic.topic_id): topic for topic in topics if topic.topic_id and topic.name}
    if not live_by_id:
        return {"upserted": 0, "removed": 0, "live": 0}

    existing = await db.get_verified_forum_topics()
    existing_by_id = {}
    for row in existing:
        try:
            existing_by_id[int(row.get("topic_id"))] = row
        except (TypeError, ValueError):
            continue

    upserted = 0
    for topic_id, topic in live_by_id.items():
        row = existing_by_id.get(topic_id) or {}
        await db.upsert_forum_topic(topic_id, topic.name)
        await db.upsert_verified_forum_topic(
            topic_id,
            topic.name,
            row.get("category_key") or _auto_category_key(topic_id),
            row.get("verification_source") or "mtproto forum topic sync",
        )
        upserted += 1

    removed = 0
    if remove_missing:
        for topic_id in existing_by_id:
            if topic_id not in live_by_id:
                await db.delete_topic(topic_id)
                removed += 1

    return {"upserted": upserted, "removed": removed, "live": len(live_by_id)}


async def sync_forum_topics_once(db: Any) -> dict[str, int]:
    topics = await fetch_forum_topics(GROUP_ID)
    remove_missing = len(topics) < _FORUM_TOPIC_FETCH_LIMIT
    if not remove_missing:
        logger.warning(
            "topic_sync: fetched %d topics, at API page limit; skipping missing-topic removals",
            len(topics),
        )
    result = await reconcile_forum_topics(db, topics, remove_missing=remove_missing)
    logger.info(
        "topic_sync: live=%d upserted=%d removed=%d",
        result["live"], result["upserted"], result["removed"],
    )
    return result


async def topic_sync_job(context: Any) -> None:
    db = context.bot_data.get("db") if getattr(context, "bot_data", None) else None
    if db is None:
        logger.warning("topic_sync: no db in bot_data")
        return
    try:
        await sync_forum_topics_once(db)
    except Exception as e:
        logger.warning("topic_sync: reconciliation failed: %s", e)


def register_topic_sync_job(app: Any) -> bool:
    if not app.job_queue:
        logger.warning("topic_sync: JobQueue unavailable")
        return False
    if not topic_sync_configured():
        logger.error(
            "topic_sync: TELEGRAM_API_ID/HASH missing; full automatic topic reconciliation cannot run. "
            "Bot API event tracking still handles topic events seen while online."
        )
    app.job_queue.run_repeating(
        topic_sync_job,
        interval=_sync_interval_seconds(),
        first=_sync_first_seconds(),
        name="forum_topic_sync",
    )
    logger.info("topic_sync: scheduled periodic forum topic reconciliation")
    return True
