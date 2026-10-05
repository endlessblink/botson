"""Offline integration: real ledger, reviewed provider mocks and Telegram stubs."""
import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiosqlite
import httpx
import pytest
import yaml
from fastapi import HTTPException

from bot.handlers import community_participation as runtime
from bot.scheduler import materializer
from bot.utils.community_participation_store import ParticipationReviewStore
from bot.utils.participation_config import read_config, save_config, validate_config
from bot.utils.pin_manager import pin_replacing_previous
from bot.utils import participation_feeds as feeds
from dashboard import app as dash

NOW = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
SUMMARY = 'העדכון החדש מאפשר לשמור את ההגדרות בין מחשבים.'


class Frozen(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


@pytest.fixture
def config():
    value = yaml.safe_load((Path(__file__).parents[1]/'config/community_participation.yaml').read_text())
    value['topic_news'].update(enabled=True, mode='auto_send', timezone='Asia/Jerusalem',
        dedupe_scope='chat', freshness_hours=24, verification_max_age_hours=1,
        context_max_age_minutes=180, minimum_relevance=0.85, global_daily_cap=2,
        topic_daily_cap=1, quiet_hours=[['22:00','09:00']], times=['11:15','17:15'],
        sources={'fixture':{'enabled':True,'feed_url':'https://publisher.example.test/feed',
                            'allowed_domains':['publisher.example.test'],'topic_label':'Synthetic technology'}},
        topic_sources={99:['fixture']})
    value['occasional_replies'].update(enabled=True, mode='auto_send', timezone='Asia/Jerusalem',
        allowed_topics=[99], allow_unsolicited=False, minimum_value=0.9, global_daily_cap=3,
        topic_daily_cap=1, thread_daily_cap=1, cooldown_minutes=120, cooldown_scope='topic',
        context_max_age_minutes=180, quiet_hours=[['22:00','09:00']])
    return value


@pytest.fixture
def integrated(monkeypatch, tmp_path, config):
    ledger = ParticipationReviewStore(tmp_path/'review.db')
    asyncio.run(ledger.initialize())
    monkeypatch.setattr(runtime, 'store', lambda: ledger)
    monkeypatch.setattr(runtime, 'read_config', lambda: deepcopy(config))
    monkeypatch.setattr(runtime, 'GROUP_ID', -10099)
    monkeypatch.setattr(runtime, 'datetime', Frozen)
    monkeypatch.setattr(runtime, 'load_copy', lambda namespace, key, **kw:
        kw['summary']+'\n'+kw['source_url'] if key=='news_post' else '[configured copy]')
    db = SimpleNamespace(get_verified_forum_topics=AsyncMock(return_value=[{'topic_id':99}]),
        get_recent_community_messages=AsyncMock(return_value=[{'thread_id':99,'message_id':4,
            'occurred_at':NOW.isoformat(),'text':'Synthetic same-topic context'}]))
    bot = SimpleNamespace(id=777, username='fixture_bot')
    article = {'source_id':'fixture','source_url':'https://publisher.example.test/story',
        'published_at':NOW.isoformat(),'verified_at':NOW.isoformat(),'source_verified':True,
        'title':'Synthetic update','source_excerpt':'Synthetic publisher text', 'event_id':'article:fixture'}
    monkeypatch.setattr(runtime, 'fetch_articles', AsyncMock(return_value=[article]))
    monkeypatch.setattr(runtime, 'generate', AsyncMock(side_effect=[json.dumps({'summary':SUMMARY,'sensitive':False}),
        json.dumps({'pass':True,'factually_supported':True,'sensitive':False,'relevance':0.95,
                    'duplicate_event':False,'event_key':'fixture-product-20261006'})]))
    send = AsyncMock(return_value=SimpleNamespace(message_id=101))
    monkeypatch.setattr(runtime, 'safe_send', send)
    return ledger, db, bot, article, send


def test_exact_reviewed_news_is_sent_once_and_retains_source(config, integrated):
    ledger, db, bot, _, send = integrated
    async def scenario():
        prepared, reason = await runtime.prepare_news(db, config, topic_id=99, source_id='fixture', now=NOW)
        assert reason is None and 'source_excerpt' not in prepared['candidate']
        assert await runtime.deliver(bot, db, config, 'topic_news', prepared) == 'sent'
        assert await runtime.deliver(bot, db, config, 'topic_news', prepared) == 'already_recorded'
        assert await ledger.status(prepared['key']) == 'sent'
    asyncio.run(scenario())
    send.assert_awaited_once()
    assert send.call_args.kwargs['text'] == SUMMARY+'\nhttps://publisher.example.test/story'
    assert send.call_args.kwargs['message_thread_id'] == 99
    assert send.call_args.kwargs['disable_notification'] is True


@pytest.mark.parametrize('change', ['context','policy','opt_out'])
def test_revalidation_cancels_without_sending_on_changed_state(config, integrated, monkeypatch, change):
    ledger, db, bot, _, send = integrated
    async def scenario():
        prepared, _ = await runtime.prepare_news(db, config, topic_id=99, source_id='fixture', now=NOW)
        if change=='context':
            db.get_recent_community_messages.return_value=[]
        elif change=='policy':
            changed=deepcopy(config); changed['topic_news']['enabled']=False
            monkeypatch.setattr(runtime,'read_config',lambda:changed)
        else:
            await ledger.set_topic_opt_out('topic_news',-10099,99,opted_out=True,at=NOW)
        assert await runtime.deliver(bot,db,config,'topic_news',prepared)==('persisted_opt_out' if change=='opt_out' else 'changed_context_or_policy')
        assert await ledger.status(prepared['key'])==(None if change=='opt_out' else 'cancelled')
    asyncio.run(scenario()); send.assert_not_called()


def test_uncertain_send_holds_identity_and_is_never_retried(config, integrated):
    ledger, db, bot, _, send = integrated
    send.side_effect=RuntimeError('synthetic transport uncertainty')
    async def scenario():
        prepared,_=await runtime.prepare_news(db,config,topic_id=99,source_id='fixture',now=NOW)
        assert await runtime.deliver(bot,db,config,'topic_news',prepared)=='uncertain'
        assert await runtime.deliver(bot,db,config,'topic_news',prepared)=='already_recorded'
        assert await ledger.status(prepared['key'])=='uncertain'
    asyncio.run(scenario()); assert send.await_count==1


@pytest.mark.parametrize('reason',['empty_context','unverified_topic','stale_article','sensitive','review_unavailable','cap'])
def test_news_skips_unsuitable_or_unavailable_inputs(config, integrated, monkeypatch, reason):
    ledger, db, bot, article, send = integrated
    if reason=='empty_context': db.get_recent_community_messages.return_value=[]
    if reason=='unverified_topic': db.get_verified_forum_topics.return_value=[]
    if reason=='stale_article': article['published_at']='2026-09-01T12:00:00+00:00'
    if reason=='sensitive': runtime.generate.side_effect=[json.dumps({'summary':SUMMARY,'sensitive':True})]
    if reason=='review_unavailable': runtime.generate.side_effect=[json.dumps({'summary':SUMMARY,'sensitive':False}),None]
    if reason=='cap':
        monkeypatch.setattr(ledger,'available',AsyncMock(return_value=False))
    prepared,_=asyncio.run(runtime.prepare_news(db,config,topic_id=99,source_id='fixture',now=NOW))
    assert prepared is None; send.assert_not_called()
    if reason in {'empty_context','unverified_topic','cap','stale_article'}: runtime.generate.assert_not_called()


def test_mentions_only_reply_uses_durable_own_id_opt_out(config, integrated, monkeypatch):
    ledger, db, bot, _, send = integrated
    message=SimpleNamespace(message_id=5,message_thread_id=99,text='@fixture_bot בדיקת תשובה',reply_to_message=None,date=NOW)
    user=SimpleNamespace(id=123,is_bot=False)
    update=SimpleNamespace(effective_message=message,effective_user=user,effective_chat=SimpleNamespace(id=-10099))
    ctx=SimpleNamespace(bot=bot,bot_data={'db':db})
    runtime.generate.side_effect=[json.dumps({'decision':'reply','text':'אפשר לשמור את ההגדרות מהתפריט.','sensitive':False}),
        json.dumps({'pass':True,'sensitive':False,'value':0.95})]
    asyncio.run(runtime.handle_reply(update,ctx)); send.assert_awaited_once()
    assert send.call_args.kwargs['reply_to_message_id']==5
    assert send.call_args.kwargs['reply_markup'].inline_keyboard[0][0].callback_data=='participation_opt_out'
    asyncio.run(ledger.set_member_opt_out(-10099,123,actor_user_id=123,opted_out=True,at=NOW))
    message.message_id=6; send.reset_mock(); runtime.generate.reset_mock()
    asyncio.run(runtime.handle_reply(update,ctx)); send.assert_not_called(); runtime.generate.assert_not_called()


@pytest.mark.parametrize('case',['unsolicited','bot','wrong_chat','unselected_topic','quiet'])
def test_ineligible_reply_never_invokes_provider(config, integrated, case):
    _,db,bot,_,send=integrated
    message=SimpleNamespace(message_id=5,message_thread_id=99,text='@fixture_bot בדיקה',reply_to_message=None,date=NOW)
    user=SimpleNamespace(id=123,is_bot=False); chat=SimpleNamespace(id=-10099)
    if case=='unsolicited': message.text='Ordinary conversation'
    if case=='bot': user.is_bot=True
    if case=='wrong_chat': chat.id=-20099
    if case=='unselected_topic': message.message_thread_id=100
    if case=='quiet': config['occasional_replies']['quiet_hours']=[['00:00','23:59']]
    asyncio.run(runtime.handle_reply(SimpleNamespace(effective_message=message,effective_user=user,effective_chat=chat),
        SimpleNamespace(bot=bot,bot_data={'db':db})))
    send.assert_not_called(); runtime.generate.assert_not_called()


@pytest.mark.parametrize('field,value', [('global_daily_cap',0),('minimum_value',float('nan')),
    ('cooldown_minutes',float('inf')),('allow_unsolicited',True),('allowed_topics',[100]),('timezone','bad/zone')])
def test_configuration_rejects_invalid_or_broader_reply_policy(config,field,value):
    config['occasional_replies'][field]=value
    with pytest.raises((ValueError,KeyError)):
        validate_config(config,{99})


def test_config_cas_and_weekly_file_preservation(config,tmp_path):
    path=tmp_path/'community_participation.yaml'; path.write_text(yaml.safe_dump(config))
    weekly=tmp_path/'settings.yaml'; weekly.write_text('weekly_state_review: {revision: 7, enabled: true}\n')
    before=weekly.read_bytes()
    saved=save_config(path,config,expected_revision=0,verified_topics={99},actor='synthetic operator')
    assert saved['revision']==1 and weekly.read_bytes()==before
    with pytest.raises(RuntimeError): save_config(path,config,expected_revision=0,verified_topics={99},actor='retry')
    assert read_config(path)['revision']==1


def test_feed_adapter_uses_publisher_dates_domains_and_byte_budget(config,monkeypatch):
    monkeypatch.setattr(feeds,'public_host',AsyncMock())
    rss=b'''<rss version="2.0"><channel><item><title>Fixture</title><link>https://publisher.example.test/story</link><description>Publisher excerpt</description><pubDate>Tue, 06 Oct 2026 12:00:00 GMT</pubDate></item><item><title>No date</title><link>https://publisher.example.test/undated</link><description>Excerpt</description></item><item><title>Wrong domain</title><link>https://other.example.test/story</link><description>Excerpt</description><pubDate>Tue, 06 Oct 2026 12:00:00 GMT</pubDate></item></channel></rss>'''
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=rss))) as client:
            articles=await feeds.fetch_articles('fixture',config['topic_news']['sources']['fixture'],config['runtime'],now=NOW,client=client)
            assert len(articles)==1 and articles[0]['source_verified'] is True
            assert articles[0]['published_at']==NOW.isoformat()
            tiny=deepcopy(config['runtime']); tiny['max_feed_bytes']=10
            with pytest.raises(ValueError): await feeds.fetch_articles('fixture',config['topic_news']['sources']['fixture'],tiny,now=NOW,client=client)
    asyncio.run(scenario())


