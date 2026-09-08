"""Evaluate the conversation-review candidate without sending or storing messages."""

import asyncio
import json
import logging
from pathlib import Path

from bot.scheduler.materializer import _generate_with_claude
from bot.utils.conversation_quality import review_conversation


async def main() -> int:
    fixtures = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/conversation_quality_eval.json').read_text())
    if not isinstance(fixtures, list) or len(fixtures) != 6:
        raise ValueError('Expected exactly six evaluation fixtures')
    logging.disable(logging.CRITICAL)
    mismatch = False
    for fixture in fixtures:
        verdict, reason = await review_conversation(
            fixture['text'], category=fixture['category'],
            recent_texts=[], generate=_generate_with_claude,
        )
        print(json.dumps({
            'id': fixture['id'], 'expected': fixture['expected'],
            'verdict': verdict, 'reason': reason,
        }, ensure_ascii=False), flush=True)
        mismatch |= verdict != fixture['expected']
    return int(mismatch)


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
