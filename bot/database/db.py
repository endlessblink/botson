"""Async database connection and query helpers."""

import json
import os
import logging
import re
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
            "ALTER TABLE emoji_puzzle_rounds ADD COLUMN session_id INTEGER",
            "CREATE TABLE IF NOT EXISTS verified_forum_topics (topic_id INTEGER PRIMARY KEY, verified_name TEXT NOT NULL, category_key TEXT NOT NULL UNIQUE, verification_source TEXT NOT NULL, verified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (topic_id) REFERENCES forum_topics(topic_id))",
            "CREATE TABLE IF NOT EXISTS bot_message_routing (handler TEXT PRIMARY KEY, play_topic_id INTEGER, teaser_topic_ids TEXT NOT NULL DEFAULT '[]', updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE TABLE IF NOT EXISTS trivia_interest_responses (scheduled_msg_id INTEGER NOT NULL, user_id INTEGER NOT NULL, display_name TEXT, responded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (scheduled_msg_id, user_id))",
            "CREATE TABLE IF NOT EXISTS message_engagement (scheduled_msg_id INTEGER PRIMARY KEY, telegram_message_id INTEGER NOT NULL, channel_topic_id INTEGER, reactions INTEGER NOT NULL DEFAULT 0, distinct_reactors INTEGER NOT NULL DEFAULT 0, last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
            "CREATE INDEX IF NOT EXISTS idx_message_engagement_tg_id ON message_engagement(telegram_message_id, channel_topic_id)",
            "CREATE TABLE IF NOT EXISTS message_reactors (scheduled_msg_id INTEGER NOT NULL, user_id INTEGER NOT NULL, reaction_type TEXT, reacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (scheduled_msg_id, user_id))",
            # T-172: operator feedback capture. Every rejection/edit of an
            # AI suggestion is stored so future generation can learn from it
            # (T-174). Data-capture only at this phase — no consumer yet.
            "CREATE TABLE IF NOT EXISTS content_feedback ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "source TEXT NOT NULL, "
            "content_type TEXT NOT NULL, "
            "topic_key TEXT, "
            "original_text TEXT NOT NULL, "
            "verdict TEXT NOT NULL, "
            "reason TEXT, "
            "corrected_text TEXT, "
            "suggestion_metadata TEXT"
            ")",
            "CREATE INDEX IF NOT EXISTS idx_content_feedback_type_time ON content_feedback(content_type, created_at DESC)",
            "CREATE INDEX IF NOT EXISTS idx_content_feedback_verdict ON content_feedback(verdict, created_at DESC)",
            # T-174: operator-approved style guidance learned from feedback.
            # Multiple versions can exist; status='active' is the one the
            # prompt builder reads. Apply requires explicit operator action.
            "CREATE TABLE IF NOT EXISTS content_style_profile ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "profile_key TEXT NOT NULL, "
            "version INTEGER NOT NULL, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "guidance TEXT NOT NULL, "
            "source_feedback_ids TEXT, "
            "status TEXT NOT NULL DEFAULT 'draft', "
            "UNIQUE (profile_key, version)"
            ")",
            "CREATE INDEX IF NOT EXISTS idx_style_profile_active ON content_style_profile(profile_key, status, version DESC)",
            # T-184 (Gap 4): audit trail of changes to config/operator_prefs.md.
            # Written by /teach, /apply-proposal, /untrain, /canonize (later).
            # Read by /api/operator-prefs/session-report so the operator can
            # see "what did the system learn this session?" in one place.
            "CREATE TABLE IF NOT EXISTS operator_prefs_changes ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "source TEXT NOT NULL, "       # teach | apply-proposal | untrain | canonize
            "section TEXT NOT NULL, "      # e.g. 'Hebrew content rules', 'Good examples'
            "change_kind TEXT NOT NULL, "  # 'add' | 'remove' | 'replace'
            "before_excerpt TEXT, "
            "after_excerpt TEXT, "
            "source_feedback_ids TEXT"
            ")",
            "CREATE INDEX IF NOT EXISTS idx_prefs_changes_time ON operator_prefs_changes(created_at DESC)",
            # Gap 10 (2026-05-16): persist AI suggest jobs so a dashboard
            # restart mid-run doesn't return "AI suggest job not found".
            # The in-memory _AI_SUGGEST_JOBS dict still holds the live
            # asyncio.Task reference (not picklable); on startup any row
            # still marked pending/running is reclassified as failed so
            # the operator can retry.
            "CREATE TABLE IF NOT EXISTS ai_suggest_jobs ("
            "id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "completed_at TIMESTAMP, "
            "target_date TEXT, "
            "week_offset INTEGER NOT NULL DEFAULT 0, "
            "result_json TEXT, "
            "error TEXT"
            ")",
            "CREATE INDEX IF NOT EXISTS idx_ai_suggest_jobs_created ON ai_suggest_jobs(created_at DESC)",
            # DM menu (bot/handlers/dm_menu.py): per-user opt-in to activity
            # types. Default is opt-in required — a user only gets DM heads-ups
            # for types they explicitly toggled on. Keyed (user_id, type).
            "CREATE TABLE IF NOT EXISTS user_activity_preferences ("
            "user_id INTEGER NOT NULL, "
            "activity_type TEXT NOT NULL, "
            "opted_in INTEGER NOT NULL DEFAULT 1, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (user_id, activity_type)"
            ")",
            # Dedupe log for opt-in DM notifications: one row per (scheduled
            # message, user) we've already DMed, so a re-dispatch never
            # double-notifies. Event rows key on the scheduled_messages.id
            # that produced them.
            "CREATE TABLE IF NOT EXISTS activity_notification_log ("
            "scheduled_msg_id INTEGER NOT NULL, "
            "user_id INTEGER NOT NULL, "
            "notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (scheduled_msg_id, user_id)"
            ")",
        ]
        for sql in migrations:
            try:
                await self._db.execute(sql)
            except Exception as e:  # noqa: BLE001
                if "duplicate column" not in str(e).lower():
                    logger.warning("Migration skipped/failed: %s (%s)", sql, e)

        await self._seed_default_handler_routing()

    async def _seed_default_handler_routing(self):
        """Seed bot_message_routing with default per-handler targets on first run.

        Only inserts rows that don't exist yet (INSERT OR IGNORE), so operator
        edits via the dashboard are never overwritten. Defaults reflect the
        post-2026-04-22 routing decision: bot-generated content lives in
        botson_corner (4037); events go to welcome (341).
        """
        defaults = [
            ("trivia_round", 4037),
            ("trivia_scheduled", 4037),
            ("trivia_warmup", 341),
            ("emoji_puzzle", 4037),
            ("free_games", 4037),
            ("facts_tidbit", 4037),
            ("facts_spooky", 4037),
            ("weekly_roundup", 4037),
            ("weekly_leaderboard", 4037),
            ("events_publish", 341),
            ("events_reminder", 341),
        ]
        for handler, topic_id in defaults:
            try:
                await self._db.execute(
                    "INSERT OR IGNORE INTO bot_message_routing (handler, play_topic_id, teaser_topic_ids) VALUES (?, ?, '[]')",
                    (handler, topic_id),
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("handler routing seed skipped/failed for %s: %s", handler, e)

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

        from ..utils.game_categories import canonical_emoji_media_type
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
                    canonical_emoji_media_type(puzzle.get("media_type", "general")),
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

        return ""

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
        """Get top point earners from the last seven days."""
        cutoff = (datetime.now(_IL_TZ) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            """SELECT a.target_user_id, a.description, m.display_name, m.karma_points
               FROM activity_log a
               JOIN members m ON m.user_id = a.target_user_id
               WHERE a.timestamp >= ?
                 AND a.target_user_id IS NOT NULL
                 AND a.description LIKE '+%'""",
            (cutoff,),
        ) as cursor:
            rows = await cursor.fetchall()

        scores: dict[int, dict] = {}
        for row in rows:
            match = re.match(r"^\+(\d+)\b", row[1] or "")
            if not match:
                continue
            user_id = int(row[0])
            entry = scores.setdefault(user_id, {
                "user_id": user_id,
                "display_name": row[2],
                "karma_points": int(row[3] or 0),
                "weekly_stars": 0,
            })
            entry["weekly_stars"] += int(match.group(1))

        return sorted(
            scores.values(),
            key=lambda m: (-m["weekly_stars"], -m["karma_points"], m["display_name"] or ""),
        )[:limit]

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

    async def get_event_voters(self, event_id: int) -> dict:
        """Resolve RSVP user_ids for an event to display_name + username.

        Returns {'yes': [{user_id, display_name, username}, ...], 'maybe': [...]}
        in the order user_ids appear in the stored JSON arrays.

        Array order is "most-recent-click-last" (update_event_rsvp removes and
        re-appends on every click), not chronological first-RSVP. Fine for the
        admin events panel; if chronological order is ever needed, introduce a
        per-vote log table like `poll_votes`.
        """
        import json
        event = await self.get_event(event_id)
        if not event:
            return {"yes": [], "maybe": []}

        yes_ids = json.loads(event["rsvp_yes"] or "[]")
        maybe_ids = json.loads(event["rsvp_maybe"] or "[]")
        all_ids = list({*yes_ids, *maybe_ids})

        members_by_id: dict = {}
        if all_ids:
            placeholders = ",".join("?" * len(all_ids))
            async with self._db.execute(
                f"SELECT user_id, username, display_name FROM members "
                f"WHERE user_id IN ({placeholders})",
                all_ids,
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                members_by_id[row["user_id"]] = dict(row)

        def resolve(ids):
            out = []
            for uid in ids:
                m = members_by_id.get(uid)
                if m:
                    out.append({
                        "user_id": uid,
                        "display_name": m["display_name"] or f"User {uid}",
                        "username": m["username"],
                    })
                else:
                    out.append({
                        "user_id": uid,
                        "display_name": f"User {uid}",
                        "username": None,
                    })
            return out

        return {"yes": resolve(yes_ids), "maybe": resolve(maybe_ids)}

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

    async def get_recent_activity_subjects(
        self, *, action_type: str, days: int, key: str = "fact_id"
    ) -> list[str]:
        """Return ids embedded in activity_log descriptions for a given
        action_type within the last ``days`` days.

        Handlers that want anti-repeat behavior log per-item identifiers in
        the description as ``<key>:<id>`` markers (e.g. ``fact_id:dybbuk_origin``);
        this method parses them out so ``pick_*`` functions can exclude
        recently-sent items. Phase A.1.1 wires this for facts; emoji and
        discussion handlers can reuse with their own ``key`` values.
        """
        if days <= 0:
            return []
        cutoff = (datetime.now(_IL_TZ) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            "SELECT description FROM activity_log WHERE action_type = ? AND timestamp >= ?",
            (action_type, cutoff),
        ) as cur:
            rows = await cur.fetchall()
        import re as _re
        # Gap 11 (2026-05-18): a marker value may be a single id
        # (`fact_id:abc`) OR a +-joined list (`categories:movies+gaming`,
        # `media_type:song+book`). Both forms come from
        # _subject_markers_for_log. Each token in the joined list is
        # returned as a separate string for the caller.
        pattern = _re.compile(rf"\b{_re.escape(key)}:([A-Za-z0-9_\-+]+)")
        ids: list[str] = []
        for row in rows:
            desc = str(row["description"] or "")
            m = pattern.search(desc)
            if m:
                for token in m.group(1).split("+"):
                    token = token.strip()
                    if token:
                        ids.append(token)
        return ids

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

    async def upsert_verified_forum_topic(self, topic_id: int, verified_name: str, category_key: str, verification_source: str):
        """Store a human-verified topic mapping used for trusted sends."""
        now = _now_il()
        await self._db.execute(
            """INSERT INTO verified_forum_topics (topic_id, verified_name, category_key, verification_source, verified_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(category_key) DO UPDATE SET
                   topic_id = excluded.topic_id,
                   verified_name = excluded.verified_name,
                   verification_source = excluded.verification_source,
                   verified_at = excluded.verified_at""",
            (topic_id, verified_name, category_key, verification_source, now),
        )
        await self._db.commit()

    async def get_verified_forum_topics(self) -> list[dict]:
        """Get trusted topic mappings joined with latest observed activity."""
        async with self._db.execute(
            """SELECT v.topic_id, v.verified_name, v.category_key, v.verification_source, v.verified_at,
                      f.name AS observed_name, f.last_seen_at
               FROM verified_forum_topics v
               LEFT JOIN forum_topics f ON f.topic_id = v.topic_id
               ORDER BY v.category_key"""
        ) as cursor:
            return [dict(row) for row in await cursor.fetchall()]

    async def get_verified_topic_id(self, category_key: str) -> int | None:
        """Return the trusted topic_id for a category, or None if unverified."""
        async with self._db.execute(
            "SELECT topic_id FROM verified_forum_topics WHERE category_key = ?",
            (category_key,),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else None

    async def is_verified_topic_id(self, topic_id: int) -> bool:
        """Return True iff topic_id has a row in verified_forum_topics."""
        async with self._db.execute(
            "SELECT 1 FROM verified_forum_topics WHERE topic_id = ? LIMIT 1",
            (int(topic_id),),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def delete_topic(self, topic_id: int) -> None:
        """Remove a topic from both verified_forum_topics and forum_topics.

        Called when a live send to the thread fails with "message thread not
        found" (the admin deleted the topic in Telegram) so the picker and
        routing tables stop offering a dead target. Also used by the cleanup
        script to enforce the "only verified topics exist" invariant.
        """
        tid = int(topic_id)
        await self._db.execute("DELETE FROM verified_forum_topics WHERE topic_id = ?", (tid,))
        await self._db.execute("DELETE FROM forum_topics WHERE topic_id = ?", (tid,))
        await self._db.commit()

    async def get_handler_routing(self, handler: str) -> dict | None:
        """Return the routing row for a handler, or None if absent."""
        async with self._db.execute(
            "SELECT handler, play_topic_id, teaser_topic_ids, updated_at FROM bot_message_routing WHERE handler = ?",
            (handler,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            try:
                teaser_ids = json.loads(row["teaser_topic_ids"] or "[]")
            except Exception:
                teaser_ids = []
            return {
                "handler": row["handler"],
                "play_topic_id": int(row["play_topic_id"]) if row["play_topic_id"] is not None else None,
                "teaser_topic_ids": [int(x) for x in teaser_ids if isinstance(x, (int, str))],
                "updated_at": row["updated_at"],
            }

    async def list_handler_routings(self) -> list[dict]:
        """Return all handler routing rows."""
        async with self._db.execute(
            "SELECT handler, play_topic_id, teaser_topic_ids, updated_at FROM bot_message_routing ORDER BY handler"
        ) as cursor:
            rows = await cursor.fetchall()
        out = []
        for row in rows:
            try:
                teaser_ids = json.loads(row["teaser_topic_ids"] or "[]")
            except Exception:
                teaser_ids = []
            out.append({
                "handler": row["handler"],
                "play_topic_id": int(row["play_topic_id"]) if row["play_topic_id"] is not None else None,
                "teaser_topic_ids": [int(x) for x in teaser_ids if isinstance(x, (int, str))],
                "updated_at": row["updated_at"],
            })
        return out

    async def set_handler_routing(self, handler: str, play_topic_id: int | None, teaser_topic_ids: list[int] | None = None) -> None:
        """Upsert a handler routing row."""
        teaser_json = json.dumps([int(x) for x in (teaser_topic_ids or [])])
        await self._db.execute(
            """INSERT INTO bot_message_routing (handler, play_topic_id, teaser_topic_ids, updated_at)
               VALUES (?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(handler) DO UPDATE SET
                   play_topic_id = excluded.play_topic_id,
                   teaser_topic_ids = excluded.teaser_topic_ids,
                   updated_at = CURRENT_TIMESTAMP""",
            (handler, play_topic_id, teaser_json),
        )
        await self._db.commit()

    async def remove_verified_forum_topic(self, category_key: str):
        """Remove a trusted topic mapping."""
        await self._db.execute("DELETE FROM verified_forum_topics WHERE category_key = ?", (category_key,))
        await self._db.commit()

    # ── Scheduled Messages (Content Calendar) ────────────────

    async def get_scheduled_messages(
        self, date_from: str, date_to: str, include_cancelled: bool = False
    ) -> list[dict]:
        """Get scheduled messages for a date range.

        By default cancelled rows are excluded — delete_scheduled_message marks
        status='cancelled' and the calendar/public views must not show them as
        still-pending content. The planner passes include_cancelled=True so it
        can treat cancelled rows as "skip markers" and suppress the pool fallback
        for those slots.
        """
        if include_cancelled:
            sql = """SELECT * FROM scheduled_messages
                     WHERE scheduled_date >= ? AND scheduled_date <= ?
                     ORDER BY scheduled_date, scheduled_time"""
        else:
            sql = """SELECT * FROM scheduled_messages
                     WHERE scheduled_date >= ? AND scheduled_date <= ?
                       AND status != 'cancelled'
                     ORDER BY scheduled_date, scheduled_time"""
        async with self._db.execute(sql, (date_from, date_to)) as cursor:
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

    async def get_upcoming_scheduled_games(
        self, current_date: str, current_time: str, limit: int = 5
    ) -> list[dict]:
        """Get the next few sign-up-able game warm-ups for the DM menu.

        The sign-up surface for trivia AND Emoji Night is the
        `trivia_warmup_rsvp` row (both game types use that message_type per
        CLAUDE.md — the executable trivia_round/emoji_puzzle rows are not RSVP
        targets). Includes already-`sent` warm-ups so a user who opens the menu
        after the warm-up fired can still RSVP (the trivint_ button keys on the
        warm-up row id); `scheduled` warm-ups whose RSVP hasn't opened render
        info-only. Caller passes the current IL date/time (mirrors
        get_due_messages).
        """
        async with self._db.execute(
            """SELECT * FROM scheduled_messages
               WHERE status IN ('scheduled', 'sent')
                 AND message_type = 'trivia_warmup_rsvp'
                 AND (scheduled_date > ?
                      OR (scheduled_date = ? AND scheduled_time >= ?))
               ORDER BY scheduled_date, scheduled_time
               LIMIT ?""",
            (current_date, current_date, current_time, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    # ── Activity Preferences (DM menu opt-in) ────────────────

    async def get_activity_preference(self, user_id: int, activity_type: str) -> bool:
        """Whether a user opted into DM heads-ups for an activity type.

        Default policy is opt-in required: absence of a row means False.
        """
        async with self._db.execute(
            "SELECT opted_in FROM user_activity_preferences WHERE user_id=? AND activity_type=?",
            (user_id, activity_type),
        ) as cursor:
            row = await cursor.fetchone()
        return bool(row and row["opted_in"])

    async def set_activity_preference(
        self, user_id: int, activity_type: str, opted_in: bool
    ):
        """Set (upsert) a user's opt-in state for an activity type."""
        await self._db.execute(
            """INSERT INTO user_activity_preferences (user_id, activity_type, opted_in, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id, activity_type) DO UPDATE SET
                   opted_in = excluded.opted_in,
                   updated_at = excluded.updated_at""",
            (user_id, activity_type, 1 if opted_in else 0, _now_il()),
        )
        await self._db.commit()

    async def get_user_preferences(self, user_id: int) -> dict[str, bool]:
        """All stored opt-in states for a user, keyed by activity_type."""
        async with self._db.execute(
            "SELECT activity_type, opted_in FROM user_activity_preferences WHERE user_id=?",
            (user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {r["activity_type"]: bool(r["opted_in"]) for r in rows}

    async def list_opted_in_users(self, activity_type: str) -> list[int]:
        """User ids who opted into DM heads-ups for an activity type."""
        async with self._db.execute(
            "SELECT user_id FROM user_activity_preferences WHERE activity_type=? AND opted_in=1",
            (activity_type,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [int(r["user_id"]) for r in rows]

    async def was_notified(self, scheduled_msg_id: int, user_id: int) -> bool:
        """Whether we already DMed this user about this scheduled item."""
        async with self._db.execute(
            "SELECT 1 FROM activity_notification_log WHERE scheduled_msg_id=? AND user_id=?",
            (scheduled_msg_id, user_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def mark_notified(self, scheduled_msg_id: int, user_id: int):
        """Record that we DMed this user about this scheduled item (idempotent)."""
        await self._db.execute(
            "INSERT OR IGNORE INTO activity_notification_log (scheduled_msg_id, user_id) VALUES (?, ?)",
            (scheduled_msg_id, user_id),
        )
        await self._db.commit()

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
                                        poll_duration: int | None = None,
                                        status: str = "scheduled") -> int:
        """Create a new scheduled message. Returns ID.

        status defaults to 'scheduled' (sends at scheduled_time). Pass 'draft'
        to hold for admin review — get_due_messages filters status='scheduled'
        so drafts never auto-send.
        """
        async with self._db.execute(
            """INSERT INTO scheduled_messages
               (text, message_type, channel_topic_id, target_group,
                scheduled_date, scheduled_time, recurrence, recurrence_days,
                auto_pin, created_by, cover_path, poll_options, poll_duration, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (text, message_type, channel_topic_id, target_group,
             scheduled_date, scheduled_time, recurrence, recurrence_days,
             auto_pin, created_by, cover_path, poll_options, poll_duration, status),
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

    async def mark_message_skipped(self, msg_id: int, reason: str):
        """Mark a scheduled message as intentionally skipped, not failed."""
        await self._db.execute(
            "UPDATE scheduled_messages SET status = 'skipped', error_message = ? WHERE id = ?",
            (reason, msg_id),
        )
        await self._db.commit()

    async def get_draft_messages(self) -> list[dict]:
        """Get all draft messages awaiting approval."""
        async with self._db.execute(
            "SELECT * FROM scheduled_messages WHERE status = 'draft' ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_used_discussion_texts(self, *, window_days: int | None = None) -> list[str]:
        """Return distinct discussion texts, most-recent-first.

        Used by the materializer to dedupe pool picks: once a question has
        been proposed in any form (preview that became a row, manual save,
        AI-fill), it is considered used and won't be re-picked.

        T-169: changed from `set[str]` to ordered `list[str]` (most-recent
        first) and added an optional `window_days` bound so callers can feed
        the LLM a deterministic, complete "do not repeat" list instead of a
        non-deterministic set slice.
        """
        if window_days is not None and window_days > 0:
            from datetime import date as _date, timedelta as _td
            cutoff = (_date.today() - _td(days=int(window_days))).isoformat()
            query = (
                "SELECT text, MAX(COALESCE(created_at, scheduled_date)) AS last_at "
                "FROM scheduled_messages "
                "WHERE message_type = 'discussion' AND text != '' AND text IS NOT NULL "
                "AND COALESCE(scheduled_date, '') >= ? "
                "GROUP BY text ORDER BY last_at DESC"
            )
            params: tuple = (cutoff,)
        else:
            query = (
                "SELECT text, MAX(COALESCE(created_at, scheduled_date)) AS last_at "
                "FROM scheduled_messages "
                "WHERE message_type = 'discussion' AND text != '' AND text IS NOT NULL "
                "GROUP BY text ORDER BY last_at DESC"
            )
            params = ()
        async with self._db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        seen: set[str] = set()
        ordered: list[str] = []
        for r in rows:
            text = r[0]
            if text and text not in seen:
                seen.add(text)
                ordered.append(text)
        return ordered

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
        media_type: str = "general",
    ) -> int:
        """Insert a new puzzle into the pool. `aliases` is a JSON-encoded list of strings.

        BUG-1 chokepoint: media_type is canonicalized at the lowest write
        layer so every caller (dashboard endpoints, bulk import, AI seed,
        future paths) writes canonical values without needing to know.
        """
        from ..utils.game_categories import canonical_emoji_media_type
        media_type = canonical_emoji_media_type(media_type)
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
        """Update puzzle fields. Only known columns are applied.

        BUG-1 chokepoint: media_type is canonicalized at the lowest write
        layer (same as create_emoji_puzzle).
        """
        allowed = {
            "emoji_prompt", "answer_he", "answer_en", "aliases",
            "difficulty", "media_type", "enabled",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        if "media_type" in sets:
            from ..utils.game_categories import canonical_emoji_media_type
            sets["media_type"] = canonical_emoji_media_type(sets["media_type"])
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

    async def create_emoji_session(
        self,
        chat_id: int,
        message_thread_id: int | None,
        puzzle_count: int,
    ) -> int:
        """Create a new Emoji Night session grouping multiple rounds."""
        async with self._db.execute(
            """INSERT INTO emoji_puzzle_sessions
               (chat_id, message_thread_id, started_at, puzzle_count, winner_summary, status)
               VALUES (?, ?, ?, ?, '[]', 'active')""",
            (chat_id, message_thread_id, _now_il(), puzzle_count),
        ) as cursor:
            session_id = cursor.lastrowid
        await self._db.commit()
        return session_id

    async def get_active_session(
        self, chat_id: int, message_thread_id: int | None,
    ) -> dict | None:
        """Return the currently active Emoji Night session for a chat/thread."""
        async with self._db.execute(
            """SELECT * FROM emoji_puzzle_sessions
               WHERE chat_id = ?
                 AND ((message_thread_id IS NULL AND ? IS NULL) OR message_thread_id = ?)
                 AND status = 'active'
               ORDER BY started_at DESC
               LIMIT 1""",
            (chat_id, message_thread_id, message_thread_id),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def complete_emoji_session(
        self,
        session_id: int,
        winner_summary: list[dict] | None = None,
    ) -> bool:
        """Mark a session complete and store its wrap-up summary payload."""
        summary_json = json.dumps(winner_summary or [], ensure_ascii=False)
        async with self._db.execute(
            """UPDATE emoji_puzzle_sessions
               SET ended_at = ?, winner_summary = ?, status = 'completed'
               WHERE id = ? AND status = 'active'""",
            (_now_il(), summary_json, session_id),
        ) as cursor:
            changed = cursor.rowcount > 0
        await self._db.commit()
        return changed

    async def get_session_leaderboard(self, session_id: int) -> list[dict]:
        """Aggregate total points within one session for the wrap-up message."""
        async with self._db.execute(
            """SELECT
                 a.user_id AS user_id,
                 COALESCE(m.display_name, CAST(a.user_id AS TEXT)) AS display_name,
                 SUM(a.points_awarded) AS total_points,
                 COUNT(*) AS correct_answers,
                 MIN(a.answered_at) AS first_answer_at
               FROM emoji_puzzle_answers a
               JOIN emoji_puzzle_rounds r ON r.id = a.round_id
               LEFT JOIN members m ON m.user_id = a.user_id
               WHERE r.session_id = ?
               GROUP BY a.user_id, COALESCE(m.display_name, CAST(a.user_id AS TEXT))
               ORDER BY total_points DESC, correct_answers DESC, first_answer_at ASC, a.user_id ASC""",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_session_unsolved_rounds(self, session_id: int) -> list[dict]:
        """All still-active rounds in a session, with puzzle answers for wrap-up reveal."""
        async with self._db.execute(
            """SELECT r.*, p.emoji_prompt, p.answer_he, p.answer_en
               FROM emoji_puzzle_rounds r
               JOIN emoji_puzzles p ON p.id = r.puzzle_id
               WHERE r.session_id = ?
                 AND NOT EXISTS (
                     SELECT 1 FROM emoji_puzzle_answers a WHERE a.round_id = r.id
                 )
               ORDER BY r.sent_at ASC""",
            (session_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_round_answer_count(self, round_id: int) -> int:
        """Number of accepted answers recorded for one round."""
        async with self._db.execute(
            "SELECT COUNT(*) FROM emoji_puzzle_answers WHERE round_id = ?",
            (round_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0] or 0) if row else 0

    async def record_emoji_correct_answer(
        self,
        round_id: int,
        user_id: int,
        message_id: int,
        points_by_rank: list[int],
    ) -> dict | None:
        """Record one user's correct answer for a round with place-based scoring."""
        async with self._db.execute(
            "SELECT 1 FROM emoji_puzzle_answers WHERE round_id = ? AND user_id = ? LIMIT 1",
            (round_id, user_id),
        ) as cursor:
            if await cursor.fetchone():
                return None

        async with self._db.execute(
            "SELECT COUNT(*) FROM emoji_puzzle_answers WHERE round_id = ?",
            (round_id,),
        ) as cursor:
            count = int((await cursor.fetchone())[0] or 0)

        rank = count + 1
        points = points_by_rank[rank - 1] if rank <= len(points_by_rank) else 0
        answered_at = _now_il()
        try:
            async with self._db.execute(
                """INSERT INTO emoji_puzzle_answers
                   (round_id, user_id, message_id, answered_at, answer_rank, points_awarded)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (round_id, user_id, message_id, answered_at, rank, points),
            ):
                pass
        except Exception:
            await self._db.rollback()
            return None

        if rank == 1:
            await self._db.execute(
                """UPDATE emoji_puzzle_rounds
                   SET winner_user_id = ?, winner_message_id = ?, solved_at = ?
                   WHERE id = ?""",
                (user_id, message_id, answered_at, round_id),
            )
        await self._db.commit()
        return {
            "round_id": round_id,
            "user_id": user_id,
            "answer_rank": rank,
            "points_awarded": points,
            "answered_at": answered_at,
        }

    async def close_session_rounds(self, session_id: int) -> int:
        """Close all rounds in a session when the game ends."""
        async with self._db.execute(
            """UPDATE emoji_puzzle_rounds
               SET revealed_at = ?,
                   status = CASE
                       WHEN EXISTS (
                           SELECT 1 FROM emoji_puzzle_answers a WHERE a.round_id = emoji_puzzle_rounds.id
                       ) THEN 'solved'
                       ELSE 'revealed'
                   END
               WHERE session_id = ? AND status = 'active'""",
            (_now_il(), session_id),
        ) as cursor:
            changed = cursor.rowcount or 0
        await self._db.commit()
        return changed

    async def get_recent_emoji_sessions(self, limit: int = 20) -> list[dict]:
        """Recent Emoji Night sessions for dashboard history."""
        async with self._db.execute(
            """SELECT * FROM emoji_puzzle_sessions
               ORDER BY started_at DESC
               LIMIT ?""",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_recent_emoji_puzzle_ids(self, *, days: int = 30) -> set[int]:
        """Return puzzle_ids that appeared in any round started in the last
        ``days`` days. Used by the session picker to exclude recently-played
        puzzles so the pool feels fresh — Phase A.1.2."""
        if days <= 0:
            return set()
        cutoff = (datetime.now(_IL_TZ) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        async with self._db.execute(
            "SELECT DISTINCT puzzle_id FROM emoji_puzzle_rounds WHERE sent_at >= ?",
            (cutoff,),
        ) as cur:
            rows = await cur.fetchall()
        return {int(r["puzzle_id"]) for r in rows if r["puzzle_id"] is not None}

    async def start_emoji_round(
        self,
        session_id: int | None,
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
               (session_id, puzzle_id, chat_id, message_thread_id, message_id, sent_at,
                 status, award_points)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (session_id, puzzle_id, chat_id, message_thread_id, message_id, sent_at, award_points),
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

    async def mark_session_rounds_revealed(self, session_id: int) -> int:
        """Mark all still-active rounds in a session as revealed at wrap-up time."""
        async with self._db.execute(
            """UPDATE emoji_puzzle_rounds
               SET revealed_at = ?, status = 'revealed'
               WHERE session_id = ? AND status = 'active'""",
            (_now_il(), session_id),
        ) as cursor:
            changed = cursor.rowcount or 0
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
                      sm.channel_topic_id, sm.target_group, sm.cover_path,
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

    # ── Trivia Interest Check (warm-up RSVP) ─────────────────

    async def add_trivia_interest_response(
        self, scheduled_msg_id: int, user_id: int, display_name: str
    ) -> tuple[int, bool]:
        """Upsert an interest response. Returns (new_total_count, already_responded)."""
        async with self._db.execute(
            "SELECT 1 FROM trivia_interest_responses WHERE scheduled_msg_id=? AND user_id=?",
            (scheduled_msg_id, user_id),
        ) as cur:
            already = await cur.fetchone() is not None
        await self._db.execute(
            """INSERT INTO trivia_interest_responses (scheduled_msg_id, user_id, display_name, responded_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(scheduled_msg_id, user_id) DO UPDATE SET
                   display_name = excluded.display_name,
                   responded_at = excluded.responded_at""",
            (scheduled_msg_id, user_id, display_name, _now_il()),
        )
        await self._db.commit()
        async with self._db.execute(
            "SELECT COUNT(*) AS n FROM trivia_interest_responses WHERE scheduled_msg_id=?",
            (scheduled_msg_id,),
        ) as cur:
            row = await cur.fetchone()
        count = int(row["n"]) if row else 1
        return count, already

    async def get_trivia_interest_responses(self, scheduled_msg_id: int) -> list[dict]:
        """All respondents for one warm-up message."""
        async with self._db.execute(
            """SELECT user_id, display_name, responded_at
               FROM trivia_interest_responses WHERE scheduled_msg_id=? ORDER BY responded_at""",
            (scheduled_msg_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]

    # ── Message engagement (Phase B reaction tracking) ────────

    async def find_scheduled_id_by_telegram_message(
        self, telegram_message_id: int, channel_topic_id: int | None
    ) -> int | None:
        """Resolve a Telegram (chat-thread, message_id) tuple to our scheduled_messages.id.

        The reaction handler receives only the Telegram message id; we map it
        back to the row that produced it via `sent_message_id`. Returns None
        when the reacted-to message wasn't sent by the bot's calendar.
        """
        sql = "SELECT id FROM scheduled_messages WHERE sent_message_id = ?"
        params: list = [int(telegram_message_id)]
        if channel_topic_id is not None:
            sql += " AND channel_topic_id = ?"
            params.append(int(channel_topic_id))
        sql += " ORDER BY id DESC LIMIT 1"
        async with self._db.execute(sql, params) as cur:
            row = await cur.fetchone()
        return int(row["id"]) if row else None

    async def record_reaction_update(
        self,
        scheduled_msg_id: int,
        *,
        telegram_message_id: int,
        channel_topic_id: int | None,
        user_id: int,
        new_reaction_type: str | None,
    ) -> dict:
        """Apply one reaction delta and recompute engagement totals.

        ``new_reaction_type`` is the user's *current* reaction emoji (or
        first reaction type if the user used multiple) after the change.
        Pass None when the user removed their reaction. We track at most
        one reaction per (message, user) — sufficient for the "did this
        resonate" signal without modeling every emoji separately.

        Returns the post-update aggregate row.
        """
        if new_reaction_type:
            await self._db.execute(
                """INSERT INTO message_reactors (scheduled_msg_id, user_id, reaction_type, reacted_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(scheduled_msg_id, user_id) DO UPDATE SET
                       reaction_type = excluded.reaction_type,
                       reacted_at = excluded.reacted_at""",
                (int(scheduled_msg_id), int(user_id), new_reaction_type, _now_il()),
            )
        else:
            await self._db.execute(
                "DELETE FROM message_reactors WHERE scheduled_msg_id=? AND user_id=?",
                (int(scheduled_msg_id), int(user_id)),
            )

        async with self._db.execute(
            "SELECT COUNT(*) AS n FROM message_reactors WHERE scheduled_msg_id=?",
            (int(scheduled_msg_id),),
        ) as cur:
            row = await cur.fetchone()
        distinct = int(row["n"]) if row else 0
        # We track one reaction per user → reactions == distinct_reactors.
        # Kept as separate columns so a future change (multi-emoji per user)
        # doesn't require a schema migration.
        reactions = distinct

        await self._db.execute(
            """INSERT INTO message_engagement
                   (scheduled_msg_id, telegram_message_id, channel_topic_id,
                    reactions, distinct_reactors, last_updated)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(scheduled_msg_id) DO UPDATE SET
                   telegram_message_id = excluded.telegram_message_id,
                   channel_topic_id = excluded.channel_topic_id,
                   reactions = excluded.reactions,
                   distinct_reactors = excluded.distinct_reactors,
                   last_updated = excluded.last_updated""",
            (
                int(scheduled_msg_id),
                int(telegram_message_id),
                int(channel_topic_id) if channel_topic_id is not None else None,
                reactions,
                distinct,
                _now_il(),
            ),
        )
        await self._db.commit()
        return {
            "scheduled_msg_id": int(scheduled_msg_id),
            "reactions": reactions,
            "distinct_reactors": distinct,
        }

    async def get_message_engagement(self, scheduled_msg_id: int) -> dict | None:
        async with self._db.execute(
            """SELECT scheduled_msg_id, telegram_message_id, channel_topic_id,
                      reactions, distinct_reactors, last_updated
               FROM message_engagement WHERE scheduled_msg_id = ?""",
            (int(scheduled_msg_id),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def list_message_engagement(self, scheduled_msg_ids: list[int]) -> dict[int, dict]:
        """Bulk-read for dashboard rendering. Returns {scheduled_msg_id: row}."""
        if not scheduled_msg_ids:
            return {}
        placeholders = ",".join("?" for _ in scheduled_msg_ids)
        async with self._db.execute(
            f"""SELECT scheduled_msg_id, reactions, distinct_reactors, last_updated
                FROM message_engagement WHERE scheduled_msg_id IN ({placeholders})""",
            [int(x) for x in scheduled_msg_ids],
        ) as cur:
            rows = await cur.fetchall()
        return {int(r["scheduled_msg_id"]): dict(r) for r in rows}

    # ── Content Feedback (T-172) ─────────────────────────────

    async def record_content_feedback(
        self,
        *,
        source: str,
        content_type: str,
        original_text: str,
        verdict: str,
        topic_key: str | None = None,
        reason: str | None = None,
        corrected_text: str | None = None,
        suggestion_metadata: str | None = None,
    ) -> int:
        """Persist an operator verdict on an AI suggestion. Returns row id.

        verdict is free-form but conventional values: 'rejected',
        'bad_wording', 'accepted_after_edit', 'accepted'. Storage is
        capture-only here; consumers (T-174 style-profile learning) read
        from this table to shape future prompts.
        """
        cur = await self._db.execute(
            """INSERT INTO content_feedback
                   (source, content_type, topic_key, original_text, verdict,
                    reason, corrected_text, suggestion_metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(source), str(content_type), topic_key,
                str(original_text), str(verdict),
                reason, corrected_text, suggestion_metadata,
            ),
        )
        await self._db.commit()
        return int(cur.lastrowid or 0)

    async def get_content_feedback(self, feedback_id: int) -> dict | None:
        """Return a single content_feedback row by id (Gap 13 enrichment)."""
        async with self._db.execute(
            """SELECT id, created_at, source, content_type, topic_key,
                       original_text, verdict, reason, corrected_text,
                       suggestion_metadata
                FROM content_feedback WHERE id = ?""",
            (int(feedback_id),),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def update_content_feedback_reason(
        self, feedback_id: int, reason: str,
    ) -> bool:
        """Overwrite the reason on a content_feedback row (Gap 13: pill
        enrichment combines the original short reason with the operator's
        chosen follow-up chip)."""
        async with self._db.execute(
            "UPDATE content_feedback SET reason = ? WHERE id = ?",
            (str(reason), int(feedback_id)),
        ) as cur:
            changed = (cur.rowcount or 0) > 0
        await self._db.commit()
        return bool(changed)

    async def get_active_style_profile(self, profile_key: str = "planner_hebrew_default") -> dict | None:
        """Return the currently-active style profile, or None if none applied yet."""
        async with self._db.execute(
            "SELECT id, profile_key, version, updated_at, guidance, source_feedback_ids, status "
            "FROM content_style_profile WHERE profile_key = ? AND status = 'active' "
            "ORDER BY version DESC LIMIT 1",
            (profile_key,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def insert_style_profile(
        self,
        *,
        profile_key: str,
        guidance: str,
        source_feedback_ids: str | None = None,
        status: str = "draft",
    ) -> int:
        """Insert a new style-profile version. Returns the new row id.

        Caller (the apply endpoint) is responsible for flipping the prior
        active row to status='superseded' atomically with the apply step.
        """
        async with self._db.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM content_style_profile WHERE profile_key = ?",
            (profile_key,),
        ) as cur:
            row = await cur.fetchone()
        next_version = int((row[0] if row else 1) or 1)
        cur2 = await self._db.execute(
            "INSERT INTO content_style_profile (profile_key, version, guidance, source_feedback_ids, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (profile_key, next_version, guidance, source_feedback_ids, status),
        )
        await self._db.commit()
        return int(cur2.lastrowid or 0)

    async def activate_style_profile(self, profile_id: int, profile_key: str = "planner_hebrew_default") -> None:
        """Atomically demote any active profile to 'superseded' and activate the given id."""
        await self._db.execute(
            "UPDATE content_style_profile SET status = 'superseded' "
            "WHERE profile_key = ? AND status = 'active'",
            (profile_key,),
        )
        await self._db.execute(
            "UPDATE content_style_profile SET status = 'active' WHERE id = ?",
            (int(profile_id),),
        )
        await self._db.commit()

    # ── Operator-prefs change audit (T-184, Gap 4) ──────────────────

    async def record_prefs_change(
        self,
        *,
        source: str,
        section: str,
        change_kind: str,
        before_excerpt: str | None = None,
        after_excerpt: str | None = None,
        source_feedback_ids: str | None = None,
    ) -> int:
        """Append an audit row for a change to config/operator_prefs.md.
        Best-effort: callers wrap in try/except so an audit failure never
        blocks the actual write."""
        cur = await self._db.execute(
            "INSERT INTO operator_prefs_changes "
            "(source, section, change_kind, before_excerpt, after_excerpt, source_feedback_ids) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(source), str(section), str(change_kind),
             (before_excerpt or None), (after_excerpt or None),
             (source_feedback_ids or None)),
        )
        await self._db.commit()
        return int(cur.lastrowid or 0)

    async def list_prefs_changes(
        self,
        *,
        since_iso: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if since_iso:
            clauses.append("created_at >= ?")
            params.append(str(since_iso))
        params.append(int(max(1, min(limit, 2000))))
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self._db.execute(
            "SELECT id, created_at, source, section, change_kind, "
            "before_excerpt, after_excerpt, source_feedback_ids "
            f"FROM operator_prefs_changes{where} "
            "ORDER BY created_at DESC LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def get_rejected_pool_texts(
        self,
        *,
        content_type: str | None = None,
        limit: int = 500,
    ) -> set[str]:
        """T-183 (Gap 5): return the set of `original_text` strings that the
        operator has rejected, optionally filtered by content_type. Used by
        pool selectors (facts, emoji puzzles, trivia, story tidbits) to
        skip items the operator has already said no to — closes the gap
        where pool-sourced rejections weren't blacklisting future picks.

        Normalises whitespace so trivial formatting differences don't
        defeat the filter.
        """
        clauses = ["verdict IN ('rejected','bad_wording')"]
        params: list = []
        if content_type:
            clauses.append("content_type = ?")
            params.append(content_type)
        params.append(int(max(1, min(limit, 2000))))
        where = " WHERE " + " AND ".join(clauses)
        async with self._db.execute(
            f"SELECT original_text FROM content_feedback{where} "
            f"ORDER BY created_at DESC LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
        out: set[str] = set()
        for r in rows:
            t = (r[0] or "").strip()
            if not t:
                continue
            # Normalise whitespace so "x  y\n" and "x y" match.
            normalized = " ".join(t.split())
            out.add(normalized)
        return out

    async def list_content_feedback(
        self,
        *,
        content_type: str | None = None,
        verdict: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        clauses = []
        params: list = []
        if content_type:
            clauses.append("content_type = ?")
            params.append(content_type)
        if verdict:
            clauses.append("verdict = ?")
            params.append(verdict)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(max(1, min(limit, 500))))
        async with self._db.execute(
            f"""SELECT id, created_at, source, content_type, topic_key,
                       original_text, verdict, reason, corrected_text,
                       suggestion_metadata
                FROM content_feedback{where}
                ORDER BY created_at DESC LIMIT ?""",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Emoji puzzle data hygiene (BUG-1, 2026-05-17) ────────

    async def normalize_emoji_puzzle_media_types(self) -> dict:
        """Rewrite legacy media_type aliases to canonical values.

        Uses bot.utils.game_categories.EMOJI_PUZZLE_ALIASES as the single
        source of truth. Returns {mappings, updated, before, after}.
        Idempotent.
        """
        from ..utils.game_categories import EMOJI_PUZZLE_ALIASES

        async with self._db.execute(
            "SELECT media_type, COUNT(*) FROM emoji_puzzles GROUP BY media_type"
        ) as cur:
            before_rows = await cur.fetchall()
        before = {str(r[0] or ""): int(r[1]) for r in before_rows}

        applied: dict[str, str] = {}
        total_updated = 0
        for alias, canonical in EMOJI_PUZZLE_ALIASES.items():
            if alias == canonical:
                continue
            async with self._db.execute(
                "UPDATE emoji_puzzles SET media_type = ? WHERE media_type = ?",
                (canonical, alias),
            ) as cur:
                changed = int(cur.rowcount or 0)
            if changed:
                applied[alias] = canonical
                total_updated += changed
        if total_updated:
            await self._db.commit()

        async with self._db.execute(
            "SELECT media_type, COUNT(*) FROM emoji_puzzles GROUP BY media_type"
        ) as cur:
            after_rows = await cur.fetchall()
        after = {str(r[0] or ""): int(r[1]) for r in after_rows}

        return {
            "mappings": applied,
            "updated": total_updated,
            "before": before,
            "after": after,
        }

    # ── AI suggest jobs (Gap 10: survive dashboard restarts) ──

    async def create_ai_suggest_job(
        self, job_id: str, *, target_date: str | None, week_offset: int
    ) -> None:
        await self._db.execute(
            "INSERT INTO ai_suggest_jobs (id, status, target_date, week_offset) "
            "VALUES (?, 'pending', ?, ?)",
            (job_id, target_date, int(week_offset)),
        )
        await self._db.commit()

    async def update_ai_suggest_job(
        self,
        job_id: str,
        *,
        status: str,
        result_json: str | None = None,
        error: str | None = None,
        mark_completed: bool = False,
    ) -> None:
        sets = ["status = ?"]
        params: list = [status]
        if result_json is not None:
            sets.append("result_json = ?")
            params.append(result_json)
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if mark_completed:
            sets.append("completed_at = CURRENT_TIMESTAMP")
        params.append(job_id)
        await self._db.execute(
            f"UPDATE ai_suggest_jobs SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        await self._db.commit()

    async def get_ai_suggest_job(self, job_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, status, created_at, completed_at, target_date, "
            "week_offset, result_json, error FROM ai_suggest_jobs WHERE id = ?",
            (job_id,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def cleanup_ai_suggest_jobs(self, ttl_seconds: int = 900) -> int:
        async with self._db.execute(
            f"DELETE FROM ai_suggest_jobs "
            f"WHERE created_at < datetime('now', '-{int(ttl_seconds)} seconds')"
        ) as cur:
            deleted = cur.rowcount
        await self._db.commit()
        return int(deleted or 0)

    async def recover_orphaned_ai_suggest_jobs(self) -> int:
        """Mark any pending/running jobs as failed at startup.

        Their asyncio.Task died with the previous process, so the operator
        must retry. Returns the count of recovered rows.
        """
        async with self._db.execute(
            "UPDATE ai_suggest_jobs "
            "SET status = 'failed', "
            "    error = 'AI suggest interrupted by dashboard restart — retry', "
            "    completed_at = CURRENT_TIMESTAMP "
            "WHERE status IN ('pending', 'running')"
        ) as cur:
            recovered = cur.rowcount
        await self._db.commit()
        return int(recovered or 0)
