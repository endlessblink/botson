"""Constructive previews preserve moderator control and rejection safeguards."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from bot.utils import conversation_quality as quality
from dashboard import app as dash


@pytest.fixture
def contract(monkeypatch):
    config = {
        'alternative_attempts': 2, 'alternative_max_chars': 240,
        'alternative_prompt': '{context}\n{guidance}\nLimit: {max_chars}',
    }
    monkeypatch.setattr(quality, 'load_yaml', lambda _: config)
    return config


def preview(generate, review, validate=lambda _: []):
    return asyncio.run(quality.suggest_conversation_alternative(
        'rejected', reason='too generic', category='fixture topic',
        recent_texts=['previous'], guidance='operator rules',
        community_context=['synthetic current topic context'],
        generate=generate, review=review, validate=validate,
    ))


def test_alternative_retries_rejected_idea_then_returns_approval_required_preview(contract):
    generate = AsyncMock(side_effect=['rejected', 'new concrete fixture'])
    review = AsyncMock(return_value=(True, 'useful'))
    result = preview(generate, review)
    assert result == {'text': 'new concrete fixture', 'reason': 'useful', 'needs_approval': True}
    assert generate.await_count == 2
    review.assert_awaited_once()
    assert review.await_args.kwargs['recent_texts'] == ['rejected', 'previous', 'rejected']
    assert 'synthetic current topic context' in generate.await_args.args[0]


def test_failed_content_validation_never_reaches_semantic_acceptance(contract):
    review = AsyncMock(return_value=(True, 'would pass'))
    result = preview(AsyncMock(return_value='blocked content'), review, lambda _: ['blocked'])
    assert result['text'] == '' and 'blocked' in result['reason']
    review.assert_not_awaited()


def test_reviewer_outage_does_not_produce_unreviewed_alternative(contract):
    generate = AsyncMock(return_value='new fixture')
    result = preview(generate, AsyncMock(return_value=(False, 'semantic review unavailable: offline')))
    assert result['text'] == ''
    generate.assert_awaited_once()


def test_missing_alternative_configuration_stops_before_generation(contract):
    contract.pop('alternative_prompt')
    generate = AsyncMock()
    with pytest.raises(ValueError):
        preview(generate, AsyncMock())
    generate.assert_not_awaited()


def request(payload, authenticated=True):
    return SimpleNamespace(session={'authenticated': authenticated}, json=AsyncMock(return_value=payload))


def test_preview_endpoint_checks_moderator_session_before_reading_draft():
    db = SimpleNamespace(get_scheduled_message=AsyncMock())
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.suggest_calendar_alternative(1, request({}, False), db))
    assert error.value.status_code == 303
    db.get_scheduled_message.assert_not_awaited()


def test_preview_endpoint_returns_reviewed_text_without_writing(monkeypatch):
    row = {'id': 1, 'status': 'draft', 'text': 'rejected', 'message_type': 'discussion',
           'channel_topic_id': 99, 'scheduled_date': '2099-01-01'}
    db = SimpleNamespace(
        get_scheduled_message=AsyncMock(return_value=dict(row)),
        get_verified_forum_topics=AsyncMock(return_value=[{'topic_id': 99}]),
    )
    monkeypatch.setattr(dash, '_collect_community_messages', AsyncMock(return_value=[
        {'thread_id': 99, 'text': 'synthetic community context'},
    ]))
    monkeypatch.setattr(dash, '_topic_display_name', AsyncMock(return_value='fixture topic'))
    monkeypatch.setattr(dash, '_fetch_recent_sent_for_dedup', AsyncMock(return_value=[]))
    monkeypatch.setattr(dash, 'build_generation_prompt', lambda *a, **k: 'guidance')
    alternative = AsyncMock(return_value={'text': 'reviewed fixture', 'reason': 'useful', 'needs_approval': True})
    monkeypatch.setattr(quality, 'suggest_conversation_alternative', alternative)
    result = asyncio.run(dash.suggest_calendar_alternative(1, request({'expected_text': 'rejected', 'reason': 'weak'}), db))
    assert result['preview_only'] is True and result['needs_approval'] is True
    assert result['text'] == 'reviewed fixture'
    assert alternative.await_args.kwargs['community_context'] == ['synthetic community context']
    assert db.get_scheduled_message.await_count == 2
    assert row['status'] == 'draft' and row['text'] == 'rejected'


@pytest.mark.parametrize('status,text,code', [('scheduled', 'rejected', 409), ('draft', 'changed', 409)])
def test_preview_endpoint_refuses_changed_or_sendable_row(status, text, code):
    db = SimpleNamespace(get_scheduled_message=AsyncMock(return_value={'status': status, 'text': text}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.suggest_calendar_alternative(1, request({'expected_text': 'rejected', 'reason': 'weak'}), db))
    assert error.value.status_code == code
