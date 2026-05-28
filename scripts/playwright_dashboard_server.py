"""Start a disposable dashboard server for Playwright tests."""

import asyncio
import json
import os
from pathlib import Path

import uvicorn

from bot.database.db import Database


async def _prepare_db(db_path: str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()

    db = Database(str(path))
    await db.init()
    try:
        await db.upsert_forum_topic(4037, "הפינה של בוטסון")
        await db.upsert_forum_topic(341, "עדכונים")
        await db.upsert_verified_forum_topic(4037, "הפינה של בוטסון", "botson_corner", "playwright")
        await db.upsert_verified_forum_topic(341, "עדכונים", "welcome", "playwright")
        await db.set_handler_routing("trivia_round", 4037, [])
        await db.set_handler_routing("trivia_warmup", 4037, [])
        await db.set_handler_routing("emoji_puzzle", 4037, [])

        marker = "warmup-rsvp:playwright-dashboard"
        await db.create_scheduled_message(
            text="חימום משחק בדיקה — לחצו כדי לקבל תזכורת",
            message_type="trivia_warmup_rsvp",
            channel_topic_id=4037,
            target_group="main",
            scheduled_date="2099-01-01",
            scheduled_time="20:00",
            poll_options=json.dumps({
                "min_ready_players": 2,
                "game_time": "21:00",
                "theme_label": "בדיקה",
                "activity_label": "טריוויה בדיקה",
                "warmup_marker": marker,
            }, ensure_ascii=False),
            status="scheduled",
        )
        await db.create_scheduled_message(
            text="",
            message_type="trivia_round",
            channel_topic_id=4037,
            target_group="main",
            scheduled_date="2099-01-01",
            scheduled_time="21:00",
            poll_options=json.dumps({
                "theme_label": "בדיקה",
                "categories": [],
                "question_count": 5,
                "min_ready_players": 2,
                "activity_label": "טריוויה בדיקה",
                "warmup_marker": marker,
            }, ensure_ascii=False),
            status="scheduled",
        )
    finally:
        await db.close()


if __name__ == "__main__":
    db_path = os.environ.get("DB_PATH", "./data/playwright-dashboard.db")
    asyncio.run(_prepare_db(db_path))
    uvicorn.run(
        "dashboard.app:app",
        host="127.0.0.1",
        port=int(os.environ.get("DASHBOARD_PORT", "3978")),
        reload=False,
    )
