"""Legacy direct pool fill must not schedule Emoji Night."""

import unittest
from unittest.mock import AsyncMock, patch

from dashboard.app import _ai_fill_pool_rows_for_week
from bot.database.db import Database


class LegacyPoolFillBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_pool_fill_does_not_create_emoji_row(self):
        db = Database(":memory:")
        await db.init()
        try:
            await db.create_emoji_puzzle("🎬", "סרט", "Movie", media_type="movie")
            with patch("dashboard.app.load_yaml", return_value={"tidbit": [], "spooky": []}), \
                 patch("dashboard.app._handler_play_topic_or_error", new=AsyncMock(return_value=4037)):
                result = await _ai_fill_pool_rows_for_week(db, week_offset=1)

            assert result["by_type"] == {}
            async with db._db.execute(
                "SELECT COUNT(*) FROM scheduled_messages WHERE message_type = 'emoji_puzzle'"
            ) as cursor:
                assert (await cursor.fetchone())[0] == 0
        finally:
            await db.close()
