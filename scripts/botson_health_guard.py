#!/usr/bin/env python3
"""Recurring production health guard for Botson.

Daily mode is read-only. Weekly mode adds focused regression tests and an
explicit Sherlocks Den smoke send, never the main group.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bot.utils.redaction import redact_sensitive


IL_TZ = ZoneInfo("Asia/Jerusalem")
GAME_TYPES = {"trivia_round", "emoji_puzzle"}
WARMUP_TYPE = "trivia_warmup_rsvp"
DIGEST_TYPES = {
    "trivia_round",
    "emoji_puzzle",
    "facts_tidbit",
    "facts_spooky",
    "free_games",
    "weekly_roundup",
    "weekly_leaderboard",
}
ROUTING_BY_MESSAGE_TYPE = {
    "trivia_round": "trivia_round",
    "emoji_puzzle": "emoji_puzzle",
    "trivia_warmup_rsvp": "trivia_warmup",
    "facts_tidbit": "facts_tidbit",
    "facts_spooky": "facts_spooky",
    "free_games": "free_games",
    "weekly_roundup": "weekly_roundup",
    "weekly_leaderboard": "weekly_leaderboard",
}
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
    "tests/test_emoji_puzzle_theme_routing.py",
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


def _state_path() -> Path:
    return Path(os.environ.get("BOTSON_HEALTH_STATE_PATH") or str(REPO_ROOT / "data" / "health_guard_state.json"))


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
        output = redact_sensitive(exc.stdout or "")[-2000:]
        return Check(name, "fail", f"timed out after {timeout_s}s", {"cmd": cmd, "output": output}, time.monotonic() - start)
    output = redact_sensitive((proc.stdout or "") + (proc.stderr or "")).strip()
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


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _canonical_emoji_media_type(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    aliases = {
        "movies": "movie",
        "film": "movie",
        "films": "movie",
        "tv": "series",
        "show": "series",
        "shows": "series",
        "music": "song",
        "songs": "song",
        "book": "book",
        "books": "book",
        "game": "game",
        "games": "game",
    }
    return aliases.get(value, value or "general")


def _emoji_puzzle_count(args: argparse.Namespace) -> int:
    explicit = getattr(args, "emoji_puzzle_count", 0)
    if explicit:
        return max(1, int(explicit))
    try:
        import yaml  # type: ignore

        settings_path = REPO_ROOT / "config" / "settings.yaml"
        settings = yaml.safe_load(settings_path.read_text(encoding="utf-8")) or {}
        return max(1, int((settings.get("schedule", {}).get("emoji_puzzle", {}) or {}).get("puzzle_count") or 5))
    except Exception:
        return 5


def _emoji_fresh_pool_count(conn: sqlite3.Connection, media_types: list[str], *, days: int = 30) -> tuple[int, int]:
    if not (_table_exists(conn, "emoji_puzzles") and _table_exists(conn, "emoji_puzzle_rounds")):
        return 0, 0
    allowed = {_canonical_emoji_media_type(m) for m in media_types if str(m or "").strip()}
    params: list[object] = []
    where = ["enabled = 1"]
    if allowed:
        placeholders = ",".join("?" for _ in allowed)
        where.append(f"media_type IN ({placeholders})")
        params.extend(sorted(allowed))
    where_sql = " AND ".join(where)
    total = int(conn.execute(f"SELECT COUNT(*) FROM emoji_puzzles WHERE {where_sql}", params).fetchone()[0] or 0)
    cutoff = (_now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    recent = int(conn.execute(
        f"""
        SELECT COUNT(*)
          FROM emoji_puzzles
         WHERE {where_sql}
           AND id IN (
               SELECT DISTINCT puzzle_id
                 FROM emoji_puzzle_rounds
                WHERE datetime(sent_at) >= datetime(?)
           )
        """,
        [*params, cutoff],
    ).fetchone()[0] or 0)
    return max(0, total - recent), total


def _load_trivia_questions() -> list[dict]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load((REPO_ROOT / "config" / "trivia.yaml").read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    questions = data.get("questions", []) if isinstance(data, dict) else []
    return [q for q in questions if isinstance(q, dict)]


def _trivia_repeat_window_days() -> int:
    try:
        import yaml  # type: ignore

        settings = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")) or {}
        raw = (settings.get("trivia") or {}).get("question_repeat_window_days")
        return max(0, min(365, int(raw or 30)))
    except Exception:
        return 30


def _trivia_question_key(question: dict) -> str:
    text = str((question or {}).get("text") or "").strip()
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12] if text else ""


def _recent_trivia_question_keys(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "activity_log"):
        return set()
    days = _trivia_repeat_window_days()
    if days <= 0:
        return set()
    cutoff = (_now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    pattern = re.compile(r"\bquestion_id:([A-Za-z0-9_\-+]+)")
    keys: set[str] = set()
    try:
        rows = conn.execute(
            "SELECT description FROM activity_log WHERE action_type = ? AND timestamp >= ?",
            ("trivia_round", cutoff),
        ).fetchall()
    except sqlite3.Error:
        return set()
    for row in rows:
        match = pattern.search(str(row["description"] or ""))
        if not match:
            continue
        keys.update(token.strip() for token in match.group(1).split("+") if token.strip())
    return keys


def _valid_trivia_question(question: dict) -> bool:
    text = str(question.get("text") or "").strip()
    options = question.get("options")
    if not text or not isinstance(options, list) or len(options) < 2:
        return False
    try:
        correct = int(question.get("correct"))
    except (TypeError, ValueError):
        return False
    return 0 <= correct < len(options)


def _trivia_row_pool_counts(row: dict, questions: list[dict], recent_keys: set[str]) -> dict:
    payload = row.get("_payload") or _json_obj(row.get("poll_options"))
    try:
        required = int(payload.get("question_count") or 10)
    except (TypeError, ValueError):
        required = 10
    required = max(1, min(20, required))
    raw_categories = payload.get("categories") or []
    if isinstance(raw_categories, str):
        raw_categories = [raw_categories]
    categories = [str(cat).strip() for cat in raw_categories if str(cat).strip()]
    category_set = {cat.lower() for cat in categories}

    seen_total: set[str] = set()
    seen_fresh: set[str] = set()
    total = 0
    fresh = 0
    invalid = 0
    for question in questions:
        if category_set:
            category = str(question.get("category") or "").strip().lower()
            if category not in category_set:
                continue
        if not _valid_trivia_question(question):
            invalid += 1
            continue
        text = str(question.get("text") or "").strip()
        if text in seen_total:
            continue
        seen_total.add(text)
        total += 1
        key = _trivia_question_key(question)
        if key and key in recent_keys:
            continue
        if text in seen_fresh:
            continue
        seen_fresh.add(text)
        fresh += 1
    return {
        "required": required,
        "fresh": fresh,
        "total": total,
        "invalid": invalid,
        "categories": categories,
    }


def check_schedule(args: argparse.Namespace) -> Check:
    path = args.db or _db_path()
    if not Path(path).exists():
        return Check("schedule", "fail", f"DB not found: {path}")
    today = _now().date().isoformat()
    expected_min = args.expected_min_ready
    issues: list[str] = []
    warnings: list[str] = []
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
        stale_sessions: list[dict] = []
        if _table_exists(conn, "emoji_puzzle_sessions"):
            cutoff = (_now() - timedelta(hours=max(1, int(args.stale_session_hours)))).strftime("%Y-%m-%d %H:%M:%S")
            stale_sessions = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT id, chat_id, message_thread_id, started_at, status
                      FROM emoji_puzzle_sessions
                     WHERE status = 'active'
                       AND datetime(started_at) < datetime(?)
                     ORDER BY started_at
                    """,
                    (cutoff,),
                )
            ]
        emoji_freshness_by_row: dict[int, tuple[int, int]] = {}
        puzzle_count = _emoji_puzzle_count(args)
        for row in rows:
            if row.get("message_type") != "emoji_puzzle" or row.get("status") != "scheduled":
                continue
            payload = _json_obj(row.get("poll_options"))
            media_types = payload.get("media_types") if isinstance(payload.get("media_types"), list) else []
            emoji_freshness_by_row[int(row["id"])] = _emoji_fresh_pool_count(conn, [str(m) for m in media_types])
        trivia_questions = _load_trivia_questions()
        recent_trivia_keys = _recent_trivia_question_keys(conn)
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
        freshness = emoji_freshness_by_row.get(int(row["id"]))
        if freshness is not None:
            fresh_count, total_count = freshness
            puzzle_count = _emoji_puzzle_count(args)
            if fresh_count < puzzle_count:
                media_types = payload.get("media_types") if isinstance(payload.get("media_types"), list) else []
                warnings.append(
                    f"{row_label} will auto-refill emoji pool ({fresh_count}/{puzzle_count} fresh, "
                    f"total={total_count}) for media_types={media_types or 'any'}"
                )
        if row["message_type"] == "trivia_round" and row.get("status") == "scheduled":
            trivia_counts = _trivia_row_pool_counts(row, trivia_questions, recent_trivia_keys)
            required = int(trivia_counts["required"])
            fresh_count = int(trivia_counts["fresh"])
            total_count = int(trivia_counts["total"])
            invalid_count = int(trivia_counts["invalid"])
            categories = trivia_counts["categories"] or ["(general — full pool)"]
            if total_count < required:
                issues.append(
                    f"{row_label} trivia pool too small ({total_count}/{required} loadable, "
                    f"{fresh_count}/{required} fresh, invalid={invalid_count}) for categories={categories}"
                )
            elif fresh_count < required:
                warnings.append(
                    f"{row_label} trivia can run only with top-up or repeat-window fallback "
                    f"({fresh_count}/{required} fresh, total={total_count}) for categories={categories}"
                )
    for session in stale_sessions:
        issues.append(
            "stale active emoji session "
            f"#{session['id']} chat={session['chat_id']} thread={session['message_thread_id']} "
            f"started_at={session['started_at']}"
        )
    summary["issues"] = len(issues)
    if issues:
        return Check("schedule", "fail", "; ".join(issues[:8]), {"summary": summary, "issues": issues, "warnings": warnings})
    if warnings:
        return Check("schedule", "warn", "; ".join(warnings[:8]), {"summary": summary, "issues": issues, "warnings": warnings})
    return Check("schedule", "ok", f"{summary['game_rows']} future game rows checked", summary)


