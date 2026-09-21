"""Shared fail-closed semantic review for conversation candidates."""

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from string import Formatter

from bot.utils.config import load_yaml
from bot.utils.redaction import redact_sensitive

logger = logging.getLogger(__name__)
_SCORES = ('specificity', 'naturalness', 'novelty', 'channel_fit', 'answerability', 'payoff')


def _review_contract() -> tuple[str, str, int, int, int]:
    config = load_yaml('hot_take_review.yaml')
    reviewer_prompt = config['reviewer_prompt'].strip()
    candidate_prompt = config['candidate_prompt'].strip()
    if not reviewer_prompt or not candidate_prompt:
        raise ValueError('reviewer configuration missing')
    fields = {field for _, field, _, _ in Formatter().parse(candidate_prompt) if field is not None}
    if not {'text', 'category', 'recent_texts'} <= fields:
        raise ValueError('reviewer candidate template missing required context fields')
    minimum = config['minimum_score']
    score_min, score_max = config['score_min'], config['score_max']
    if any(type(value) is not int for value in (minimum, score_min, score_max)) or not score_min <= minimum <= score_max:
        raise ValueError('invalid reviewer score configuration')
    return reviewer_prompt, candidate_prompt, minimum, score_min, score_max


def _parse_verdict(result: Mapping, minimum: int, score_min: int, score_max: int) -> tuple[bool, str]:
    if not isinstance(result.get('pass'), bool):
        raise ValueError('reviewer response missing pass boolean')
    if any(type(result.get(key)) is not int or not score_min <= result[key] <= score_max for key in _SCORES):
        raise ValueError('reviewer response has invalid quality scores')
    reason = str(result.get('reason') or '').strip() or 'reviewer gave no reason'
    return result['pass'] and all(result[key] >= minimum for key in _SCORES), reason


async def review_conversation(
    text: str,
    *,
    category: str | None = None,
    recent_texts: Sequence[str] | None = None,
    generate: Callable[[str], Awaitable[str]],
) -> tuple[bool, str]:
    """Review a candidate using an injected provider; unavailable review rejects."""
    try:
        reviewer_prompt, candidate_prompt, minimum, score_min, score_max = _review_contract()
        if not text.strip():
            raise ValueError('candidate missing')
        recent_block = '\n'.join(str(item).strip() for item in (recent_texts or []) if str(item).strip())
        prompt = reviewer_prompt + '\n\n' + candidate_prompt.format(
            category=category or '', recent_texts=recent_block, text=text.strip(),
        )
        raw = await generate(prompt)
        candidate = (raw or '').strip()
        start, end = candidate.find('{'), candidate.rfind('}')
        if start < 0 or end <= start:
            raise ValueError('reviewer returned no JSON object')
        result = json.loads(candidate[start:end + 1])
        return _parse_verdict(result, minimum, score_min, score_max)
    except Exception as error:
        safe_error = redact_sensitive(error)
        logger.warning('Conversation semantic review failed closed: %s', safe_error)
        return False, f'semantic review unavailable: {safe_error}'


async def review_conversations(
    candidates: Sequence[Mapping],
    *,
    generate: Callable[[str], Awaitable[str]],
) -> dict[int, tuple[bool, str]]:
    """Review a candidate batch in one provider call; any invalid batch fails closed."""
    ids = [candidate.get('id') for candidate in candidates]
    expected_ids = {candidate_id for candidate_id in ids if type(candidate_id) is int}

    def reject_all(error: object) -> dict[int, tuple[bool, str]]:
        safe_error = redact_sensitive(error)
        logger.warning('Conversation batch semantic review failed closed: %s', safe_error)
        return {
            candidate_id: (False, f'semantic review unavailable: {safe_error}')
            for candidate_id in expected_ids
        }

    try:
        if len(expected_ids) != len(candidates):
            raise ValueError('candidate ids missing or duplicated')
        if not candidates:
            return {}
        reviewer_prompt, candidate_prompt, minimum, score_min, score_max = _review_contract()
        rendered = []
        for candidate in candidates:
            text = str(candidate.get('text') or '').strip()
            if not text:
                raise ValueError('candidate missing')
            recent_block = '\n'.join(
                str(item).strip()
                for item in (candidate.get('recent_texts') or [])
                if str(item).strip()
            )
            rendered.append({
                'id': candidate['id'],
                'candidate': candidate_prompt.format(
                    category=str(candidate.get('category') or ''),
                    recent_texts=recent_block,
                    text=text,
                ),
            })
        prompt = (
            reviewer_prompt
            + '\n\nReview every candidate below independently with the identical rubric. '
            'Return JSON only as {"items":[...]} where each item contains its original integer id '
            'and exactly the same pass, reason, specificity, naturalness, novelty, channel_fit, '
            'answerability, and payoff fields required above. Do not omit or duplicate ids.\n\n'
            + json.dumps(rendered, ensure_ascii=False)
        )
        raw = await generate(prompt)
        payload = (raw or '').strip()
        start, end = payload.find('{'), payload.rfind('}')
        if start < 0 or end <= start:
            raise ValueError('reviewer returned no JSON object')
        parsed = json.loads(payload[start:end + 1])
        items = parsed.get('items') if isinstance(parsed, dict) else None
        if not isinstance(items, list):
            raise ValueError('reviewer response missing items list')
        results: dict[int, tuple[bool, str]] = {}
        for item in items:
            if not isinstance(item, dict) or type(item.get('id')) is not int:
                raise ValueError('reviewer item missing integer id')
            item_id = item['id']
            if item_id not in expected_ids:
                raise ValueError('reviewer returned unknown id')
            if item_id in results:
                raise ValueError('reviewer returned duplicate id')
            results[item_id] = _parse_verdict(item, minimum, score_min, score_max)
        if set(results) != expected_ids:
            raise ValueError('reviewer omitted candidate id')
        return results
    except Exception as error:
        return reject_all(error)
