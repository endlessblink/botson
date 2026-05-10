"""Shared Hebrew-weekday + time-of-day context block for LLM prompts.

When a generation prompt only carries an ISO date (e.g. ``2026-05-10``)
the model has to do its own date arithmetic to pick a Hebrew day name,
and that's unreliable — we've seen Sunday rows fire with "בוקר של שבת"
content because the LLM miscalculated. Translating the date into a
Hebrew weekday + time-of-day bucket *inside* the prompt removes that
class of hallucination.

Both ``dashboard/app.py:build_generation_prompt`` and
``bot/scheduler/materializer.py:_generate_fresh_text`` use the same
output here so the two pipelines can't disagree about the day.
"""

from __future__ import annotations

from datetime import date


HEBREW_DAY_NAMES: tuple[str, ...] = (
    "ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת",
)


def hebrew_day_name(scheduled_date: str) -> str | None:
    """Return the Hebrew day name (without 'יום ' prefix) for an ISO date.

    None when the input is missing or unparseable. Sun=ראשון, …, Sat=שבת.
    """
    if not scheduled_date:
        return None
    try:
        d = date.fromisoformat(scheduled_date)
    except (ValueError, TypeError):
        return None
    hebrew_idx = (d.weekday() + 1) % 7  # python Mon=0 → Hebrew Sun=0
    return HEBREW_DAY_NAMES[hebrew_idx]


def time_of_day_bucket(scheduled_time: str | None) -> str | None:
    """Return a Hebrew time-of-day bucket label, or None if unparseable."""
    if not scheduled_time:
        return None
    try:
        hour = int(str(scheduled_time).split(":")[0])
    except (ValueError, IndexError):
        return None
    if 5 <= hour < 11:
        return "בוקר"
    if 11 <= hour < 15:
        return "צהריים"
    if 15 <= hour < 19:
        return "אחר הצהריים"
    if 19 <= hour < 23:
        return "ערב"
    return "לילה"


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
        parts.append(f"יום {day} ({scheduled_date})")
    bucket = time_of_day_bucket(scheduled_time)
    if bucket and scheduled_time:
        parts.append(f"בשעה {scheduled_time} ({bucket})")
    if not parts:
        return ""
    return (
        "\n\nהקשר זמן ההודעה: " + " · ".join(parts) +
        ". התוכן צריך להרגיש מתאים ליום ולשעה האלה — לא טקסט גנרי שיכול להיות בכל זמן."
        " אם הטקסט מציין יום בשבוע, חייב להיות בדיוק היום הזה ולא יום אחר."
    )