def _routing_map(conn: sqlite3.Connection) -> dict[str, int | None]:
    try:
        rows = conn.execute("SELECT handler, play_topic_id FROM bot_message_routing").fetchall()
    except sqlite3.Error:
        return {}
    return {str(r["handler"]): r["play_topic_id"] for r in rows}


def check_coverage(args: argparse.Namespace) -> Check:
    path = args.db or _db_path()
    if not Path(path).exists():
        return Check("coverage", "fail", f"DB not found: {path}")
    today = _now().date()
    end_day = today + timedelta(days=max(1, int(args.coverage_days)) - 1)
    today_s = today.isoformat()
    end_s = end_day.isoformat()
    issues: list[str] = []
    warnings: list[str] = []
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                """
                SELECT id, status, message_type, scheduled_date, scheduled_time,
                       channel_topic_id, target_group, poll_options
                  FROM scheduled_messages
                 WHERE status = 'scheduled'
                   AND scheduled_date BETWEEN ? AND ?
                 ORDER BY scheduled_date, scheduled_time, id
                """,
                (today_s, end_s),
            )
        ]
        routing = _routing_map(conn)
    finally:
        conn.close()

    if not rows:
        warnings.append(f"no scheduled rows between {today_s} and {end_s}")

    digest_today = [r for r in rows if r.get("scheduled_date") == today_s and r.get("message_type") in DIGEST_TYPES]
    digest_window = [r for r in rows if r.get("message_type") in DIGEST_TYPES]
    games_window = [r for r in rows if r.get("message_type") in GAME_TYPES]
    trivia_window = [r for r in rows if r.get("message_type") == "trivia_round"]
    if not digest_today:
        warnings.append(f"no daily-digest activity rows scheduled for today ({today_s})")
    if not digest_window:
        warnings.append(f"no digestable activity rows scheduled through {end_s}")
    if len(digest_today) > 8:
        warnings.append(f"daily digest has {len(digest_today)} rows today; keyboard shows only 8")
    if not games_window:
        warnings.append(f"no games scheduled between {today_s} and {end_s}")
    elif not trivia_window:
        warnings.append(f"no trivia_round games scheduled between {today_s} and {end_s}")

    for row in rows:
        mtype = str(row.get("message_type") or "")
        handler = ROUTING_BY_MESSAGE_TYPE.get(mtype)
        row_label = f"#{row['id']} {mtype} {row['scheduled_date']} {row['scheduled_time']}"
        if handler:
            if handler not in routing:
                issues.append(f"{row_label} missing bot_message_routing row for {handler}")
            elif routing.get(handler) is None:
                issues.append(f"{row_label} has null play_topic_id for routing handler {handler}")
        if (
            str(row.get("target_group") or "main") == "main"
            and mtype in ROUTING_BY_MESSAGE_TYPE
            and row.get("channel_topic_id") is None
        ):
            issues.append(f"{row_label} targets main but has no channel_topic_id")

    data = {
        "window": {"start": today_s, "end": end_s},
        "scheduled_rows": len(rows),
        "digest_today": len(digest_today),
        "digest_window": len(digest_window),
        "games_window": len(games_window),
        "issues": issues,
        "warnings": warnings,
    }
    if issues:
        return Check("coverage", "fail", "; ".join(issues[:8]), data)
    if warnings:
        return Check("coverage", "warn", "; ".join(warnings[:8]), data)
    return Check("coverage", "ok", f"{len(rows)} scheduled rows checked through {end_s}", data)


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
        current_stamp: datetime | None = None
        for line in lines:
            try:
                current_stamp = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=IL_TZ)
            except ValueError:
                pass
            if not RECENT_BAD_LOG.search(line):
                continue
            stamp = current_stamp or _now()
            if stamp >= cutoff:
                hits.append(redact_sensitive(line)[:500])
    if hits:
        return Check("logs", "fail", f"{len(hits)} recent bad log line(s)", {"hits": hits[-20:]})
    return Check("logs", "ok", f"no bad log lines in last {args.since_hours}h")


