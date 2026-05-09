"""Shared freshness checks for generated community-facing content.

Ban-list source of truth lives in ``config/freshness.yaml``. Both this
runtime validator and the hardcoded-content guardian test
(``tests/test_no_hardcoded_content.py``) load from that file so there's no
parallel copy in code. When new stale phrasing slips through, edit the
YAML — no code change required.
"""

from __future__ import annotations

import logging
import re

from .config import load_yaml

logger = logging.getLogger(__name__)


TEXT_CONTENT_TYPES = {"morning", "evening", "discussion", "custom"}
EXECUTABLE_TYPES = {
    "trivia_round",
    "emoji_puzzle",
    "facts_tidbit",
    "facts_spooky",
    "free_games",
    "weekly_roundup",
    "weekly_leaderboard",
}


def _load_fragment_lists() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Read fragment lists from ``config/freshness.yaml``.

    Falls back to empty tuples + a logged warning when the file is missing
    or malformed; this disables freshness rejection rather than crashing,
    so a config typo doesn't take the bot offline.
    """
    try:
        data = load_yaml("freshness.yaml") or {}
    except FileNotFoundError:
        logger.warning("freshness: config/freshness.yaml missing — fragment list empty")
        return (), (), ()
    except Exception as e:
        logger.warning("freshness: failed to load config/freshness.yaml: %s", e)
        return (), (), ()
    stale = tuple(str(x) for x in (data.get("stale_fragments") or []))
    misleading = tuple(str(x) for x in (data.get("misleading_ready_fragments") or []))
    generic = tuple(str(x) for x in (data.get("generic_fragments") or []))
    return stale, misleading, generic


STALE_FRAGMENTS, MISLEADING_READY_FRAGMENTS, GENERIC_FRAGMENTS = _load_fragment_lists()


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_internal_label(text: str) -> bool:
    return normalize_text(text).startswith("[internal:")


def has_hebrew(text: str) -> bool:
    return any("\u0590" <= ch <= "\u05ff" for ch in text or "")


def freshness_rejection(
    text: str,
    *,
    avoid_texts: set[str] | None = None,
    source_examples: set[str] | None = None,
    allow_internal: bool = False,
) -> str | None:
    """Return a rejection reason, or None when the text is safe to store/send."""
    raw = (text or "").strip()
    if not raw:
        return "empty"
    if allow_internal and is_internal_label(raw):
        return None
    if not has_hebrew(raw):
        return "no Hebrew"
    normalized = normalize_text(raw)
    for fragment in STALE_FRAGMENTS + MISLEADING_READY_FRAGMENTS + GENERIC_FRAGMENTS:
        if normalize_text(fragment) in normalized:
            return f"forbidden fragment: {fragment}"
    for existing in avoid_texts or set():
        existing_norm = normalize_text(existing)
        if existing_norm and (
            normalized == existing_norm
            or normalized in existing_norm
            or existing_norm in normalized
        ):
            return "repeated scheduled text"
    for example in source_examples or set():
        example_norm = normalize_text(example)
        if example_norm and (
            normalized == example_norm
            or normalized in example_norm
            or example_norm in normalized
        ):
            return "copied static example"
    return None


def stale_reasons_for_row(row: dict, *, source_examples: set[str] | None = None) -> list[str]:
    """Reasons a scheduled_messages row should be reviewed/cleaned."""
    text = str(row.get("text") or "")
    mtype = str(row.get("message_type") or "")
    reason = freshness_rejection(
        text,
        source_examples=source_examples,
        allow_internal=mtype in EXECUTABLE_TYPES,
    )
    return [reason] if reason else []
