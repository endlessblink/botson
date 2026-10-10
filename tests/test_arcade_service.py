"""Local-only game proof: authenticated scope, replay, timing and real SQLite."""
import asyncio
import hashlib
import hmac
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock
from urllib.parse import urlencode

import httpx
import pytest
import yaml

from services.arcade.app import create_app
from services.arcade.auth import player_key,verify_init_data
from services.arcade.engine import Engine,GameError

TOKEN='synthetic-token-for-offline-tests'
NOW=1791288000


class Clock:
    def __init__(self):self.at=NOW
    def __call__(self):return self.at
    def advance(self,value):self.at+=value


def signed(*,user_id=123,at=NOW,is_bot=False):
    fields={'auth_date':str(at),'user':json.dumps({'id':user_id,'is_bot':is_bot,'first_name':'Synthetic'})}
    secret=hmac.new(b'WebAppData',TOKEN.encode(),hashlib.sha256).digest()
    text='\n'.join(f'{k}={v}' for k,v in sorted(fields.items()))
    fields['hash']=hmac.new(secret,text.encode(),hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def cfg():return yaml.safe_load((Path(__file__).parents[1]/'config/arcade.yaml').read_text())['service']


@pytest.fixture
def game(cfg,tmp_path):
    clock=Clock();cfg['max_rounds']=3
    engine=Engine(cfg,tmp_path/'game.db',secret=b'synthetic-secret',clock=clock,alias_prefix='Player')
    session=engine.admit(player_key(TOKEN,123))
    return engine,session,clock


def ready(engine,clock,round_number):
    clock.advance((round_number*(engine.settings['flash_ms']+engine.settings['gap_ms'])+engine.settings['ready_ms'])/1000)


def test_signed_admission_is_fresh_and_does_not_return_profile():
    assert verify_init_data(signed(),TOKEN,now=NOW,max_age=180)==123


@pytest.mark.parametrize('raw',[signed().replace('Synthetic','Changed'),signed(at=NOW-181),signed(at=NOW+1),
                               signed(is_bot=True),signed()+'&auth_date=1','',signed(user_id=-1)])
def test_invalid_admission_is_refused(raw):
    with pytest.raises((ValueError,TypeError)):verify_init_data(raw,TOKEN,now=NOW,max_age=180)


def test_server_score_and_exact_replay_survive_store_reopen(game,cfg):
    engine,session,clock=game; current=engine.start(session)
    for turn in range(1,4):
        ready(engine,clock,turn)
        previous=current
        current=engine.answer(session,current['run_id'],turn,current['sequence'])
        assert engine.answer(session,previous['run_id'],turn,previous['sequence'])==current
        assert current['score']==turn*cfg['points_per_round']
    assert current['finished'] is True
    board=engine.leaderboard();assert board[0]['score']==300 and set(board[0])=={'rank','alias','score'}
    reopened=Engine(cfg,engine.db_path,secret=b'synthetic-secret',clock=clock,alias_prefix='Player')
    assert reopened.leaderboard()==board


def test_early_wrong_foreign_and_changed_replay_cannot_inflate_score(game):
    engine,session,clock=game; first=engine.start(session)
    with pytest.raises(GameError,match='watch_sequence'):engine.answer(session,first['run_id'],1,first['sequence'])
    foreign=engine.admit('other-player')
    with pytest.raises(GameError,match='run_not_owned'):engine.answer(foreign,first['run_id'],1,first['sequence'])
    ready(engine,clock,1)
    wrong=[(first['sequence'][0]+1)%engine.settings['pads']]
    finished=engine.answer(session,first['run_id'],1,wrong)
    assert finished['score']==0 and finished['finished'] and engine.leaderboard()==[]
    with pytest.raises(GameError,match='replay_changed'):engine.answer(session,first['run_id'],1,first['sequence'])


def test_same_day_challenge_and_equal_scores_get_equal_ranks(game):
    engine,a,clock=game;b=engine.admit('second-player')
    first,second=engine.start(a),engine.start(b)
    assert first['sequence']==second['sequence']
    ready(engine,clock,1)
    for session,run in ((a,first),(b,second)):engine.answer(session,run['run_id'],1,run['sequence'])
    assert [row['rank'] for row in engine.leaderboard()]==[1,1]


def test_session_deadline_and_start_limits(game):
    engine,session,clock=game;run=engine.start(session)
    with pytest.raises(GameError,match='run_already_active'):engine.start(session)
    clock.advance(engine.settings['answer_window_seconds']+3)
    result=engine.answer(session,run['run_id'],1,run['sequence']);assert result['finished'] and result['score']==0
    with pytest.raises(GameError,match='attempt_limit'):engine.start(session)
    clock.advance(engine.settings['session_seconds'])
    with pytest.raises(GameError,match='session_expired'):engine.start(session)


def test_production_http_auth_scope_body_and_membership_recheck(cfg,tmp_path):
    cfg.update(enabled=True,origin='https://arcade.example.test');clock=Clock();member=AsyncMock(side_effect=[True,False])
    app=create_app(cfg,db_path=tmp_path/'api.db',bot_token=TOKEN,group_id=-10099,membership=member,clock=clock)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=cfg['origin']) as client:
            assert (await client.get('/api/leaderboard')).status_code==401
            response=await client.post('/api/session',json={'init_data':signed()});assert response.status_code==200
            header={'Authorization':'Bearer '+response.json()['session']}
            assert (await client.post('/api/runs',json={'score':99999},headers=header)).status_code==422
            run=(await client.post('/api/runs',json={},headers=header)).json()
            assert (await client.post('/api/runs/'+run['run_id']+'/answer',json={'round':True,'sequence':[0]},headers=header)).status_code==422
            assert (await client.post('/api/runs/'+run['run_id']+'/answer',json={'round':1,'sequence':run['sequence'],'score':99999},headers=header)).status_code==422
            clock.advance(cfg['membership_recheck_seconds'])
            assert (await client.get('/api/leaderboard',headers=header)).status_code==403
            assert (await client.get('/api/leaderboard',headers=header)).status_code==401
    asyncio.run(scenario());assert member.await_count==2


