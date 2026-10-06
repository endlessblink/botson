"""Server checks moves and timing; clients never submit a score."""
import hashlib
import hmac
import secrets
import sqlite3
import threading
from datetime import datetime, timezone


class GameError(ValueError):
    pass


class Engine:
    def __init__(self, settings, db_path, *, secret, clock, alias_prefix):
        self.settings, self.db_path, self.secret = settings, str(db_path), secret
        self.clock, self.alias_prefix = clock, alias_prefix
        self.lock = threading.RLock()
        self.sessions, self.runs = {}, {}
        with self.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS best_scores
                (player_key TEXT PRIMARY KEY, alias TEXT NOT NULL, score INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS attempts
                (player_key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
                 last_started REAL NOT NULL, PRIMARY KEY(player_key,day));''')

    def connection(self):
        return sqlite3.connect(self.db_path, timeout=5)

    def alias(self, key):
        return self.alias_prefix + '-' + key[:6].upper()

    def admit(self, key, user_id=None):
        with self.lock:
            now = self.clock()
            self.sessions = {k:v for k,v in self.sessions.items() if v['expires'] > now}
            self.runs = {k:v for k,v in self.runs.items() if now-v['started'] <= self.settings['run_lifetime_seconds']}
            if len(self.sessions) >= self.settings['max_sessions']:
                raise GameError('session_capacity')
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {'key':key, 'user_id':user_id,
                'expires':now+self.settings['session_seconds'], 'membership_at':now}
            return token

    def session(self, token):
        value = self.sessions.get(token)
        if not value or value['expires'] <= self.clock():
            raise GameError('session_expired')
        return value

    def _challenge(self, run):
        at = self.clock()
        run['ready_at'] = at + (len(run['sequence'])*(self.settings['flash_ms']+self.settings['gap_ms']) + self.settings['ready_ms'])/1000
        run['deadline'] = run['ready_at'] + self.settings['answer_window_seconds']
        return {'run_id':run['id'], 'round':len(run['sequence']), 'sequence':run['sequence'][:],
            'score':run['score'], 'finished':False,
            'flash_ms':self.settings['flash_ms'], 'gap_ms':self.settings['gap_ms'],
            'ready_ms':self.settings['ready_ms']}

    def start(self, session_token):
        with self.lock:
            session = self.session(session_token); key = session['key']; now = self.clock()
            for run in self.runs.values():
                if run['key']==key and (now>run['deadline'] or now-run['started']>self.settings['run_lifetime_seconds']):
                    run['finished']=True
            if any(r['key']==key and not r['finished'] and now-r['started']<=self.settings['run_lifetime_seconds'] for r in self.runs.values()):
                raise GameError('run_already_active')
            day = datetime.fromtimestamp(now,timezone.utc).date().isoformat()
            with self.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT count,last_started FROM attempts WHERE player_key=? AND day=?',(key,day)).fetchone()
                if row and (row[0]>=self.settings['daily_attempts'] or now-row[1]<self.settings['start_cooldown_seconds']):
                    raise GameError('attempt_limit')
                db.execute('INSERT INTO attempts VALUES (?,?,1,?) ON CONFLICT(player_key,day) DO UPDATE SET count=count+1,last_started=excluded.last_started',(key,day,now))
            # Everyone receives the same daily progressive challenge.
            full = [hmac.new(self.secret, f'{day}:{n}'.encode(), hashlib.sha256).digest()[0] % self.settings['pads'] for n in range(self.settings['max_rounds'])]
            run_id = secrets.token_urlsafe(24)
            run = {'id':run_id,'key':key,'sequence':full[:1],'full':full,'score':0,'started':now,'finished':False,'receipts':{}}
            self.runs[run_id]=run
            return self._challenge(run)

    def answer(self, session_token, run_id, round_number, sequence):
        with self.lock:
            key=self.session(session_token)['key']; run=self.runs.get(run_id)
            if not run or run['key']!=key:
                raise GameError('run_not_owned')
            if round_number in run['receipts']:
                previous, receipt=run['receipts'][round_number]
                if previous != sequence:
                    raise GameError('replay_changed')
                return receipt
            if run['finished'] or type(round_number) is not int or round_number!=len(run['sequence']):
                raise GameError('round_changed')
            if not isinstance(sequence,list) or len(sequence)!=round_number or any(type(x) is not int or not 0<=x<self.settings['pads'] for x in sequence):
                raise GameError('invalid_moves')
            now=self.clock()
            if now < run['ready_at']:
                raise GameError('watch_sequence')
            valid = now<=run['deadline'] and now-run['started']<=self.settings['run_lifetime_seconds'] and sequence==run['sequence']
            if valid:
                run['score'] += self.settings['points_per_round']
            run['finished'] = not valid or round_number>=self.settings['max_rounds']
            # Persist only the best verified score, even if a player closes early.
            if run['score']:
                with self.connection() as db:
                    db.execute('INSERT INTO best_scores VALUES (?,?,?) ON CONFLICT(player_key) DO UPDATE SET score=MAX(score,excluded.score)',(key,self.alias(key),run['score']))
            if run['finished']:
                receipt={'run_id':run_id,'score':run['score'],'round':round_number,'finished':True}
            else:
                run['sequence']=run['full'][:round_number+1]
                receipt=self._challenge(run)
            run['receipts'][round_number]=(sequence[:],receipt)
            return receipt

    def leaderboard(self):
        with self.connection() as db:
            rows=db.execute('SELECT alias,score FROM best_scores ORDER BY score DESC,alias LIMIT ?', (self.settings['leaderboard_size'],)).fetchall()
        result=[]; previous=None; rank=0
        for index,(alias,score) in enumerate(rows,1):
            if score!=previous: rank=index
            result.append({'rank':rank,'alias':alias,'score':score}); previous=score
        return result
