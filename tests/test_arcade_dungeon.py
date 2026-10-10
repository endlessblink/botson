"""Dungeon Dash: server-owned rules, daily seed, weekly board."""
from pathlib import Path

import pytest
import yaml

from services.arcade.dungeon import Dungeon
from services.arcade.engine import Engine, GameError

NOW = 1791288000


@pytest.fixture
def setup(tmp_path):
    cfg = yaml.safe_load((Path(__file__).parents[1] / 'config/arcade.yaml').read_text())['service']
    clock = [NOW]
    engine = Engine(cfg, tmp_path / 'd.db', secret=b'synthetic', clock=lambda: clock[0], alias_prefix='P')
    return engine, Dungeon(engine), clock


def play(engine, dungeon, key, picks=None):
    token = engine.admit(key)
    view = dungeon.start(token)
    while not view['finished']:
        view = dungeon.choose(token, view['run_id'], 0 if picks is None else picks)
    return token, view


def test_same_day_same_dungeon_for_everyone(setup):
    engine, dungeon, _ = setup
    a = dungeon.start(engine.admit('aaaaaa1'))
    b = dungeon.start(engine.admit('bbbbbb2'))
    assert a['doors'] == b['doors'] and a['floor'] == 1


def test_run_always_ends_and_scores_persist(setup):
    engine, dungeon, _ = setup
    _, view = play(engine, dungeon, 'aaaaaa1')
    assert view['finished'] and view['outcome'] in {'victory', 'defeat', 'died'}
    board = dungeon.leaderboard()
    assert board[0]['score'] == view['score'] and board[0]['rank'] == 1


def test_client_cannot_pick_outside_the_options_or_skip_ahead(setup):
    engine, dungeon, _ = setup
    token = engine.admit('aaaaaa1')
    view = dungeon.start(token)
    for bad in (-1, 3, True, 'x'):
        with pytest.raises(GameError):
            dungeon.choose(token, view['run_id'], bad)
    with pytest.raises(GameError):
        dungeon.start(token)  # one active run at a time


def test_other_players_cannot_drive_my_run(setup):
    engine, dungeon, _ = setup
    view = dungeon.start(engine.admit('aaaaaa1'))
    with pytest.raises(GameError):
        dungeon.choose(engine.admit('bbbbbb2'), view['run_id'], 0)


def test_weekly_board_resets_next_week(setup):
    engine, dungeon, clock = setup
    play(engine, dungeon, 'aaaaaa1')
    assert dungeon.leaderboard()
    clock[0] += 8 * 86400
    assert dungeon.leaderboard() == []


def test_daily_attempt_limit(setup):
    engine, dungeon, clock = setup
    token = engine.admit('aaaaaa1')
    limit = engine.settings['dungeon']['daily_attempts']
    for _ in range(limit):
        view = dungeon.start(token)
        while not view['finished']:
            view = dungeon.choose(token, view['run_id'], 0)
        clock[0] += engine.settings['start_cooldown_seconds']
    with pytest.raises(GameError, match='attempt_limit'):
        dungeon.start(token)
