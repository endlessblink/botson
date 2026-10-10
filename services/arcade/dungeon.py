"""Dungeon Dash: server-side roguelike. The client only ever sends a pick."""
import hashlib
import hmac
import secrets
from datetime import datetime, timezone

from .engine import GameError

HINTS = {'monster': ('noise',), 'trap': ('noise', 'shine'),
         'treasure': ('shine', 'warm'), 'shrine': ('warm',), 'mystery': ('fog',)}
KINDS = ('monster', 'trap', 'treasure', 'shrine', 'mystery')
RELICS = ('sword', 'shield', 'potion', 'coin', 'lantern')


class Dungeon:
    def __init__(self, engine):
        self.e = engine
        self.cfg = engine.settings['dungeon']
        self.runs = {}
        with engine.connection() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS dungeon_weekly
                (player_key TEXT NOT NULL, week TEXT NOT NULL, alias TEXT NOT NULL,
                 score INTEGER NOT NULL, floors INTEGER NOT NULL, PRIMARY KEY(player_key,week));
                CREATE TABLE IF NOT EXISTS dungeon_attempts
                (player_key TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
                 last_started REAL NOT NULL, PRIMARY KEY(player_key,day));''')

    # ---- deterministic daily dungeon -------------------------------------
    def _rolls(self, day, label, count):
        digest = hmac.new(self.e.secret, f'{day}:dd:{label}'.encode(), hashlib.sha256).digest()
        return digest[:count]

    def _floor(self, day, floor):
        if floor == self.cfg['floors']:
            return [{'kind': 'boss', 'hint': 'boss', 'strength': self.cfg['boss_strength']}]
        rolls = self._rolls(day, f'f{floor}', 12)
        doors = []
        for i in range(self.cfg['doors']):
            kind = KINDS[rolls[i] % len(KINDS)]
            if i == 0 and all(KINDS[rolls[j] % len(KINDS)] == 'trap' for j in range(self.cfg['doors'])):
                kind = 'treasure'
            hints = HINTS[kind]
            strength = 1 + floor // 2 + rolls[6 + i] % 2
            doors.append({'kind': kind, 'hint': hints[rolls[3 + i] % len(hints)],
                          'strength': strength, 'mystery': KINDS[rolls[9 + i] % 4]})
        return doors

    def _relics(self, day, floor):
        rolls = self._rolls(day, f'r{floor}', 4)
        first = rolls[0] % len(RELICS)
        second = (first + 1 + rolls[1] % (len(RELICS) - 1)) % len(RELICS)
        return [RELICS[first], RELICS[second]]

    # ---- views ------------------------------------------------------------
    def _view(self, run, log=None):
        view = {'run_id': run['id'], 'game': 'dungeon', 'floor': run['floor'],
                'floors': self.cfg['floors'], 'hp': run['hp'], 'max_hp': self.cfg['max_hp'],
                'power': run['power'], 'gold': run['gold'], 'shield': run['shield'],
                'relics': run['owned'][:], 'phase': run['phase'], 'finished': run['phase'] == 'done',
                'score': self._score(run), 'log': log or [], 'outcome': run.get('outcome')}
        if run['phase'] == 'door':
            view['doors'] = [{'hint': d['kind'] if run['lantern'] and d['kind'] != 'mystery' else d['hint']}
                             for d in run['doors']]
        elif run['phase'] == 'relic':
            view['offers'] = run['offers'][:]
        return view

    def _score(self, run):
        score = run['gold'] + run['cleared'] * self.cfg['floor_points']
        if run.get('outcome') == 'victory':
            score += self.cfg['victory_points'] + run['hp'] * self.cfg['hp_points']
        return score

    # ---- lifecycle --------------------------------------------------------
    def start(self, token):
        e = self.e
        with e.lock:
            key = e.session(token)['key']; now = e.clock()
            self.runs = {k: v for k, v in self.runs.items()
                         if now - v['started'] <= e.settings['run_lifetime_seconds'] * 4}
            if any(r['key'] == key and r['phase'] != 'done' for r in self.runs.values()):
                raise GameError('run_already_active')
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            with e.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute('SELECT count,last_started FROM dungeon_attempts WHERE player_key=? AND day=?',
                                 (key, day)).fetchone()
                if row and (row[0] >= self.cfg['daily_attempts'] or now - row[1] < e.settings['start_cooldown_seconds']):
                    raise GameError('attempt_limit')
                db.execute('INSERT INTO dungeon_attempts VALUES (?,?,1,?) ON CONFLICT(player_key,day) '
                           'DO UPDATE SET count=count+1,last_started=excluded.last_started', (key, day, now))
            run = {'id': secrets.token_urlsafe(24), 'key': key, 'day': day, 'started': now,
                   'floor': 1, 'hp': self.cfg['start_hp'], 'power': 1, 'gold': 0, 'shield': 0,
                   'coin': False, 'lantern': False, 'owned': [], 'cleared': 0, 'phase': 'door',
                   'offers': [], 'outcome': None}
            run['doors'] = self._floor(day, 1)
            self.runs[run['id']] = run
            return self._view(run)

    def choose(self, token, run_id, pick):
        e = self.e
        with e.lock:
            key = e.session(token)['key']; run = self.runs.get(run_id)
            if not run or run['key'] != key:
                raise GameError('run_not_owned')
            if run['phase'] == 'done':
                raise GameError('run_finished')
            if type(pick) is not int:
                raise GameError('invalid_pick')
            if run['phase'] == 'door':
                if not 0 <= pick < len(run['doors']):
                    raise GameError('invalid_pick')
                log = self._open(run, run['doors'][pick])
            else:
                if not 0 <= pick < len(run['offers']):
                    raise GameError('invalid_pick')
                log = self._take(run, run['offers'][pick])
            if run['phase'] == 'done':
                self._save(run)
            return self._view(run, log)

    # ---- rules ------------------------------------------------------------
    def _gold(self, run, amount):
        run['gold'] += amount * 3 // 2 if run['coin'] else amount

    def _hurt(self, run, damage, log):
        if run['shield'] and damage > 0:
            run['shield'] -= 1
            log.append('shield_blocked')
            return
        run['hp'] -= damage
        log.append('hurt')

    def _open(self, run, door):
        floor, log, kind = run['floor'], [], door['kind']
        if kind == 'mystery':
            kind = door['mystery']
            log.append('mystery_' + kind)
        if kind == 'boss' or kind == 'monster':
            gap = door['strength'] - run['power']
            if gap <= 0:
                log.append('boss_won' if door['kind'] == 'boss' else 'monster_won')
                self._gold(run, self.cfg['monster_gold'] * floor)
                if door['kind'] == 'boss':
                    run['outcome'] = 'victory'
            else:
                log.append('monster_lost')
                self._hurt(run, gap, log)
                if door['kind'] == 'boss':
                    run['outcome'] = 'defeat'
        elif kind == 'trap':
            log.append('trap')
            self._hurt(run, self.cfg['trap_damage'], log)
        elif kind == 'treasure':
            log.append('treasure')
            self._gold(run, self.cfg['treasure_gold'] * floor)
        elif kind == 'shrine':
            log.append('shrine')
            run['hp'] = min(self.cfg['max_hp'], run['hp'] + self.cfg['shrine_heal'])
        if run['hp'] <= 0:
            run['hp'] = 0
            run['outcome'] = 'died'
            run['phase'] = 'done'
        elif door['kind'] == 'boss':
            run['cleared'] += run['outcome'] == 'victory'
            run['phase'] = 'done'
        else:
            run['cleared'] += 1
            run['offers'] = self._relics(run['day'], floor)
            run['phase'] = 'relic'
        return log

    def _take(self, run, relic):
        run['owned'].append(relic)
        log = ['relic_' + relic]
        if relic == 'sword':
            run['power'] += 1
        elif relic == 'shield':
            run['shield'] += 1
        elif relic == 'potion':
            run['hp'] = min(self.cfg['max_hp'], run['hp'] + self.cfg['potion_heal'])
        elif relic == 'coin':
            run['coin'] = True
        elif relic == 'lantern':
            run['lantern'] = True
        run['floor'] += 1
        run['doors'] = self._floor(run['day'], run['floor'])
        run['phase'] = 'door'
        return log

    # ---- persistence ------------------------------------------------------
    def week(self):
        year, week, _ = datetime.fromtimestamp(self.e.clock(), timezone.utc).isocalendar()
        return f'{year}-W{week:02d}'

    def _save(self, run):
        score = self._score(run)
        with self.e.connection() as db:
            db.execute('INSERT INTO dungeon_weekly VALUES (?,?,?,?,?) ON CONFLICT(player_key,week) DO UPDATE SET '
                       'score=MAX(score,excluded.score),floors=MAX(floors,excluded.floors)',
                       (run['key'], self.week(), self.e.alias(run['key']), score, run['cleared']))

    def leaderboard(self):
        with self.e.connection() as db:
            rows = db.execute('SELECT alias,score,floors FROM dungeon_weekly WHERE week=? '
                              'ORDER BY score DESC,floors DESC,alias LIMIT ?',
                              (self.week(), self.e.settings['leaderboard_size'])).fetchall()
        result, previous, rank = [], None, 0
        for index, (alias, score, floors) in enumerate(rows, 1):
            if (score, floors) != previous:
                rank = index
            result.append({'rank': rank, 'alias': alias, 'score': score, 'floors': floors})
            previous = (score, floors)
        return result
