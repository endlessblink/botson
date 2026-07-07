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
        "state_file": "",
        "alerts": True,
        "alert_repeat_hours": 24,
        "alert_timeout_seconds": 1,
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
                poll_options TEXT,
                error_message TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _insert(path: str, *, row_id: int, status: str, message_type: str, marker: str, min_ready: int = 1) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            INSERT INTO scheduled_messages
            (id, status, message_type, scheduled_date, scheduled_time, channel_topic_id, poll_options, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                row_id,
                status,
                message_type,
                "2099-01-01",
                "22:00",
                4037,
                json.dumps({"warmup_marker": marker, "min_ready_players": min_ready}),
            ),
        )
        conn.commit()
    finally:
        conn.close()


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

    def test_alerts_stay_quiet_on_first_ok(self):
        with tempfile.TemporaryDirectory() as td, \
             patch.object(guard, "_send_telegram", return_value=[]) as send:
            result = self._result("ok")

            alert = guard.handle_alerts(result, _args(state_file=str(Path(td) / "state.json")))

        self.assertFalse(alert["sent"])
        send.assert_not_called()

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
            marker = "warmup-rsvp:emoji:2099-01-01:22:00"
            _insert(tmp.name, row_id=1, status="scheduled", message_type="trivia_warmup_rsvp", marker=marker)
            _insert(tmp.name, row_id=2, status="scheduled", message_type="emoji_puzzle", marker=marker)

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


if __name__ == "__main__":
    unittest.main()
