"""Pin a bot message and retire the bot's own previous pin in that topic.

Only message ids the bot recorded itself are ever unpinned, so members' pins
are never touched. The ledger table is created lazily to keep this module
self-contained.
"""

import logging

logger = logging.getLogger(__name__)

_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS bot_pins (
    chat_id    INTEGER NOT NULL,
    topic_id   INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    pinned_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chat_id, topic_id, message_id)
)
"""


async def pin_replacing_previous(
    bot, db, *, chat_id, topic_id, message_id, disable_notification: bool = True
) -> None:
    """Pin ``message_id``; unpin the bot's earlier pins in the same topic."""
    topic = int(topic_id or 0)
    conn = db._db
    await conn.execute(_LEDGER_DDL)
    cur = await conn.execute(
        "SELECT message_id FROM bot_pins WHERE chat_id=? AND topic_id=? AND message_id<>?",
        (chat_id, topic, message_id),
    )
    previous = [row[0] for row in await cur.fetchall()]

    await bot.pin_chat_message(
        chat_id=chat_id, message_id=message_id, disable_notification=disable_notification
    )
    await conn.execute(
        "INSERT OR IGNORE INTO bot_pins (chat_id, topic_id, message_id) VALUES (?, ?, ?)",
        (chat_id, topic, message_id),
    )

    for old_id in previous:
        try:
            await bot.unpin_chat_message(chat_id=chat_id, message_id=old_id)
        except Exception as e:  # noqa: BLE001 - already gone or unpinned by hand
            logger.warning("Failed to unpin old bot pin %d: %s", old_id, e)
        await conn.execute(
            "DELETE FROM bot_pins WHERE chat_id=? AND topic_id=? AND message_id=?",
            (chat_id, topic, old_id),
        )
    await conn.commit()
