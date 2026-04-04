"""Scheduled jobs configuration using python-telegram-bot's built-in JobQueue."""

import logging
from datetime import time
from zoneinfo import ZoneInfo

from telegram.ext import Application

from ..utils.config import TIMEZONE

logger = logging.getLogger(__name__)

_tz = ZoneInfo(TIMEZONE)


def setup_jobs(app: Application) -> None:
    """Register all scheduled jobs with the application's job queue."""
    from ..handlers.goals import send_morning_prompt, send_evening_prompt
    from ..handlers.karma import send_weekly_leaderboard
    from ..handlers.roundup import send_weekly_roundup
    from ..handlers.discussions import send_discussion_prompt
    from ..handlers.events import send_event_reminder
    from ..handlers.trivia import send_scheduled_trivia

    jq = app.job_queue
    if not jq:
        logger.error("JobQueue not available — scheduled jobs will not run")
        return

    # Morning prompt — 08:00 Israel time daily
    jq.run_daily(
        send_morning_prompt,
        time=time(hour=8, minute=0, tzinfo=_tz),
        name="morning_prompt",
    )

    # Evening prompt — 21:00 Israel time daily
    jq.run_daily(
        send_evening_prompt,
        time=time(hour=21, minute=0, tzinfo=_tz),
        name="evening_prompt",
    )

    # Weekly karma leaderboard — Friday 18:00
    jq.run_daily(
        send_weekly_leaderboard,
        time=time(hour=18, minute=0, tzinfo=_tz),
        days=(4,),  # 4 = Friday (Monday=0)
        name="weekly_leaderboard",
    )

    # Weekly roundup — Friday 18:05 (offset to avoid overlap)
    jq.run_daily(
        send_weekly_roundup,
        time=time(hour=18, minute=5, tzinfo=_tz),
        days=(4,),  # 4 = Friday
        name="weekly_roundup",
    )

    # Discussion prompts — 3 times daily, random channel each time
    for hour in (10, 14, 18):
        jq.run_daily(
            send_discussion_prompt,
            time=time(hour=hour, minute=0, tzinfo=_tz),
            name=f"discussion_prompt_{hour}",
        )

    # Event reminders — daily at 09:00, reminds about tomorrow's events
    jq.run_daily(
        send_event_reminder,
        time=time(hour=9, minute=0, tzinfo=_tz),
        name="event_reminder",
    )

    # Scheduled trivia — Wednesday and Saturday evenings
    for day in (2, 5):  # Wednesday=2, Saturday=5
        jq.run_daily(
            send_scheduled_trivia,
            time=time(hour=20, minute=0, tzinfo=_tz),
            days=(day,),
            name=f"trivia_day_{day}",
        )

    logger.info("Scheduled %d jobs via JobQueue", len(jq.jobs()))
