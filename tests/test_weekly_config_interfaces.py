"""Offline fixtures for shared configuration, DST and own-ID membership changes."""

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
import yaml
from fastapi import HTTPException

from bot.database.db import Database
from bot.handlers import weekly_state_review as weekly
from bot.utils.weekly_checkin_config import (StaleWeeklyConfig, next_weekly_occurrence,
                                           read_weekly_config, write_weekly_config)
from dashboard import app as dash


@pytest.fixture
def interfaces(monkeypatch, tmp_path):
    config = {'enabled': False, 'mode': 'auto_send', 'days': [6], 'time': '19:00',
              'timezone': 'Asia/Jerusalem', 'topic_id': 99, 'question': 'שאלת בדיקה לפרויקט הבנייה',
              'tag_usernames': ['fixture_member'], 'selected_members': [{'user_id': 101, 'username': 'fixture_member'}],
              'initial_topic_category': 'ai_en', 'excluded_user_ids': [], 'pin_enabled': True,
              'revision': 3, 'audit': []}
    path = tmp_path / 'settings.yaml'
    path.write_text(yaml.safe_dump({'weekly_state_review': config, 'unrelated': {'preserve': True}}, allow_unicode=True))
    monkeypatch.setattr(dash, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(weekly, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(dash, '_signal_weekly_reload', lambda: True)
    monkeypatch.setenv('BOTSON_AGENT_API_TOKEN', 'synthetic-test-token')
    db = SimpleNamespace(is_verified_topic_id=AsyncMock(return_value=True),
        get_verified_forum_topics=AsyncMock(return_value=[{'topic_id': 99, 'category_key': 'ai_en'}]),
        get_chat_members_for_tagging=AsyncMock(return_value=[{'user_id': 101, 'username': 'fixture_member'}]),
        begin_agent_api_action=AsyncMock(return_value=('new', None)), complete_agent_api_action=AsyncMock(return_value=True))
    body = {key: value for key, value in config.items() if key not in {'audit', 'revision', 'selected_members'}}
    body['expected_revision'] = 3
    body['enabled'] = True
    return path, config, db, body


def request(body, *, authenticated=True, headers=None):
    return SimpleNamespace(session={'authenticated': authenticated}, headers=headers or {},
        state=SimpleNamespace(), method='PUT', url=SimpleNamespace(path='/api/agent/weekly-checkin'),
        json=AsyncMock(return_value=deepcopy(body)), body=AsyncMock(return_value=b'synthetic-body'))


def test_preview_is_exact_write_free_and_activation_requires_its_receipt(interfaces):
    path, config, db, body = interfaces
    original = path.read_bytes()
    preview = asyncio.run(dash.preview_weekly_state_review(request(body), db))
    assert path.read_bytes() == original
    assert preview['preview_only'] and preview['needs_approval']
    assert preview['text'] == config['question'] + '\n\n@fixture_member'
    assert preview['selected_members'] == config['selected_members']
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.update_weekly_state_review(request(body), db))
    assert error.value.status_code == 409
    body.update(preview_receipt=preview['preview_receipt'], activation_approved=True)
    result = asyncio.run(dash.update_weekly_state_review(request(body), db))
    assert result['weekly_state_review']['revision'] == 4
    saved = yaml.safe_load(path.read_text())
    assert saved['weekly_state_review']['enabled'] is True
    assert saved['weekly_state_review']['audit'][0]['actor'] == 'dashboard'
    assert saved['unrelated'] == {'preserve': True}


def test_changed_preview_or_stale_revision_cannot_enable(interfaces):
    path, config, db, body = interfaces
    preview = asyncio.run(dash.preview_weekly_state_review(request(body), db))
    body.update(preview_receipt=preview['preview_receipt'], activation_approved=True)
    body['time'] = '20:00'
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.update_weekly_state_review(request(body), db))
    assert error.value.status_code == 409
    body['expected_revision'] = 2
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.preview_weekly_state_review(request(body), db))
    assert error.value.status_code == 409 and read_weekly_config(path) == config


@pytest.mark.parametrize('field,value', [
    ('timezone', 'invented/timezone'), ('time', '24:00'), ('time', '1:2'),
    ('tag_usernames', ['unknown_member']), ('tag_usernames', ['@bad space']),
    ('tag_usernames', ['@@fixture_member']), ('tag_usernames', [False]),
    ('selected_user_ids', [999]), ('selected_user_ids', [True]), ('enabled', 'true'),
    ('mode', 'unsupported'), ('days', [6, 5]), ('topic_id', 100),
])
def test_shared_validation_rejects_malformed_or_unknown_selection(interfaces, field, value):
    path, _, db, body = interfaces
    body[field] = value
    with pytest.raises(HTTPException):
        asyncio.run(dash.preview_weekly_state_review(request(body), db))
    assert read_weekly_config(path)['enabled'] is False


