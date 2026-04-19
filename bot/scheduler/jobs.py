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
    """Pass Hebrew week days through unchanged.

    PTB's JobQueue.run_daily uses the same 0=Sunday convention as our config
    (_CRON_MAPPING = sun,mon,tue,wed,thu,fri,sat), so no conversion is needed.
    """
    return tuple(days)


def _parse_schedule(raw) -> dict:
    """Normalize schedule entry — handles both old (string) and new (dict) format."""
    if isinstance(raw, dict):
        return raw
    # Old format: plain string like "08:00" or "friday 18:00"
    if isinstance(raw, str):
        return {"time": raw, "days": [0, 1, 2, 3, 4, 5, 6]}
    return {"time": "00:00", "days": []}


def setup_jobs(app: Application) -> None:
    """Register scheduled jobs with the application's job queue.

    Text-content jobs (morning_prompt, evening_prompt, discussion_prompt) are
    NOT registered here anymore — they live in `scheduled_messages` via the
    materializer (bot/scheduler/materializer.py) and are sent by the
    `calendar_checker` job in bot/handlers/calendar.py. This gives dashboard
    and bot a single source of truth: every text slot is a DB row, period.

    Dynamic-content jobs (leaderboard, roundup, trivia, event_reminder) stay
    here as APScheduler cron jobs because their content is computed at send
    time from live DB state.
    """
    from ..handlers.levels import send_weekly_leaderboard
    from ..handlers.roundup import send_weekly_roundup
    from ..handlers.events import send_event_reminder
    from ..handlers.trivia import send_scheduled_trivia
    from ..handlers.emoji_puzzle import reveal_unsolved_rounds_job, send_scheduled_emoji_night
    from ..handlers.free_games import send_free_games

    jq = app.job_queue
    if not jq:
        logger.error("JobQueue not available — scheduled jobs will not run")
        return

    settings = get_settings()
    schedule = settings.get("schedule", {})

    # ── Weekly leaderboard ──
    leaderboard = _parse_schedule(schedule.get("weekly_leaderboard", {"time": "18:00", "days": [4]}))
    lb_time = _parse_time(leaderboard.get("time", "18:00"))
    lb_days = _hebrew_to_python_days(leaderboard.get("days", [4]))
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
    roundup_days = _hebrew_to_python_days(roundup.get("days", [4]))
    if roundup_days:
        jq.run_daily(
            send_weekly_roundup,
            time=roundup_time,
            days=roundup_days,
            name="weekly_roundup",
        )

    # ── Free games RSS — daily check ──
    fg = _parse_schedule(schedule.get("free_games", {"time": "10:00", "days": [0, 1, 2, 3, 4, 5, 6]}))
    fg_time = _parse_time(fg.get("time", "10:00"))
    fg_days = _hebrew_to_python_days(fg.get("days", [0, 1, 2, 3, 4, 5, 6]))
    if fg_days:
        jq.run_daily(
            send_free_games,
            time=fg_time,
            days=fg_days,
            name="free_games",
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
    for day in _hebrew_to_python_days(trivia_days):
        jq.run_daily(
            send_scheduled_trivia,
            time=trivia_time,
            days=(day,),
            name=f"trivia_day_{day}",
        )

    # ── Emoji Night — dashboard-configured weekly session ──
    emoji_sched = schedule.get("emoji_puzzle", {"time": "22:00", "days": []})
    if isinstance(emoji_sched, dict):
        emoji_time = _parse_time(emoji_sched.get("time", "22:00"))
        emoji_days = emoji_sched.get("days", []) or []
    else:
        emoji_time = time(hour=22, minute=0, tzinfo=_tz)
        emoji_days = []
    for day in _hebrew_to_python_days(emoji_days):
        jq.run_daily(
            send_scheduled_emoji_night,
            time=emoji_time,
            days=(day,),
            name=f"emoji_puzzle_day_{day}",
        )

    # ── Daily materializer refill — 00:05 IDT ──
    # Belt-and-suspenders: keeps `scheduled_messages` populated with the next
    # 14 days of morning/evening/discussion slots even for long-running bots
    # that never restart or reload.
    jq.run_daily(
        _materialize_job,
        time=time(hour=0, minute=5, tzinfo=_tz),
        name="materializer_daily",
    )

    jq.run_repeating(
        reveal_unsolved_rounds_job,
        interval=3600,
        first=600,
        name="emoji_puzzle_reveal",
    )

    logger.info("Scheduled %d cron jobs via JobQueue (text content → materializer)", len(jq.jobs()))


async def _materialize_job(context):
    """APScheduler wrapper around materializer.materialize_forward."""
    from .materializer import materialize_forward
    db = context.bot_data.get("db")
    if db:
        await materialize_forward(db)


def reload_jobs(app: Application) -> None:
    """Remove all scheduled jobs and re-register from fresh settings.

    Call this after settings.yaml changes to pick up new times/days
    without restarting the bot.
    """
    jq = app.job_queue
    if not jq:
        logger.error("JobQueue not available — cannot reload")
        return

    # Remove all existing jobs
    for job in jq.jobs():
        job.schedule_removal()
    logger.info("Removed all scheduled jobs for reload")

    # Re-register from fresh settings
    setup_jobs(app)
