"""Shared freshness checks for generated community-facing content."""

from __future__ import annotations

import re


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

STALE_FRAGMENTS = (
    "מתחממים לחידת אימוג'י",
    "נפתח Emoji Night בנושא",
    "נקודות למהירים",
    "מתחילים סיבוב",
    "בפינה של בוטסון",
    "תיבחר מהמאגר בזמן השליחה",
    "ייבחר מהמאגר בזמן השליחה",
    "הבוט יבדוק וישלח אם נמצא משחק רלוונטי",
    "יופק מנתוני הפעילות בזמן השליחה",
    "תופק מנתוני הרמות בזמן השליחה",
    "חידת אמוג'י של הערב",
    "פוסט קהילתי — למה בוטסון קיים",
    "סלוט 1/5",
    "trivia-israel-announce",
    "Emoji Night seed review",
    "emoji-puzzle-seed",
)

MISLEADING_READY_FRAGMENTS = (
    "מסמנים שהם בפנים",
    "שמסמנים שהם בפנים",
    "צריך לפחות",
)

GENERIC_FRAGMENTS = (
    "מה עשה לכם את היום",
    "מה טוב היום",
    "היום הזה עוד לא הוחלט",
    "הגענו לאמצע השבוע",
    "מבוגר האחראי בסיטואציה",
)


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
