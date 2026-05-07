from __future__ import annotations

from pathlib import Path
from typing import Any
import re

import yaml


class TriviaVerificationError(ValueError):
    pass


_GENERIC_TRIVIA_PATTERNS = (
    "מה הצבע",
    "מה המספר",
    "מה השם",
    "מה נכון",
    "איזה מהבאים נכון",
)


def _norm_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def review_trivia_questions(
    questions: list[dict[str, Any]],
    *,
    allowed_categories: list[str] | None = None,
    existing_questions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Deterministic trivia reviewer for AI-generated questions.

    This is intentionally conservative and cheap: it validates structure,
    duplicate risk, category fit, and obvious low-quality/generic patterns
    before generated questions can be saved or shown as acceptable output.
    """
    allowed = {str(c).strip() for c in (allowed_categories or []) if str(c).strip()}
    existing_texts = {
        _norm_text(q.get("text") or "")
        for q in (existing_questions or [])
        if isinstance(q, dict) and (q.get("text") or "").strip()
    }
    seen_texts: set[str] = set()
    issues: list[str] = []

    for i, raw in enumerate(questions, start=1):
        try:
            q = _normalize_question(raw, index=i)
        except TriviaVerificationError as e:
            issues.append(str(e))
            continue

        text = q["text"]
        norm = _norm_text(text)
        if norm in seen_texts or norm in existing_texts:
            issues.append(f"Question {i} duplicates an existing/generated question")
        seen_texts.add(norm)

        if allowed and q["category"] not in allowed:
            issues.append(
                f"Question {i} category '{q['category']}' is outside requested subject(s): {sorted(allowed)}"
            )
        if len(text) < 12:
            issues.append(f"Question {i} is too short to be meaningful")
        if len(text) > 180:
            issues.append(f"Question {i} is too long for live trivia")
        if any(pattern in text for pattern in _GENERIC_TRIVIA_PATTERNS):
            issues.append(f"Question {i} looks too generic: {text[:80]}")
        if len(set(q["options"])) != 4:
            issues.append(f"Question {i} has duplicate answer options")
        correct_answer = q["options"][q["correct"]]
        if correct_answer.lower() in {"כולם", "כל התשובות", "אף אחד", "אף תשובה"}:
            issues.append(f"Question {i} uses a weak catch-all correct answer")

    if issues:
        raise TriviaVerificationError("Trivia reviewer rejected output: " + "; ".join(issues[:8]))
    return {"reviewed_count": len(questions), "reviewer": "deterministic-trivia-reviewer-v1"}


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

    result = {
        "text": text,
        "options": normalized_options,
        "correct": correct,
        "category": category,
    }
    # Optional provenance fields — preserved verbatim if present.
    added_at = question.get("added_at")
    if added_at:
        result["added_at"] = str(added_at).strip()
    source = question.get("source")
    if source:
        result["source"] = str(source).strip()
    return result


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
    teaser_topic_id: int | None = None,
    teaser_text: str | None = None,
) -> dict[str, Any]:
    normalized_target = str(target or "").strip().lower()
    if normalized_target not in {"main", "test"}:
        raise TriviaVerificationError(f"Unknown trivia target '{target}'")

    chat_id = test_group_id if normalized_target == "test" else main_group_id
    if not chat_id:
        raise TriviaVerificationError(f"No chat id configured for target '{normalized_target}'")

    normalized_categories = [str(category).strip() for category in categories if str(category).strip()]
    theme = str(theme_label or "").strip() or (normalized_categories[0] if normalized_categories else "כללי")
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
        # Test target: the test group is a regular chat without forum topics,
        # so any topic_id picked from the main-group dropdown would land in a
        # thread Telegram doesn't recognize ("message thread not found"). Strip
        # both play and teaser thread ids and post to the test-group root.
        # Operator's main-group selections survive in localStorage so switching
        # back to target=main brings them back automatically.
        stripped_topic = int(topic_id) if topic_id is not None else None
        source = str(topic_verification_source or "").strip() or "test-target-no-topic"
        provenance = {
            "target": normalized_target,
            "topic_id": None,
            "verification_source": source,
            "stripped_main_topic_id": stripped_topic,
        }
        thread_id = None

    teaser_id = None
    if teaser_topic_id is not None:
        if normalized_target == "test":
            # Same reason as above: test group has no forums, no teasers fire.
            provenance["stripped_teaser_topic_id"] = int(teaser_topic_id)
        else:
            teaser_id = int(teaser_topic_id)
            if live_topic_ids is not None and teaser_id not in live_topic_ids:
                raise TriviaVerificationError(
                    f"Teaser topic {teaser_id} is not present in live verified topics"
                )
            if thread_id is not None and teaser_id == thread_id:
                # Pointless to teaser the same thread we play in; drop silently.
                teaser_id = None

    teaser_msg = None
    if teaser_id is not None and teaser_text is not None:
        cleaned = str(teaser_text).strip()
        if cleaned:
            teaser_msg = cleaned

    return {
        "chat_id": int(chat_id),
        "pre_roll_s": preroll,
        "thread_id": thread_id,
        "teaser_topic_id": teaser_id,
        "teaser_text": teaser_msg,
        "theme_label": theme,
        "categories": normalized_categories,
        "question_count": count,
        "target_provenance": provenance,
    }