CLI_TIMING_LINE = re.compile(
    r"\[cli-timing\] (\w+) (\w+) (?:in|after) ([\d.]+)s \(ctx=([\w-]+)\)"
)


def _cli_timing_samples(hours: int) -> tuple[list[tuple[str, str, float, str]], str]:
    """Pull [cli-timing] lines from journald for both services.

    Returns ((cli, outcome, seconds, context) tuples, reason) where reason
    is "ok" | "no-journalctl" | "unreadable".

    The reason matters more than it looks. When the service user isn't in
    the `systemd-journal` group, journalctl exits 0 and prints "No journal
    files were opened due to insufficient permissions" — so a naive reader
    sees zero timing lines and concludes "no calls, all healthy", forever.
    A monitor that cannot fail is worse than no monitor, so the caller
    turns that case into a warning instead of an all-clear.
    """
    if not shutil.which("journalctl"):
        return [], "no-journalctl"
    cmd = [
        "journalctl", "-u", "botson-dashboard.service", "-u", "botson.service",
        "--since", f"{int(hours)} hours ago", "--no-pager",
    ]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=90)
    except (subprocess.TimeoutExpired, OSError):
        return [], "unreadable"
    blob = (proc.stdout or "") + (proc.stderr or "")
    if "insufficient permissions" in blob or proc.returncode not in (0, 1):
        return [], "unreadable"
    out: list[tuple[str, str, float, str]] = []
    for match in CLI_TIMING_LINE.finditer(proc.stdout or ""):
        try:
            out.append((match.group(1), match.group(2), float(match.group(3)), match.group(4)))
        except ValueError:
            continue
    return out, "ok"


