"""Configured, verified flex destinations; synthetic DB and provider fixtures."""
import asyncio
import copy
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot.database.db import Database
from dashboard import app as dash


TEXT = 'איזה ספר גרם לכם לשנות דעה על דמות ששנאתם בהתחלה?'


def settings_for(strategy, kind='custom'):
    settings = copy.deepcopy(dash.get_settings())
    settings['topics'] = {'welcome': 202, 'goals': 303, 'discussions': {'books': 101}}
    settings['schedule'] = {}
    scope = {'enabled': True, 'max_suggestions': 1, 'per_day_max': 1,
             'min_lead_minutes': 10,
             'windows': [{'start': '21:15', 'end': '21:15', 'step_minutes': 15}],
             'allowed_types': [kind], 'topic_strategy': strategy, 'handler': 'flex_fixture'}
    settings['ai_populate']['flex'] = {'enabled': True, 'day': scope, 'week': copy.deepcopy(scope)}
    return settings


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = cls(2099, 1, 1, 20, 29)
        return value.replace(tzinfo=tz) if tz else value


@pytest.mark.parametrize('strategy,kind,expected', [
    ('discussion_category', 'discussion', 101),
    ('discussion_category', 'custom', 101),
    ('topics.welcome', 'custom', 202),
    ('topics.goals', 'custom', 303),
    ('handler_routing', 'custom', 404),
])
@pytest.mark.parametrize('scope', ['day', 'week'])
def test_suggest_is_write_free_and_commit_keeps_configured_route(monkeypatch, tmp_path, strategy, kind, expected, scope):
    settings = settings_for(strategy, kind)
    monkeypatch.setattr(dash, 'get_settings', lambda: settings)
    monkeypatch.setattr(dash, 'datetime', Clock)
    monkeypatch.setattr(dash, '_render_group_stats_context', AsyncMock(return_value=''))
    monkeypatch.setattr(dash, '_review_discussion_quality', AsyncMock(return_value=(True, 'fixture')))
    async def review_batch(candidates):
        return {int(item['id']): (True, 'fixture') for item in candidates}
    monkeypatch.setattr(dash, '_review_discussion_quality_batch', review_batch)
    async def generate(prompt, **kwargs):
        if kwargs.get('context') == 'planner.flex_batch':
            return json.dumps({'items': [{'id': 1, 'text': TEXT}]}), []
        return TEXT, []
    monkeypatch.setattr(dash, '_generate_with_fallbacks', generate)

    async def scenario():
        db = Database(str(tmp_path/'routing.db'))
        await db.init()
        try:
            for topic, key, name in [(101, 'books', 'ספרים'), (202, 'welcome', 'ברוכים הבאים'),
                                     (303, 'goals', 'יעדים'), (404, 'flex_fixture', 'ספרים')]:
                await db.upsert_verified_forum_topic(topic, name, key, 'synthetic fixture')
            await db.set_handler_routing('flex_fixture', 404, [])
            result = await dash._ai_suggest_calendar(db, **(
                {'target_date': '2099-01-01'} if scope == 'day' else {'week_of': '2099-01-01'}))
            before = await db.get_scheduled_messages('2098-12-28', '2099-01-03')
            body = {'approved': result['suggestions']}
            request = SimpleNamespace(session={'authenticated': True}, state=SimpleNamespace(), headers={},
                json=AsyncMock(return_value=body), body=AsyncMock(return_value=json.dumps(body).encode()))
            committed = await dash.ai_suggest_commit(request, db)
            after = await db.get_scheduled_messages('2098-12-28', '2099-01-03')
            return result, before, committed, after
        finally:
            await db.close()
    result, before, committed, after = asyncio.run(scenario())
    assert not before
    assert len(result['suggestions']) == 1, result
    row = result['suggestions'][0]
    assert (row['topic_id'], row['message_type'], row['time']) == (expected, kind, '21:15')
    assert committed['inserted'] == 1, committed
    assert len(after) == 1 and after[0]['channel_topic_id'] == expected


@pytest.mark.parametrize('strategy,kind,verification', [
    ('', 'custom', True),
    ('invented_destination', 'custom', True),
    ('topics.welcome', 'custom', False),
    ('handler_routing', 'custom', True),
    ('discussion_category', 'discussion', False),
    ('topics.welcome', 'discussion', True),
])
def test_missing_or_inappropriate_route_skips_before_generation(monkeypatch, tmp_path, strategy, kind, verification):
    settings = settings_for(strategy, kind)
    monkeypatch.setattr(dash, 'get_settings', lambda: settings)
    monkeypatch.setattr(dash, 'datetime', Clock)
    monkeypatch.setattr(dash, '_render_group_stats_context', AsyncMock(return_value=''))
    generator = AsyncMock()
    monkeypatch.setattr(dash, '_generate_with_fallbacks', generator)
    async def scenario():
        db = Database(str(tmp_path/'missing-route.db'))
        await db.init()
        try:
            if verification:
                await db.upsert_verified_forum_topic(202, 'ברוכים הבאים', 'welcome', 'synthetic fixture')
            result = await dash._ai_suggest_calendar(db, target_date='2099-01-01')
            rows = await db.get_scheduled_messages('2099-01-01', '2099-01-01')
            return result, rows
        finally:
            await db.close()
    result, rows = asyncio.run(scenario())
    assert not result['suggestions'] and not rows
    assert any(reason['code'] == 'missing_flex_routing' and reason['detail']
               for reason in result['skip_reasons']), result
    generator.assert_not_awaited()


def test_configured_category_alone_cannot_activate_unverified_topic():
    settings = settings_for('discussion_category', 'discussion')
    categories = [{'topic_id': 101, 'category_key': 'books', 'name': 'ספרים'}]
    routes, problems = asyncio.run(dash._resolve_ai_flex_routes(
        SimpleNamespace(), settings, 'day', categories, {202: 'ברוכים הבאים'}))
    assert routes == {} and problems[0][0] == 'discussion'


def test_missing_verification_read_fails_closed(monkeypatch, tmp_path):
    settings = settings_for('topics.welcome')
    monkeypatch.setattr(dash, 'get_settings', lambda: settings)
    monkeypatch.setattr(dash, 'datetime', Clock)
    monkeypatch.setattr(dash, '_render_group_stats_context', AsyncMock(return_value=''))
    generator = AsyncMock()
    monkeypatch.setattr(dash, '_generate_with_fallbacks', generator)
    async def scenario():
        db = Database(str(tmp_path/'unavailable-verification.db'))
        await db.init()
        try:
            await db.upsert_verified_forum_topic(202, 'ברוכים הבאים', 'welcome', 'synthetic fixture')
            monkeypatch.setattr(db, 'get_verified_forum_topics', AsyncMock(side_effect=RuntimeError('fixture outage')))
            return await dash._ai_suggest_calendar(db, target_date='2099-01-01')
        finally:
            await db.close()
    result = asyncio.run(scenario())
    assert not result['suggestions']
    assert any(reason['code'] == 'missing_flex_routing' for reason in result['skip_reasons'])
    generator.assert_not_awaited()
