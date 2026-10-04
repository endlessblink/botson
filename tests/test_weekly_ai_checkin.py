"""Selected AI-topic invitations use synthetic recipients and mocked Telegram."""

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from bot.database.db import Database
from bot.handlers import weekly_state_review as weekly
from bot.utils.config import get_settings
from dashboard import app as dash


def test_weekly_configuration_waits_for_actual_schedule_and_recipients():
    config = get_settings()['weekly_state_review']
    assert config['enabled'] is False
    assert config['days'] == [6] and config['time'] == '19:00'
    assert config['timezone'] == 'Asia/Jerusalem' and config['mode'] == 'auto_send'
    assert config['not_before'] == '2026-10-10'
    assert config['topic_id'] == 3113 and config['tag_usernames'] == []
    assert config['selected_members'] == [] and config['pin_enabled'] is True


def test_selected_handles_are_valid_deduplicated_and_never_all_members():
    assert weekly.selected_usernames(['@fixture_member', 'FIXTURE_MEMBER']) == ['fixture_member']
    assert weekly.selected_usernames([]) == []
    with pytest.raises(ValueError):
        weekly.selected_usernames(['all', 'arbitrary text @another'])
    with pytest.raises(ValueError):
        weekly.resolve_selected_users(['fixture_member'], [])


def test_weekly_key_uses_sunday_start_and_the_local_calendar_week():
    sunday = datetime(2026, 1, 4, 12, tzinfo=ZoneInfo('Asia/Jerusalem'))
    key = weekly.weekly_review_key(sunday, 99)
    assert key == weekly.weekly_review_key(sunday + timedelta(days=1), 99)
    assert key == weekly.weekly_review_key(sunday + timedelta(days=6), 99)
    assert key != weekly.weekly_review_key(sunday + timedelta(days=7), 99)


@pytest.mark.parametrize('send_error', [False, True])
def test_weekly_retry_does_not_duplicate_even_when_transport_outcome_is_uncertain(monkeypatch, tmp_path, send_error):
    config = {'enabled': True, 'topic_id': 99, 'question': 'הזמנת בדיקה לפרויקט הבנייה',
              'tag_usernames': ['fixture_member'], 'initial_topic_category': 'ai_en',
              'selected_members': [{'user_id': 101, 'username': 'fixture_member'}],
              'timezone': 'Asia/Jerusalem', 'mode': 'auto_send'}
    monkeypatch.setattr(weekly, '_review_config', lambda: config)
    monkeypatch.setattr(weekly, 'is_auto_blocked_on', lambda _: False)
    monkeypatch.setattr(weekly, 'GROUP_ID', -10099)
    send = AsyncMock(side_effect=TimeoutError('uncertain') if send_error else None,
                     return_value=SimpleNamespace(message_id=55))
    monkeypatch.setattr(weekly, 'safe_send', send)
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
        status='member', user=SimpleNamespace(id=101, username='fixture_member', is_bot=False),
    )))

    async def scenario():
        db = Database(str(tmp_path/'weekly.db'))
        await db.init()
        try:
            await db.upsert_verified_forum_topic(99, 'Synthetic AI topic', 'ai_en', 'fixture')
            await db.upsert_chat_member(-10099, 101, 'fixture_member', 'Synthetic Member')
            context = SimpleNamespace(bot=bot, bot_data={'db': db})
            first = await weekly.send_weekly_state_review(context, force=True)
            second = await weekly.send_weekly_state_review(context, force=True)
            return first, second
        finally:
            await db.close()
    result = asyncio.run(scenario())
    assert result == ((None, None) if send_error else (55, 55))
    send.assert_awaited_once()
    assert send.await_args.kwargs['text'] == 'הזמנת בדיקה לפרויקט הבנייה\n\n@fixture_member'


