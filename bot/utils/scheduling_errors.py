"""Shared exception type for scheduled-activity dispatch.

Lives outside ``bot/handlers`` so handler modules can import it without
creating a circular dependency on ``calendar.py`` (which imports the
handlers themselves).

Raise ``SkippedActivity`` from a dispatch handler when the no-send is a
legitimate no-op (pool exhausted on cooldown, feature disabled,
already-active session, no candidates today, blackout date) — NOT when
something actually went wrong. The calendar's exception handler maps it
to ``mark_message_skipped`` instead of ``mark_message_failed``, so the
dashboard activity log distinguishes "nothing to do" from "broken".
"""
from __future__ import annotations


class SkippedActivity(RuntimeError):
    """Scheduled activity made an intentional no-op decision."""