def test_existing_cli_failure_does_not_read_or_use_paid_api(monkeypatch,tmp_path):
    monkeypatch.setattr(materializer.shutil,'which',lambda _:None)
    monkeypatch.setattr(materializer.os.path,'expanduser',lambda _:str(tmp_path/'missing-cli'))
    assert asyncio.run(materializer._generate_with_claude('synthetic',allow_paid_fallback=False)) is None


def test_general_pins_preserve_weekly_ownership_and_retry_failed_unpin(tmp_path):
    async def scenario():
        async with aiosqlite.connect(str(tmp_path/'pins.db')) as conn:
            db=SimpleNamespace(_db=conn); bot=AsyncMock()
            await conn.execute('CREATE TABLE weekly_checkin_posts(message_id INTEGER,pinned INTEGER)')
            await conn.execute('INSERT INTO weekly_checkin_posts VALUES(40,1)'); await conn.commit()
            await pin_replacing_previous(bot,db,chat_id=-10099,topic_id=99,message_id=10)
            bot.unpin_chat_message.side_effect=RuntimeError('synthetic temporary failure')
            await pin_replacing_previous(bot,db,chat_id=-10099,topic_id=99,message_id=11)
            async with conn.execute('SELECT message_id FROM bot_pins ORDER BY message_id') as cur:
                assert [r[0] for r in await cur.fetchall()]==[10,11]
            bot.unpin_chat_message.side_effect=None
            await pin_replacing_previous(bot,db,chat_id=-10099,topic_id=99,message_id=12)
            async with conn.execute('SELECT message_id FROM bot_pins') as cur: assert await cur.fetchall()==[(12,)]
            async with conn.execute('SELECT pinned FROM weekly_checkin_posts WHERE message_id=40') as cur: assert await cur.fetchone()==(1,)
            assert all(call.kwargs['message_id']!=40 for call in bot.unpin_chat_message.await_args_list)
    asyncio.run(scenario())


