"""Shared date and time context block for LLM prompts."""

from __future__ import annotations

from datetime import date

from .config import get_settings
from .copy import load_copy


def _time_context_copy() -> dict:
    return (get_settings().get("copy") or {}).get("time_context") or {}


def _day_names() -> tuple[str, ...]:
    configured = _time_context_copy().get("day_names") or []
    names = tuple(str(x) for x in configured if str(x).strip())
    if len(names) >= 7:
        return names[:7]
    return tuple(f"day-{idx}" for idx in range(7))


HEBREW_DAY_NAMES: tuple[str, ...] = _day_names()


def hebrew_day_name(scheduled_date: str) -> str | None:
    """Return the configured day name for an ISO date.

    None when the input is missing or unparseable.
    """
    if not scheduled_date:
        return None
    try:
        d = date.fromisoformat(scheduled_date)
    except (ValueError, TypeError):
        return None
    day_names = _day_names()
    return day_names[(d.weekday() + 1) % 7]


def time_of_day_bucket(scheduled_time: str | None) -> str | None:
    """Return a Hebrew time-of-day bucket label, or None if unparseable."""
    if not scheduled_time:
        return None
    try:
        hour = int(str(scheduled_time).split(":")[0])
    except (ValueError, IndexError):
        return None
    buckets = _time_context_copy().get("buckets") or {}
    if 5 <= hour < 11:
        return str(buckets.get("morning") or "morning")
    if 11 <= hour < 15:
        return str(buckets.get("noon") or "noon")
    if 15 <= hour < 19:
        return str(buckets.get("afternoon") or "afternoon")
    if 19 <= hour < 23:
        return str(buckets.get("evening") or "evening")
    return str(buckets.get("night") or "night")


def format_time_context(
    scheduled_date: str | None, scheduled_time: str | None
) -> str:
    """Render a Hebrew weekday + time-of-day prompt-context block.

    Empty string when neither input is supplied. Otherwise returns a
    leading double-newline followed by a one-line context plus an
    instruction telling the model to anchor on it.
    """
    parts: list[str] = []
    day = hebrew_day_name(scheduled_date or "")
    if day and scheduled_date:
        parts.append(load_copy("time_context", "day_part", default="{day} ({date})", day=day, date=scheduled_date))
    bucket = time_of_day_bucket(scheduled_time)
    if bucket and scheduled_time:
        parts.append(load_copy("time_context", "time_part", default="{time} ({bucket})", time=scheduled_time, bucket=bucket))
    if not parts:
        return ""
    prefix = load_copy("time_context", "context_prefix", default="\n\nTime context: ")
    suffix = load_copy("time_context", "context_suffix", default="")
    return prefix + " · ".join(parts) + suffix
