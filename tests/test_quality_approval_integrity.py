"""Synthetic acceptance-boundary regressions; no providers or live messages."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from bot.database.db import Database
from bot.utils import conversation_quality as quality
from dashboard import app as dash


ORIGINAL = 'איזה ספר גרם לכם לשנות דעה על דמות ששנאתם בהתחלה?'
PARAPHRASE = 'על דמות ששנאתם בהתחלה איזה ספר גרם לכם לשנות דעה?'


def request(body):
    return SimpleNamespace(session={'authenticated': True}, state=SimpleNamespace(), headers={},
        json=AsyncMock(return_value=body), body=AsyncMock(return_value=json.dumps(body).encode()))


def approved(text, topic=99, day='2099-01-01', kind='discussion'):
    return {'date': day, 'time': '09:00', 'message_type': kind, 'topic_id': topic,
            'text': text, 'source': 'ai-fill-flex'}


@pytest.mark.parametrize('second', [ORIGINAL, PARAPHRASE])
def test_checked_batch_dedupes_same_topic_before_scheduling(monkeypatch, tmp_path, second):
    reviewer = AsyncMock(return_value={0: (True, 'fixture'), 1: (True, 'fixture'), 2: (True, 'fixture')})
    monkeypatch.setattr(dash, '_review_discussion_quality_batch', reviewer)
    async def scenario():
        db = Database(str(tmp_path/'approval.db'))
        await db.init()
        try:
            result = await dash.ai_suggest_commit(request({'approved': [
                approved(ORIGINAL), approved(second, day='2099-01-02', kind='morning'),
                approved(second, topic=100, day='2099-01-03'),
            ]}), db)
            rows = await db.get_scheduled_messages('2099-01-01', '2099-01-03')
            return result, rows
        finally:
            await db.close()
    result, rows = asyncio.run(scenario())
    assert result['inserted'] == 2
    assert [row['channel_topic_id'] for row in rows] == [99, 100]
    assert any('repeated' in error or 'near-duplicate' in error for error in result['errors'])
    assert result['pending'][0]['date'] == '2099-01-02'


def test_commit_dedupes_cross_type_history_but_not_test_group(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, '_review_discussion_quality_batch', AsyncMock(return_value={0:(True,'fixture')}))
    async def scenario():
        db = Database(str(tmp_path/'history.db'))
        await db.init()
        try:
            await db.create_scheduled_message(ORIGINAL, 'morning', 99, 'main', '2098-01-01', '09:00', status='scheduled')
            rejected = await dash.ai_suggest_commit(request({'approved':[approved(PARAPHRASE)]}), db)
            await db.create_scheduled_message(ORIGINAL, 'evening', 100, 'test', '2098-01-01', '09:00', status='sent')
            accepted = await dash.ai_suggest_commit(request({'approved':[approved(ORIGINAL, topic=100)]}), db)
            return rejected, accepted
        finally:
            await db.close()
    rejected, accepted = asyncio.run(scenario())
    assert rejected['inserted'] == 0 and 'near-duplicate' in rejected['errors'][0]
    assert accepted['inserted'] == 1


def test_missing_dedup_history_fails_closed_before_insert(monkeypatch):
    db = SimpleNamespace(_db=None, create_scheduled_message=AsyncMock())
    monkeypatch.setattr(dash, '_review_discussion_quality_batch', AsyncMock(return_value={}))
    result = asyncio.run(dash.ai_suggest_commit(request({'approved':[approved(ORIGINAL)]}), db))
    assert result['inserted'] == 0 and 'history unavailable' in result['errors'][0]
    db.create_scheduled_message.assert_not_awaited()


def test_approval_reviewer_receives_resolved_topic_category(monkeypatch, tmp_path):
    reviewer=AsyncMock(return_value={0:(True,'fixture')})
    monkeypatch.setattr(dash,'_review_discussion_quality_batch',reviewer)
    monkeypatch.setattr(dash,'_discussion_category_for_topic',lambda _: 'fixture-topic')
    async def scenario():
        db=Database(str(tmp_path/'category.db'))
        await db.init()
        try:
            result=await dash.ai_suggest_commit(request({'approved':[approved(ORIGINAL)]}),db)
            assert result['inserted']==1
        finally:
            await db.close()
    asyncio.run(scenario())
    assert reviewer.await_args.args[0][0]['category']=='fixture-topic'


def test_unrelated_admin_save_cannot_undo_concurrent_opt_out(monkeypatch, tmp_path):
    from copy import deepcopy
    import yaml
    from bot.utils.weekly_checkin_config import write_weekly_config, read_weekly_config
    path=tmp_path/'settings.yaml'
    config={'revision':1,'enabled':True,'mode':'auto_send','days':[6],'time':'19:00',
            'timezone':'Asia/Jerusalem','pin_enabled':True,'selected_members':[{'user_id':101,'username':'fixture_member'}],
            'tag_usernames':['fixture_member'],'excluded_user_ids':[],'audit':[]}
    old={'weekly_state_review':config,'unrelated_setting':'old'}
    path.write_text(yaml.safe_dump(old))
    stale=deepcopy(old)
    write_weekly_config(path,expected_revision=None,actor='member:101',action='opt_out',
                        member_change=({'user_id':101,'username':'fixture_member'},False))
    latest=read_weekly_config(path)
    stale['unrelated_setting']='new'
    monkeypatch.setattr(dash,'CONFIG_DIR',tmp_path)
    dash._save_settings_file(stale)
    saved=yaml.safe_load(path.read_text())
    assert saved['unrelated_setting']=='new'
    assert saved['weekly_state_review']==latest
    assert latest['revision']==2 and latest['selected_members']==[]
    assert latest['enabled'] is True and latest['pin_enabled'] is True


def test_topic_context_is_selected_before_limit_and_keeps_feed_global_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, 'GROUP_ID', -10099)
    async def scenario():
        db = Database(str(tmp_path/'context.db'))
        await db.init()
        try:
            now = datetime.now(timezone.utc)
            await db.record_recent_community_message(chat_id=-10099, message_id=1, thread_id=99,
                sender_name='Fixture', text='synthetic chosen-topic context',
                occurred_at=(now-timedelta(minutes=30)).isoformat())
            for number in range(10,22):
                await db.record_recent_community_message(chat_id=-10099, message_id=number, thread_id=100,
                    sender_name='Fixture', text='synthetic other-topic noise', occurred_at=now.isoformat())
            sent_id = await db.create_scheduled_message('synthetic chosen-topic bot output','custom',99,'main',
                                                       '2099-01-01','09:00',status='sent')
            await db.mark_message_sent(sent_id,999)
            selected = await dash._collect_community_messages(db,1,2,thread_id=99)
            global_feed = await dash._collect_community_messages(db,1,2)
            return selected,global_feed
        finally:
            await db.close()
    selected, global_feed = asyncio.run(scenario())
    assert len(selected)==2 and all(item['thread_id']==99 for item in selected)
    assert any(item['text']=='synthetic chosen-topic context' for item in selected)
    assert any(item['thread_id']==100 for item in global_feed)


@pytest.mark.parametrize('failure', [None, RuntimeError('synthetic provider unavailable')])
def test_alternative_generator_outage_has_no_candidate(monkeypatch, failure):
    monkeypatch.setattr(quality,'load_yaml',lambda _: {
        'alternative_attempts':2,'alternative_max_chars':240,
        'alternative_prompt':'{context}\n{guidance}\n{max_chars}'})
    generator=AsyncMock(side_effect=failure, return_value=None)
    reviewer=AsyncMock()
    result=asyncio.run(quality.suggest_conversation_alternative(ORIGINAL,reason='fixture',category='fixture',
        recent_texts=[],guidance='fixture',community_context=['fixture'],generate=generator,
        review=reviewer,validate=lambda _: []))
    assert result['text']=='' and result['needs_approval'] is True
    reviewer.assert_not_awaited()


def test_alternative_retry_cannot_paraphrase_a_rejected_attempt(monkeypatch):
    monkeypatch.setattr(quality,'load_yaml',lambda _: {
        'alternative_attempts':2,'alternative_max_chars':240,
        'alternative_prompt':'{context}\n{guidance}\n{max_chars}'})
    reviewer=AsyncMock(side_effect=[(False,'fixture rejection'),(True,'must not be used')])
    result=asyncio.run(quality.suggest_conversation_alternative('unrelated rejected fixture',reason='fixture',
        category='fixture',recent_texts=[],guidance='fixture',community_context=['fixture'],
        generate=AsyncMock(side_effect=[ORIGINAL,PARAPHRASE]),review=reviewer,validate=lambda _: []))
    assert result['text']=='' and 'repeats' in result['reason']
    reviewer.assert_awaited_once()


@pytest.mark.parametrize('fields',[{'created_by':'weekly-checkin'},{'target_group':'test'}])
def test_special_or_other_group_draft_never_uses_main_group_alternative_context(fields):
    db=SimpleNamespace(get_scheduled_message=AsyncMock(return_value={'status':'draft',**fields}))
    with pytest.raises(HTTPException) as error:
        asyncio.run(dash.suggest_calendar_alternative(1,request({}),db))
    assert error.value.status_code==422


def test_feedback_http_auth_capture_filters_and_restart_memory(monkeypatch, tmp_path):
    monkeypatch.setattr(dash,'DASHBOARD_PASSWORD','synthetic-fixture-password')
    monkeypatch.setattr(dash,'_RECENT_FEEDBACK_CACHE',{'__global__':[]})
    abstraction=AsyncMock()
    monkeypatch.setattr(dash,'_schedule_rule_abstraction',abstraction)
    async def scenario():
        db=Database(str(tmp_path/'feedback.db'))
        await db.init()
        old_overrides=dict(dash.app.dependency_overrides)
        dash.app.dependency_overrides[dash.get_db]=lambda: db
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=dash.app),base_url='http://fixture') as client:
                body={'source':'fixture','content_type':'discussion','topic_key':'fixture-topic',
                      'original_text':ORIGINAL,'verdict':'rejected','reason':'A concrete synthetic reason long enough for abstraction',
                      'suggestion_metadata':{'fixture':True}}
                denied=await client.post('/api/content-feedback',json=body)
                assert denied.status_code==401
                login=await client.post('/login',data={'password':'synthetic-fixture-password'})
                assert login.status_code==303
                saved=await client.post('/api/content-feedback',json=body)
                assert saved.status_code==200 and saved.json()['id']>0
                listed=await client.get('/api/content-feedback',params={'content_type':'discussion','verdict':'rejected'})
                assert listed.status_code==200
                rows=await db.list_content_feedback(verdict='rejected')
                assert len(rows)==1 and json.loads(rows[0]['suggestion_metadata'])=={'fixture':True}
                abstraction.assert_awaited_once()
                dash._RECENT_FEEDBACK_CACHE.clear()
                await dash._hydrate_recent_feedback_cache(db)
                assert 'fixture-topic' in dash._RECENT_FEEDBACK_CACHE
                assert dash._RECENT_FEEDBACK_CACHE['fixture-topic'][0]['id']==saved.json()['id']
        finally:
            dash.app.dependency_overrides.clear();dash.app.dependency_overrides.update(old_overrides)
            await db.close()
    asyncio.run(scenario())
