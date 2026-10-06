"""Independent opt-in arcade; does not import or start Botson."""
import logging
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .auth import player_key, verify_init_data
from .engine import Engine, GameError

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).parent
# Telegram request URLs contain the existing token; never log HTTP transports.
logging.getLogger('httpx').setLevel(logging.CRITICAL)
logging.getLogger('httpcore').setLevel(logging.CRITICAL)


class Admission(BaseModel):
    model_config = ConfigDict(extra='forbid')
    init_data: StrictStr = Field(default='', max_length=8192)


class Moves(BaseModel):
    model_config = ConfigDict(extra='forbid')
    round: StrictInt
    sequence: list[StrictInt] = Field(max_length=64)


def create_app(settings=None, *, db_path=None, demo=False, bot_token=None,
               group_id=None, membership=None, clock=time.time):
    cfg = settings or yaml.safe_load((ROOT/'config/arcade.yaml').read_text())['service']
    cfg = dict(cfg)
    copy = yaml.safe_load((ROOT/'config/copy/arcade.yaml').read_text())
    if demo:
        if cfg['bind_host'] not in {'127.0.0.1','::1','localhost'}:
            raise ValueError('demo_must_be_loopback')
        cfg.update(enabled=True, origin=f'http://127.0.0.1:{cfg["port"]}')
    production = cfg['enabled'] and not demo
    token = (bot_token if bot_token is not None else os.getenv('BOT_TOKEN','')) if production else ''
    group = (group_id if group_id is not None else int(os.getenv('GROUP_ID','0'))) if production else 0
    if cfg['enabled'] and not demo and (not token or not group or not cfg['origin']):
        raise ValueError('existing_bot_and_group_and_origin_required')
    if cfg['enabled']:
        for key in ('auth_max_age_seconds','session_seconds','membership_recheck_seconds','max_sessions',
            'max_rounds','pads','flash_ms','gap_ms','ready_ms','answer_window_seconds','run_lifetime_seconds',
            'start_cooldown_seconds','daily_attempts','points_per_round','leaderboard_size'):
            if type(cfg[key]) is not int or cfg[key]<1:
                raise ValueError('invalid_budget:'+key)
        if cfg['pads'] != len(copy['pad_labels']) or cfg['max_rounds'] > 64:
            raise ValueError('invalid_game_shape')
        parts=urlsplit(cfg['origin'])
        if parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment or not parts.hostname:
            raise ValueError('invalid_origin')
        if not demo and parts.scheme != 'https':
            raise ValueError('https_origin_required')
    path=Path(db_path or ROOT/'data/arcade.db')
    engine=None
    if cfg['enabled']:
        path.parent.mkdir(parents=True,exist_ok=True)
        engine=Engine(cfg,path,secret=secrets.token_bytes(32) if demo else player_key(token,group).encode(),
                      clock=clock,alias_prefix=copy['alias_prefix'])
    app=FastAPI(title='Botson Arcade',docs_url=None,redoc_url=None,openapi_url=None)
    app.state.engine, app.state.config = engine, cfg
    app.mount('/assets',StaticFiles(directory=HERE/'static'),name='assets')
    templates=Environment(loader=FileSystemLoader(HERE/'static'),autoescape=select_autoescape(['html']))

    @app.middleware('http')
    async def boundaries(request, call_next):
        if demo and (not request.client or request.client.host not in {'127.0.0.1','::1'}):
            return HTMLResponse(status_code=403)
        if cfg['enabled'] and request.url.path.startswith('/api/'):
            expected=urlsplit(cfg['origin'])
            if request.headers.get('host') != expected.netloc or request.headers.get('origin',cfg['origin']) != cfg['origin']:
                return HTMLResponse(status_code=403)
        result=await call_next(request)
        result.headers.update({'Cache-Control':'no-store','Referrer-Policy':'no-referrer',
            'X-Content-Type-Options':'nosniff',
            'Content-Security-Policy':"default-src 'self'; script-src 'self' https://telegram.org; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors https://web.telegram.org https://*.telegram.org"})
        return result

    async def is_member(user_id):
        if membership:
            return await membership(user_id)
        try:
            async with httpx.AsyncClient(timeout=5,follow_redirects=False) as client:
                response=await client.post('https://api.telegram.org/bot'+token+'/getChatMember',
                                          json={'chat_id':group,'user_id':user_id})
                value=response.json(); member=value.get('result') or {}
                return (value.get('ok') is True and (member.get('user') or {}).get('id')==user_id and
                    ((member.get('status') in {'creator','administrator','member'}) or
                     (member.get('status')=='restricted' and member.get('is_member') is True)))
        except Exception:
            return False

    def available():
        if engine is None:
            raise HTTPException(503,'disabled')

    async def authenticated(request):
        available()
        scheme,_,value=request.headers.get('Authorization','').partition(' ')
        if scheme.lower()!='bearer':
            raise HTTPException(401,'admission_required')
        try:
            session=engine.session(value)
        except GameError:
            raise HTTPException(401,'session_expired') from None
        if not demo and clock()-session['membership_at']>=cfg['membership_recheck_seconds']:
            if not await is_member(session['user_id']):
                engine.sessions.pop(value,None)
                raise HTTPException(403,'not_current_member')
            session['membership_at']=clock()
        return value

    @app.get('/',response_class=HTMLResponse)
    async def index():
        return templates.get_template('index.html').render(copy=copy,demo=demo)

    @app.post('/api/session')
    async def session(body: Admission):
        available()
        try:
            if demo:
                user_id=None; key=secrets.token_hex(32)
            else:
                user_id=verify_init_data(body.init_data,token,now=clock(),max_age=cfg['auth_max_age_seconds'])
                if not await is_member(user_id):
                    raise ValueError('not_current_member')
                key=player_key(token,user_id)
            admission=engine.admit(key,user_id)
        except (ValueError,TypeError):
            raise HTTPException(403,'admission_refused') from None
        return {'session':admission,'alias':engine.alias(key)}

    @app.post('/api/runs')
    async def start(request: Request, body: dict):
        admission=await authenticated(request)
        if body:
            raise HTTPException(422,'no_client_score_or_rules')
        try:
            return engine.start(admission)
        except GameError as error:
            raise HTTPException(409,str(error)) from None

    @app.post('/api/runs/{run_id}/answer')
    async def answer(run_id: str, body: Moves, request: Request):
        admission=await authenticated(request)
        try:
            return engine.answer(admission,run_id,body.round,body.sequence)
        except GameError as error:
            raise HTTPException(409,str(error)) from None

    @app.get('/api/leaderboard')
    async def board(request: Request):
        await authenticated(request)
        return {'players':engine.leaderboard()}

    return app


app=create_app()
