"""Phase B: reaction tracking on bot's scheduled messages.

The handler subscribes to ``message_reaction`` updates, finds the
``scheduled_messages`` row by ``sent_message_id``, and recomputes
aggregate engagement counts from the ``message_reactors`` set.

Tests pin: add → 1, second user adds → 2, first user removes → 1,
unknown message id no-ops, idempotent re-add.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.database.db import Database
from bot.handlers.reactions import handle_message_reaction


def _ctx_with_db(db: Database) -> SimpleNamespace:
    """Mimic ContextTypes.DEFAULT_TYPE just enough for the handler."""
    return SimpleNamespace(bot_data={"db": db})


def _emoji_reaction(emoji: str) -> SimpleNamespace:
    """Stand-in for telegram.ReactionTypeEmoji — handler only reads .emoji."""
    return SimpleNamespace(emoji=emoji)


def _make_update(
    *,
    telegram_message_id: int,
    user_id: int,
    new_reaction: tuple,
    old_reaction: tuple = (),
) -> SimpleNamespace:
    """Build a minimal Update with .message_reaction shaped like PTB's payload."""
    payload = SimpleNamespace(
        message_id=telegram_message_id,
        user=SimpleNamespace(id=user_id),
        chat=SimpleNamespace(id=-1003747545764),
        new_reaction=new_reaction,
        old_reaction=old_reaction,
    )
    return SimpleNamespace(message_reaction=payload)


class ReactionTrackingPipeline(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.db = Database(":memory:")
        await self.db.init()
        # Pretend the bot already sent message id 5001 in topic 4037
        # — that's what the calendar_checker would have stored.
        self.scheduled_id = await self.db.create_scheduled_message(
            text="בוקר טוב",
            message_type="morning",
            channel_topic_id=4037,
            target_group="main",
            scheduled_date="2099-01-01",
            scheduled_time="08:00",
            recurrence=None,
            recurrence_days=None,
            auto_pin=False,
            created_by="auto",
        )
        await self.db._db.execute(
            "UPDATE scheduled_messages SET sent_message_id = ?, status = 'sent' WHERE id = ?",
            (5001, self.scheduled_id),
        )
        await self.db._db.commit()

    async def asyncTearDown(self):
        await self.db.close()

    async def test_first_reaction_increments_to_one(self):
        ctx = _ctx_with_db(self.db)
        update = _make_update(
            telegram_message_id=5001,
            user_id=42,
            new_reaction=(_emoji_reaction("👍"),),
        )
        await handle_message_reaction(update, ctx)

        row = await self.db.get_message_engagement(self.scheduled_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["reactions"], 1)
        self.assertEqual(row["distinct_reactors"], 1)

    async def test_second_user_reaction_increments_distinct(self):
        ctx = _ctx_with_db(self.db)
        await handle_message_reaction(
            _make_update(telegram_message_id=5001, user_id=42, new_reaction=(_emoji_reaction("👍"),)),
            ctx,
        )
        await handle_message_reaction(
            _make_update(telegram_message_id=5001, user_id=99, new_reaction=(_emoji_reaction("❤"),)),
            ctx,
        )
        row = await self.db.get_message_engagement(self.scheduled_id)
        self.assertEqual(row["distinct_reactors"], 2)
        self.assertEqual(row["reactions"], 2)

    async def test_user_removes_reaction_decrements_to_zero(self):
        ctx = _ctx_with_db(self.db)
        await handle_message_reaction(
            _make_update(telegram_message_id=5001, user_id=42, new_reaction=(_emoji_reaction("👍"),)),
            ctx,
        )
        # User clears their reaction → empty new_reaction tuple
        await handle_message_reaction(
            _make_update(
                telegram_message_id=5001,
                user_id=42,
                new_reaction=(),
                old_reaction=(_emoji_reaction("👍"),),
            ),
            ctx,
        )
        row = await self.db.get_message_engagement(self.scheduled_id)
        self.assertEqual(row["distinct_reactors"], 0)
        self.assertEqual(row["reactions"], 0)

    async def test_idempotent_re_add_does_not_double_count(self):
        ctx = _ctx_with_db(self.db)
        for _ in range(3):
            await handle_message_reaction(
                _make_update(telegram_message_id=5001, user_id=42, new_reaction=(_emoji_reaction("👍"),)),
                ctx,
            )
        row = await self.db.get_message_engagement(self.scheduled_id)
        self.assertEqual(row["distinct_reactors"], 1)

    async def test_user_changes_reaction_type_stays_at_one(self):
        ctx = _ctx_with_db(self.db)
        await handle_message_reaction(
            _make_update(telegram_message_id=5001, user_id=42, new_reaction=(_emoji_reaction("👍"),)),
            ctx,
        )
        await handle_message_reaction(
            _make_update(
                telegram_message_id=5001,
                user_id=42,
                new_reaction=(_emoji_reaction("❤"),),
                old_reaction=(_emoji_reaction("👍"),),
            ),
            ctx,
        )
        row = await self.db.get_message_engagement(self.scheduled_id)
        self.assertEqual(row["distinct_reactors"], 1)

    async def test_reaction_on_unknown_message_is_a_noop(self):
        ctx = _ctx_with_db(self.db)
        await handle_message_reaction(
            _make_update(telegram_message_id=99999, user_id=42, new_reaction=(_emoji_reaction("👍"),)),
            ctx,
        )
        row = await self.db.get_message_engagement(self.scheduled_id)
        self.assertIsNone(row, "unknown message id must not create rows")

    async def test_anonymous_reaction_with_no_user_is_a_noop(self):
        ctx = _ctx_with_db(self.db)
        # Anonymous group reactor → user is None
        payload = SimpleNamespace(
            message_id=5001,
            user=None,
            chat=SimpleNamespace(id=-1003747545764),
            new_reaction=(_emoji_reaction("👍"),),
            old_reaction=(),
        )
        update = SimpleNamespace(message_reaction=payload)
        await handle_message_reaction(update, ctx)
        row = await self.db.get_message_engagement(self.scheduled_id)
        self.assertIsNone(row)


class BulkReadHelper(unittest.IsolatedAsyncioTestCase):
    async def test_list_message_engagement_returns_keyed_dict(self):
        db = Database(":memory:")
        await db.init()
        try:
            # Two scheduled rows, one with reactions, one without.
            id_a = await db.create_scheduled_message(
                text="א", message_type="morning", channel_topic_id=4037,
                target_group="main", scheduled_date="2099-01-01", scheduled_time="08:00",
                recurrence=None, recurrence_days=None, auto_pin=False, created_by="auto",
            )
            id_b = await db.create_scheduled_message(
                text="ב", message_type="evening", channel_topic_id=4037,
                target_group="main", scheduled_date="2099-01-01", scheduled_time="21:00",
                recurrence=None, recurrence_days=None, auto_pin=False, created_by="auto",
            )
            await db._db.execute(
                "UPDATE scheduled_messages SET sent_message_id=? WHERE id=?",
                (5001, id_a),
            )
            await db._db.commit()
            ctx = _ctx_with_db(db)
            await handle_message_reaction(
                _make_update(telegram_message_id=5001, user_id=1, new_reaction=(_emoji_reaction("👍"),)),
                ctx,
            )

            mapped = await db.list_message_engagement([id_a, id_b])
            self.assertIn(id_a, mapped)
            self.assertNotIn(id_b, mapped, "no engagement row → not in result")
            self.assertEqual(mapped[id_a]["distinct_reactors"], 1)
        finally:
            await db.close()


if __name__ == "__main__":
    unittest.main()
