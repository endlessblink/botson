"""Async database connection and query helpers."""

import json
import os
import logging
from datetime import datetime, date, timedelta
from pathlib import Path

from zoneinfo import ZoneInfo

import aiosqlite

from .models import SCHEMA

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _now_il() -> str:
    """Return current Israel time as ISO string."""
    return datetime.now(_IL_TZ).strftime("%Y-%m-%d %H:%M:%S")

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
        await self._migrate()
        await self._db.commit()
        logger.info("Database initialized at %s", self.db_path)

    async def _migrate(self):
        """Apply idempotent schema migrations for pre-existing DBs.

        Each migration catches OperationalError so re-runs are no-ops.
        """
        migrations = [
            "ALTER TABLE scheduled_messages ADD COLUMN cover_path TEXT",
            "ALTER TABLE scheduled_messages ADD COLUMN poll_options TEXT",
            "ALTER TABLE scheduled_messages ADD COLUMN poll_duration INTEGER",
            "ALTER TABLE events ADD COLUMN cover_path TEXT",
            "ALTER TABLE events ADD COLUMN auto_pin INTEGER DEFAULT 0",
            "ALTER TABLE events ADD COLUMN topic_id INTEGER",
            "ALTER TABLE events ADD COLUMN source_poll_message_id INTEGER",
            "ALTER TABLE events ADD COLUMN source_poll_option_key TEXT",
        ]
        for sql in migrations:
            try:
                await self._db.execute(sql)
            except Exception as e:  # noqa: BLE001
                if "duplicate column" not in str(e).lower():
                    logger.warning("Migration skipped/failed: %s (%s)", sql, e)

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

    async def seed_emoji_puzzles(self, puzzles: list[dict]):
        """Seed emoji puzzles from YAML if the pool is still empty."""
        async with self._db.execute("SELECT COUNT(*) FROM emoji_puzzles") as cursor:
            count = (await cursor.fetchone())[0]

        if count > 0 or not puzzles:
            return

        for puzzle in puzzles:
            aliases = puzzle.get("aliases", []) or []
            await self._db.execute(
                """INSERT INTO emoji_puzzles
                   (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    puzzle["emoji_prompt"],
                    puzzle["answer_he"],
                    puzzle["answer_en"],
                    json.dumps(aliases, ensure_ascii=False),
                    int(puzzle.get("difficulty", 2)),
                    puzzle.get("media_type", "movie"),
                    1 if puzzle.get("enabled", True) else 0,
                    _now_il(),
                ),
            )
        await self._db.commit()
        logger.info("Seeded %d emoji puzzles", len(puzzles))

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
                (_now_il(), row[0]),
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
        since = (datetime.now(_IL_TZ) - timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
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
                           created_by: int,
                           cover_path: str | None = None,
                           auto_pin: bool = False,
                           topic_id: int | None = None,
                           source_poll_message_id: int | None = None,
                           source_poll_option_key: str | None = None) -> int:
        """Create a new event. Returns event ID.

        New optional fields support dashboard-driven Telegram posting:
          cover_path:           media path (relative to MEDIA_DIR) for photo+caption layout
          auto_pin:             pin the posted Telegram message
          topic_id:             forum topic to post into (message_thread_id)
          source_poll_*:        provenance for events created from a poll's winning option
        """
        async with self._db.execute(
            """INSERT INTO events (title, description, event_date, event_time, location,
                                   created_by, cover_path, auto_pin, topic_id,
                                   source_poll_message_id, source_poll_option_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (title, description, event_date, event_time, location, created_by,
             cover_path, 1 if auto_pin else 0, topic_id,
             source_poll_message_id, source_poll_option_key),
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
        """Log a bot action for the activity dashboard. Uses Israel time."""
        await self._db.execute(
            "INSERT INTO activity_log (action_type, description, target_user_id, target_channel, timestamp) VALUES (?, ?, ?, ?, ?)",
            (action_type, description, target_user_id, target_channel, _now_il()),
        )
        await self._db.commit()

    async def get_activity_log(self, limit: int = 100) -> list[dict]:
        """Get recent activity log entries."""
        async with self._db.execute(
            "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Blocked Users ────────────────────────────────────────

    async def block_user(self, user_id: int, blocked_by: str = "", reason: str = ""):
        """Block a user. Silently ignores if already blocked."""
        await self._db.execute(
            "INSERT OR IGNORE INTO blocked_users (user_id, blocked_by, reason) VALUES (?, ?, ?)",
            (user_id, blocked_by, reason),
        )
        await self._db.commit()

    async def unblock_user(self, user_id: int):
        """Remove a user from the blocked list."""
        await self._db.execute(
            "DELETE FROM blocked_users WHERE user_id = ?", (user_id,)
        )
        await self._db.commit()

    async def is_blocked(self, user_id: int) -> bool:
        """Return True if the user is blocked."""
        async with self._db.execute(
            "SELECT 1 FROM blocked_users WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get_blocked_users(self) -> list[dict]:
        """Get all blocked users with display names where available."""
        async with self._db.execute(
            """SELECT b.*, m.display_name FROM blocked_users b
               LEFT JOIN members m ON b.user_id = m.user_id
               ORDER BY b.timestamp DESC""",
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Forum Topics ─────────────────────────────────────────

    async def upsert_forum_topic(self, topic_id: int, name: str):
        """Track a forum topic seen in messages. Uses Israel time."""
        now = _now_il()
        await self._db.execute(
            """INSERT INTO forum_topics (topic_id, name, last_seen_at)
               VALUES (?, ?, ?)
               ON CONFLICT(topic_id) DO UPDATE SET
               name = excluded.name, last_seen_at = ?""",
            (topic_id, name, now, now),
        )
        await self._db.commit()

    async def get_forum_topics(self) -> list[dict]:
        """Get all known forum topics."""
        async with self._db.execute(
            "SELECT topic_id, name, last_seen_at FROM forum_topics ORDER BY name"
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    # ── Scheduled Messages (Content Calendar) ────────────────

    async def get_scheduled_messages(self, date_from: str, date_to: str) -> list[dict]:
        """Get scheduled messages for a date range."""
        async with self._db.execute(
            """SELECT * FROM scheduled_messages
               WHERE scheduled_date >= ? AND scheduled_date <= ?
               ORDER BY scheduled_date, scheduled_time""",
            (date_from, date_to),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_due_messages(self, current_date: str, current_time: str) -> list[dict]:
        """Get messages that are due to be sent now."""
        async with self._db.execute(
            """SELECT * FROM scheduled_messages
               WHERE status = 'scheduled'
               AND scheduled_date = ?
               AND scheduled_time <= ?
               ORDER BY scheduled_time""",
            (current_date, current_time),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def create_scheduled_message(self, text: str, message_type: str,
                                        channel_topic_id: int | None,
                                        target_group: str,
                                        scheduled_date: str, scheduled_time: str,
                                        recurrence: str | None = None,
                                        recurrence_days: str | None = None,
                                        auto_pin: bool = False,
                                        created_by: str = "dashboard",
                                        cover_path: str | None = None,
                                        poll_options: str | None = None,
                                        poll_duration: int | None = None) -> int:
        """Create a new scheduled message. Returns ID."""
        async with self._db.execute(
            """INSERT INTO scheduled_messages
               (text, message_type, channel_topic_id, target_group,
                scheduled_date, scheduled_time, recurrence, recurrence_days,
                auto_pin, created_by, cover_path, poll_options, poll_duration)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (text, message_type, channel_topic_id, target_group,
             scheduled_date, scheduled_time, recurrence, recurrence_days,
             auto_pin, created_by, cover_path, poll_options, poll_duration),
        ) as cursor:
            msg_id = cursor.lastrowid
        await self._db.commit()
        return msg_id

    async def update_scheduled_message(self, msg_id: int, **fields):
        """Update a scheduled message."""
        if not fields:
            return
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [msg_id]
        await self._db.execute(
            f"UPDATE scheduled_messages SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()

    async def mark_message_sent(self, msg_id: int, sent_message_id: int):
        """Mark a scheduled message as sent."""
        await self._db.execute(
            "UPDATE scheduled_messages SET status = 'sent', sent_at = ?, sent_message_id = ? WHERE id = ?",
            (_now_il(), sent_message_id, msg_id),
        )
        await self._db.commit()

    async def mark_message_failed(self, msg_id: int, error: str):
        """Mark a scheduled message as failed."""
        await self._db.execute(
            "UPDATE scheduled_messages SET status = 'failed', error_message = ? WHERE id = ?",
            (error, msg_id),
        )
        await self._db.commit()

    async def get_draft_messages(self) -> list[dict]:
        """Get all draft messages awaiting approval."""
        async with self._db.execute(
            "SELECT * FROM scheduled_messages WHERE status = 'draft' ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def delete_scheduled_message(self, msg_id: int):
        """Cancel a scheduled message."""
        await self._db.execute(
            "UPDATE scheduled_messages SET status = 'cancelled' WHERE id = ?", (msg_id,)
        )
        await self._db.commit()

    async def cancel_future_auto_scheduled_messages(self, from_date: str) -> int:
        """Cancel all future auto-materialized rows from a given date onward.

        Used by the materializer on config reload. Leaves user-committed rows
        (created_by != 'auto') untouched. Returns number of rows cancelled.
        """
        async with self._db.execute(
            """UPDATE scheduled_messages
               SET status = 'cancelled'
               WHERE created_by = 'auto'
                 AND status = 'scheduled'
                 AND scheduled_date >= ?""",
            (from_date,),
        ) as cursor:
            count = cursor.rowcount
        await self._db.commit()
        return count

    # ── Free Games (RSS freebie feed) ────────────────────────

    async def is_game_posted(self, guid: str) -> bool:
        """Return True if a free-game GUID has already been posted."""
        async with self._db.execute(
            "SELECT 1 FROM free_games_posted WHERE guid = ?", (guid,)
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_game_posted(self, guid: str, title: str, store: str | None,
                                link: str | None, message_id: int | None = None):
        """Record that a free game was posted (for dedup)."""
        await self._db.execute(
            """INSERT OR IGNORE INTO free_games_posted
               (guid, title, store, link, message_id, posted_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (guid, title, store, link, message_id, _now_il()),
        )
        await self._db.commit()

    async def recent_free_games(self, limit: int = 20) -> list[dict]:
        """Recent free-game posts for the dashboard."""
        async with self._db.execute(
            """SELECT id, guid, title, store, link, posted_at, message_id
               FROM free_games_posted
               ORDER BY posted_at DESC LIMIT ?""",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def unpost_free_game(self, guid: str) -> int:
        """Delete a free-game posted record so it can be re-posted."""
        async with self._db.execute(
            "DELETE FROM free_games_posted WHERE guid = ?", (guid,)
        ) as cursor:
            count = cursor.rowcount
        await self._db.commit()
        return count

    # ── Topic Observations (off-topic routing, Phase 0: observe only) ────

    async def log_topic_observation(
        self,
        user_id: int,
        from_topic_id: int | None,
        message_id: int | None,
        keyword_hits: str,
        fit_label: str,
        suggested_topic_id: int | None,
    ):
        """Record an observation of a message's topic fit. Phase 0: no user action."""
        await self._db.execute(
            """INSERT INTO topic_observations
               (user_id, from_topic_id, message_id, keyword_hits, fit_label, suggested_topic_id, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, from_topic_id, message_id, keyword_hits, fit_label, suggested_topic_id, _now_il()),
        )
        await self._db.commit()

    async def get_topic_observations(self, days: int = 14, limit: int = 500) -> list[dict]:
        """Get recent topic observations for the moderation dashboard."""
        since = (datetime.now(_IL_TZ) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            """SELECT * FROM topic_observations
               WHERE timestamp >= ?
               ORDER BY timestamp DESC LIMIT ?""",
            (since, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_topic_observation_counts(self, days: int = 14) -> list[dict]:
        """Per-topic counts of on/off/unknown/no_rule observations in the window."""
        since = (datetime.now(_IL_TZ) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            """SELECT from_topic_id, fit_label, COUNT(*) AS n
               FROM topic_observations
               WHERE timestamp >= ?
               GROUP BY from_topic_id, fit_label
               ORDER BY from_topic_id""",
            (since,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Emoji Puzzles (weekly movie/show emoji riddle) ───────

    async def create_emoji_puzzle(
        self,
        emoji_prompt: str,
        answer_he: str,
        answer_en: str,
        aliases: str = "[]",
        difficulty: int = 2,
        media_type: str = "movie",
    ) -> int:
        """Insert a new puzzle into the pool. `aliases` is a JSON-encoded list of strings."""
        async with self._db.execute(
            """INSERT INTO emoji_puzzles
               (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, _now_il()),
        ) as cursor:
            puzzle_id = cursor.lastrowid
        await self._db.commit()
        return puzzle_id

    async def list_emoji_puzzles(self, enabled_only: bool = False) -> list[dict]:
        """Return all puzzles in the pool, ordered by least-used first."""
        sql = "SELECT * FROM emoji_puzzles"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY times_used ASC, id ASC"
        async with self._db.execute(sql) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_emoji_puzzle(self, puzzle_id: int) -> dict | None:
        """Return a single puzzle by id."""
        async with self._db.execute(
            "SELECT * FROM emoji_puzzles WHERE id = ?", (puzzle_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def update_emoji_puzzle(self, puzzle_id: int, **fields) -> bool:
        """Update puzzle fields. Only known columns are applied."""
        allowed = {
            "emoji_prompt", "answer_he", "answer_en", "aliases",
            "difficulty", "media_type", "enabled",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        cols = ", ".join(f"{k} = ?" for k in sets)
        params = list(sets.values()) + [puzzle_id]
        async with self._db.execute(
            f"UPDATE emoji_puzzles SET {cols} WHERE id = ?", params,
        ) as cursor:
            changed = cursor.rowcount > 0
        await self._db.commit()
        return changed

    async def delete_emoji_puzzle(self, puzzle_id: int) -> bool:
        """Remove a puzzle from the pool. Historical rounds remain."""
        async with self._db.execute(
            "DELETE FROM emoji_puzzles WHERE id = ?", (puzzle_id,)
        ) as cursor:
            changed = cursor.rowcount > 0
        await self._db.commit()
        return changed

    async def pick_next_emoji_puzzle(self) -> dict | None:
        """Pick the next puzzle for a round: least-used enabled puzzle.

        Ties broken by lowest id (deterministic, stable over restarts).
        """
        async with self._db.execute(
            """SELECT * FROM emoji_puzzles
               WHERE enabled = 1
               ORDER BY times_used ASC, id ASC
               LIMIT 1"""
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def start_emoji_round(
        self,
        puzzle_id: int,
        chat_id: int,
        message_id: int,
        message_thread_id: int | None,
        award_points: int,
    ) -> int:
        """Record a new active round. Also increments times_used on the puzzle."""
        sent_at = _now_il()
        async with self._db.execute(
            """INSERT INTO emoji_puzzle_rounds
               (puzzle_id, chat_id, message_thread_id, message_id, sent_at,
                status, award_points)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (puzzle_id, chat_id, message_thread_id, message_id, sent_at, award_points),
        ) as cursor:
            round_id = cursor.lastrowid
        await self._db.execute(
            "UPDATE emoji_puzzles SET times_used = times_used + 1 WHERE id = ?",
            (puzzle_id,),
        )
        await self._db.commit()
        return round_id

    async def get_active_emoji_round_for_message(
        self, chat_id: int, message_id: int,
    ) -> dict | None:
        """Find the active round whose puzzle message matches (chat_id, message_id).

        Used by the reply watcher to match an incoming reply to its puzzle round.
        """
        async with self._db.execute(
            """SELECT r.*, p.answer_he, p.answer_en, p.aliases, p.emoji_prompt
               FROM emoji_puzzle_rounds r
               JOIN emoji_puzzles p ON p.id = r.puzzle_id
               WHERE r.chat_id = ? AND r.message_id = ? AND r.status = 'active'
               LIMIT 1""",
            (chat_id, message_id),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_active_emoji_rounds(self) -> list[dict]:
        """All rounds still awaiting a solve (for bot restart recovery)."""
        async with self._db.execute(
            """SELECT r.*, p.answer_he, p.answer_en, p.aliases, p.emoji_prompt
               FROM emoji_puzzle_rounds r
               JOIN emoji_puzzles p ON p.id = r.puzzle_id
               WHERE r.status = 'active'
               ORDER BY r.sent_at ASC"""
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def mark_emoji_round_solved(
        self, round_id: int, winner_user_id: int, winner_message_id: int,
    ) -> bool:
        """Flag a round as solved by the given user.

        Uses a conditional UPDATE so two concurrent correct replies can't both
        win — only the first caller to actually change a row returns True.
        """
        async with self._db.execute(
            """UPDATE emoji_puzzle_rounds
               SET winner_user_id = ?, winner_message_id = ?,
                   solved_at = ?, status = 'solved'
               WHERE id = ? AND status = 'active'""",
            (winner_user_id, winner_message_id, _now_il(), round_id),
        ) as cursor:
            won = cursor.rowcount > 0
        await self._db.commit()
        return won

    async def mark_emoji_round_revealed(self, round_id: int) -> bool:
        """Flag an unsolved round as revealed (answer posted after 24h)."""
        async with self._db.execute(
            """UPDATE emoji_puzzle_rounds
               SET revealed_at = ?, status = 'revealed'
               WHERE id = ? AND status = 'active'""",
            (_now_il(), round_id),
        ) as cursor:
            changed = cursor.rowcount > 0
        await self._db.commit()
        return changed

    async def get_emoji_rounds_to_reveal(self, age_hours: int = 24) -> list[dict]:
        """Active rounds older than `age_hours` — candidates for auto-reveal."""
        cutoff = (datetime.now(_IL_TZ) - timedelta(hours=age_hours)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            """SELECT r.*, p.answer_he, p.answer_en, p.aliases, p.emoji_prompt
               FROM emoji_puzzle_rounds r
               JOIN emoji_puzzles p ON p.id = r.puzzle_id
               WHERE r.status = 'active' AND r.sent_at <= ?
               ORDER BY r.sent_at ASC""",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def user_has_emoji_win_since(self, user_id: int, days: int = 7) -> bool:
        """True if the user has won an emoji round within the last N days.

        Used to cap a single player from farming weekly wins (anti-dominance).
        """
        cutoff = (datetime.now(_IL_TZ) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            """SELECT 1 FROM emoji_puzzle_rounds
               WHERE winner_user_id = ? AND solved_at >= ?
               LIMIT 1""",
            (user_id, cutoff),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def get_emoji_round_stats(self) -> dict:
        """Summary stats for the dashboard puzzles page."""
        async with self._db.execute(
            """SELECT
                 COUNT(*)                                    AS rounds_total,
                 SUM(CASE WHEN status = 'solved'   THEN 1 ELSE 0 END) AS rounds_solved,
                 SUM(CASE WHEN status = 'revealed' THEN 1 ELSE 0 END) AS rounds_revealed,
                 SUM(CASE WHEN status = 'active'   THEN 1 ELSE 0 END) AS rounds_active
               FROM emoji_puzzle_rounds"""
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else {
                "rounds_total": 0, "rounds_solved": 0,
                "rounds_revealed": 0, "rounds_active": 0,
            }

    # ── Poll Votes (inline-button polls) ─────────────────────

    async def set_poll_vote(self, message_id: int, option_key: str,
                            user_id: int, display_name: str):
        """Record (or update) a vote. Idempotent on the (msg, opt, user) PK."""
        await self._db.execute(
            """INSERT INTO poll_votes (message_id, option_key, user_id, display_name, voted_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(message_id, option_key, user_id) DO UPDATE SET
                   display_name = excluded.display_name,
                   voted_at = excluded.voted_at""",
            (message_id, option_key, user_id, display_name, _now_il()),
        )
        await self._db.commit()

    async def delete_poll_vote(self, message_id: int, option_key: str, user_id: int):
        """Remove a vote (user un-clicked the option)."""
        await self._db.execute(
            "DELETE FROM poll_votes WHERE message_id = ? AND option_key = ? AND user_id = ?",
            (message_id, option_key, user_id),
        )
        await self._db.commit()

    async def get_poll_votes(self, message_id: int) -> list[dict]:
        """All votes for one poll message — used to hydrate in-memory cache on bot startup."""
        async with self._db.execute(
            """SELECT message_id, option_key, user_id, display_name, voted_at
               FROM poll_votes WHERE message_id = ? ORDER BY voted_at""",
            (message_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def load_all_poll_votes(self) -> list[dict]:
        """All votes across all polls — used at bot startup to rebuild _votes cache."""
        async with self._db.execute(
            """SELECT message_id, option_key, user_id, display_name, voted_at
               FROM poll_votes ORDER BY message_id, voted_at"""
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def get_poll_results(self, message_id: int) -> list[dict]:
        """Aggregate vote counts per option for one poll. Returns [{option_key, count, voters[]}]."""
        async with self._db.execute(
            """SELECT option_key,
                      COUNT(*) AS count,
                      GROUP_CONCAT(display_name, ', ') AS voters
               FROM poll_votes
               WHERE message_id = ?
               GROUP BY option_key
               ORDER BY count DESC, option_key""",
            (message_id,),
        ) as cursor:
            return [dict(r) for r in await cursor.fetchall()]

    async def list_recent_polls(self, limit: int = 20) -> list[dict]:
        """Recent scheduled polls with vote totals. Joins scheduled_messages → poll_votes.

        Returns the source poll record (text, options, sent_at) plus an
        `options_with_counts` dict mapping option_key → count, for the dashboard
        'Create event from poll' picker.
        """
        async with self._db.execute(
            """SELECT sm.id AS schedule_id, sm.text, sm.poll_options,
                      sm.sent_message_id AS message_id, sm.sent_at,
                      sm.channel_topic_id, sm.target_group,
                      COALESCE(SUM(pv_count.n), 0) AS total_votes
               FROM scheduled_messages sm
               LEFT JOIN (
                   SELECT message_id, COUNT(*) AS n
                   FROM poll_votes GROUP BY message_id
               ) pv_count ON pv_count.message_id = sm.sent_message_id
               WHERE sm.message_type = 'poll' AND sm.sent_message_id IS NOT NULL
               GROUP BY sm.id
               ORDER BY sm.sent_at DESC
               LIMIT ?""",
            (limit,),
        ) as cursor:
            polls = [dict(r) for r in await cursor.fetchall()]

        for poll in polls:
            results = await self.get_poll_results(poll["message_id"])
            poll["options_with_counts"] = {r["option_key"]: r for r in results}
        return polls
