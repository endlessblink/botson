import asyncio
import json
from functools import wraps
from unittest.mock import AsyncMock

import pytest

from bot.utils import conversation_quality as quality


SCORES = ('specificity', 'naturalness', 'novelty', 'channel_fit', 'answerability', 'payoff')


def run_async(test):
    @wraps(test)
    def run(*args, **kwargs):
        return asyncio.run(test(*args, **kwargs))
    return run


@pytest.fixture
def config(monkeypatch):
    value = {
        'reviewer_prompt': 'Reject routine check-ins and repetitive ideas.',
        'candidate_prompt': 'Channel: {category}\nRecent: {recent_texts}\nCandidate: {text}',
        'minimum_score': 4, 'score_min': 1, 'score_max': 5,
    }
    monkeypatch.setattr(quality, 'load_yaml', lambda _: value)
    return value


def response(**changes):
    return json.dumps({'pass': True, 'reason': 'concrete', **dict.fromkeys(SCORES, 4), **changes})


@run_async
async def test_accepted_candidate_receives_semantic_context(config):
    generate = AsyncMock(return_value=response())
    assert await quality.review_conversation('candidate', category='morning', recent_texts=['previous'], generate=generate) == (True, 'concrete')
    prompt = generate.call_args.args[0]
    assert all(part in prompt for part in ['morning', 'previous', 'candidate', config['reviewer_prompt']])


@run_async
@pytest.mark.parametrize('score', SCORES)
async def test_each_score_below_configured_minimum_rejects(config, score):
    assert not (await quality.review_conversation('candidate', generate=AsyncMock(return_value=response(**{score: 3}))))[0]


@run_async
@pytest.mark.parametrize('raw', ['no JSON', '{}', '[]', response(**{'pass': 'true'}), response(specificity=True), response(payoff=6), response(novelty=2.5), response(**{'pass': False})])
async def test_malformed_or_failed_review_rejects(config, raw):
    assert not (await quality.review_conversation('candidate', generate=AsyncMock(return_value=raw)))[0]


@run_async
async def test_provider_failure_rejects(config):
    assert not (await quality.review_conversation('candidate', generate=AsyncMock(side_effect=RuntimeError('offline'))))[0]


@run_async
@pytest.mark.parametrize('key', ['reviewer_prompt', 'candidate_prompt', 'minimum_score', 'score_min', 'score_max'])
async def test_missing_config_fails_closed_without_generation(config, key):
    config.pop(key)
    generate = AsyncMock(return_value=response())
    assert not (await quality.review_conversation('candidate', generate=generate))[0]
    generate.assert_not_awaited()


@run_async
async def test_config_loading_failure_fails_closed(monkeypatch):
    monkeypatch.setattr(quality, 'load_yaml', lambda _: (_ for _ in ()).throw(ValueError('invalid config')))
    assert not (await quality.review_conversation('candidate', generate=AsyncMock()))[0]


@run_async
async def test_fenced_json_compatibility(config):
    assert (await quality.review_conversation('candidate', generate=AsyncMock(return_value='```json\n' + response() + '\n```')))[0]


@run_async
@pytest.mark.parametrize('field', ['text', 'category', 'recent_texts'])
@pytest.mark.parametrize('replacement', ['', '{{%s}}'])
async def test_missing_or_escaped_context_field_rejects_before_provider(config, field, replacement):
    config['candidate_prompt'] = config['candidate_prompt'].replace(
        '{' + field + '}', replacement % field if replacement else '',
    )
    generate = AsyncMock(return_value=response())
    assert not (await quality.review_conversation('candidate', generate=generate))[0]
    generate.assert_not_awaited()


@run_async
async def test_malformed_reply_is_retried_then_accepted(config):
    config['technical_attempts'] = 3
    broken = '{\n"pass": true,\n"reason": "uses "quoted" hebrew", "specificity": 4}'
    generate = AsyncMock(side_effect=[broken, response()])
    assert await quality.review_conversation('candidate', generate=generate) == (True, 'concrete')
    assert generate.await_count == 2


@run_async
async def test_wellformed_rejection_is_not_retried(config):
    config['technical_attempts'] = 3
    generate = AsyncMock(return_value=response(**{'pass': False, 'reason': 'generic'}))
    assert await quality.review_conversation('candidate', generate=generate) == (False, 'generic')
    assert generate.await_count == 1


@run_async
async def test_exhausted_technical_retries_fail_closed(config, caplog):
    config['technical_attempts'] = 2
    generate = AsyncMock(return_value='not json at all')
    passed, reason = await quality.review_conversation('candidate', generate=generate)
    assert passed is False and reason.startswith('semantic review unavailable')
    assert generate.await_count == 2
    assert 'excerpt: not json at all' in caplog.text


@run_async
async def test_batch_review_retries_malformed_reply(config):
    config['technical_attempts'] = 2
    good = json.dumps({'items': [{'id': 1, 'pass': True, 'reason': 'ok', **dict.fromkeys(SCORES, 4)}]})
    generate = AsyncMock(side_effect=['{"items": [broken', good])
    assert await quality.review_conversations([{'id': 1, 'text': 'x'}], generate=generate) == {1: (True, 'ok')}
