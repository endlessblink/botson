"""Full Telegram member-roster synchronization for chat-scoped features."""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


def member_sync_configured() -> bool:
    return bool(
        os.getenv("TELEGRAM_API_ID")
        and os.getenv("TELEGRAM_API_HASH")
        and os.getenv("TELEGRAM_SESSION_STRING", "").strip()
    )


async def sync_chat_members(db: Any, chat_id: int) -> int | None:
    """Refresh the stored roster from an authorized MTProto user session."""
    if not member_sync_configured():
        return None

    try:
        from telethon import TelegramClient
        from telethon.sessions import StringSession
    except ImportError:
        logger.warning("member sync unavailable: telethon is not installed")
        return None

    client = TelegramClient(
        StringSession(os.environ["TELEGRAM_SESSION_STRING"].strip()),
        int(os.environ["TELEGRAM_API_ID"]),
        os.environ["TELEGRAM_API_HASH"],
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            logger.warning("member sync unavailable: Telegram session is not authorized")
            return None
        members: list[tuple[int, int, str | None, str]] = []
        async for user in client.iter_participants(int(chat_id)):
            if getattr(user, "bot", False) or getattr(user, "deleted", False):
                continue
            first_name = (getattr(user, "first_name", None) or "").strip()
            last_name = (getattr(user, "last_name", None) or "").strip()
            display_name = " ".join(part for part in (first_name, last_name) if part)
            if not display_name:
                display_name = str(getattr(user, "id", ""))
            members.append((
                int(chat_id),
                int(user.id),
                getattr(user, "username", None),
                display_name,
            ))
        await db.upsert_chat_members(members)
        count = len(members)
        logger.info("member sync: chat=%s roster=%s", chat_id, count)
        return count
    finally:
        await client.disconnect()