def test_agent_requires_auth_context_receipt_and_same_activation_receipt(interfaces, monkeypatch):
    path, _, db, body = interfaces
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.preview_weekly_state_review(request(body, authenticated=False), db))
    assert error.value.status_code == 401
    headers = {'authorization': 'Bearer synthetic-test-token', 'idempotency-key': 'fixture-action-123'}
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.preview_weekly_state_review(request(body, authenticated=False, headers=headers), db))
    assert error.value.status_code == 428
    context_gate = AsyncMock()
    monkeypatch.setattr(dash, '_require_community_context_receipt', context_gate)
    preview = asyncio.run(dash.preview_weekly_state_review(request(body, authenticated=False, headers=headers), db))
    body.update(preview_receipt=preview['preview_receipt'], activation_approved=True)
    result = asyncio.run(dash.update_weekly_state_review(request(body, authenticated=False, headers=headers), db))
    assert result['weekly_state_review']['revision'] == 4
    assert read_weekly_config(path)['audit'][0]['actor'] == 'agent'
    assert context_gate.await_count == 2
    db.complete_agent_api_action.assert_awaited_once()
    # Completed retries replay before applying a now-stale expected revision.
    db.begin_agent_api_action.return_value = ('complete', '{"status":"ok","revision":4}')
    replay = asyncio.run(dash.update_weekly_state_review(request(body, authenticated=False, headers=headers), db))
    assert replay.status_code == 200 and b'"revision":4' in replay.body
    assert read_weekly_config(path)['revision'] == 4


def test_versioned_writer_preserves_other_settings_and_refuses_lost_update(interfaces):
    path, config, _, _ = interfaces
    saved, applied = write_weekly_config(path, expected_revision=3, candidate=config, actor='fixture', action='configure')
    assert applied and saved['revision'] == 4
    with pytest.raises(StaleWeeklyConfig):
        write_weekly_config(path, expected_revision=3, candidate=config, actor='stale', action='configure')
    assert read_weekly_config(path)['revision'] == 4


def test_dst_gap_skips_that_week_and_fold_uses_first_occurrence():
    config = {'timezone': 'America/New_York', 'days': [0], 'time': '02:30'}
    occurrence = next_weekly_occurrence(config, datetime(2026, 3, 7, 12, tzinfo=timezone.utc))
    assert occurrence.date().isoformat() == '2026-03-15'
    config['time'] = '01:30'
    occurrence = next_weekly_occurrence(config, datetime(2026, 10, 31, 12, tzinfo=timezone.utc))
    assert occurrence.date().isoformat() == '2026-11-01' and occurrence.fold == 0
    assert occurrence.utcoffset().total_seconds() == -14400


def test_self_service_changes_only_own_id_once_in_recorded_context(interfaces, monkeypatch, tmp_path):
    path, config, _, _ = interfaces
    config['selected_members'].append({'user_id': 202, 'username': 'another_fixture'})
    write_weekly_config(path, expected_revision=3, candidate=config, actor='fixture', action='configure')
    monkeypatch.setattr(weekly, '_review_config', lambda: read_weekly_config(path))
    monkeypatch.setattr(weekly, 'GROUP_ID', -10099)
    send = AsyncMock()
    monkeypatch.setattr(weekly, 'safe_send', send)
    user = SimpleNamespace(id=202, username='another_fixture', is_bot=False)
    message = SimpleNamespace(message_id=77, message_thread_id=99, text='הסר אותי מהתיוג',
                              reply_to_message=SimpleNamespace(message_id=55))
    update = SimpleNamespace(message=message, effective_user=user, effective_chat=SimpleNamespace(id=-10099))

    async def scenario():
        db = Database(str(tmp_path/'membership.db'))
        await db.init()
        try:
            context = SimpleNamespace(bot=object(), bot_data={'db': db})
            # Unrelated replies cannot modify config or cause confirmation spam.
            await weekly.checkin_membership_reply(update, context)
            assert read_weekly_config(path)['revision'] == 4
            await db.record_weekly_checkin_post(-10099, 99, 55, 'fixture-week')
            await weekly.checkin_membership_reply(update, context)
            await weekly.checkin_membership_reply(update, context)
            return read_weekly_config(path)
        finally:
            await db.close()
    saved = asyncio.run(scenario())
    assert saved['selected_members'] == [{'user_id': 101, 'username': 'fixture_member'}]
    assert saved['excluded_user_ids'] == [202] and saved['revision'] == 5
    send.assert_awaited_once()
    assert send.await_args.kwargs['reply_to_message_id'] == 77


