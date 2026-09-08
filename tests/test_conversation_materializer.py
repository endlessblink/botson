"""Generation must review ideas, including unseen paraphrases, before scheduling."""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from bot.scheduler import materializer


REJECTED = (
    "ראשון בבוקר - מה הדבר שאתם לוקחים איתכם מהשבוע שעבר?",
    "☀️ ראשון בבוקר — איזה דבר אתם משאירים למחצית השבוע?",
    "איזה רגע מהימים האחרונים תרצו לשמור להמשך השבוע?",
)
VALID = "איזה ספר גרם לכם לשנות דעה על דמות ששנאתם בהתחלה?"


@pytest.mark.parametrize("kind", ["morning", "evening", "discussion"])
@pytest.mark.parametrize("candidate", REJECTED)
def test_semantic_rejection_never_becomes_generated_text(kind, candidate):
    reviewer = AsyncMock(return_value=(False, "routine reflection without a concrete subject"))
    with patch.object(materializer, "review_conversation", reviewer, create=True), patch.object(
        materializer, "_generate_with_claude", AsyncMock(return_value=json.dumps({"text": candidate}))), patch.object(
        materializer, "freshness_rejection", return_value=None):
        result = asyncio.run(materializer._generate_fresh_text(
            kind, category=None, examples=[], used_texts=[],
            scheduled_date="2026-09-06", scheduled_time="09:00"))
    assert result is None
    assert reviewer.await_count == materializer._quality_gate_candidates()


def test_retired_examples_are_not_positive_prompt_material_and_valid_text_survives():
    generate = AsyncMock(return_value=json.dumps({"text": VALID}))
    reviewer = AsyncMock(return_value=(True, "concrete conversational payoff"))
    with patch.object(materializer, "review_conversation", reviewer, create=True), patch.object(
        materializer, "_generate_with_claude", generate):
        result = asyncio.run(materializer._generate_fresh_text(
            "morning", category=None, examples=[REJECTED[0]], used_texts=[],
            scheduled_date="2026-09-06", scheduled_time="09:00"))
    assert result == VALID
    assert REJECTED[0] not in generate.call_args.args[0]
    reviewer.assert_awaited_once()
