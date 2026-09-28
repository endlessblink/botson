"""Regression coverage for Botson's browserless calendar control API."""

import asyncio
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from bot.database.db import Database
from dashboard.app import (
    _begin_agent_api_action,
    _complete_agent_api_action,
    _is_agent_api_request,
    _require_calendar_api_auth,
    create_calendar_item,
)


def make_request(*, token="", key="", session=None, body=b'{"text":"hello"}', payload=None):
    return SimpleNamespace(
        headers={
            "authorization": f"Bearer {token}" if token else "",
            "idempotency-key": key,
        },
        session=session or {},
        method="POST",
        url=SimpleNamespace(path="/api/calendar"),
        body=lambda: _body(body),
        json=lambda: _json(payload or json.loads(body)),
        state=SimpleNamespace(),
    )


async def _body(value):
    return value


async def _json(value):
    return value


def test_agent_token_is_separate_from_dashboard_session(monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    valid = make_request(token="agent-secret-value")
    invalid = make_request(token="wrong")
    dashboard = make_request(session={"authenticated": True})

    assert _is_agent_api_request(valid)
    assert not _is_agent_api_request(invalid)
    assert not _is_agent_api_request(dashboard)
    _require_calendar_api_auth(valid)
    _require_calendar_api_auth(dashboard)
    with pytest.raises(HTTPException) as error:
        _require_calendar_api_auth(invalid)
    assert error.value.status_code == 401


def test_agent_auth_fails_closed_without_configured_token(monkeypatch):
    monkeypatch.delenv("BOTSON_AGENT_API_TOKEN", raising=False)
    request = make_request(token="anything")

    assert not _is_agent_api_request(request)
    with pytest.raises(HTTPException) as error:
        _require_calendar_api_auth(request)
    assert error.value.status_code == 401


def test_agent_mutation_requires_idempotency_key(monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    request = make_request(token="agent-secret-value")

    with pytest.raises(HTTPException) as error:
        asyncio.run(_begin_agent_api_action(request, db=None))
    assert error.value.status_code == 400
    assert "Idempotency-Key" in error.value.detail


def test_agent_action_replays_completed_response_and_rejects_uncertain_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    async def scenario():
        db = Database(str(tmp_path / "agent-api.db"))
        await db.init()
        try:
            request = make_request(token="agent-secret-value", key="request-12345678")
            assert await _begin_agent_api_action(request, db) is None
            result = {"status": "ok", "id": 42}
            await _complete_agent_api_action(request, db, result)

            replay = await _begin_agent_api_action(request, db)
            assert isinstance(replay, JSONResponse)
            assert json.loads(replay.body) == result

            uncertain = make_request(token="agent-secret-value", key="request-pending-123")
            assert await _begin_agent_api_action(uncertain, db) is None
            with pytest.raises(HTTPException) as error:
                await _begin_agent_api_action(uncertain, db)
            assert error.value.status_code == 409
            assert "uncertain" in error.value.detail

            different = make_request(
                token="agent-secret-value",
                key="request-12345678",
                body=b'{"text":"different"}',
            )
            with pytest.raises(HTTPException) as error:
                await _begin_agent_api_action(different, db)
            assert error.value.status_code == 409
            assert "another request" in error.value.detail
        finally:
            await db.close()

    asyncio.run(scenario())


def test_agent_create_defaults_to_draft_and_replays_without_duplicate_row(tmp_path, monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    target_day = (date.today() + timedelta(days=5)).isoformat()
    payload = {
        "text": "A new community note",
        "message_type": "custom",
        "scheduled_date": target_day,
        "scheduled_time": "12:00",
    }

    async def scenario():
        db = Database(str(tmp_path / "agent-create.db"))
        await db.init()
        try:
            request = make_request(
                token="agent-secret-value",
                key="create-request-1234",
                body=json.dumps(payload).encode(),
                payload=payload,
            )
            result = await create_calendar_item(request, db)
            row = await db.get_scheduled_message(result["id"])
            assert row["status"] == "draft"

            replay = await create_calendar_item(request, db)
            assert isinstance(replay, JSONResponse)
            assert json.loads(replay.body) == result
            rows = await db.get_scheduled_messages(target_day, target_day)
            assert [item["id"] for item in rows] == [result["id"]]
        finally:
            await db.close()

    asyncio.run(scenario())
