import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.database.db import Database
from bot.handlers import community_replies
from bot.utils.config import GROUP_ID


def test_reply_trigger_requires_direct_bot_reply_or_exact_mention():
    bot_user = SimpleNamespace(id=700, is_bot=True)
    assert community_replies._is_addressed_to_bot(
        SimpleNamespace(reply_to_message=SimpleNamespace(from_user=bot_user), text="Thanks"),
        700,
        "botson_bot",
    ) == "reply"
    assert community_replies._is_addressed_to_bot(
        SimpleNamespace(reply_to_message=None, text="@Botson_Bot what do you think?"),
        700,
        "botson_bot",
    ) == "mention"
    assert community_replies._is_addressed_to_bot(
        SimpleNamespace(reply_to_message=None, text="@other_bot hello"),
        700,
        "botson_bot",
    ) is None
    assert community_replies._is_addressed_to_bot(
        SimpleNamespace(reply_to_message=SimpleNamespace(from_user=SimpleNamespace(id=701, is_bot=True)), text="Thanks"),
        700,
        "botson_bot",
    ) is None


def test_reply_receipt_claim_is_persistent_and_idempotent(tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            assert await db.claim_community_reply(GROUP_ID, 1234)
            assert not await db.claim_community_reply(GROUP_ID, 1234)
            await db.finish_community_reply(
                GROUP_ID, 1234, status="sent", reply_message_id=4321
            )
            async with db._db.execute(
                "SELECT status, reply_message_id FROM community_reply_receipts WHERE chat_id=? AND inbound_message_id=?",
                (GROUP_ID, 1234),
            ) as cursor:
                row = await cursor.fetchone()
            assert tuple(row) == ("sent", 4321)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_repeated_telegram_update_sends_only_one_reviewed_reply(tmp_path, monkeypatch):
    async def scenario():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        await db._db.execute(
            "INSERT INTO verified_forum_topics(topic_id, verified_name, category_key, verification_source) VALUES (?, ?, ?, ?)",
            (88, "verified test topic", "topic_88", "test"),
        )
        await db._db.commit()

        from_user = SimpleNamespace(id=42, is_bot=False, first_name="Ran", last_name=None, username=None)
        addressed_bot = SimpleNamespace(id=700, is_bot=True)
        source_message = SimpleNamespace(
            message_id=200,
            message_thread_id=88,
            text="תודה, זה בדיוק מה שקרה עם הכלי הזה",
            caption=None,
            from_user=from_user,
            reply_to_message=SimpleNamespace(
                message_id=199,
                from_user=addressed_bot,
                text="איזה כלי AI באמת הפתיע אתכם השבוע?",
                caption=None,
            ),
        )
        calls = []

        class FakeBot:
            id = 700
            username = "botson_bot"

            async def send_message(self, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(
                    message_id=201,
                    message_thread_id=88,
                    text=kwargs["text"],
                    date=datetime.now(timezone.utc),
                )

        context = SimpleNamespace(bot=FakeBot(), bot_data={"db": db})
        update = SimpleNamespace(
            effective_message=source_message,
            effective_user=from_user,
            effective_chat=SimpleNamespace(id=GROUP_ID),
        )
        monkeypatch.setattr(
            community_replies,
            "get_settings",
            lambda: {
                "community_replies": {
                    "enabled": True,
                    "context_hours": 24,
                    "context_messages": 10,
                    "context_max_chars": 1800,
                    "max_reply_chars": 240,
                }
            },
        )
        generator = AsyncMock(side_effect=[
            '{"decision":"reply","text":"הקטע הוא שהוא פתר את הבעיה הלא נכונה 😅"}',
            '{"pass":true,"reason":"direct and specific"}',
        ])
        monkeypatch.setattr(community_replies, "_generate_with_claude", generator)

        try:
            await community_replies.handle_addressed_message(update, context)
            await community_replies.handle_addressed_message(update, context)
            assert len(calls) == 1
            assert calls[0]["message_thread_id"] == 88
            assert calls[0]["reply_to_message_id"] == 200
            assert calls[0]["text"] == "הקטע הוא שהוא פתר את הבעיה הלא נכונה 😅"
            assert generator.await_count == 2
            recent = await db.get_recent_community_messages(
                GROUP_ID, since=datetime.now(timezone.utc).replace(year=2020)
            )
            assert any(item["message_id"] == 201 and item["source"] == "botson" for item in recent)
            async with db._db.execute(
                "SELECT status, reply_message_id FROM community_reply_receipts WHERE chat_id=? AND inbound_message_id=?",
                (GROUP_ID, 200),
            ) as cursor:
                receipt = await cursor.fetchone()
            assert tuple(receipt) == ("sent", 201)
        finally:
            await db.close()

    asyncio.run(scenario())


def test_malformed_or_non_passing_review_fails_closed():
    assert community_replies._json_object("not json") is None
    assert community_replies._json_object('["reply"]') is None
    assert community_replies._json_object('{"pass":false,"reason":"generic"}') == {
        "pass": False,
        "reason": "generic",
    }