def test_demo_is_loopback_only_and_origin_host_bound(cfg,tmp_path):
    app=create_app(cfg,db_path=tmp_path/'demo.db',demo=True)
    async def scenario():
        transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234))
        async with httpx.AsyncClient(transport=transport,base_url='http://127.0.0.1:8082') as client:
            root=await client.get('/');assert root.status_code==200 and 'no-store' in root.headers['cache-control']
            assert 'https://telegram.org/js' not in root.text
            assert (await client.post('/api/session',json={},headers={'Origin':'https://other.example.test'})).status_code==403
            assert (await client.post('/api/session',json={},headers={'Host':'other.example.test'})).status_code==403
            assert (await client.post('/api/session',json={})).status_code==200
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('192.0.2.1',1234)),base_url='http://127.0.0.1:8082') as client:
            assert (await client.get('/')).status_code==403
    asyncio.run(scenario())


def test_nonmember_or_missing_existing_configuration_never_gets_session(cfg,tmp_path):
    cfg.update(enabled=True,origin='https://arcade.example.test')
    with pytest.raises(ValueError):create_app(cfg,db_path=tmp_path/'no.db',bot_token='',group_id=-10099)
    app=create_app(cfg,db_path=tmp_path/'deny.db',bot_token=TOKEN,group_id=-10099,membership=AsyncMock(return_value=False))
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=cfg['origin']) as client:
            assert (await client.post('/api/session',json={'init_data':signed()})).status_code==403
    asyncio.run(scenario())


def test_disabled_service_has_no_store_or_implicit_demo(cfg,tmp_path):
    path=tmp_path/'disabled.db';app=create_app(cfg,db_path=path,bot_token='',group_id=0)
    assert not path.exists() and app.state.engine is None


@pytest.mark.parametrize('base_path',['','/arcade','/arcade/','/games/arcade/'])
def test_base_path_serves_assets_admission_play_and_page_reload(cfg,tmp_path,base_path):
    prefix=base_path.rstrip('/')
    cfg.update(enabled=True,origin='https://existing-host.example.test',base_path=base_path)
    clock=Clock();member=AsyncMock(return_value=True)
    app=create_app(cfg,db_path=tmp_path/'prefix.db',bot_token=TOKEN,
                   group_id=-10099,membership=member,clock=clock)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=cfg['origin']) as client:
            entry=prefix+'/'
            page=await client.get(entry)
            assert page.status_code==200
            assert 'no-store' in page.headers['cache-control']
            embedded=re.search(r'id="arcade-base-path" type="application/json">(.*?)</script>',page.text)
            assert json.loads(embedded.group(1))==prefix
            assets=re.findall(r'(?:href|src)="([^"]*/assets/[^"]+)"',page.text)
            assert assets==[prefix+'/assets/game.css',prefix+'/assets/game.js',prefix+'/assets/dungeon.js']
            for path in assets:
                assert (await client.get(path)).status_code==200
            # A reload/request carrying a query stays in the selected path.
            assert (await client.get(entry+'?reload=1')).text==page.text
            if prefix:
                redirect=await client.get(prefix)
                assert redirect.status_code==307
                assert redirect.headers['location']==cfg['origin']+entry
                for path in ['/','/assets/game.js','/api/session','/arcadex/api/leaderboard']:
                    response=await client.get(path)
                    assert response.status_code==404
            api=prefix+'/api'
            assert (await client.get(api+'/leaderboard')).status_code==401
            assert (await client.post(api+'/session',json={'init_data':signed()},
                headers={'Origin':'https://other.example.test'})).status_code==403
            assert (await client.post(api+'/session',json={'init_data':signed()},
                headers={'Host':'other.example.test'})).status_code==403
            session=await client.post(api+'/session',json={'init_data':signed()})
            assert session.status_code==200
            headers={'Authorization':'Bearer '+session.json()['session']}
            run=(await client.post(api+'/runs',json={},headers=headers)).json()
            ready(app.state.engine,clock,1)
            payload={'round':1,'sequence':run['sequence']}
            answer=await client.post(api+'/runs/'+run['run_id']+'/answer',json=payload,headers=headers)
            assert answer.status_code==200 and answer.json()['score']==cfg['points_per_round']
            replay=await client.post(api+'/runs/'+run['run_id']+'/answer',json=payload,headers=headers)
            assert replay.json()==answer.json()
            board=await client.get(api+'/leaderboard',headers=headers)
            assert board.json()['players'][0]['score']==cfg['points_per_round']
    asyncio.run(scenario())


@pytest.mark.parametrize('base_path',[None,2,'arcade','//','/arcade//','/../arcade','/arcade/..',
    '/arcade?x=1','/arcade#fragment','/arcade%2Fother','/arcade\\other','/arcade /other'])
def test_ambiguous_base_path_is_refused_before_store_creation(cfg,tmp_path,base_path):
    cfg['base_path']=base_path
    path=tmp_path/'bad-prefix.db'
    with pytest.raises(ValueError,match='invalid_base_path'):
        create_app(cfg,db_path=path,bot_token='',group_id=0)
    assert not path.exists()
