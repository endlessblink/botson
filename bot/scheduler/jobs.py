"""Scheduled jobs configuration using python-telegram-bot's built-in JobQueue.

Reads all schedule config from settings.yaml — times, days, everything.
No hardcoded values.
"""

import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application

from ..utils.config import TIMEZONE, get_settings

logger = logging.getLogger(__name__)

_tz = ZoneInfo(TIMEZONE)


def _parse_time(value: str) -> time:
    """Parse 'HH:MM' string into a time object with timezone."""
    parts = value.strip().split(":")
    return time(hour=int(parts[0]), minute=int(parts[1]), tzinfo=_tz)


def _hebrew_to_python_days(days: list) -> tuple:
    """Convert Hebrew week days (0=Sunday) to Python weekday (0=Monday).

    Hebrew: 0=א׳(Sun), 1=ב׳(Mon), 2=ג׳(Tue), 3=ד׳(Wed), 4=ה׳(Thu), 5=ו׳(Fri), 6=ש׳(Sat)
    Python:  0=Mon,     1=Tue,     2=Wed,     3=Thu,     4=Fri,     5=Sat,     6=Sun
    """
    return tuple((d - 1) % 7 for d in days)


def _parse_schedule(raw) -> dict:
    """Normalize schedule entry — handles both old (string) and new (dict) format."""
    if isinstance(raw, dict):
        return raw
    # Old format: plain string like "08:00" or "friday 18:00"
    if isinstance(raw, str):
        return {"time": raw, "days": [0, 1, 2, 3, 4, 5, 6]}
    return {"time": "00:00", "days": []}


def setup_jobs(app: Application) -> None:
    """Register all scheduled jobs with the application's job queue."""
    from ..handlers.goals import send_morning_prompt, send_evening_prompt
    from ..handlers.levels import send_weekly_leaderboard
    from ..handlers.roundup import send_weekly_roundup
    from ..handlers.discussions import send_discussion_prompt
    from ..handlers.events import send_event_reminder
    from ..handlers.trivia import send_scheduled_trivia

    jq = app.job_queue
    if not jq:
        logger.error("JobQueue not available — scheduled jobs will not run")
        return

    settings = get_settings()
    schedule = settings.get("schedule", {})

    # ── Morning prompt ──
    morning = _parse_schedule(schedule.get("morning_prompt", "08:00"))
    morning_time = _parse_time(morning.get("time", "08:00"))
    morning_days = tuple(morning.get("days", [0, 1, 2, 3, 4, 5, 6]))
    if morning_days:
        jq.run_daily(
            send_morning_prompt,
            time=morning_time,
            days=morning_days,
            name="morning_prompt",
        )

    # ── Evening prompt ──
    evening = _parse_schedule(schedule.get("evening_prompt", "21:00"))
    evening_time = _parse_time(evening.get("time", "21:00"))
    evening_days = tuple(evening.get("days", [0, 1, 2, 3, 4, 5, 6]))
    if evening_days:
        jq.run_daily(
            send_evening_prompt,
            time=evening_time,
            days=evening_days,
            name="evening_prompt",
        )

    # ── Weekly leaderboard ──
    leaderboard = _parse_schedule(schedule.get("weekly_leaderboard", {"time": "18:00", "days": [4]}))
    lb_time = _parse_time(leaderboard.get("time", "18:00"))
    lb_days = tuple(leaderboard.get("days", [4]))
    if lb_days:
        jq.run_daily(
            send_weekly_leaderboard,
            time=lb_time,
            days=lb_days,
            name="weekly_leaderboard",
        )

    # ── Weekly roundup ──
    roundup = _parse_schedule(schedule.get("weekly_roundup", {"time": "18:00", "days": [4]}))
    roundup_time = _parse_time(roundup.get("time", "18:00"))
    roundup_days = tuple(roundup.get("days", [4]))
    if roundup_days:
        jq.run_daily(
            send_weekly_roundup,
            time=roundup_time,
            days=roundup_days,
            name="weekly_roundup",
        )

    # ── Discussion prompts ──
    disc = _parse_schedule(schedule.get("discussion_prompt", {"times": ["10:00", "14:00", "18:00"], "days": [0, 1, 2, 3, 4]}))
    disc_days = tuple(disc.get("days", [0, 1, 2, 3, 4]))
    disc_times = disc.get("times", [])
    # Handle old format: single string "10:00,14:00,18:00"
    if isinstance(disc_times, str):
        disc_times = [t.strip() for t in disc_times.split(",")]
    if disc_days:
        for t_str in disc_times:
            t = _parse_time(t_str)
            jq.run_daily(
                send_discussion_prompt,
                time=t,
                days=disc_days,
                name=f"discussion_prompt_{t.hour}",
            )

    # ── Event reminders — daily at 09:00 ──
    jq.run_daily(
        send_event_reminder,
        time=time(hour=9, minute=0, tzinfo=_tz),
        name="event_reminder",
    )

    # ── Scheduled trivia — Wednesday and Saturday evenings ──
    trivia_sched = schedule.get("trivia", {"time": "20:00", "days": [2, 5]})
    if isinstance(trivia_sched, dict):
        trivia_time = _parse_time(trivia_sched.get("time", "20:00"))
        trivia_days = trivia_sched.get("days", [2, 5])
    else:
        trivia_time = time(hour=20, minute=0, tzinfo=_tz)
        trivia_days = [2, 5]
    for day in trivia_days:
        jq.run_daily(
            send_scheduled_trivia,
            time=trivia_time,
            days=(day,),
            name=f"trivia_day_{day}",
        )

    logger.info("Scheduled %d jobs via JobQueue", len(jq.jobs()))