def test_shared_configuration_requires_exact_activation_and_auth(config,tmp_path,monkeypatch):
    path=tmp_path/'community_participation.yaml'; path.write_text(yaml.safe_dump(config))
    monkeypatch.setattr(dash,'CONFIG_DIR',tmp_path)
    db=SimpleNamespace(get_verified_forum_topics=AsyncMock(return_value=[{'topic_id':99}]))
    def request(body,auth=True):
        return SimpleNamespace(session={'authenticated':auth},headers={},state=SimpleNamespace(),json=AsyncMock(return_value=deepcopy(body)))
    body={'config':config,'expected_revision':0}
    with pytest.raises(HTTPException) as err: asyncio.run(dash.preview_participation_settings(request(body,False),db))
    assert err.value.status_code==401
    preview=asyncio.run(dash.preview_participation_settings(request(body),db))
    original=path.read_bytes()
    with pytest.raises(HTTPException) as err: asyncio.run(dash.update_participation_settings(request(body),db))
    assert err.value.status_code==409 and path.read_bytes()==original
    body.update(preview_receipt=preview['preview_receipt'],activation_approved=True)
    body['config']['topic_news']['global_daily_cap']=3
    with pytest.raises(HTTPException) as err: asyncio.run(dash.update_participation_settings(request(body),db))
    assert err.value.status_code==409
    body['config']['topic_news']['global_daily_cap']=2
    result=asyncio.run(dash.update_participation_settings(request(body),db))
    assert result['config']['revision']==1 and (tmp_path.parent/'data/reload_participation.flag').exists()


