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
from .time_context import HEBREW_DAY_NAMES, hebrew_day_name

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


# Layer 2 day-anchor validator. We pin the patterns where a Hebrew day
# name appears as a *claim about today* (vs. neutral mention). Catches
# the 2026-05-10 regression where the LLM ignored the prompt anchor and
# generated "בוקר של שבת" on a Sunday row.
_DAY_ALT = "|".join(HEBREW_DAY_NAMES)
_DAY_PATTERNS_AS_TODAY = (
    re.compile(rf"\bיום\s+({_DAY_ALT})\b"),
    re.compile(rf"\b(?:בוקר|ערב|צהריים|לילה)\s+של\s+({_DAY_ALT})\b"),
    re.compile(rf"\b(?:בוקר|ערב|צהריים|לילה)\s+({_DAY_ALT})\b"),
    re.compile(rf"\b({_DAY_ALT})\s+(?:בבוקר|בערב|בלילה|בצהריים)\b"),
    re.compile(rf"\b({_DAY_ALT})\s+(?:שמח|טוב|שלום)\b"),
)


def day_anchor_rejection(text: str, scheduled_date: str | None) -> str | None:
    """Reject text that names a Hebrew day-of-week different from the
    scheduled day. Returns a rejection reason or None."""
    if not scheduled_date:
        return None
    expected = hebrew_day_name(scheduled_date)
    if not expected:
        return None
    for pattern in _DAY_PATTERNS_AS_TODAY:
        for match in pattern.finditer(text):
            named = match.group(1)
            if named and named != expected:
                return (
                    f"wrong day-of-week: text claims {named!r} "
                    f"but scheduled day is {expected!r} ({scheduled_date})"
                )
    return None


def freshness_rejection(
    text: str,
    *,
    avoid_texts: set[str] | None = None,
    source_examples: set[str] | None = None,
    allow_internal: bool = False,
    scheduled_date: str | None = None,
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
    day_reason = day_anchor_rejection(raw, scheduled_date)
    if day_reason:
        return day_reason
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
