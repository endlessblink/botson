"""Regression tests for the private, short-retention community context feed."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from bot.database.db import Database
from dashboard.app import get_agent_community_messages
from bot.handlers import topic_tracker
from bot.utils import topic_guard


def _request(token="agent-secret-value"):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}" if token else ""},
        session={},
    )


def test_private_feed_requires_auth_and_returns_only_recent_messages(tmp_path, monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    monkeypatch.setattr("dashboard.app.GROUP_ID", -100123)

    async def scenario():
        db = Database(str(tmp_path / "community-context.db"))
        await db.init()
        try:
            now = datetime.now(timezone.utc).replace(microsecond=0)
            await db.record_recent_community_message(
                chat_id=-100123, message_id=10, thread_id=4037,
                sender_name="Lotem", text="I choose falling asleep quickly",
                occurred_at=(now - timedelta(minutes=5)).isoformat(),
            )
            await db.record_recent_community_message(
                chat_id=-100123, message_id=11, thread_id=4037,
                sender_name="Old member", text="expired context",
                occurred_at=(now - timedelta(hours=49)).isoformat(),
            )
            await db.record_recent_community_message(
                chat_id=-100999, message_id=12, thread_id=4037,
                sender_name="Other group", text="must stay private",
                occurred_at=now.isoformat(),
            )
            await db.record_recent_community_message(
                chat_id=-100123, message_id=9001, thread_id=4037,
                sender_name="Botson", source="botson", text="Botson poll follow-up",
                occurred_at=now.isoformat(),
            )
            await db._db.execute(
                """INSERT INTO scheduled_messages
                   (text, message_type, channel_topic_id, target_group, scheduled_date,
                    scheduled_time, status, sent_at, sent_message_id)
                   VALUES (?, 'custom', 4037, 'main', ?, ?, 'sent', ?, 9001)""",
                ("Botson poll follow-up", now.date().isoformat(), "12:00:00", now.astimezone(ZoneInfo("Asia/Jerusalem")).strftime("%Y-%m-%d %H:%M:%S")),
            )
            await db._db.commit()

            with pytest.raises(HTTPException) as error:
                await get_agent_community_messages(_request("wrong"), 24, 100, db)
            assert error.value.status_code == 401

            result = await get_agent_community_messages(_request(), 24, 100, db)
            assert result["retention_hours"] == 24
            assert result["messages"] == [{
                "message_id": 10,
                "thread_id": 4037,
                "sender_name": "Lotem",
                "text": "I choose falling asleep quickly",
                "occurred_at": (now - timedelta(minutes=5)).isoformat(),
                "source": "member",
            }, {
                "message_id": 9001,
                "thread_id": 4037,
                "sender_name": "Botson",
                "text": "Botson poll follow-up",
                "occurred_at": now.isoformat(),
                "source": "botson",
            }]
            async with db._db.execute(
                "SELECT chat_id, message_id FROM recent_community_messages ORDER BY message_id"
            ) as cursor:
                stored = [tuple(row) for row in await cursor.fetchall()]
            assert stored == [(-100123, 10), (-100999, 12), (-100123, 9001)]
        finally:
            await db.close()

    asyncio.run(scenario())


def test_safe_send_captures_bot_output_once_and_only_after_success(monkeypatch):
    monkeypatch.setattr(topic_guard, "GROUP_ID", -100123)
    monkeypatch.setattr(topic_guard, "TEST_GROUP_ID", -100999)

    class DB:
        def __init__(self):
            self.records = []

        async def is_verified_topic_id(self, topic_id):
            return topic_id == 4037

        async def delete_topic(self, topic_id):
            raise AssertionError("valid topic must not be deleted")

        async def record_recent_community_message(self, **kwargs):
            self.records.append(kwargs)

    class Bot:
        async def send_message(self, **kwargs):
            return SimpleNamespace(
                message_id=77,
                    message_thread_id=kwargs.get("message_thread_id"),
                date=datetime.now(timezone.utc),
                text=kwargs["text"],
            )

        async def send_poll(self, **kwargs):
            raise RuntimeError("Telegram rejected the poll")

    async def scenario():
        db = DB()
        bot = Bot()
        sent = await topic_guard.safe_send(
            bot, db, "send_message", chat_id=-100123,
            message_thread_id=4037, text="A Botson reply",
        )
        assert sent.message_id == 77
        assert db.records[0]["source"] == "botson"
        assert db.records[0]["thread_id"] == 4037
        assert db.records[0]["text"] == "A Botson reply"

        await topic_guard.safe_send(
            bot, db, "send_message", chat_id=123,
            text="private reply",
        )
        with pytest.raises(RuntimeError, match="rejected"):
            await topic_guard.safe_send(
                bot, db, "send_poll", chat_id=-100123,
                message_thread_id=4037, question="A poll?", options=["Yes", "No"],
            )
        assert len(db.records) == 1

    asyncio.run(scenario())


def test_capture_ignores_other_chats_commands_and_bots(tmp_path, monkeypatch):
    monkeypatch.setattr(topic_tracker, "GROUP_ID", -100123)
    monkeypatch.setattr(topic_tracker, "get_settings", lambda: {"bot": {"community_context_recent_hours": 24}})

    async def scenario():
        db = Database(str(tmp_path / "capture-context.db"))
        await db.init()
        context = SimpleNamespace(bot_data={"db": db})
        user = SimpleNamespace(id=7, first_name="Noa", last_name=None, username=None, is_bot=False)
        try:
            def update(*, chat_id, message_id, text, is_bot=False):
                message = SimpleNamespace(
                    chat_id=chat_id, message_id=message_id, message_thread_id=4037,
                    text=text, caption=None, date=datetime.now(timezone.utc),
                )
                sender = SimpleNamespace(**{**vars(user), "is_bot": is_bot})
                return SimpleNamespace(message=message, effective_user=sender)

            await topic_tracker.capture_recent_community_message(
                update(chat_id=-100999, message_id=1, text="other group"), context
            )
            await topic_tracker.capture_recent_community_message(
                update(chat_id=-100123, message_id=2, text="/admin hidden-value"), context
            )
            await topic_tracker.capture_recent_community_message(
                update(chat_id=-100123, message_id=3, text="bot message", is_bot=True), context
            )
            await topic_tracker.capture_recent_community_message(
                update(chat_id=-100123, message_id=4, text="A useful reply"), context
            )
            result = await db.get_recent_community_messages(
                -100123, since=datetime.now(timezone.utc) - timedelta(hours=1)
            )
            assert [(item["message_id"], item["sender_name"], item["text"]) for item in result] == [
                (4, "Noa", "A useful reply")
            ]
        finally:
            await db.close()

    asyncio.run(scenario())
