#!/usr/bin/env python3
"""Recurring production health guard for Botson.

Daily mode is read-only. Weekly mode adds focused regression tests and an
explicit Sherlocks Den smoke send, never the main group.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
IL_TZ = ZoneInfo("Asia/Jerusalem")
GAME_TYPES = {"trivia_round", "emoji_puzzle"}
WARMUP_TYPE = "trivia_warmup_rsvp"
RECENT_BAD_LOG = re.compile(
    r"(Traceback|ERROR|warmup_rsvp_gate: .*decision=skip|Skipped scheduled message .*warmup_rsvp_gate)",
    re.IGNORECASE,
)
WEEKLY_TESTS = [
    "tests/test_daily_activity_digest.py",
    "tests/test_calendar_scheduled_games.py",
    "tests/test_scheduler_e2e_trivia_launch.py",
    "tests/test_no_hardcoded_content.py",
    "tests/test_dm_menu.py",
    "tests/test_engagement_capture.py",
    "tests/test_rsvp_rates_diagnostics.py",
    "tests/test_orphan_game_and_topic_spacing.py",
    "tests/test_planner_generation_pipeline.py",
    "tests/test_planner_coercion_and_chips.py",
]


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    data: dict | list | None = None
    duration_s: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status in {"ok", "warn"}


def _now() -> datetime:
    return datetime.now(IL_TZ)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _db_path() -> str:
    return os.environ.get("DB_PATH") or str(REPO_ROOT / "data" / "bot.db")


def _run(
    name: str,
    cmd: list[str],
    *,
    timeout_s: int,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    warn_codes: set[int] | None = None,
) -> Check:
    start = time.monotonic()
    merged_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env=merged_env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        return Check(name, "fail", f"timed out after {timeout_s}s", {"cmd": cmd, "output": (exc.stdout or "")[-2000:]}, time.monotonic() - start)
    output = ((proc.stdout or "") + (proc.stderr or "")).strip()
    warn_codes = warn_codes or set()
    if proc.returncode == 0:
        return Check(name, "ok", output[-2000:], {"cmd": cmd, "exit_code": proc.returncode}, time.monotonic() - start)
    status = "warn" if proc.returncode in warn_codes else "fail"
    return Check(name, status, output[-4000:], {"cmd": cmd, "exit_code": proc.returncode}, time.monotonic() - start)


def check_services() -> Check:
    if not shutil.which("systemctl"):
        return Check("services", "warn", "systemctl unavailable; skipped service check")
    failures: list[str] = []
    states: dict[str, str] = {}
    for svc in ("botson.service", "botson-dashboard.service"):
        proc = subprocess.run(["systemctl", "is-active", svc], text=True, capture_output=True)
        state = (proc.stdout or proc.stderr or "").strip() or f"rc={proc.returncode}"
        states[svc] = state
        if proc.returncode != 0 or state != "active":
            failures.append(f"{svc}={state}")
    if failures:
        return Check("services", "fail", ", ".join(failures), states)
    return Check("services", "ok", "bot and dashboard active", states)


def check_generation_health(args: argparse.Namespace) -> Check:
    cmd = [
        str(REPO_ROOT / "scripts" / "check_generation_health.py"),
        "--timeout-seconds",
        str(args.generation_timeout_seconds),
    ]
    if args.planner_health:
        cmd.extend(["--planner", "--min-suggestions", str(args.min_suggestions)])
    warn_codes = {1} if args.allow_degraded_generation else set()
    return _run("generation_health", cmd, timeout_s=args.generation_timeout_seconds + 45, warn_codes=warn_codes)


def _json_obj(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def check_schedule(args: argparse.Namespace) -> Check:
    path = args.db or _db_path()
    if not Path(path).exists():
        return Check("schedule", "fail", f"DB not found: {path}")
    today = _now().date().isoformat()
    expected_min = args.expected_min_ready
    issues: list[str] = []
    summary: dict[str, int] = {"game_rows": 0, "warmup_rows": 0, "issues": 0}
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, status, message_type, scheduled_date, scheduled_time,
                       channel_topic_id, poll_options, error_message
                  FROM scheduled_messages
                 WHERE message_type IN ('trivia_round','emoji_puzzle','trivia_warmup_rsvp')
                   AND scheduled_date >= ?
                 ORDER BY scheduled_date, scheduled_time, id
                """,
                (today,),
            )
        ]
    finally:
        conn.close()
    warmups_by_marker: dict[str, list[dict]] = {}
    for row in rows:
        payload = _json_obj(row.get("poll_options"))
        marker = str(payload.get("warmup_marker") or "").strip()
        row["_payload"] = payload
        if row["message_type"] == WARMUP_TYPE:
            summary["warmup_rows"] += 1
            min_ready_raw = payload.get("min_ready_players")
            try:
                min_ready = int(min_ready_raw or 0)
            except (TypeError, ValueError):
                min_ready = 0
            if expected_min >= 0 and min_ready_raw is not None and min_ready != expected_min:
                issues.append(
                    f"#{row['id']} {row['message_type']} {row['scheduled_date']} {row['scheduled_time']} "
                    f"min_ready_players={min_ready}, expected {expected_min}"
                )
            if marker:
                warmups_by_marker.setdefault(marker, []).append(row)
    for row in rows:
        if row["message_type"] not in GAME_TYPES:
            continue
        summary["game_rows"] += 1
        payload = row["_payload"]
        row_label = f"#{row['id']} {row['message_type']} {row['scheduled_date']} {row['scheduled_time']}"
        status = str(row.get("status") or "")
        if status in {"failed", "skipped"}:
            issues.append(f"{row_label} is {status}: {row.get('error_message') or ''}".strip())
        min_ready_raw = payload.get("min_ready_players")
        try:
            min_ready = int(min_ready_raw or 0)
        except (TypeError, ValueError):
            min_ready = 0
        if expected_min >= 0 and min_ready_raw is not None and min_ready != expected_min:
            issues.append(f"{row_label} min_ready_players={min_ready}, expected {expected_min}")
        if min_ready > 0:
            marker = str(payload.get("warmup_marker") or "").strip()
            if not marker:
                issues.append(f"{row_label} requires RSVP but has no warmup_marker")
                continue
            paired = [w for w in warmups_by_marker.get(marker, []) if w.get("status") in {"scheduled", "sent"}]
            if not paired:
                issues.append(f"{row_label} has no scheduled/sent warmup for marker {marker}")
    summary["issues"] = len(issues)
    if issues:
        return Check("schedule", "fail", "; ".join(issues[:8]), {"summary": summary, "issues": issues})
    return Check("schedule", "ok", f"{summary['game_rows']} future game rows checked", summary)