def test_news_http_callers_cannot_manufacture_verification(monkeypatch):
    request=SimpleNamespace(session={'authenticated':True},headers={},json=AsyncMock(return_value={
        'topic_id':99,'source_id':'fixture','source_verified':True,'relevance':1}))
    with pytest.raises(HTTPException) as err: asyncio.run(dash.preview_participation_news(request,SimpleNamespace()))
    assert err.value.status_code==422


def test_cli_only_mode_has_no_tools_persistence_or_paid_credentials(monkeypatch,tmp_path):
    from bot.utils import cli_home
    executable=tmp_path/'fixture-cli'; executable.write_text('synthetic')
    monkeypatch.setattr(materializer.shutil,'which',lambda _:str(executable))
    monkeypatch.setattr(cli_home,'claude_cli_env',lambda:{'ANTHROPIC_API_KEY':'synthetic-key','ANTHROPIC_AUTH_TOKEN':'synthetic-token','SAFE_MARKER':'keep'})
    process=SimpleNamespace(returncode=0,communicate=AsyncMock(return_value=(b'{"decision":"skip"}',b'')))
    spawn=AsyncMock(return_value=process)
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    result=asyncio.run(materializer._generate_with_claude('synthetic prompt',allow_paid_fallback=False))
    assert result=='{"decision":"skip"}'
    args=spawn.call_args.args; env=spawn.call_args.kwargs['env']
    assert args[args.index('--tools')+1]=='' and '--no-session-persistence' in args
    assert args[args.index('--disallowedTools')+1]=='*'
    assert args[args.index('--setting-sources')+1]=='' and '--strict-mcp-config' in args
    assert 'ANTHROPIC_API_KEY' not in env and 'ANTHROPIC_AUTH_TOKEN' not in env
    assert env['SAFE_MARKER']=='keep'


