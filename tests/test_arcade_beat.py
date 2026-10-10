"""Beat Runner: deterministic daily track, server-side replay, weekly board."""
from pathlib import Path

import pytest
import yaml

from services.arcade.beat import BeatRunner, beat_time, build_track, clean_inputs, simulate, track_end_ms
from services.arcade.engine import Engine, GameError

NOW = 1791288000


@pytest.fixture
def setup(tmp_path):
    cfg = yaml.safe_load((Path(__file__).parents[1] / 'config/arcade.yaml').read_text())['service']
    clock = [NOW]
    engine = Engine(cfg, tmp_path / 'b.db', secret=b'synthetic', clock=lambda: clock[0], alias_prefix='P')
    return engine, BeatRunner(engine), clock, cfg['beat']


def perfect_inputs(track, cfg, offset=0):
    return [{'t': beat_time(cfg, i) + offset, 'a': item if item != 'coin' else 'jump'}
            for i, item in enumerate(track) if item != 'rest']


def run_and_finish(engine, runner, clock, cfg, key, inputs_for=perfect_inputs):
    token = engine.admit(key)
    started = runner.start(token)
    clock[0] += track_end_ms(cfg) / 1000 + 1
    return token, started, runner.finish(token, started['run_id'], inputs_for(started['track'], cfg))


def test_track_is_deterministic_per_day_and_starts_with_rest(setup):
    _, _, _, cfg = setup
    a = build_track(b'k', '2026-10-10', cfg)
    assert a == build_track(b'k', '2026-10-10', cfg) and len(a) == cfg['beats']
    assert a != build_track(b'k', '2026-10-11', cfg)
    assert set(a[:cfg['intro_beats']]) == {'rest'}


def test_flawless_replay_scores_the_maximum_and_is_saved(setup):
    engine, runner, clock, cfg = setup
    _, started, receipt = run_and_finish(engine, runner, clock, cfg, 'aaaaaa1')
    assert receipt['completed'] and receipt['miss'] == 0 and receipt['strays'] == 0
    assert receipt['perfect'] == sum(1 for x in started['track'] if x in ('jump', 'slide'))
    assert runner.leaderboard()[0]['score'] == receipt['score'] > 0


def test_wrong_action_and_empty_log_cost_lives(setup):
    _, _, _, cfg = setup
    track = build_track(b'k', '2026-10-10', cfg)
    assert simulate(track, [], cfg)['completed'] is False
    wrong = [(beat_time(cfg, i), 'slide' if x == 'jump' else 'jump')
             for i, x in enumerate(track) if x in ('jump', 'slide')]
    assert simulate(track, wrong, cfg)['miss'] >= cfg['lives']


def test_tampered_logs_are_rejected(setup):
    _, _, _, cfg = setup
    good = {'t': 3000, 'a': 'jump'}
    for bad in ([{'t': 3000}], [{'t': 3000, 'a': 'fly'}], [{'t': True, 'a': 'jump'}],
                [{'t': -5, 'a': 'jump'}], [{'t': 10**9, 'a': 'jump'}],
                [good, {'t': 3050, 'a': 'slide'}],           # faster than a human
                [{'t': 3000, 'a': 'jump', 'score': 9999}],   # extra field
                [good] * 200, 'x'):
        with pytest.raises(GameError):
            clean_inputs(bad, cfg)


def test_instant_submission_is_refused_and_replay_is_idempotent(setup):
    engine, runner, clock, cfg = setup
    token = engine.admit('aaaaaa1')
    started = runner.start(token)
    log = perfect_inputs(started['track'], cfg)
    with pytest.raises(GameError, match='too_early'):
        runner.finish(token, started['run_id'], log)
    clock[0] += track_end_ms(cfg) / 1000
    receipt = runner.finish(token, started['run_id'], log)
    assert runner.finish(token, started['run_id'], log) == receipt
    with pytest.raises(GameError, match='replay_changed'):
        runner.finish(token, started['run_id'], log[:-1])


def test_other_players_cannot_submit_for_my_run(setup):
    engine, runner, clock, cfg = setup
    started = runner.start(engine.admit('aaaaaa1'))
    with pytest.raises(GameError, match='run_not_owned'):
        runner.finish(engine.admit('bbbbbb2'), started['run_id'], [])


def test_spamming_costs_points(setup):
    _, _, _, cfg = setup
    track = build_track(b'k', '2026-10-10', cfg)
    clean = simulate(track, [(beat_time(cfg, i), x) for i, x in enumerate(track) if x in ('jump', 'slide')], cfg)
    spam = [(t, 'jump') for t in range(0, track_end_ms(cfg), cfg['min_gap_ms'])]
    assert simulate(track, spam, cfg)['score'] < clean['score']


def test_weekly_board_resets_and_daily_attempts_are_limited(setup):
    engine, runner, clock, cfg = setup
    run_and_finish(engine, runner, clock, cfg, 'aaaaaa1')
    assert runner.leaderboard()
    clock[0] += 8 * 86400
    assert runner.leaderboard() == []
    token = engine.admit('cccccc3')
    for _ in range(cfg['daily_attempts']):
        started = runner.start(token)
        clock[0] += track_end_ms(cfg) / 1000 + engine.settings['start_cooldown_seconds']
        runner.finish(token, started['run_id'], [])
    with pytest.raises(GameError, match='attempt_limit'):
        runner.start(token)
