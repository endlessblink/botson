import os
import tempfile
import unittest

from bot.database.db import Database


class ActionCooldownTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmpdir.name, "test.db"))
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()
        self.tmpdir.cleanup()

    async def test_claim_is_atomic_until_cooldown_expires(self):
        self.assertTrue(await self.db.claim_action_cooldown("welcome:chat:topic", 86400))
        self.assertFalse(await self.db.claim_action_cooldown("welcome:chat:topic", 86400))

        await self.db._db.execute(
            "UPDATE action_cooldowns SET claimed_at = '2000-01-01 00:00:00' WHERE cooldown_key = ?",
            ("welcome:chat:topic",),
        )
        await self.db._db.commit()

        self.assertTrue(await self.db.claim_action_cooldown("welcome:chat:topic", 86400))