def test_news_preview_does_not_prune_context_rows_or_write_review_rows(config,integrated):
    ledger,db,_,_,_=integrated
    before=ledger.path.read_bytes()
    asyncio.run(runtime.prepare_news(db,config,topic_id=99,source_id='fixture',now=NOW))
    assert ledger.path.read_bytes()==before
    assert db.get_recent_community_messages.call_args.kwargs['prune_expired'] is False


def test_pin_failure_never_removes_previous_owned_pin(tmp_path):
    async def scenario():
        async with aiosqlite.connect(str(tmp_path/'pins.db')) as conn:
            db=SimpleNamespace(_db=conn); bot=AsyncMock()
            await pin_replacing_previous(bot,db,chat_id=-10099,topic_id=99,message_id=10)
            bot.pin_chat_message.side_effect=RuntimeError('synthetic denied pin')
            with pytest.raises(RuntimeError): await pin_replacing_previous(bot,db,chat_id=-10099,topic_id=99,message_id=11)
            bot.unpin_chat_message.assert_not_called()
            async with conn.execute('SELECT message_id FROM bot_pins') as cur: assert await cur.fetchall()==[(10,)]
    asyncio.run(scenario())


def test_real_participation_page_renders_configured_sources_and_valid_script(config,tmp_path,monkeypatch):
    import re,shutil,subprocess
    from starlette.requests import Request
    (tmp_path/'community_participation.yaml').write_text(yaml.safe_dump(config))
    monkeypatch.setattr(dash,'CONFIG_DIR',tmp_path)
    req=Request({'type':'http','method':'GET','path':'/participation','root_path':'','scheme':'http',
        'server':('localhost',8000),'headers':[],'query_string':b'','session':{'authenticated':True},'app':dash.app,'router':dash.app.router})
    db=SimpleNamespace(get_verified_forum_topics=AsyncMock(return_value=[{'topic_id':99,'verified_name':'Synthetic topic'}]))
    rendered=asyncio.run(dash.participation_page(req,db)).body.decode()
    assert 'https://publisher.example.test/feed' in rendered and 'expected_revision' in rendered
    assert 'preview_receipt' in rendered and 'activation_approved' in rendered
    scripts=re.findall(r'<script[^>]*>([\s\S]*?)</script>',rendered)
    node=shutil.which('node')
    if node:
        target=tmp_path/'participation.js'; target.write_text('\n'.join(scripts))
        outcome=subprocess.run([node,'--check',str(target)],capture_output=True,text=True)
        assert outcome.returncode==0,outcome.stderr


