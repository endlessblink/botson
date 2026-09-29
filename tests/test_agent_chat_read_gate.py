"""Agents must read the actual chat before creating, editing, scheduling, or
sending group content. A schedule/activity log is not chat history."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from bot.database.db import Database
from dashboard.app import (
    _begin_agent_api_action,
    create_calendar_item,
    get_agent_community_messages,
)

TOKEN = "agent-secret-value"


def _read_request():
    return SimpleNamespace(headers={"authorization": f"Bearer {TOKEN}"}, session={})


def _mutation(*, receipt="", key="gate-key-0001", method="POST", payload=None):
    body = json.dumps(payload or {"text": "x"}).encode()
    headers = {"authorization": f"Bearer {TOKEN}", "idempotency-key": key}
    if receipt:
        headers["x-community-context-receipt"] = receipt
    return SimpleNamespace(
        headers=headers, session={}, method=method,
        url=SimpleNamespace(path="/api/calendar"),
        body=lambda: _value(body), json=lambda: _value(payload or {"text": "x"}),
        state=SimpleNamespace(),
    )


async def _value(v):
    return v


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", TOKEN)
    monkeypatch.setattr("dashboard.app.GROUP_ID", -100123)
    return tmp_path


async def _db(tmp_path):
    db = Database(str(tmp_path / "gate.db"))
    await db.init()
    return db


async def _member_message(db, message_id, minutes_ago=1):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    await db.record_recent_community_message(
        chat_id=-100123, message_id=message_id, thread_id=4037,
        sender_name="Member", text="member text",
        occurred_at=(now - timedelta(minutes=minutes_ago)).isoformat(),
    )


def test_mutation_without_chat_read_is_refused(env):
    async def scenario():
        db = await _db(env)
        try:
            with pytest.raises(HTTPException) as error:
                await _begin_agent_api_action(_mutation(), db)
            assert error.value.status_code == 428
            assert "/api/agent/community/messages" in error.value.detail
        finally:
            await db.close()

    asyncio.run(scenario())


def test_fresh_chat_read_allows_mutation(env):
    async def scenario():
        db = await _db(env)
        try:
            await _member_message(db, 1)
            read = await get_agent_community_messages(_read_request(), 24, 100, db)
            assert read["context_receipt"].startswith("v1.")
            assert await _begin_agent_api_action(_mutation(receipt=read["context_receipt"]), db) is None
        finally:
            await db.close()

    asyncio.run(scenario())


def test_new_chat_message_after_read_invalidates_receipt(env):
    async def scenario():
        db = await _db(env)
        try:
            await _member_message(db, 1, minutes_ago=5)
            read = await get_agent_community_messages(_read_request(), 24, 100, db)
            await _member_message(db, 2, minutes_ago=0)
            with pytest.raises(HTTPException) as error:
                await _begin_agent_api_action(_mutation(receipt=read["context_receipt"]), db)
            assert error.value.status_code == 409
        finally:
            await db.close()

    asyncio.run(scenario())


def test_narrow_read_cannot_hide_newer_messages(env):
    """A receipt is always bound to the full retention window."""
    async def scenario():
        db = await _db(env)
        try:
            await _member_message(db, 1, minutes_ago=5)
            await _member_message(db, 2, minutes_ago=1)
            read = await get_agent_community_messages(_read_request(), 1, 1, db)
            assert await _begin_agent_api_action(_mutation(receipt=read["context_receipt"]), db) is None
        finally:
            await db.close()

    asyncio.run(scenario())


def test_forged_or_expired_receipt_is_refused(env, monkeypatch):
    async def scenario():
        db = await _db(env)
        try:
            read = await get_agent_community_messages(_read_request(), 24, 100, db)
            parts = read["context_receipt"].split(".")
            forged = ".".join([parts[0], parts[1], parts[2], "0" * 32])
            with pytest.raises(HTTPException) as error:
                await _begin_agent_api_action(_mutation(receipt=forged), db)
            assert error.value.status_code == 428

            real_time = __import__("time").time
            monkeypatch.setattr("dashboard.app.time.time", lambda: real_time() + 3600)
            with pytest.raises(HTTPException) as error:
                await _begin_agent_api_action(_mutation(receipt=read["context_receipt"], key="gate-key-0002"), db)
            assert error.value.status_code == 428
            assert "expired" in error.value.detail
        finally:
            await db.close()

    asyncio.run(scenario())


def test_cancel_does_not_require_chat_read(env):
    async def scenario():
        db = await _db(env)
        try:
            assert await _begin_agent_api_action(_mutation(method="DELETE"), db) is None
        finally:
            await db.close()

    asyncio.run(scenario())


def test_create_endpoint_enforces_gate_before_writing(env):
    async def scenario():
        db = await _db(env)
        try:
            day = (date.today() + timedelta(days=2)).isoformat()
            payload = {"text": "draft", "scheduled_date": day, "scheduled_time": "12:00"}
            with pytest.raises(HTTPException) as error:
                await create_calendar_item(_mutation(payload=payload), db)
            assert error.value.status_code == 428
            rows = await db.get_scheduled_messages(day, day, include_cancelled=True)
            assert rows == []
        finally:
            await db.close()

    asyncio.run(scenario())
