"""Async database connection and query helpers."""

import os
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

import aiosqlite

from .models import SCHEMA

logger = logging.getLogger(__name__)


class Database:
    """Async SQLite database wrapper."""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.getenv("DB_PATH", "./data/bot.db")
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        """Initialize database connection and create tables."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        logger.info("Database initialized at %s", self.db_path)

    async def close(self):
        """Close database connection."""
        if self._db:
            await self._db.close()
            logger.info("Database connection closed")

    # ── Members ──────────────────────────────────────────────

    async def upsert_member(self, user_id: int, username: str | None, display_name: str):
        """Insert or update a member."""
        await self._db.execute(
            """INSERT INTO members (user_id, username, display_name)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username = excluded.username,
                   display_name = excluded.display_name""",
            (user_id, username, display_name),
        )
        await self._db.commit()

    async def get_member(self, user_id: int) -> dict | None:
        """Get a member by user_id."""
        async with self._db.execute(
            "SELECT * FROM members WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_member_count_since(self, since: datetime) -> int:
        """Count members who joined since a given datetime."""
        async with self._db.execute(
            "SELECT COUNT(*) FROM members WHERE joined_at >= ?", (since.isoformat(),)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]

    # ── Karma ────────────────────────────────────────────────

    async def add_karma(self, giver_id: int, receiver_id: int, message_id: int):
        """Add a karma point and log it."""
        await self._db.execute(
            "INSERT INTO karma_log (giver_id, receiver_id, message_id) VALUES (?, ?, ?)",
            (giver_id, receiver_id, message_id),
        )
        await self._db.execute(
            "UPDATE members SET karma_points = karma_points + 1 WHERE user_id = ?",
            (receiver_id,),
        )
        await self._db.commit()

    async def get_karma(self, user_id: int) -> int:
        """Get karma points for a user."""
        async with self._db.execute(
            "SELECT karma_points FROM members WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_karma_given_today(self, giver_id: int) -> int:
        """Count how many karma points a user has given today."""
        today = date.today().isoformat()
        async with self._db.execute(
            "SELECT COUNT(*) FROM karma_log WHERE giver_id = ? AND DATE(timestamp) = ?",
            (giver_id, today),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0]

    async def get_leaderboard(self, limit: int = 10) -> list[dict]:
        """Get top karma earners."""
        async with self._db.execute(
            "SELECT user_id, display_name, karma_points FROM members ORDER BY karma_points DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def reset_karma(self):
        """Reset all karma points (new season)."""
        await self._db.execute("UPDATE members SET karma_points = 0")
        await self._db.execute("DELETE FROM karma_log")
        await self._db.commit()

    # ── Daily Prompts ────────────────────────────────────────

    async def seed_prompts(self, prompts: dict[str, list[str]]):
        """Seed prompts from YAML config if not already in DB."""
        async with self._db.execute("SELECT COUNT(*) FROM daily_prompts") as cursor:
            count = (await cursor.fetchone())[0]

        if count > 0:
            return  # Already seeded

        for prompt_type, texts in prompts.items():
            for text in texts:
                await self._db.execute(
                    "INSERT INTO daily_prompts (type, text) VALUES (?, ?)",
                    (prompt_type, text),
                )
        await self._db.commit()
        logger.info("Seeded %d prompts", sum(len(v) for v in prompts.values()))

    async def get_random_prompt(self, prompt_type: str) -> str:
        """Get a random unused prompt. Reset if all used."""
        async with self._db.execute(
            "SELECT id, text FROM daily_prompts WHERE type = ? AND last_used_at IS NULL ORDER BY RANDOM() LIMIT 1",
            (prompt_type,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            # All prompts used, reset
            await self._db.execute(
                "UPDATE daily_prompts SET last_used_at = NULL WHERE type = ?",
                (prompt_type,),
            )
            await self._db.commit()
            async with self._db.execute(
                "SELECT id, text FROM daily_prompts WHERE type = ? ORDER BY RANDOM() LIMIT 1",
                (prompt_type,),
            ) as cursor:
                row = await cursor.fetchone()

        if row:
            await self._db.execute(
                "UPDATE daily_prompts SET last_used_at = ? WHERE id = ?",
                (datetime.now().isoformat(), row[0]),
            )
            await self._db.commit()
            return row[1]

        return "☀️ בוקר טוב!" if prompt_type == "morning" else "🌙 ערב טוב!"

    # ── Spam Log ─────────────────────────────────────────────

    async def log_spam(self, user_id: int, message_text: str, rule: str, action: str):
        """Log a spam action."""
        await self._db.execute(
            "INSERT INTO spam_log (user_id, message_text, rule_triggered, action) VALUES (?, ?, ?, ?)",
            (user_id, message_text, rule, action),
        )
        await self._db.commit()

    async def get_recent_messages(self, user_id: int, seconds: int = 60) -> list[dict]:
        """Get recent messages from a user for duplicate detection."""
        since = (datetime.now() - timedelta(seconds=seconds)).isoformat()
        async with self._db.execute(
            "SELECT message_text FROM spam_log WHERE user_id = ? AND timestamp >= ?",
            (user_id, since),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Streaks ──────────────────────────────────────────────

    async def update_streak(self, user_id: int):
        """Update a member's goal streak."""
        today = date.today()

        async with self._db.execute(
            "SELECT current_streak, longest_streak, last_post_date FROM streaks WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()

        if not row:
            await self._db.execute(
                "INSERT INTO streaks (user_id, current_streak, longest_streak, last_post_date) VALUES (?, 1, 1, ?)",
                (user_id, today.isoformat()),
            )
        else:
            last_date = date.fromisoformat(row[2]) if row[2] else None
            if last_date == today:
                return  # Already posted today
            elif last_date == today - timedelta(days=1):
                new_streak = row[0] + 1
            else:
                new_streak = 1

            longest = max(row[1], new_streak)
            await self._db.execute(
                "UPDATE streaks SET current_streak = ?, longest_streak = ?, last_post_date = ? WHERE user_id = ?",
                (new_streak, longest, today.isoformat(), user_id),
            )

        await self._db.commit()

    async def get_streak(self, user_id: int) -> dict:
        """Get streak info for a user."""
        async with self._db.execute(
            "SELECT current_streak, longest_streak, last_post_date FROM streaks WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"current": row[0], "longest": row[1], "last_date": row[2]}
            return {"current": 0, "longest": 0, "last_date": None}

    async def get_top_streaks(self, limit: int = 5) -> list[dict]:
        """Get top streak holders."""
        async with self._db.execute(
            """SELECT s.user_id, m.display_name, s.current_streak, s.longest_streak
               FROM streaks s JOIN members m ON s.user_id = m.user_id
               ORDER BY s.current_streak DESC LIMIT ?""",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Stats ────────────────────────────────────────────────

    async def get_weekly_karma_leaders(self, limit: int = 3) -> list[dict]:
        """Get top karma earners this week."""
        week_ago = (datetime.now() - timedelta(days=7)).isoformat()
        async with self._db.execute(
            """SELECT k.receiver_id, m.display_name, COUNT(*) as weekly_karma
               FROM karma_log k JOIN members m ON k.receiver_id = m.user_id
               WHERE k.timestamp >= ?
               GROUP BY k.receiver_id
               ORDER BY weekly_karma DESC LIMIT ?""",
            (week_ago, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
