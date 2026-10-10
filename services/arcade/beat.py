"""Beat Runner: the client plays locally, the server replays the input log.

The track is a deterministic function of the daily seed. A submitted score is
never trusted: the server re-simulates the exact inputs against the track.
"""
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone

from .engine import GameError

JUMP, SLIDE, COIN, REST = 'jump', 'slide', 'coin', 'rest'
ACTIONS = (JUMP, SLIDE)


def build_track(secret, day, cfg):
    """One item per beat; the first beats are always empty so players can settle in."""
    track = []
    block = 0
    while len(track) < cfg['beats']:
        digest = hmac.new(secret, f'{day}:bt:{block}'.encode(), hashlib.sha256).digest()
        for value in digest:
            roll = value % 10
            track.append(REST if roll < 2 else JUMP if roll < 5 else SLIDE if roll < 8 else COIN)
        block += 1
    track = track[:cfg['beats']]
    for i in range(min(cfg['intro_beats'], len(track))):
        track[i] = REST
    return track


def beat_time(cfg, index):
    return cfg['lead_ms'] + index * 60000 // cfg['bpm']


def track_end_ms(cfg):
    return beat_time(cfg, cfg['beats'])


def simulate(track, inputs, cfg):
    """Pure, deterministic judge. `inputs` is a time-ordered list of (t_ms, action)."""
    used = [False] * len(inputs)
    lives, combo, best_combo = cfg['lives'], 0, 0
    score = perfect = good = missed = coins = 0
    stop_after = None
    for index, item in enumerate(track):
        if item == REST:
            continue
        at = beat_time(cfg, index)
        nearest = None
        for n, (t, _action) in enumerate(inputs):
            if used[n] or abs(t - at) > cfg['good_ms']:
                continue
            if nearest is None or abs(t - at) < abs(inputs[nearest][0] - at):
                nearest = n
        if item == COIN:
            if nearest is not None:
                used[nearest] = True
                coins += 1
                score += cfg['coin_points']
            continue
        hit = nearest is not None and inputs[nearest][1] == item
        if nearest is not None:
            used[nearest] = True
        if hit:
            multiplier = min(1 + combo // cfg['combo_step'], cfg['max_multiplier'])
            exact = abs(inputs[nearest][0] - at) <= cfg['perfect_ms']
            score += (cfg['perfect_points'] if exact else cfg['good_points']) * multiplier
            perfect += exact
            good += not exact
            combo += 1
            best_combo = max(best_combo, combo)
        else:
            missed += 1
            combo = 0
            lives -= 1
            if lives <= 0:
                stop_after = at + cfg['good_ms']
                break
    strays = sum(1 for n, (t, _a) in enumerate(inputs)
                 if not used[n] and (stop_after is None or t <= stop_after))
    score = max(0, score - strays * cfg['stray_penalty'])
    return {'score': score, 'perfect': perfect, 'good': good, 'miss': missed, 'coins': coins,
            'strays': strays, 'best_combo': best_combo, 'lives': max(lives, 0),
            'completed': stop_after is None,
            'ended_ms': track_end_ms(cfg) if stop_after is None else stop_after}


def clean_inputs(raw, cfg):
    """Reject logs no human could have produced; return [(t, action)]."""
    if not isinstance(raw, list) or len(raw) > cfg['beats'] * 2:
        raise GameError('invalid_log')
    end = track_end_ms(cfg) + cfg['good_ms']
    cleaned, previous = [], None
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != {'t', 'a'}:
            raise GameError('invalid_log')
        t, action = entry['t'], entry['a']
        if type(t) is not int or action not in ACTIONS or not 0 <= t <= end:
            raise GameError('invalid_log')
        if previous is not None and t - previous < cfg['min_gap_ms']:
            raise GameError('invalid_log')
        cleaned.append((t, action))
        previous = t
    return cleaned


class BeatRunner:
    def __init__(self, engine):
        self.e = engine
        self.cfg = engine.settings['beat']
        self.runs = {}
        with engine.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS beat_weekly
                (player_key TEXT NOT NULL, week TEXT NOT NULL, alias TEXT NOT NULL,
                 score INTEGER NOT NULL, combo INTEGER NOT NULL, PRIMARY KEY(player_key,week));
                CREATE TABLE IF NOT EXISTS beat_attempts
                (player_key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
                 last_started REAL NOT NULL, PRIMARY KEY(player_key,day));''')

    def _lifetime(self):
        return track_end_ms(self.cfg) / 1000 + self.cfg['finish_window_seconds']

    def start(self, token):
        e, cfg = self.e, self.cfg
        with e.lock:
            key = e.session(token)['key']; now = e.clock()
            self.runs = {k: v for k, v in self.runs.items() if now - v['started'] <= self._lifetime()}
            if any(r['key'] == key and not r['finished'] for r in self.runs.values()):
                raise GameError('run_already_active')
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            with e.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT count,last_started FROM beat_attempts WHERE player_key=? AND day=?',
                                 (key, day)).fetchone()
                if row and (row[0] >= cfg['daily_attempts'] or now - row[1] < e.settings['start_cooldown_seconds']):
                    raise GameError('attempt_limit')
                db.execute('INSERT INTO beat_attempts VALUES (?,?,1,?) ON CONFLICT(player_key,day) '
                           'DO UPDATE SET count=count+1,last_started=excluded.last_started', (key, day, now))
            track = build_track(e.secret, day, cfg)
            run = {'id': secrets.token_urlsafe(24), 'key': key, 'track': track, 'started': now,
                   'finished': False, 'receipt': None, 'log': None}
            self.runs[run['id']] = run
            return {'run_id': run['id'], 'game': 'beat', 'track': track, 'bpm': cfg['bpm'],
                    'beat_ms': 60000 // cfg['bpm'], 'lead_ms': cfg['lead_ms'], 'lives': cfg['lives'],
                    'perfect_ms': cfg['perfect_ms'], 'good_ms': cfg['good_ms'],
                    'min_gap_ms': cfg['min_gap_ms'], 'finished': False}

    def finish(self, token, run_id, raw_inputs):
        e, cfg = self.e, self.cfg
        with e.lock:
            key = e.session(token)['key']; run = self.runs.get(run_id)
            if not run or run['key'] != key:
                raise GameError('run_not_owned')
            log = json.dumps(raw_inputs, sort_keys=True)
            if run['finished']:
                if run['log'] != log:
                    raise GameError('replay_changed')
                return run['receipt']
            inputs = clean_inputs(raw_inputs, cfg)
            result = simulate(run['track'], inputs, cfg)
            # A real run takes as long as the track (or until the last life is gone).
            if e.clock() - run['started'] < result['ended_ms'] / 1000 - cfg['finish_slack_seconds']:
                raise GameError('too_early')
            receipt = {'run_id': run_id, 'game': 'beat', 'finished': True, **result}
            run.update(finished=True, receipt=receipt, log=log)
            with e.connection() as db:
                db.execute('INSERT INTO beat_weekly VALUES (?,?,?,?,?) ON CONFLICT(player_key,week) DO UPDATE SET '
                           'score=MAX(score,excluded.score),combo=MAX(combo,excluded.combo)',
                           (key, self.week(), e.alias(key), result['score'], result['best_combo']))
            return receipt

    def week(self):
        year, week, _ = datetime.fromtimestamp(self.e.clock(), timezone.utc).isocalendar()
        return f'{year}-W{week:02d}'

    def leaderboard(self):
        with self.e.connection() as db:
            rows = db.execute('SELECT alias,score,combo FROM beat_weekly WHERE week=? '
                              'ORDER BY score DESC,combo DESC,alias LIMIT ?',
                              (self.week(), self.e.settings['leaderboard_size'])).fetchall()
        result, previous, rank = [], None, 0
        for index, (alias, score, combo) in enumerate(rows, 1):
            if (score, combo) != previous:
                rank = index
            result.append({'rank': rank, 'alias': alias, 'score': score, 'combo': combo})
            previous = (score, combo)
        return result