def test_pin_replaces_only_recorded_owned_weekly_pin(monkeypatch, tmp_path):
    monkeypatch.setattr(weekly, 'GROUP_ID', -10099)
    bot = SimpleNamespace(pin_chat_message=AsyncMock(), unpin_chat_message=AsyncMock())
    async def scenario():
        db = Database(str(tmp_path/'pins.db'))
        await db.init()
        try:
            await db.record_weekly_checkin_post(-10099, 99, 41, 'old-fixture-week')
            await db.set_weekly_checkin_pin(-10099, 99, 41, True)
            await weekly.record_delivered_checkin(bot, db, topic_id=99, message_id=55,
                                                 week_key='new-fixture-week', pin_enabled=True)
            assert await db.weekly_checkin_pins(-10099, 99) == [55]
        finally:
            await db.close()
    asyncio.run(scenario())
    bot.pin_chat_message.assert_awaited_once_with(chat_id=-10099, message_id=55, disable_notification=True)
    bot.unpin_chat_message.assert_awaited_once_with(chat_id=-10099, message_id=41)


def test_scheduler_registers_real_timezone_trigger_and_future_start(monkeypatch, tmp_path):
    from telegram.ext import JobQueue
    from bot.scheduler import jobs
    from bot.utils import config as config_module
    directory = tmp_path/'config'
    directory.mkdir()
    monkeypatch.setattr(config_module, 'CONFIG_DIR', directory)
    queue = JobQueue()
    config = {'enabled': True, 'days': [6], 'time': '19:00', 'timezone': 'Asia/Jerusalem',
              'not_before': '2026-10-10', 'revision': 7}
    status = jobs.setup_weekly_checkin_job(SimpleNamespace(job_queue=queue), {'weekly_state_review': config})
    assert status['registered'] is True
    next_due = datetime.fromisoformat(status['next_due'])
    assert next_due.weekday() == 5 and next_due.hour == 19
    assert next_due.date().isoformat() >= '2026-10-10'
    assert len(queue.jobs()) == 1
    assert queue.jobs()[0].name == 'weekly_state_review'
    config['enabled'] = False
    # A disabled fresh queue never registers a job or a catch-up callback.
    empty = JobQueue()
    assert jobs.setup_weekly_checkin_job(SimpleNamespace(job_queue=empty), {'weekly_state_review': config})['registered'] is False
    assert empty.jobs() == ()


@pytest.mark.parametrize('wrong_context', ['group', 'topic', 'reply', 'another_id', 'bot'])
def test_self_service_cannot_change_another_member_or_unrelated_context(interfaces, monkeypatch, wrong_context):
    path, config, _, _ = interfaces
    monkeypatch.setattr(weekly, '_review_config', lambda: read_weekly_config(path))
    monkeypatch.setattr(weekly, 'GROUP_ID', -10099)
    user = SimpleNamespace(id=101, username='fixture_member', is_bot=wrong_context == 'bot')
    message = SimpleNamespace(message_id=77, message_thread_id=100 if wrong_context == 'topic' else 99,
        text='הסר אותי מהתיוג @another_fixture' if wrong_context == 'another_id' else 'הסר אותי מהתיוג',
        reply_to_message=None if wrong_context == 'reply' else SimpleNamespace(message_id=55))
    update = SimpleNamespace(message=message, effective_user=user,
                             effective_chat=SimpleNamespace(id=-10098 if wrong_context == 'group' else -10099))
    db = SimpleNamespace(is_weekly_checkin_post=AsyncMock(return_value=True))
    send = AsyncMock()
    monkeypatch.setattr(weekly, 'safe_send', send)
    asyncio.run(weekly.checkin_membership_reply(update, SimpleNamespace(bot=object(), bot_data={'db': db})))
    assert read_weekly_config(path) == config
    send.assert_not_awaited()


def test_new_member_can_opt_in_without_handle_and_is_id_bound(interfaces, monkeypatch, tmp_path):
    path, _, _, _ = interfaces
    monkeypatch.setattr(weekly, '_review_config', lambda: read_weekly_config(path))
    monkeypatch.setattr(weekly, 'GROUP_ID', -10099)
    user = SimpleNamespace(id=303, username=None, is_bot=False)
    update = SimpleNamespace(effective_user=user, effective_chat=SimpleNamespace(id=-10099),
        message=SimpleNamespace(message_id=77, message_thread_id=99, text='תייג אותי',
                                reply_to_message=SimpleNamespace(message_id=55)))
    live = SimpleNamespace(status='member', user=user)
    bot = SimpleNamespace(get_chat_member=AsyncMock(return_value=live))
    send = AsyncMock()
    monkeypatch.setattr(weekly, 'safe_send', send)
    async def scenario():
        db = Database(str(tmp_path/'optin.db'))
        await db.init()
        try:
            await db.record_weekly_checkin_post(-10099, 99, 55, 'fixture-week')
            await weekly.checkin_membership_reply(update, SimpleNamespace(bot=bot, bot_data={'db': db}))
            saved = read_weekly_config(path)
            text, entities = weekly.render_weekly_state_review(saved)
            assert any(entity.user.id == 303 for entity in entities)
            assert {'user_id': 303, 'username': None} in saved['selected_members']
            assert any(item['user_id'] == 303 for item in await db.get_chat_members_for_tagging(-10099))
            return text
        finally:
            await db.close()
    assert '@all' not in asyncio.run(scenario())
    send.assert_awaited_once()
