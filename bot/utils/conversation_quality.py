"""Shared fail-closed semantic review for conversation candidates."""

import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from string import Formatter

from bot.utils.config import load_yaml
from bot.utils.redaction import redact_sensitive

logger = logging.getLogger(__name__)
_SCORES = ('specificity', 'naturalness', 'novelty', 'channel_fit', 'answerability', 'payoff')


async def review_conversation(
    text: str,
    *,
    category: str | None = None,
    recent_texts: Sequence[str] | None = None,
    generate: Callable[[str], Awaitable[str]],
) -> tuple[bool, str]:
    """Review a candidate using an injected provider; unavailable review rejects."""
    try:
        config = load_yaml('hot_take_review.yaml')
        reviewer_prompt = config['reviewer_prompt'].strip()
        candidate_prompt = config['candidate_prompt'].strip()
        if not reviewer_prompt or not candidate_prompt or not text.strip():
            raise ValueError('reviewer configuration or candidate missing')
        fields = {field for _, field, _, _ in Formatter().parse(candidate_prompt) if field is not None}
        if not {'text', 'category', 'recent_texts'} <= fields:
            raise ValueError('reviewer candidate template missing required context fields')
        minimum = config['minimum_score']
        score_min, score_max = config['score_min'], config['score_max']
        if any(type(value) is not int for value in (minimum, score_min, score_max)) or not score_min <= minimum <= score_max:
            raise ValueError('invalid reviewer score configuration')
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
        if not isinstance(result.get('pass'), bool):
            raise ValueError('reviewer response missing pass boolean')
        if any(type(result.get(key)) is not int or not score_min <= result[key] <= score_max for key in _SCORES):
            raise ValueError('reviewer response has invalid quality scores')
        reason = str(result.get('reason') or '').strip() or 'reviewer gave no reason'
        return result['pass'] and all(result[key] >= minimum for key in _SCORES), reason
    except Exception as error:
        safe_error = redact_sensitive(error)
        logger.warning('Conversation semantic review failed closed: %s', safe_error)
        return False, f'semantic review unavailable: {safe_error}'
