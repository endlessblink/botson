from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import botson_health_guard as guard


def _args(**overrides):
    base = {
        "db": "",
        "log": "",
        "expected_min_ready": 1,
        "since_hours": 24,
        "coverage_days": 2,
        "state_file": "",
        "alerts": True,
        "alert_repeat_hours": 24,
        "alert_timeout_seconds": 1,
        "weekly_smoke_max_age_days": 8,
        "stale_session_hours": 6,
        "emoji_puzzle_count": 5,
    }
    base.update(overrides)
    return type("Args", (), base)()


def _init_db(path: str) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE scheduled_messages (
                id INTEGER PRIMARY KEY,
                status TEXT,
                message_type TEXT,
                scheduled_date TEXT,
                scheduled_time TEXT,
                channel_topic_id INTEGER,
                target_group TEXT DEFAULT 'main',
                poll_options TEXT,
                error_message TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE bot_message_routing (
                handler TEXT PRIMARY KEY,
                play_topic_id INTEGER,
                teaser_topic_ids TEXT NOT NULL DEFAULT '[]',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE emoji_puzzle_sessions (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER NOT NULL,
                message_thread_id INTEGER,
                started_at TIMESTAMP NOT NULL,
                ended_at TIMESTAMP,
                puzzle_count INTEGER NOT NULL,
                winner_summary TEXT DEFAULT '[]',
                status TEXT DEFAULT 'active'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE emoji_puzzles (
                id INTEGER PRIMARY KEY,
                emoji_prompt TEXT NOT NULL,
                answer_he TEXT NOT NULL,
                answer_en TEXT NOT NULL,
                aliases TEXT DEFAULT '[]',
                difficulty INTEGER DEFAULT 2,
                media_type TEXT DEFAULT 'general',
                enabled INTEGER DEFAULT 1,
                times_used INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE emoji_puzzle_rounds (
                id INTEGER PRIMARY KEY,
                session_id INTEGER,
                puzzle_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                message_thread_id INTEGER,
                message_id INTEGER NOT NULL,
                sent_at TIMESTAMP NOT NULL,
                status TEXT DEFAULT 'active',
                award_points INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE activity_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                description TEXT,
                target_user_id INTEGER,
                target_channel TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert(
    path: str, *, row_id: int, status: str, message_type: str, marker: str,
    min_ready: int = 1, extra_payload: dict | None = None,
) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO scheduled_messages
            (id, status, message_type, scheduled_date, scheduled_time, channel_topic_id, target_group, poll_options, error_message)
            VALUES (?, ?, ?, ?, ?, ?, 'main', ?, NULL)
            """,
            (
                row_id,
                status,
                message_type,
                "2099-01-01",
                "22:00",
                4037,
                json.dumps({"warmup_marker": marker, "min_ready_players": min_ready, **(extra_payload or {})}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _route(path: str, handler: str, topic_id: int | None = 4037) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "INSERT INTO bot_message_routing (handler, play_topic_id, teaser_topic_ids) VALUES (?, ?, '[]')",
            (handler, topic_id),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_used_song_pool(path: str, *, count: int = 5) -> None:
    conn = sqlite3.connect(path)
    try:
        for idx in range(count):
            puzzle_id = idx + 1
            conn.execute(
                """
                INSERT INTO emoji_puzzles
                (id, emoji_prompt, answer_he, answer_en, media_type, enabled, created_at)
                VALUES (?, ?, ?, ?, 'song', 1, '2099-01-01 00:00:00')
                """,
                (puzzle_id, f"song-{puzzle_id}", f"שיר {puzzle_id}", f"Song {puzzle_id}"),
            )
            conn.execute(
                """
                INSERT INTO emoji_puzzle_rounds
                (id, session_id, puzzle_id, chat_id, message_thread_id, message_id, sent_at, status, award_points)
                VALUES (?, 1, ?, -1001, 4037, ?, '2099-01-01 01:00:00', 'revealed', 0)
                """,
                (puzzle_id, puzzle_id, 7000 + puzzle_id),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_fresh_song_pool(path: str, *, count: int = 5) -> None:
    conn = sqlite3.connect(path)
    try:
        for idx in range(count):
            puzzle_id = idx + 1
            conn.execute(
                """
                INSERT INTO emoji_puzzles
                (id, emoji_prompt, answer_he, answer_en, media_type, enabled, created_at)
                VALUES (?, ?, ?, ?, 'song', 1, '2099-01-01 00:00:00')
                """,
                (puzzle_id, f"song-{puzzle_id}", f"שיר {puzzle_id}", f"Song {puzzle_id}"),
            )
        conn.commit()
    finally:
        conn.close()


def _trivia_question(text: str, category: str) -> dict:
    return {
        "text": text,
        "options": ["a", "b", "c"],
        "correct": 1,
        "category": category,
    }


class BotsonHealthGuardTests(unittest.TestCase):
    def _result(self, status: str, *, check_status: str = "ok", detail: str = "") -> dict:
        return {
            "status": status,
            "ok": status == "ok",
            "mode": "daily",
            "checks": [{
                "name": "schedule",
                "status": check_status,
                "detail": detail,
                "data": None,
                "duration_s": 0.0,
            }],
        }

    def test_alert_explains_schedule_cause_and_fix(self):
        result = {
            "status": "degraded",
            "mode": "daily",
            "checks": [
                {"name": "coverage", "status": "warn", "detail": "no games scheduled"},
                {"name": "weekly_smoke_freshness", "status": "warn", "detail": "old"},
            ],
        }

        with patch.object(guard, "_short_commit", return_value="abc123"):
            alert = guard._format_alert(result, recovery=False)

        self.assertIn("Cause: no approved digest/activity posts or games are scheduled for the next 2 days.", alert)
        self.assertIn("Fix: open the dashboard and approve the suggested posts and at least one upcoming game.", alert)
        self.assertIn("Also overdue: the weekly-full smoke check", alert)

    def test_alerts_stay_quiet_on_first_ok(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(guard, "_send_telegram", return_value=[]) as send:
            result = self._result("ok")

            alert = guard.handle_alerts(result, _args(state_file=str(Path(td) / "state.json")))

        self.assertFalse(alert["sent"])
        send.assert_not_called()

    def test_telegram_alert_error_redacts_bot_token(self):
        token = "1234567890:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi"
        args = _args()
        with patch.dict(
            guard.os.environ,
            {"BOT_TOKEN": token, "BOTSON_HEALTH_ALERT_CHAT_IDS": "123"},
            clear=False,
        ), patch.object(
            guard.urllib.request,
            "urlopen",
            side_effect=RuntimeError(
                f"request failed: https://api.telegram.org/bot{token}/sendMessage"
            ),
        ):
            errors = guard._send_telegram("health", timeout_s=args.alert_timeout_seconds)

        self.assertEqual(len(errors), 1)
        self.assertNotIn(token, errors[0])
        self.assertIn("[REDACTED_TELEGRAM_TOKEN]", errors[0])

    def test_alerts_send_on_new_issue_and_persist_fingerprint(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(guard, "_send_telegram", return_value=[]) as send:
            state_file = Path(td) / "state.json"
            result = self._result("failed", check_status="fail", detail="game has no warmup")

            alert = guard.handle_alerts(result, _args(state_file=str(state_file)))

            self.assertTrue(alert["sent"])
            self.assertIn("game has no warmup", send.call_args.args[0])
            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(state["last_status"], "failed")
            self.assertIn("game has no warmup", state["last_fingerprint"])

    def test_alerts_do_not_repeat_same_issue_before_repeat_window(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(guard, "_send_telegram", return_value=[]) as send:
            state_file = Path(td) / "state.json"
            result = self._result("failed", check_status="fail", detail="same issue")
            args = _args(state_file=str(state_file), alert_repeat_hours=24)

            first = guard.handle_alerts(result, args)
            second = guard.handle_alerts(result, args)

        self.assertTrue(first["sent"])
        self.assertFalse(second["sent"])
        self.assertEqual(send.call_count, 1)

    def test_alerts_send_recovery_after_failure(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(guard, "_send_telegram", return_value=[]) as send:
            state_file = Path(td) / "state.json"
            guard.handle_alerts(
                self._result("failed", check_status="fail", detail="broken"),
                _args(state_file=str(state_file)),
            )
            recovery = guard.handle_alerts(self._result("ok"), _args(state_file=str(state_file)))

        self.assertTrue(recovery["sent"])
        self.assertIn("recovered", send.call_args.args[0])

    def test_success_updates_per_mode_success_state(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "state.json"
            result = self._result("ok")
            result["mode"] = "weekly-full"

            guard.handle_alerts(result, _args(state_file=str(state_file), alerts=False))

            state = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertIn("first_seen_at", state)
            self.assertIn("weekly-full", state["last_success_by_mode"])

    def test_daily_generation_health_is_provider_only(self):
        args = SimpleNamespace(
            mode="daily",
            planner_health=False,
            min_suggestions=1,
            generation_timeout_seconds=420,
            allow_degraded_generation=False,
        )
        with patch.object(guard, "_run", return_value=guard.Check("generation_health", "ok")) as run:
            guard.check_generation_health(args)

        cmd = run.call_args.args[1]
        self.assertNotIn("--planner", cmd)

    def test_weekly_generation_health_is_provider_only_by_default(self):
        args = SimpleNamespace(
            mode="weekly-full",
            planner_health=False,
            min_suggestions=1,
            generation_timeout_seconds=420,
            allow_degraded_generation=False,
        )
        with patch.object(guard, "_run", return_value=guard.Check("generation_health", "ok")) as run:
            guard.check_generation_health(args)

        cmd = run.call_args.args[1]
        self.assertNotIn("--planner", cmd)

    def test_weekly_smoke_runs_script_through_python(self):
        args = SimpleNamespace(weekly_send=True, base_url="http://127.0.0.1:9000", smoke_timeout_seconds=240)
        with patch.object(guard, "_run", return_value=guard.Check("weekly_smoke", "ok")) as run:
            guard.check_weekly_smoke(args)

        cmd = run.call_args.args[1]
        self.assertEqual(cmd[0], guard.sys.executable)
        self.assertTrue(cmd[1].endswith("scripts/e2e_den_smoke.py"))
        self.assertIn("--send", cmd)

    def test_generation_health_can_opt_into_planner_probe(self):
        args = SimpleNamespace(
            mode="daily",
            planner_health=True,
            min_suggestions=1,
            generation_timeout_seconds=420,
            allow_degraded_generation=False,
        )
        with patch.object(guard, "_run", return_value=guard.Check("generation_health", "ok")) as run:
            guard.check_generation_health(args)

        cmd = run.call_args.args[1]
        self.assertIn("--planner", cmd)

    def test_schedule_passes_when_game_has_paired_warmup_and_expected_quorum(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            _seed_fresh_song_pool(tmp.name)
            marker = "warmup-rsvp:emoji:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(
                tmp.name,
                row_id=2,
                status="scheduled",
                message_type="emoji_puzzle",
                marker=marker,
                extra_payload={"media_types": ["music"]},
            )

            result = guard.check_schedule(_args(db=tmp.name))

        self.assertEqual(result.status, "ok", result)

    def test_schedule_fails_when_rsvp_game_has_no_warmup(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            _insert(tmp.name, row_id=2, status="scheduled", message_type="emoji_puzzle", marker="missing")

            result = guard.check_schedule(_args(db=tmp.name))

        self.assertEqual(result.status, "fail")
        self.assertIn("no scheduled/sent warmup", result.detail)

    def test_schedule_fails_on_quorum_drift(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            marker = "warmup-rsvp:trivia:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker, min_ready=2)
            _insert(tmp.name, row_id=2, status="scheduled", message_type="trivia_round", marker=marker, min_ready=2)

            result = guard.check_schedule(_args(db=tmp.name, expected_min_ready=1))

        self.assertEqual(result.status, "fail")
        self.assertIn("expected 1", result.detail)

    def test_schedule_fails_on_stale_active_emoji_session(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            conn = sqlite3.connect(tmp.name)
            try:
                conn.execute(
                    """
                    INSERT INTO emoji_puzzle_sessions
                    (id, chat_id, message_thread_id, started_at, puzzle_count, status)
                    VALUES (11, -1001, 4037, '2099-01-01 01:00:00', 5, 'active')
                    """
                )
                conn.commit()
            finally:
                conn.close()
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_schedule(_args(db=tmp.name, stale_session_hours=6))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "fail")
        self.assertIn("stale active emoji session #11", result.detail)

    def test_schedule_warns_when_upcoming_emoji_needs_auto_refill(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            _seed_used_song_pool(tmp.name)
            marker = "warmup-rsvp:emoji:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(
                tmp.name,
                row_id=2,
                status="scheduled",
                message_type="emoji_puzzle",
                marker=marker,
                extra_payload={"media_types": ["music"]},
            )
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_schedule(_args(db=tmp.name, emoji_puzzle_count=5))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "warn")
        self.assertIn("will auto-refill emoji pool (0/5 fresh, total=5)", result.detail)
        self.assertIn("media_types=['music']", result.detail)

    def test_schedule_passes_when_upcoming_trivia_pool_can_load_requested_questions(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            marker = "warmup-rsvp:trivia:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(
                tmp.name,
                row_id=2,
                status="scheduled",
                message_type="trivia_round",
                marker=marker,
                extra_payload={"categories": ["science"], "question_count": 2},
            )
            questions = [
                _trivia_question("q1", "science"),
                _trivia_question("q2", "science"),
                _trivia_question("q3", "movies"),
            ]
            with patch.object(guard, "_load_trivia_questions", return_value=questions):
                result = guard.check_schedule(_args(db=tmp.name))

        self.assertEqual(result.status, "ok", result)

    def test_schedule_fails_when_upcoming_trivia_pool_cannot_load_requested_questions(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            marker = "warmup-rsvp:trivia:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(
                tmp.name,
                row_id=2,
                status="scheduled",
                message_type="trivia_round",
                marker=marker,
                extra_payload={"categories": ["science"], "question_count": 2},
            )
            questions = [
                _trivia_question("q1", "science"),
                _trivia_question("q2", "movies"),
                {"text": "broken", "options": ["a"], "correct": 4, "category": "science"},
            ]
            with patch.object(guard, "_load_trivia_questions", return_value=questions):
                result = guard.check_schedule(_args(db=tmp.name))

        self.assertEqual(result.status, "fail")
        self.assertIn("trivia pool too small (1/2 loadable, 1/2 fresh, invalid=1)", result.detail)
        self.assertIn("categories=['science']", result.detail)

    def test_schedule_warns_when_upcoming_trivia_needs_repeat_window_fallback(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            marker = "warmup-rsvp:trivia:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(
                tmp.name,
                row_id=2,
                status="scheduled",
                message_type="trivia_round",
                marker=marker,
                extra_payload={"categories": ["science"], "question_count": 2},
            )
            used = _trivia_question("q1", "science")
            questions = [used, _trivia_question("q2", "science")]
            with patch.object(guard, "_load_trivia_questions", return_value=questions), \
                 patch.object(guard, "_recent_trivia_question_keys", return_value={guard._trivia_question_key(used)}):
                result = guard.check_schedule(_args(db=tmp.name))

        self.assertEqual(result.status, "warn")
        self.assertIn("trivia can run only with top-up or repeat-window fallback", result.detail)
        self.assertIn("(1/2 fresh, total=2)", result.detail)

    def test_logs_fail_on_recent_warmup_gate_skip(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "bot.log"
            log.write_text(
                "2099-01-01 08:00:00,000 - bot.handlers.calendar - INFO - "
                "warmup_rsvp_gate: marker=x rsvp_count=0 threshold=1 decision=skip\n",
                encoding="utf-8",
            )
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_logs(_args(log=str(log), since_hours=24))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "fail")
        self.assertIn("recent bad log", result.detail)

    def test_logs_attribute_multiline_error_to_its_old_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "bot.log"
            log.write_text(
                "2098-12-20 08:00:00,000 - bot - INFO - request failed\n"
                "Traceback (most recent call last):\n"
                "  RuntimeError: stale failure\n",
                encoding="utf-8",
            )
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_logs(_args(log=str(log), since_hours=24))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "ok", result)

    def test_logs_attribute_multiline_error_to_its_recent_timestamp(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "bot.log"
            log.write_text(
                "2099-01-01 08:00:00,000 - bot - INFO - request failed\n"
                "Traceback (most recent call last):\n"
                "  RuntimeError: recent failure\n",
                encoding="utf-8",
            )
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_logs(_args(log=str(log), since_hours=24))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "fail", result)
        self.assertIn("Traceback (most recent call last):", result.data["hits"])
        self.assertIn("  RuntimeError: recent failure", result.data["hits"])

    def test_coverage_passes_with_digest_game_routing_and_topics(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            _route(tmp.name, "emoji_puzzle", 4037)
            _route(tmp.name, "trivia_round", 4037)
            _route(tmp.name, "trivia_warmup", 4502)
            marker = "warmup-rsvp:emoji:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(tmp.name, row_id=2, status="scheduled", message_type="emoji_puzzle", marker=marker)
            trivia_marker = "warmup-rsvp:trivia:2099-01-01:20:30"
            _insert(
                tmp.name,
                row_id=3,
                status="scheduled",
                message_type="trivia_round",
                marker=trivia_marker,
                extra_payload={"categories": ["science"], "question_count": 2},
            )
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                with patch.object(guard, "_load_trivia_questions", return_value=[
                    _trivia_question("q1", "science"),
                    _trivia_question("q2", "science"),
                ]):
                    result = guard.check_coverage(_args(db=tmp.name))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "ok", result)

    def test_coverage_warns_when_games_window_has_no_trivia_round(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            _route(tmp.name, "emoji_puzzle", 4037)
            _route(tmp.name, "trivia_warmup", 4502)
            marker = "warmup-rsvp:emoji:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(tmp.name, row_id=2, status="scheduled", message_type="emoji_puzzle", marker=marker)
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_coverage(_args(db=tmp.name))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "warn")
        self.assertIn("no trivia_round games scheduled", result.detail)

    def test_coverage_fails_when_routing_missing(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            marker = "warmup-rsvp:emoji:2099-01-01:22:00"
            _insert(tmp.name, row_id=2, status="scheduled", message_type="emoji_puzzle", marker=marker)
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_coverage(_args(db=tmp.name))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "fail")
        self.assertIn("missing bot_message_routing", result.detail)

    def test_coverage_warns_when_digest_keyboard_would_be_capped(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            _init_db(tmp.name)
            _route(tmp.name, "facts_tidbit", 4037)
            for idx in range(9):
                _insert(
                    tmp.name,
                    row_id=idx + 1,
                    status="scheduled",
                    message_type="facts_tidbit",
                    marker="",
                    min_ready=0,
                )
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 1, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_coverage(_args(db=tmp.name))
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "warn")
        self.assertIn("keyboard shows only 8", result.detail)

    def test_weekly_smoke_freshness_warns_after_initial_grace(self):
        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "state.json"
            state_file.write_text(
                json.dumps({"first_seen_at": "2099-01-01T09:00:00+03:00"}),
                encoding="utf-8",
            )
            original_now = guard._now
            guard._now = lambda: guard.datetime(2099, 1, 10, 9, 0, tzinfo=guard.IL_TZ)
            try:
                result = guard.check_weekly_smoke_freshness(
                    _args(state_file=str(state_file), weekly_smoke_max_age_days=8)
                )
            finally:
                guard._now = original_now

        self.assertEqual(result.status, "warn")
        self.assertIn("no successful weekly-full smoke", result.detail)


if __name__ == "__main__":
    unittest.main()