def check_cli_health(args: argparse.Namespace) -> Check:
    """Classify the generation CLIs as healthy / slow / hanging.

    Exists because the operator should never have to read a verdict off a
    terminal. The 2026-07-27 planner stall (Populate rendering "Aborted"
    after ten minutes) was a per-call timeout problem that took a manual
    investigation to even characterise. This check does that
    characterisation on every guard run and alerts through the existing
    Telegram path when it degrades.

    Reading:
      * timeouts, but successful calls are fast  -> calls are HANGING;
        the budget should drop so the fallback runs sooner.
      * successful calls creeping up to the budget -> calls are SLOW;
        the budget should rise, or the prompts should shrink.
    """
    hours = _env_int("BOTSON_CLI_HEALTH_WINDOW_HOURS", 24)
    samples, reason = _cli_timing_samples(hours)
    if reason == "unreadable":
        # Never report all-clear from a monitor that cannot see. Fix:
        # usermod -aG systemd-journal <service user> && restart.
        return Check(
            "cli_health", "warn",
            "cannot read journald — the CLI health verdict is BLIND, not clear. "
            "Add the service user to the systemd-journal group.",
            {"window_hours": hours, "reason": reason},
        )
    if reason == "no-journalctl":
        return Check(
            "cli_health", "ok", "journalctl unavailable on this host (dev machine); skipped",
            {"reason": reason},
        )
    if not samples:
        return Check(
            "cli_health", "ok",
            f"no CLI calls recorded in the last {hours}h (idle window)",
            {"window_hours": hours},
        )

    try:
        from bot.utils.cli_home import cli_timeout_seconds
    except Exception:
        def cli_timeout_seconds(cli: str, default: int) -> int:  # type: ignore[misc]
            return default

    by_cli: dict[str, dict] = {}
    for cli, outcome, seconds, _ctx in samples:
        slot = by_cli.setdefault(cli, {"ok": [], "timeout": 0, "error": 0})
        if outcome == "ok":
            slot["ok"].append(seconds)
        elif outcome == "TIMEOUT":
            slot["timeout"] += 1
        else:
            slot["error"] += 1

    verdicts: list[str] = []
    data: dict = {"window_hours": hours, "clis": {}}
    worst = "ok"
    for cli, slot in sorted(by_cli.items()):
        budget = cli_timeout_seconds(cli, 90)
        oks = sorted(slot["ok"])
        total = len(oks) + slot["timeout"] + slot["error"]
        timeout_rate = slot["timeout"] / total if total else 0.0
        median = oks[len(oks) // 2] if oks else None
        data["clis"][cli] = {
            "calls": total, "ok": len(oks), "timeouts": slot["timeout"],
            "errors": slot["error"], "median_s": median,
            "max_s": oks[-1] if oks else None, "budget_s": budget,
            "timeout_rate": round(timeout_rate, 3),
        }
        if timeout_rate >= 0.25 and median is not None and median < budget * 0.5:
            worst = "fail"
            verdicts.append(
                f"{cli}: HANGING — {slot['timeout']}/{total} calls hit the {budget}s ceiling "
                f"while successful ones finish in ~{median:.0f}s. "
                f"Lower llm.cli_timeouts.{cli}_seconds so the fallback runs sooner."
            )
        elif median is not None and median >= budget * 0.7:
            worst = "fail" if worst == "fail" else "warn"
            verdicts.append(
                f"{cli}: SLOW — median {median:.0f}s against a {budget}s budget. "
                f"Raise llm.cli_timeouts.{cli}_seconds or shrink the prompts."
            )
        elif timeout_rate >= 0.25:
            worst = "fail" if worst == "fail" else "warn"
            verdicts.append(
                f"{cli}: {slot['timeout']}/{total} calls timed out at {budget}s "
                f"(too few successes to tell slow from hanging)."
            )
        else:
            verdicts.append(
                f"{cli}: ok — {total} calls"
                + (f", median {median:.0f}s / budget {budget}s" if median is not None else "")
            )

    return Check("cli_health", worst, " · ".join(verdicts), data)


def _load_state(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def check_weekly_smoke_freshness(args: argparse.Namespace) -> Check:
    state_file = Path(args.state_file or _state_path())
    state = _load_state(state_file)
    now = _now()
    first_seen = _parse_iso(state.get("first_seen_at"))
    if first_seen is None:
        return Check("weekly_smoke_freshness", "ok", "no health state yet; weekly freshness grace not started")
    successes = state.get("last_success_by_mode") if isinstance(state.get("last_success_by_mode"), dict) else {}
    weekly_at = _parse_iso(successes.get("weekly-full") if isinstance(successes, dict) else None)
    max_age_days = max(1, int(args.weekly_smoke_max_age_days))
    if weekly_at is None:
        age_days = (now - first_seen).total_seconds() / 86400
        if age_days > max_age_days:
            return Check(
                "weekly_smoke_freshness",
                "warn",
                f"no successful weekly-full smoke recorded for {age_days:.1f}d",
                {"state_file": str(state_file), "first_seen_at": first_seen.isoformat()},
            )
        return Check(
            "weekly_smoke_freshness",
            "ok",
            f"no weekly-full success yet; still inside {max_age_days}d initial grace",
            {"state_file": str(state_file), "first_seen_at": first_seen.isoformat()},
        )
    age_days = (now - weekly_at).total_seconds() / 86400
    if age_days > max_age_days:
        return Check(
            "weekly_smoke_freshness",
            "warn",
            f"last successful weekly-full smoke is {age_days:.1f}d old",
            {"state_file": str(state_file), "last_weekly_success_at": weekly_at.isoformat()},
        )
    return Check(
        "weekly_smoke_freshness",
        "ok",
        f"last successful weekly-full smoke is {age_days:.1f}d old",
        {"state_file": str(state_file), "last_weekly_success_at": weekly_at.isoformat()},
    )


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IL_TZ)
    return parsed


def _issue_checks(result: dict) -> list[dict]:
    return [c for c in (result.get("checks") or []) if c.get("status") in {"fail", "warn"}]


def _fingerprint(result: dict) -> str:
    issues = _issue_checks(result)
    if not issues:
        return "ok"
    parts = [
        f"{c.get('name')}:{c.get('status')}:{str(c.get('detail') or '')[:300]}"
        for c in issues
    ]
    return "|".join(parts)


def _chat_ids() -> list[str]:
    raw = os.environ.get("BOTSON_HEALTH_ALERT_CHAT_IDS") or os.environ.get("ADMIN_IDS", "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _short_commit() -> str:
    proc = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode == 0:
        return (proc.stdout or "").strip()
    return "unknown"


def _format_alert(result: dict, *, recovery: bool) -> str:
    if recovery:
        return (
            "Botson health recovered\n"
            f"mode: {result.get('mode')}\n"
            f"commit: {_short_commit()}\n"
            "status: ok"
        )
    issues = _issue_checks(result)
    issue_names = {str(check.get("name") or "") for check in issues}
    lines = [
        "Botson needs attention",
    ]
    if "coverage" in issue_names:
        lines.extend([
            "Cause: no approved digest/activity posts or games are scheduled for the next 2 days.",
            "Fix: open the dashboard and approve the suggested posts and at least one upcoming game.",
        ])
    if "weekly_smoke_freshness" in issue_names:
        lines.append("Also overdue: the weekly-full smoke check; run it after approving the schedule.")
    lines.extend([
        f"status: {result.get('status')} · commit: {_short_commit()}",
    ])
    for check in issues[:5]:
        detail = str(check.get("detail") or "").replace("\n", " ")
        if len(detail) > 500:
            detail = detail[:497] + "..."
        lines.append(f"- {check.get('name')}: {check.get('status')} — {detail}")
    lines.extend([
        "inspect:",
        "journalctl -u botson-health-daily.service -n 100 --no-pager",
        "/opt/robotnik/scripts/vps-admin.sh schedule 10",
    ])
    return "\n".join(lines)


def _send_telegram(text: str, *, timeout_s: int) -> list[str]:
    token = os.environ.get("BOT_TOKEN", "").strip()
    chats = _chat_ids()
    if not token or not chats:
        return ["BOT_TOKEN or ADMIN_IDS/BOTSON_HEALTH_ALERT_CHAT_IDS missing; alert not sent"]
    errors: list[str] = []
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for chat_id in chats:
        payload = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status >= 400:
                    errors.append(f"{chat_id}: HTTP {resp.status}")
        except Exception as exc:
            errors.append(redact_sensitive(f"{chat_id}: {exc}"))
    return errors


def handle_alerts(result: dict, args: argparse.Namespace) -> dict:
    state_file = Path(args.state_file or _state_path())
    state = _load_state(state_file)
    now = _now()
    status = str(result.get("status") or "failed")
    fingerprint = _fingerprint(result)
    previous_status = str(state.get("last_status") or "")
    previous_fingerprint = str(state.get("last_fingerprint") or "")
    last_alert_at = _parse_iso(state.get("last_alert_at"))
    repeat_due = (
        last_alert_at is None
        or (now - last_alert_at).total_seconds() >= max(60, int(args.alert_repeat_hours) * 3600)
    )
    recovery = status == "ok" and previous_status and previous_status != "ok"
    new_issue = status != "ok" and (fingerprint != previous_fingerprint or previous_status == "ok" or repeat_due)
    sent = False
    errors: list[str] = []
    if args.alerts and (recovery or new_issue):
        errors = _send_telegram(_format_alert(result, recovery=recovery), timeout_s=args.alert_timeout_seconds)
        sent = not errors
        if errors:
            print("health alert send failed: " + "; ".join(errors), file=sys.stderr)
    state.update({
        "first_seen_at": state.get("first_seen_at") or now.isoformat(),
        "last_status": status,
        "last_fingerprint": fingerprint,
        "last_result_at": now.isoformat(),
    })
    if status == "ok":
        successes = state.get("last_success_by_mode") if isinstance(state.get("last_success_by_mode"), dict) else {}
        successes[str(result.get("mode") or "unknown")] = now.isoformat()
        state["last_success_by_mode"] = successes
    if sent:
        state["last_alert_at"] = now.isoformat()
    try:
        _write_state(state_file, state)
    except OSError as exc:
        errors.append(f"state write failed: {exc}")
        print(f"health state write failed: {exc}", file=sys.stderr)
    return {
        "enabled": bool(args.alerts),
        "sent": sent,
        "recovery": recovery,
        "new_issue": new_issue,
        "state_file": str(state_file),
        "errors": errors,
    }


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
        sys.executable,
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
        check_coverage(args),
        check_weekly_smoke_freshness(args),
        check_cli_health(args),
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
    parser.add_argument("--coverage-days", type=int, default=_env_int("BOTSON_HEALTH_COVERAGE_DAYS", 2))
    parser.add_argument("--stale-session-hours", type=int, default=_env_int("BOTSON_HEALTH_STALE_SESSION_HOURS", 6))
    parser.add_argument("--emoji-puzzle-count", type=int, default=_env_int("BOTSON_HEALTH_EMOJI_PUZZLE_COUNT", 0), help="override Emoji Night puzzle_count; default reads config/settings.yaml")
    parser.add_argument("--expected-min-ready", type=int, default=_env_int("BOTSON_HEALTH_EXPECTED_MIN_READY", 1), help="-1 disables quorum drift check")
    parser.add_argument("--min-suggestions", type=int, default=_env_int("BOTSON_GENERATION_HEALTH_MIN_SUGGESTIONS", 1))
    parser.add_argument("--generation-timeout-seconds", type=int, default=_env_int("BOTSON_GENERATION_HEALTH_TIMEOUT_SECONDS", 420))
    parser.add_argument("--pytest-timeout-seconds", type=int, default=_env_int("BOTSON_HEALTH_PYTEST_TIMEOUT_SECONDS", 900))
    parser.add_argument("--smoke-timeout-seconds", type=int, default=_env_int("BOTSON_HEALTH_SMOKE_TIMEOUT_SECONDS", 240))
    parser.add_argument("--allow-degraded-generation", action="store_true", default=os.environ.get("BOTSON_GENERATION_HEALTH_ALLOW_DEGRADED") == "1")
    parser.add_argument("--planner-health", action="store_true", default=os.environ.get("BOTSON_HEALTH_DAILY_PLANNER") == "1")
    parser.add_argument("--weekly-send", action="store_true", default=os.environ.get("BOTSON_HEALTH_WEEKLY_SEND") == "1")
    parser.add_argument("--state-file", default=os.environ.get("BOTSON_HEALTH_STATE_PATH", ""))
    parser.add_argument("--alert-repeat-hours", type=int, default=_env_int("BOTSON_HEALTH_ALERT_REPEAT_HOURS", 24))
    parser.add_argument("--weekly-smoke-max-age-days", type=int, default=_env_int("BOTSON_HEALTH_WEEKLY_SMOKE_MAX_AGE_DAYS", 8))
    parser.add_argument("--alert-timeout-seconds", type=int, default=_env_int("BOTSON_HEALTH_ALERT_TIMEOUT_SECONDS", 15))
    parser.add_argument("--alerts", action=argparse.BooleanOptionalAction, default=os.environ.get("BOTSON_HEALTH_ALERTS", "1") != "0")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_guard(args)
    result["alert"] = handle_alerts(result, args)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(result)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
