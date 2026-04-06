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
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA busy_timeout=5000")
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

    # ── Points / Levels ──────────────────────────────────────

    async def add_points(self, user_id: int, points: int = 1) -> int:
        """Add points to a user. Returns the point count BEFORE adding."""
        async with self._db.execute(
            "SELECT karma_points FROM members WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            old_points = row[0] if row else 0

        await self._db.execute(
            "UPDATE members SET karma_points = karma_points + ? WHERE user_id = ?",
            (points, user_id),
        )
        await self._db.commit()
        return old_points

    async def get_points(self, user_id: int) -> int:
        """Get points for a user."""
        async with self._db.execute(
            "SELECT karma_points FROM members WHERE user_id = ?", (user_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_leaderboard(self, limit: int = 10) -> list[dict]:
        """Get top points earners."""
        async with self._db.execute(
            "SELECT user_id, display_name, karma_points FROM members ORDER BY karma_points DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def reset_points(self):
        """Reset all points (new season)."""
        await self._db.execute("UPDATE members SET karma_points = 0")
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

    async def get_weekly_leaders(self, limit: int = 3) -> list[dict]:
        """Get top points earners this week (by total points, since weekly log no longer exists)."""
        async with self._db.execute(
            """SELECT user_id, display_name, karma_points as weekly_stars
               FROM members
               ORDER BY karma_points DESC LIMIT ?""",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Events ───────────────────────────────────────────────

    async def create_event(self, title: str, description: str, event_date: str,
                           event_time: str | None, location: str | None,
                           created_by: int) -> int:
        """Create a new event. Returns event ID."""
        async with self._db.execute(
            """INSERT INTO events (title, description, event_date, event_time, location, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, description, event_date, event_time, location, created_by),
        ) as cursor:
            event_id = cursor.lastrowid
        await self._db.commit()
        return event_id

    async def get_event(self, event_id: int) -> dict | None:
        """Get an event by ID."""
        async with self._db.execute(
            "SELECT * FROM events WHERE id = ? AND active = 1", (event_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_upcoming_events(self, limit: int = 10) -> list[dict]:
        """Get upcoming active events."""
        today = date.today().isoformat()
        async with self._db.execute(
            """SELECT * FROM events WHERE active = 1 AND event_date >= ?
               ORDER BY event_date ASC, event_time ASC LIMIT ?""",
            (today, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_all_events(self) -> list[dict]:
        """Get all events (for dashboard)."""
        async with self._db.execute(
            "SELECT * FROM events ORDER BY event_date DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_event_rsvp(self, event_id: int, user_id: int, status: str):
        """Update RSVP for an event. Status: 'yes' or 'maybe'."""
        import json
        event = await self.get_event(event_id)
        if not event:
            return

        yes_list = json.loads(event["rsvp_yes"])
        maybe_list = json.loads(event["rsvp_maybe"])

        # Remove from both lists first
        yes_list = [uid for uid in yes_list if uid != user_id]
        maybe_list = [uid for uid in maybe_list if uid != user_id]

        if status == "yes":
            yes_list.append(user_id)
        elif status == "maybe":
            maybe_list.append(user_id)

        await self._db.execute(
            "UPDATE events SET rsvp_yes = ?, rsvp_maybe = ? WHERE id = ?",
            (json.dumps(yes_list), json.dumps(maybe_list), event_id),
        )
        await self._db.commit()

    async def delete_event(self, event_id: int):
        """Soft-delete an event."""
        await self._db.execute(
            "UPDATE events SET active = 0 WHERE id = ?", (event_id,)
        )
        await self._db.commit()

    async def update_event(self, event_id: int, **fields):
        """Update event fields."""
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [event_id]
        await self._db.execute(
            f"UPDATE events SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()

    # ── Trivia ───────────────────────────────────────────────

    async def add_trivia_score(self, user_id: int, correct: bool):
        """Update trivia score for a user."""
        # Ensure row exists
        await self._db.execute(
            """INSERT INTO trivia_scores (user_id, total_score, correct_answers, total_answers)
               VALUES (?, 0, 0, 0)
               ON CONFLICT(user_id) DO NOTHING""",
            (user_id,),
        )
        if correct:
            await self._db.execute(
                """UPDATE trivia_scores
                   SET total_score = total_score + 10, correct_answers = correct_answers + 1,
                       total_answers = total_answers + 1
                   WHERE user_id = ?""",
                (user_id,),
            )
        else:
            await self._db.execute(
                "UPDATE trivia_scores SET total_answers = total_answers + 1 WHERE user_id = ?",
                (user_id,),
            )
        await self._db.commit()

    async def get_trivia_leaderboard(self, limit: int = 10) -> list[dict]:
        """Get top trivia scorers."""
        async with self._db.execute(
            """SELECT t.user_id, m.display_name, t.total_score, t.correct_answers, t.total_answers
               FROM trivia_scores t JOIN members m ON t.user_id = m.user_id
               ORDER BY t.total_score DESC LIMIT ?""",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_trivia_score(self, user_id: int) -> dict:
        """Get trivia score for a user."""
        async with self._db.execute(
            "SELECT total_score, correct_answers, total_answers FROM trivia_scores WHERE user_id = ?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {"score": row[0], "correct": row[1], "total": row[2]}
            return {"score": 0, "correct": 0, "total": 0}

    async def reset_trivia_scores(self):
        """Reset all trivia scores."""
        await self._db.execute("DELETE FROM trivia_scores")
        await self._db.commit()

    # ── Activity Log ─────────────────────────────────────────

    async def log_activity(self, action_type: str, description: str,
                           target_user_id: int | None = None, target_channel: str | None = None):
        """Log a bot action for the activity dashboard."""
        await self._db.execute(
            "INSERT INTO activity_log (action_type, description, target_user_id, target_channel) VALUES (?, ?, ?, ?)",
            (action_type, description, target_user_id, target_channel),
        )
        await self._db.commit()

    async def get_activity_log(self, limit: int = 100) -> list[dict]:
        """Get recent activity log entries."""
        async with self._db.execute(
            "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Forum Topics ─────────────────────────────────────────

    async def upsert_forum_topic(self, topic_id: int, name: str):
        """Track a forum topic seen in messages."""
        await self._db.execute(
            """INSERT INTO forum_topics (topic_id, name, last_seen_at)
               VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(topic_id) DO UPDATE SET
               name = excluded.name, last_seen_at = CURRENT_TIMESTAMP""",
            (topic_id, name),
        )
        await self._db.commit()

    async def get_forum_topics(self) -> list[dict]:
        """Get all known forum topics."""
        async with self._db.execute(
            "SELECT topic_id, name, last_seen_at FROM forum_topics ORDER BY name"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]
