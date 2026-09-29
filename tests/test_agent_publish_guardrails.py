"""Agents publish to the main group on their own, inside server-side guardrails."""

import asyncio
import json
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from bot.database.db import Database
from dashboard import app as dash

TOKEN = "agent-secret-value"


def agent_request(payload=None, key="guard-key-0001", token=TOKEN):
    body = json.dumps(payload or {}).encode()
    return SimpleNamespace(
        headers={"authorization": f"Bearer {token}" if token else "", "idempotency-key": key},
        session={}, method="POST", url=SimpleNamespace(path="/api/calendar"),
        body=lambda: _v(body), json=lambda: _v(payload or {}), state=SimpleNamespace(),
    )


async def _v(value):
    return value


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", TOKEN)

    async def no_receipt_needed(_request, _db):
        return None

    monkeypatch.setattr(dash, "_require_community_context_receipt", no_receipt_needed)
    monkeypatch.setattr(dash, "get_settings", lambda: {"agent_guardrails": {
        "max_quality_rejections_per_day": 2, "send_now_early_minutes": 10}})
    monkeypatch.setattr("bot.scheduler.materializer._used_texts_for_type", AsyncMock(return_value=[]))
    review = AsyncMock(return_value=(True, "good"))
    monkeypatch.setattr(dash, "_review_discussion_quality", review)
    return tmp_path, review


def run(coro_factory, tmp_path):
    async def scenario():
        db = Database(str(tmp_path / "guard.db"))
        await db.init()
        try:
            return await coro_factory(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


def guard(db, *, row_id=5, text="post", message_type="custom", group="main",
          when=None, sending_now=False, request=None):
    when = when or datetime.now(ZoneInfo("Asia/Jerusalem"))
    return dash._agent_publish_guard(
        request or agent_request(), db, row_id=row_id, text=text, message_type=message_type,
        target_group=group, scheduled_date=when.strftime("%Y-%m-%d"),
        scheduled_time=when.strftime("%H:%M"), sending_now=sending_now,
    )


def test_custom_label_does_not_skip_review(env):
    tmp_path, review = env
    review.return_value = (False, "vague engagement bait")

    async def body(db):
        with pytest.raises(HTTPException) as error:
            await guard(db, message_type="custom")
        return error.value.status_code
    assert run(body, tmp_path) == 422
    review.assert_awaited_once()


def test_rejected_row_cannot_be_rewritten_and_retried(env):
    tmp_path, review = env

    async def body(db):
        review.return_value = (False, "generic")
        with pytest.raises(HTTPException):
            await guard(db, row_id=9, text="first try")
        review.return_value = (True, "fine")
        with pytest.raises(HTTPException) as error:
            await guard(db, row_id=9, text="reworded to slip through")
        return error.value.status_code
    assert run(body, tmp_path) == 409


def test_daily_rejection_budget_pauses_agent_publishing(env):
    tmp_path, review = env
    review.return_value = (False, "weak")

    async def body(db):
        for row_id in (1, 2):
            with pytest.raises(HTTPException):
                await guard(db, row_id=row_id)
        review.reset_mock()
        review.return_value = (True, "fine")
        with pytest.raises(HTTPException) as error:
            await guard(db, row_id=3)
        return error.value.status_code
    assert run(body, tmp_path) == 429
    review.assert_not_awaited()


def test_send_now_refuses_rows_due_later(env):
    tmp_path, _ = env
    later = datetime.now(ZoneInfo("Asia/Jerusalem")) + timedelta(hours=20)

    async def body(db):
        with pytest.raises(HTTPException) as error:
            await guard(db, when=later, sending_now=True)
        return error.value.status_code
    assert run(body, tmp_path) == 409


def test_reviewer_outage_is_not_counted_as_rejection(env):
    tmp_path, review = env
    review.return_value = (False, "semantic review unavailable: timeout")

    async def body(db):
        with pytest.raises(HTTPException) as error:
            await guard(db, row_id=4)
        review.return_value = (True, "fine")
        await guard(db, row_id=4)  # not blocked as "already rejected"
        return error.value.status_code
    assert run(body, tmp_path) == 503


def test_games_test_group_and_operator_session_are_exempt(env):
    tmp_path, review = env
    review.return_value = (False, "weak")

    async def body(db):
        await guard(db, message_type="trivia_round")
        await guard(db, group="test")
        await guard(db, request=agent_request(token=""))
    run(body, tmp_path)
    review.assert_not_awaited()


def test_agent_created_rows_are_labelled_and_sendable(env):
    tmp_path, _ = env
    from bot.handlers.calendar import _scheduler_authored_conversation

    day = (date.today() + timedelta(days=3)).isoformat()
    payload = {"text": "a concrete post", "scheduled_date": day, "scheduled_time": "12:00",
               "message_type": "custom", "status": "draft"}

    async def body(db):
        result = await dash.create_calendar_item(agent_request(payload), db)
        rows = await db.get_scheduled_messages(day, day)
        return result, rows
    result, rows = run(body, tmp_path)
    assert result["status"] == "ok"
    assert rows[0]["created_by"] == "agent"
    assert _scheduler_authored_conversation(rows[0])
