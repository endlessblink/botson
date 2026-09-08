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