def _log_files(base: Path) -> list[Path]:
    files = [base.with_name(base.name + suffix) for suffix in (".3", ".2", ".1", "")]
    return [p for p in files if p.exists()]


def check_logs(args: argparse.Namespace) -> Check:
    log_path = Path(args.log or os.environ.get("BOTSON_LOG_PATH", str(REPO_ROOT / "data" / "bot.log")))
    if not log_path.exists():
        return Check("logs", "warn", f"log not found: {log_path}")
    cutoff = _now() - timedelta(hours=args.since_hours)
    hits: list[str] = []
    for path in _log_files(log_path):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not RECENT_BAD_LOG.search(line):
                continue
            try:
                stamp = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IL_TZ)
            except ValueError:
                stamp = _now()
            if stamp >= cutoff:
                hits.append(line[:500])
    if hits:
        return Check("logs", "fail", f"{len(hits)} recent bad log line(s)", {"hits": hits[-20:]})
    return Check("logs", "ok", f"no bad log lines in last {args.since_hours}h")


def _pytest_command() -> list[str]:
    pytest_bin = REPO_ROOT / ".venv" / "bin" / "pytest"
    if pytest_bin.exists():
        return [str(pytest_bin)]
    return [sys.executable, "-m", "pytest"]


def check_weekly_tests(args: argparse.Namespace) -> Check:
    cmd = _pytest_command() + WEEKLY_TESTS + ["-q"]
    return _run("weekly_pytest", cmd, timeout_s=args.pytest_timeout_seconds)


def check_weekly_smoke(args: argparse.Namespace) -> Check:
    if not args.weekly_send:
        return Check("weekly_smoke", "warn", "skipped; pass --weekly-send or set BOTSON_HEALTH_WEEKLY_SEND=1")
    cmd = [
        str(REPO_ROOT / "scripts" / "e2e_den_smoke.py"),
        "--send",
        "--include-games",
        "--base-url",
        args.base_url,
        "--json",
    ]
    return _run("weekly_smoke", cmd, timeout_s=args.smoke_timeout_seconds)


def run_guard(args: argparse.Namespace) -> dict:
    started = _now()
    checks = [
        check_services(),
        check_generation_health(args),
        check_schedule(args),
        check_logs(args),
    ]
    if args.mode == "weekly-full":
        checks.append(check_weekly_tests(args))
        checks.append(check_weekly_smoke(args))
    failed = [c for c in checks if c.status == "fail"]
    warned = [c for c in checks if c.status == "warn"]
    status = "failed" if failed else ("degraded" if warned else "ok")
    return {
        "status": status,
        "ok": not failed,
        "mode": args.mode,
        "started_at": started.isoformat(),
        "finished_at": _now().isoformat(),
        "checks": [asdict(c) for c in checks],
    }


def _print_human(result: dict) -> None:
    print(f"Botson health guard: {result['status']} ({result['mode']})")
    for check in result["checks"]:
        detail = f" — {check['detail']}" if check.get("detail") else ""
        print(f"  [{check['status']}] {check['name']}{detail}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Botson recurring health checks.")
    parser.add_argument("--mode", choices=["daily", "weekly-full"], default="daily")
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    parser.add_argument("--db", default="", help="override DB path")
    parser.add_argument("--log", default="", help="override bot log path")
    parser.add_argument("--base-url", default=os.environ.get("BOTSON_HEALTH_BASE_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--since-hours", type=int, default=_env_int("BOTSON_HEALTH_SINCE_HOURS", 12))
    parser.add_argument("--expected-min-ready", type=int, default=_env_int("BOTSON_HEALTH_EXPECTED_MIN_READY", 1), help="-1 disables quorum drift check")
    parser.add_argument("--min-suggestions", type=int, default=_env_int("BOTSON_GENERATION_HEALTH_MIN_SUGGESTIONS", 1))
    parser.add_argument("--generation-timeout-seconds", type=int, default=_env_int("BOTSON_GENERATION_HEALTH_TIMEOUT_SECONDS", 420))
    parser.add_argument("--pytest-timeout-seconds", type=int, default=_env_int("BOTSON_HEALTH_PYTEST_TIMEOUT_SECONDS", 900))
    parser.add_argument("--smoke-timeout-seconds", type=int, default=_env_int("BOTSON_HEALTH_SMOKE_TIMEOUT_SECONDS", 240))
    parser.add_argument("--allow-degraded-generation", action="store_true", default=os.environ.get("BOTSON_GENERATION_HEALTH_ALLOW_DEGRADED") == "1")
    parser.add_argument("--planner-health", action="store_true", default=os.environ.get("BOTSON_HEALTH_DAILY_PLANNER") == "1")
    parser.add_argument("--weekly-send", action="store_true", default=os.environ.get("BOTSON_HEALTH_WEEKLY_SEND") == "1")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_guard(args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
