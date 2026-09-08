"""Retired starter pools must never bypass fresh generation."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from dashboard import app as dashboard
from bot.handlers import calendar as calendar_handler


@pytest.mark.parametrize("kind", ["morning", "evening", "discussion"])
@pytest.mark.parametrize("generated", ["fresh concrete question", None])
def test_send_now_requires_fresh_generation(kind, generated):
    request = MagicMock(session={"authenticated": True})
    request.json = AsyncMock(return_value={"type": kind})
    db = MagicMock()
    db.get_random_prompt = AsyncMock(return_value="retired question")
    db.log_activity = AsyncMock()
    db.create_scheduled_message = AsyncMock(return_value=41)
    db.mark_message_sent = AsyncMock()
    db.update_scheduled_message = AsyncMock()
    bot = MagicMock(send_message=AsyncMock(return_value=MagicMock(message_id=99)))
    with patch.object(dashboard, "get_settings", return_value={"topics": {"discussions": {"gaming": 123}}}), patch.object(dashboard, "load_yaml", return_value={"gaming": ["retired question"]}), patch("bot.scheduler.materializer._used_texts_for_type", AsyncMock(return_value=["previous question"])), patch("bot.scheduler.materializer._generate_fresh_text", AsyncMock(return_value=generated)) as generate, patch("telegram.Bot", return_value=bot):
        if generated:
            result = asyncio.run(dashboard.send_prompt_now(request, db))
            assert result["prompt"] == generated
            db.mark_message_sent.assert_awaited_once_with(41, 99)
            assert db.create_scheduled_message.call_args.kwargs["text"] == generated
            assert bot.send_message.call_args.kwargs["text"] == generated
            assert generate.call_args.kwargs["examples"] == []
            assert generate.call_args.kwargs["used_texts"] == ["previous question"]
        else:
            with pytest.raises(HTTPException) as error:
                asyncio.run(dashboard.send_prompt_now(request, db))
            assert error.value.status_code == 503
            bot.send_message.assert_not_called()
            db.mark_message_sent.assert_not_called()
        db.get_random_prompt.assert_not_called()


def test_retired_pools_cannot_influence_generation():
    with patch.object(dashboard, "load_yaml", return_value={"morning": ["retired"], "evening": ["retired"], "gaming": ["retired"]}):
        for kind in ("morning", "evening", "discussion"):
            assert dashboard._sample_pool_examples(kind, "gaming") == ""


@pytest.mark.parametrize("kind", ["morning", "evening", "discussion"])
def test_digest_rejects_semantic_failure_after_retry(kind):
    plan = {"regular_slots": [{"type": kind, "text": "generic paraphrase", "scheduled_time": "09:00"}]}
    with patch.object(dashboard, "freshness_rejection", return_value=None), patch.object(dashboard, "_validate_draft_text", side_effect=lambda text: []), patch.object(dashboard, "_review_discussion_quality", AsyncMock(return_value=(False, "generic check-in"))) as review, patch.object(dashboard, "_fetch_recent_sent_for_dedup", AsyncMock(return_value=[])), patch.object(dashboard, "build_generation_prompt", return_value="prompt"), patch.object(dashboard, "_generate_via_cli", AsyncMock(return_value="another generic paraphrase")):
        result, notes = asyncio.run(dashboard._retry_failed_regular_slots(plan, MagicMock(), "2026-09-06"))
    assert result["regular_slots"] == []
    assert review.await_count == 2
    assert "generic check-in" in notes[0]


@pytest.mark.parametrize("kind", ["morning", "evening", "discussion"])
def test_checked_suggestion_still_requires_semantic_acceptance(kind):
    request = MagicMock(session={"authenticated": True})
    request.json = AsyncMock(return_value={"approved": [{
        "date": "2099-01-01", "time": "09:00", "message_type": kind,
        "topic_id": 123, "text": "generic paraphrase", "source": "ai-fill",
    }]})
    request.body = AsyncMock(return_value=json.dumps(request.json.return_value).encode())
    db = MagicMock(_db=None)
    db.create_scheduled_message = AsyncMock()
    with patch.object(dashboard, "_validate_draft_text", side_effect=lambda text: []), patch.object(dashboard, "freshness_rejection", return_value=None), patch.object(dashboard, "_fetch_recent_sent_for_dedup", AsyncMock(return_value=[])), patch.object(dashboard, "_review_discussion_quality", AsyncMock(return_value=(False, "generic check-in"))):
        result = asyncio.run(dashboard.ai_suggest_commit(request, db))
    assert result["inserted"] == 0
    assert "generic check-in" in result["errors"][0]
    db.create_scheduled_message.assert_not_called()


@pytest.mark.parametrize("kind", ["morning", "evening", "discussion"])
def test_calendar_send_now_rejects_semantic_failure(kind):
    db = MagicMock(mark_message_sent=AsyncMock())
    msg = {"id": 42, "message_type": kind, "text": "generic paraphrase", "channel_topic_id": 123}
    with patch.object(dashboard, "freshness_rejection", return_value=None), patch("telegram.Bot"), patch("bot.scheduler.materializer._used_texts_for_type", AsyncMock(return_value=[])) as history, patch.object(dashboard, "_review_discussion_quality", AsyncMock(return_value=(False, "generic"))), patch("bot.handlers.calendar.send_message_with_optional_cover", AsyncMock()) as send:
        with pytest.raises(ValueError, match="generic"):
            asyncio.run(dashboard._send_scheduled_row(db, msg, "main"))
        history.assert_awaited_once_with(db, kind, sent_only=True)
        send.assert_not_called()
        db.mark_message_sent.assert_not_called()


@pytest.mark.parametrize("authenticated,status,code", [(False, "changed", 401), (True, "conflict", 409), (True, "changed", None), (True, "already_quarantined", None)])
def test_quarantine_endpoint_auth_and_snapshot_conflict(authenticated, status, code):
    request = MagicMock(session={"authenticated": authenticated})
    expected = {"text": "snapshot"}
    request.json = AsyncMock(return_value={"expected": expected})
    db = MagicMock(quarantine_conversation_message=AsyncMock(return_value=status))
    if code:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(dashboard.quarantine_calendar_conversation(42, request, db))
        assert exc.value.status_code == code
    else:
        result = asyncio.run(dashboard.quarantine_calendar_conversation(42, request, db))
        assert result["status"] == status
    if authenticated:
        db.quarantine_conversation_message.assert_awaited_once_with(42, expected=expected)
    else:
        db.quarantine_conversation_message.assert_not_called()


@pytest.mark.parametrize("failure", ["duplicate recent", "history unavailable"])
def test_calendar_send_now_fails_closed_before_send(failure):
    db = MagicMock(mark_message_sent=AsyncMock())
    history = AsyncMock(return_value=["sent text"])
    if failure == "history unavailable":
        history.side_effect = RuntimeError(failure)
    msg = {"id": 42, "message_type": "morning", "text": "candidate"}
    with patch("telegram.Bot"), patch("bot.scheduler.materializer._used_texts_for_type", history), patch.object(dashboard, "freshness_rejection", return_value=failure), patch("bot.handlers.calendar.send_message_with_optional_cover", AsyncMock()) as send:
        with pytest.raises((ValueError, RuntimeError), match=failure):
            asyncio.run(dashboard._send_scheduled_row(db, msg, "main"))
        send.assert_not_called()
        db.mark_message_sent.assert_not_called()


def test_prompt_send_failure_does_not_record_sent_history():
    request = MagicMock(session={"authenticated": True})
    request.json = AsyncMock(return_value={"type": "morning"})
    db = MagicMock(create_scheduled_message=AsyncMock(return_value=41), update_scheduled_message=AsyncMock(), mark_message_sent=AsyncMock())
    bot = MagicMock(send_message=AsyncMock(side_effect=RuntimeError("send unavailable")))
    with patch("bot.scheduler.materializer._used_texts_for_type", AsyncMock(return_value=[])), patch("bot.scheduler.materializer._generate_fresh_text", AsyncMock(return_value="accepted")), patch("telegram.Bot", return_value=bot):
        with pytest.raises(HTTPException):
            asyncio.run(dashboard.send_prompt_now(request, db))
    db.update_scheduled_message.assert_awaited_once_with(41, status="failed")
    db.mark_message_sent.assert_not_called()


@pytest.mark.parametrize("mutation", ["quarantine", "edit", "unchanged_draft"])
def test_calendar_send_now_claim_rejects_change_during_review(mutation):
    from bot.database.db import Database

    async def exercise():
        db = Database(":memory:")
        await db.init()
        try:
            row_id = await db.create_scheduled_message(
                message_type="morning", text="reviewed candidate", channel_topic_id=123,
                target_group="main", scheduled_date="2099-01-01", scheduled_time="09:00",
                status="draft" if mutation == "unchanged_draft" else "scheduled", created_by="auto",
            )
            snapshot = await db.get_scheduled_message(row_id)

            async def review(*args, **kwargs):
                if mutation == "quarantine":
                    assert await db.quarantine_conversation_message(row_id, expected=snapshot) == "changed"
                elif mutation == "edit":
                    await db.update_scheduled_message(row_id, text="operator changed text")
                return True, "accepted"

            with patch("telegram.Bot"), patch.object(dashboard, "freshness_rejection", return_value=None), patch.object(dashboard, "_review_discussion_quality", AsyncMock(side_effect=review)), patch("bot.handlers.calendar.send_message_with_optional_cover", AsyncMock(return_value=MagicMock(message_id=99))) as send:
                if mutation == "unchanged_draft":
                    assert await dashboard._send_scheduled_row(db, snapshot, "main") == 99
                    send.assert_awaited_once()
                else:
                    with pytest.raises(ValueError, match="changed|claimed|eligible"):
                        await dashboard._send_scheduled_row(db, snapshot, "main")
                    send.assert_not_called()
            current = await db.get_scheduled_message(row_id)
            if mutation == "unchanged_draft":
                assert current["status"] == "sent"
                assert current["sent_message_id"] == 99
            else:
                assert current["status"] != "sent"
                assert not current["sent_message_id"]
        finally:
            await db.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("kind", ["morning", "evening", "discussion"])
@pytest.mark.parametrize("outcome", ["rejected", "sent", "failed"])
def test_drawer_conversation_send_is_reviewed_and_records_history(outcome, kind):
    request = MagicMock(session={"authenticated": True})
    request.json = AsyncMock(return_value={"text": "legacy candidate", "message_type": kind, "target": "main", "topic_id": 123})
    db = MagicMock(create_scheduled_message=AsyncMock(return_value=41), mark_message_sent=AsyncMock(), update_scheduled_message=AsyncMock(), log_activity=AsyncMock())
    send = AsyncMock(return_value=MagicMock(message_id=99))
    if outcome == "failed":
        send.side_effect = RuntimeError("send failed")
    with patch.dict("os.environ", {"GROUP_ID": "123"}), patch("telegram.Bot"), patch("bot.scheduler.materializer._used_texts_for_type", AsyncMock(return_value=[])), patch.object(dashboard, "freshness_rejection", return_value=None), patch.object(dashboard, "_review_discussion_quality", AsyncMock(return_value=(outcome != "rejected", "generic"))), patch("bot.handlers.calendar.send_message_with_optional_cover", send):
        if outcome == "sent":
            asyncio.run(dashboard.send_message_to_topic(request, db))
            db.mark_message_sent.assert_awaited_once_with(41, 99)
            assert db.create_scheduled_message.call_args.kwargs["text"] == send.call_args.kwargs["text"]
        else:
            with pytest.raises(HTTPException):
                asyncio.run(dashboard.send_message_to_topic(request, db))
            db.mark_message_sent.assert_not_called()
            if outcome == "rejected":
                send.assert_not_called()
