from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import aiosqlite

from bot.utils.pin_manager import pin_replacing_previous


class _Db:
    def __init__(self, conn):
        self._db = conn


class PinReplacementTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.conn = await aiosqlite.connect(":memory:")
        self.addAsyncCleanup(self.conn.close)
        self.db = _Db(self.conn)
        self.bot = AsyncMock()

    async def _pin(self, topic, message_id):
        await pin_replacing_previous(
            self.bot, self.db, chat_id=-100, topic_id=topic, message_id=message_id
        )

    async def test_new_pin_unpins_only_the_bots_previous_pin_in_same_topic(self):
        await self._pin(7, 10)
        self.bot.unpin_chat_message.assert_not_called()
        await self._pin(7, 11)
        self.bot.unpin_chat_message.assert_awaited_once_with(chat_id=-100, message_id=10)

    async def test_other_topics_are_left_alone(self):
        await self._pin(7, 10)
        await self._pin(54, 20)
        self.bot.unpin_chat_message.assert_not_called()

    async def test_unpin_failure_does_not_break_the_new_pin(self):
        await self._pin(7, 10)
        self.bot.unpin_chat_message.side_effect = RuntimeError("gone")
        await self._pin(7, 11)
        self.bot.pin_chat_message.assert_awaited_with(
            chat_id=-100, message_id=11, disable_notification=True
        )
        cur = await self.conn.execute("SELECT message_id FROM bot_pins")
        self.assertEqual([r[0] for r in await cur.fetchall()], [11])