def test_ordinary_quiet_cycle_does_not_fetch_generate_or_send(config,integrated,tmp_path,monkeypatch):
    _,db,bot,_,send=integrated
    directory=tmp_path/'config'; directory.mkdir(); (tmp_path/'data').mkdir()
    monkeypatch.setattr(runtime,'CONFIG_DIR',directory)
    config['topic_news']['quiet_hours']=[['00:00','23:59']]
    asyncio.run(runtime.run_news_cycle(SimpleNamespace(bot=bot,bot_data={'db':db})))
    report=json.loads((tmp_path/'data/participation-cycle-status.json').read_text())
    assert report['sent']==0 and report['outcomes']==['quiet_hours']
    runtime.fetch_articles.assert_not_called(); runtime.generate.assert_not_called(); send.assert_not_called()


def test_job_reconfiguration_preserves_weekly_job_and_uses_approved_times(config,integrated,tmp_path,monkeypatch):
    _,db,_,_,_=integrated
    directory=tmp_path/'config'; directory.mkdir(); (tmp_path/'data').mkdir()
    monkeypatch.setattr(runtime,'CONFIG_DIR',directory)
    jobs=[SimpleNamespace(name='weekly_state_review')]
    class Queue:
        def get_jobs_by_name(self,name): return [j for j in jobs if j.name==name]
        def run_daily(self,callback,at,name):
            job=SimpleNamespace(name=name,at=at)
            job.schedule_removal=lambda:jobs.remove(job)
            jobs.append(job)
    app=SimpleNamespace(job_queue=Queue(),bot_data={'db':db})
    status=asyncio.run(runtime.configure_jobs(app))
    assert status['news_jobs']==2
    assert [(j.at.hour,j.at.minute) for j in jobs if j.name=='topic_news']==[(11,15),(17,15)]
    assert all(str(j.at.tzinfo)=='Asia/Jerusalem' for j in jobs if j.name=='topic_news')
    assert len([j for j in jobs if j.name=='weekly_state_review'])==1
    asyncio.run(runtime.configure_jobs(app))
    assert len([j for j in jobs if j.name=='topic_news'])==2


def test_member_button_changes_only_own_opt_out(config,integrated):
    ledger,_,_,_,_=integrated
    query=SimpleNamespace(data='participation_opt_out',message=SimpleNamespace(chat=SimpleNamespace(id=-10099)),
                          answer=AsyncMock(),edit_message_reply_markup=AsyncMock())
    update=SimpleNamespace(callback_query=query,effective_user=SimpleNamespace(id=123,is_bot=False))
    asyncio.run(runtime.member_opt_out(update,SimpleNamespace()))
    async def check():
        first=await ledger.available('occasional_replies',{'topic_id':99,'sender_user_id':123,'conversation_key':'fixture'},config['occasional_replies'],now=NOW,chat_id=-10099)
        other=await ledger.available('occasional_replies',{'topic_id':99,'sender_user_id':124,'conversation_key':'fixture'},config['occasional_replies'],now=NOW,chat_id=-10099)
        return first,other
    assert asyncio.run(check())==(False,True)
    query.data='participation_opt_in'; asyncio.run(runtime.member_opt_out(update,SimpleNamespace()))
    assert asyncio.run(check())==(True,True)


def test_private_context_redacts_identifiers_before_generation():
    redacted=runtime.private_context_text('Contact @fixture_member at fixture@example.test or +972-50-123-4567')
    assert '@fixture_member' not in redacted and 'fixture@example.test' not in redacted and '123-4567' not in redacted