def test_enabled_weekly_checkin_refuses_empty_tag_list_without_live_lookup(monkeypatch):
    monkeypatch.setattr(weekly, '_review_config', lambda: {
        'enabled': True, 'topic_id': 99, 'question': 'Synthetic invite', 'tag_usernames': [],
    })
    monkeypatch.setattr(weekly, 'is_auto_blocked_on', lambda _: False)
    send = AsyncMock()
    monkeypatch.setattr(weekly, 'safe_send', send)
    result = asyncio.run(weekly.send_weekly_state_review(SimpleNamespace(bot=object(), bot_data={'db': object()})))
    assert result is None
    send.assert_not_awaited()


@pytest.mark.parametrize('body', [
    {'expected_revision': 0, 'enabled': True, 'topic_id': 99, 'question': 'fixture', 'tag_usernames': ['fixture_member']},
    {'expected_revision': 0, 'enabled': True, 'topic_id': 99, 'question': 'fixture', 'days': [1, 2], 'time': '12:00', 'tag_usernames': ['fixture_member']},
    {'expected_revision': 0, 'enabled': True, 'topic_id': 99, 'question': 'fixture', 'days': [1], 'time': '12:00', 'tag_usernames': []},
])
def test_weekly_settings_do_not_invent_day_time_or_members(body, monkeypatch):
    save = AsyncMock()
    monkeypatch.setattr(dash, '_save_settings_file', save)
    request = SimpleNamespace(session={'authenticated': True}, state=SimpleNamespace(), json=AsyncMock(return_value=body))
    db = SimpleNamespace(is_verified_topic_id=AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.update_weekly_state_review(request, db))
    assert error.value.status_code in {400, 409}
    save.assert_not_called()


def test_dst_fold_does_not_double_send_and_next_week_still_sends(monkeypatch, tmp_path):
    zone = ZoneInfo('America/New_York')
    class Clock(datetime):
        current = datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0)
        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current
    monkeypatch.setattr(weekly, 'datetime', Clock)
    monkeypatch.setattr(weekly, 'GROUP_ID', -10099)
    config = {'enabled': True, 'timezone': 'America/New_York', 'days': [0], 'time': '01:30',
              'mode': 'auto_send', 'topic_id': 99, 'question': 'הזמנת בדיקה לפרויקט הבנייה',
              'selected_members': [{'user_id': 101, 'username': 'fixture_member'}],
              'initial_topic_category': 'ai_en'}
    monkeypatch.setattr(weekly, '_review_config', lambda: config)
    monkeypatch.setattr(weekly, 'is_auto_blocked_on', lambda _: False)
    send = AsyncMock(side_effect=[SimpleNamespace(message_id=55), SimpleNamespace(message_id=56)])
    monkeypatch.setattr(weekly, 'safe_send', send)
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
        status='member', user=SimpleNamespace(id=101, username='fixture_member', is_bot=False))))
    async def scenario():
        db = Database(str(tmp_path/'fold.db'))
        await db.init()
        try:
            await db.upsert_verified_forum_topic(99, 'Synthetic topic', 'ai_en', 'fixture')
            context = SimpleNamespace(bot=bot, bot_data={'db': db})
            assert await weekly.send_weekly_state_review(context) == 55
            Clock.current = Clock.current.replace(fold=1)
            assert await weekly.send_weekly_state_review(context) is None
            Clock.current = datetime(2026, 11, 8, 1, 30, tzinfo=zone)
            assert await weekly.send_weekly_state_review(context) == 56
            assert await weekly.send_weekly_state_review(context) == 56
        finally:
            await db.close()
    asyncio.run(scenario())
    assert send.await_count == 2


def test_no_catchup_before_approved_start_even_with_force(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 4, 19, tzinfo=ZoneInfo('Asia/Jerusalem'))
    monkeypatch.setattr(weekly, 'datetime', Clock)
    monkeypatch.setattr(weekly, '_review_config', lambda: {
        'enabled': True, 'timezone': 'Asia/Jerusalem', 'not_before': '2026-10-10',
    })
    send = AsyncMock()
    monkeypatch.setattr(weekly, 'safe_send', send)
    assert asyncio.run(weekly.send_weekly_state_review(object(), force=True)) is None
    send.assert_not_awaited()


