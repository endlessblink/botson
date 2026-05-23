"""Single source of truth for which dispatcher OWNS each recurring content type.

This bot has two dispatch mechanisms:

  * APScheduler cron jobs (``bot/scheduler/jobs.py``) — fire dynamic content that is
    computed live at send time, on a weekly/daily cadence read from
    ``settings.yaml`` ``schedule.*``.
  * The per-minute ``calendar_checker`` (``bot/handlers/calendar.py``) — dispatches
    ``scheduled_messages`` rows created by the dashboard / planner / Populate.

**A content type must have exactly ONE active dispatcher.** Wiring the same type into
both is what posted the weekly leaderboard twice on 2026-05-23 (one send from the cron
job, one from a ``weekly_leaderboard`` calendar row). This registry makes ownership
explicit and is enforced by ``tests/test_no_dual_dispatch.py``.

To add a new recurring content type, classify it here FIRST:
  * ``"cron"``     — fired by a cron job in jobs.py; the calendar dispatcher must skip
    it and it must not be creatable as a scheduled_messages row.
  * ``"calendar"`` — dispatched from scheduled_messages rows; it must NOT have an
    active cron (``schedule.<type>.days`` empty if a cron code path exists).
"""

# Owner is "cron" or "calendar". Keep in sync with jobs.py registrations and the
# calendar dispatch branches — the guardian test fails CI on drift.
DISPATCHER_OWNER: dict[str, str] = {
    "weekly_leaderboard": "cron",
    "weekly_roundup": "cron",
    "free_games": "cron",
    "emoji_puzzle": "calendar",   # cron code path exists but must stay inert (days empty)
    "trivia_round": "calendar",
    "facts_tidbit": "calendar",
    "facts_spooky": "calendar",
}

# Types the calendar dispatcher must SKIP (cron owns them). Used by the dispatcher,
# create_calendar_item, ai-suggest-commit and Populate so there is one definition.
CRON_OWNED_TYPES = frozenset(t for t, o in DISPATCHER_OWNER.items() if o == "cron")

CALENDAR_OWNED_TYPES = frozenset(t for t, o in DISPATCHER_OWNER.items() if o == "calendar")

# Calendar-owned types that ALSO have a cron registration code path in jobs.py. Their
# cron must stay inert (``schedule.<type>.days`` empty) or they become dual-dispatched.
# The guardian asserts this.
CALENDAR_OWNED_WITH_CRON_PATH = frozenset({"emoji_puzzle"})
