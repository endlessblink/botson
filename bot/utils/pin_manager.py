"""Replace recorded general pins; weekly pins retain their separate ownership."""
import asyncio
import logging
import weakref
import fcntl
from contextlib import asynccontextmanager
from pathlib import Path

logger = logging.getLogger(__name__)
_locks = weakref.WeakKeyDictionary()
_DDL = """CREATE TABLE IF NOT EXISTS bot_pins (
chat_id INTEGER NOT NULL, topic_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
PRIMARY KEY(chat_id,topic_id,message_id))"""


@asynccontextmanager
async def ownership_lock(db, connection, chat_id, topic):
    locks = _locks.setdefault(connection, {})
    lock = locks.setdefault((chat_id, topic), asyncio.Lock())
    async with lock:
        path = getattr(db, 'db_path', None)
        if not path or path == ':memory:':
            yield
            return
        # The bot and dashboard have independent connections/processes.
        with Path(str(path)+'.pins.lock').open('a') as shared:
            while True:
                try:
                    fcntl.flock(shared, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(shared, fcntl.LOCK_UN)


async def pin_replacing_previous(bot, db, *, chat_id, topic_id, message_id,
                                 disable_notification=True):
    """Only remove our ledger entries after the replacement succeeds.

    Failures stay recorded for a later retry. Unknown/member/weekly pins are
    never discovered or removed. Serialize callers sharing this connection.
    """
    if type(message_id) is not int or message_id <= 0:
        raise ValueError('invalid pin identity')
    topic = int(topic_id or 0)
    connection = db._db
    async with ownership_lock(db, connection, chat_id, topic):
        await connection.execute(_DDL)
        async with connection.execute(
            'SELECT message_id FROM bot_pins WHERE chat_id=? AND topic_id=? AND message_id<>?',
            (chat_id, topic, message_id),
        ) as cursor:
            previous = [row[0] for row in await cursor.fetchall()]
        await bot.pin_chat_message(chat_id=chat_id, message_id=message_id,
                                   disable_notification=disable_notification)
        await connection.execute('INSERT OR IGNORE INTO bot_pins(chat_id,topic_id,message_id) VALUES (?,?,?)',
                                 (chat_id, topic, message_id))
        await connection.commit()
        for old in previous:
            try:
                await bot.unpin_chat_message(chat_id=chat_id, message_id=old)
            except Exception:
                logger.warning('General pin replacement failed for message %d; retaining ownership', old)
                continue
            await connection.execute('DELETE FROM bot_pins WHERE chat_id=? AND topic_id=? AND message_id=?',
                                     (chat_id, topic, old))
            await connection.commit()
