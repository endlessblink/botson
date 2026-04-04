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

    logger.info("Scheduled %d jobs via JobQueue", len(jq.jobs()))