def test_member_change_during_live_lookup_refuses_stale_snapshot(monkeypatch):
    config = {'enabled': True, 'timezone': 'Asia/Jerusalem', 'mode': 'auto_send',
              'topic_id': 99, 'question': 'הזמנת בדיקה לפרויקט הבנייה',
              'selected_members': [{'user_id': 101, 'username': 'fixture_member'}],
              'initial_topic_category': 'ai_en'}
    current = dict(config)
    monkeypatch.setattr(weekly, '_review_config', lambda: current)
    monkeypatch.setattr(weekly, 'is_auto_blocked_on', lambda _: False)
    async def changed(*_):
        current['selected_members'] = []
        return SimpleNamespace(status='member', user=SimpleNamespace(id=101, username='fixture_member', is_bot=False))
    db = SimpleNamespace(get_verified_forum_topics=AsyncMock(return_value=[{'topic_id': 99, 'category_key': 'ai_en'}]),
                         begin_agent_api_action=AsyncMock())
    # Return independent snapshots as a real YAML read does.
    from copy import deepcopy
    monkeypatch.setattr(weekly, '_review_config', lambda: deepcopy(current))
    send = AsyncMock()
    monkeypatch.setattr(weekly, 'safe_send', send)
    context = SimpleNamespace(bot=SimpleNamespace(get_chat_member=changed), bot_data={'db': db})
    assert asyncio.run(weekly.send_weekly_state_review(context, force=True)) is None
    send.assert_not_awaited()
    db.begin_agent_api_action.assert_not_awaited()


@pytest.mark.parametrize('change', ['none', 'stale_revision', 'member_left'])
def test_approved_weekly_draft_dispatch_rechecks_exact_snapshot_and_live_member(monkeypatch, change):
    from bot.handlers import calendar
    from test_calendar_scheduled_games import FakeScheduledDb, _base_row
    config = {'enabled': True, 'mode': 'draft', 'revision': 3, 'topic_id': 99,
              'question': 'הזמנת בדיקה לפרויקט הבנייה', 'initial_topic_category': 'ai_en',
              'selected_members': [{'user_id': 101, 'username': 'fixture_member'}]}
    monkeypatch.setattr(weekly, '_review_config', lambda: config)
    monkeypatch.setattr(weekly, 'GROUP_ID', -10099)
    monkeypatch.setenv('GROUP_ID', '-10099')
    monkeypatch.setenv('BOT_TOKEN', '123:fixture')
    monkeypatch.setattr(calendar, 'should_skip_scheduled_message', lambda *_: False)
    row = _base_row('custom')
    row.update(created_by='weekly-checkin', target_group='main', channel_topic_id=99,
               text=weekly.build_weekly_state_review(config),
               poll_options=json.dumps({'weekly_revision': 2 if change == 'stale_revision' else 3,
                                        'weekly_checkin_key': 'fixture-week', 'weekly_pin_enabled': True}))
    db = FakeScheduledDb(row)
    db.get_verified_forum_topics = AsyncMock(return_value=[{'topic_id': 99, 'category_key': 'ai_en'}])
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(
        status='left' if change == 'member_left' else 'member',
        user=SimpleNamespace(id=101, username='fixture_member', is_bot=False))))
    monkeypatch.setattr('telegram.Bot', lambda *_: bot)
    send = AsyncMock(return_value=SimpleNamespace(message_id=55))
    monkeypatch.setattr(calendar, 'safe_send', send)
    record = AsyncMock()
    monkeypatch.setattr(weekly, 'record_delivered_checkin', record)
    monkeypatch.setattr('bot.handlers.dm_menu.notify_opted_in_users', AsyncMock())
    asyncio.run(calendar.check_and_send_due_messages(SimpleNamespace(bot_data={'db': db}, bot=bot)))
    if change == 'none':
        send.assert_awaited_once()
        assert send.await_args.kwargs['entities'][0].user.id == 101
        assert db.sent == [(123, 55)]
        record.assert_awaited_once()
    else:
        send.assert_not_awaited()
        record.assert_not_awaited()
        assert not db.sent and db.skipped
