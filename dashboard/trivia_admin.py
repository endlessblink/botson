from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class TriviaVerificationError(ValueError):
    pass


def _normalize_question(question: dict[str, Any], *, index: int) -> dict[str, Any]:
    if not isinstance(question, dict):
        raise TriviaVerificationError(f"Question {index} must be an object")

    text = str(question.get("text") or "").strip()
    options = question.get("options") or []
    correct = question.get("correct")
    category = str(question.get("category") or "כללי").strip() or "כללי"

    if not text:
        raise TriviaVerificationError(f"Question {index} is missing text")
    if not isinstance(options, list) or len(options) != 4:
        raise TriviaVerificationError(f"Question {index} must have exactly 4 options")
    normalized_options = [str(option).strip() for option in options]
    if any(not option for option in normalized_options):
        raise TriviaVerificationError(f"Question {index} has empty options")
    if not isinstance(correct, int) or correct < 0 or correct > 3:
        raise TriviaVerificationError(f"Question {index} has invalid correct index")

    return {
        "text": text,
        "options": normalized_options,
        "correct": correct,
        "category": category,
    }


def save_and_verify_trivia_questions(path: str | Path, questions: list[dict[str, Any]]) -> dict[str, Any]:
    path = Path(path)
    if not questions:
        raise TriviaVerificationError("No trivia questions supplied")

    normalized = [_normalize_question(question, index=i + 1) for i, question in enumerate(questions)]
    payload = {"questions": normalized}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    with open(path, "r", encoding="utf-8") as f:
        persisted = yaml.safe_load(f) or {}
    persisted_questions = persisted.get("questions") or []
    reloaded = [_normalize_question(question, index=i + 1) for i, question in enumerate(persisted_questions)]

    if reloaded != normalized:
        raise TriviaVerificationError("Persisted trivia questions do not match requested questions")

    return {
        "verified_count": len(reloaded),
        "first_question": reloaded[0]["text"],
        "last_question": reloaded[-1]["text"],
    }


def build_round_trigger_payload(
    *,
    target: str,
    main_group_id: int,
    test_group_id: int | None,
    pre_roll_s: int,
    topic_id: int | None,
    topic_verification_source: str,
    theme_label: str,
    categories: list[str],
    question_count: int,
    live_topic_ids: set[int] | None = None,
) -> dict[str, Any]:
    normalized_target = str(target or "").strip().lower()
    if normalized_target not in {"main", "test"}:
        raise TriviaVerificationError(f"Unknown trivia target '{target}'")

    chat_id = test_group_id if normalized_target == "test" else main_group_id
    if not chat_id:
        raise TriviaVerificationError(f"No chat id configured for target '{normalized_target}'")

    normalized_categories = [str(category).strip() for category in categories if str(category).strip()]
    if not normalized_categories:
        raise TriviaVerificationError("At least one trivia category is required")

    theme = str(theme_label or "").strip() or normalized_categories[0]
    preroll = max(5, min(3600, int(pre_roll_s)))
    count = max(1, min(20, int(question_count)))

    if normalized_target == "main":
        if topic_id is None:
            raise TriviaVerificationError("Main-group trivia requires a verified topic_id")
        if live_topic_ids is not None and int(topic_id) not in live_topic_ids:
            raise TriviaVerificationError(f"Topic {topic_id} is not present in live verified topics")
        source = str(topic_verification_source or "").strip()
        if not source:
            raise TriviaVerificationError("Main-group trivia requires a verification source")
        provenance = {
            "target": normalized_target,
            "topic_id": int(topic_id),
            "verification_source": source,
            "verification_source_kind": "operator_asserted",
            "live_topic_verified": live_topic_ids is not None,
        }
        thread_id = int(topic_id)
    else:
        source = str(topic_verification_source or "").strip() or "test-target-no-topic"
        provenance = {
            "target": normalized_target,
            "topic_id": int(topic_id) if topic_id is not None else None,
            "verification_source": source,
        }
        thread_id = int(topic_id) if topic_id is not None else None

    return {
        "chat_id": int(chat_id),
        "pre_roll_s": preroll,
        "thread_id": thread_id,
        "theme_label": theme,
        "categories": normalized_categories,
        "question_count": count,
        "target_provenance": provenance,
    }
