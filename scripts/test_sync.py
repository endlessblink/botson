"""End-to-end sync verification for the materializer migration.

Runs a battery of tests that confirm dashboard preview, bot DB rows, schedule
config, and feature flags all derive from a single source of truth.

Usage:  .venv/bin/python3 scripts/test_sync.py
Exits 0 if every test passes, 1 otherwise.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))

from bot.database.db import Database  # noqa: E402
from bot.scheduler.materializer import compute_week_previews, materialize_forward, _build_committed_index  # noqa: E402
from bot.utils.config import get_settings, is_feature_enabled  # noqa: E402

TZ = ZoneInfo("Asia/Jerusalem")
DB_PATH = "data/bot.db"

results: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((name, cond, detail))
    mark = "✓" if cond else "✗"
    print(f"  {mark} {name}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def _hebrew_day(d: date) -> int:
    """Python weekday (Mon=0..Sun=6) → Hebrew weekday (Sun=0..Sat=6)."""
    return (d.weekday() + 1) % 7


async def test_single_source_imports() -> None:
    section("T1. Single source of truth at module level")

    # Dashboard's compute_week_previews must be the exact same object as the bot's.
    from dashboard.app import compute_week_previews as dash_fn  # type: ignore
    from bot.scheduler.materializer import compute_week_previews as bot_fn
    check(
        "dashboard.compute_week_previews IS bot.scheduler.materializer.compute_week_previews",
        dash_fn is bot_fn,
        f"{id(dash_fn)} vs {id(bot_fn)}",
    )

    # Dashboard's feature-check must delegate to the bot helper (no second implementation).
    from dashboard.app import _is_feature_enabled as dash_feat  # type: ignore
    from bot.utils.config import is_feature_enabled as bot_feat
    check(
        "dashboard._is_feature_enabled IS bot.utils.config.is_feature_enabled",
        dash_feat is bot_feat,
    )

    # Old duplicate implementation is now a compat shim that still delegates.
    from dashboard.app import _is_feature_enabled_simple
    # call-through: for a known-enabled feature the shim should match bot helper
    check(
        "_is_feature_enabled_simple shim delegates correctly",
        _is_feature_enabled_simple({}, "morning_prompt") == is_feature_enabled("morning_prompt"),
    )


async def test_day_of_week_mapping() -> None:
    section("T2. Day-of-week mapping (Hebrew Sun=0 throughout)")

    settings = get_settings()
    schedule = settings.get("schedule", {})
    morning_days = schedule.get("morning_prompt", {}).get("days", [])
    evening_days = schedule.get("evening_prompt", {}).get("days", [])

    # Query DB: every scheduled auto morning row must fall on a configured morning day.
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT scheduled_date FROM scheduled_messages "
        "WHERE status='scheduled' AND created_by='auto' AND message_type='morning'"
    )
    morning_dates = [date.fromisoformat(r[0]) for r in c.fetchall()]
    morning_off_days = [d for d in morning_dates if _hebrew_day(d) not in morning_days]
    check(
        f"every auto morning row falls on configured days {morning_days}",
        not morning_off_days,
        f"{len(morning_dates)} rows, {len(morning_off_days)} on wrong days" + (
            f" (offenders: {[d.isoformat() for d in morning_off_days[:3]]})" if morning_off_days else ""
        ),
    )

    c.execute(
        "SELECT scheduled_date FROM scheduled_messages "
        "WHERE status='scheduled' AND created_by='auto' AND message_type='evening'"
    )
    evening_dates = [date.fromisoformat(r[0]) for r in c.fetchall()]
    evening_off = [d for d in evening_dates if _hebrew_day(d) not in evening_days]
    check(
        f"every auto evening row falls on configured days {evening_days}",
        not evening_off,
        f"{len(evening_dates)} rows, {len(evening_off)} on wrong days",
    )

    conn.close()


async def test_no_duplicate_slots() -> None:
    section("T3. No duplicate (date,time,type) slots")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """SELECT scheduled_date, scheduled_time, message_type, COUNT(*) AS n
           FROM scheduled_messages
           WHERE status='scheduled'
           GROUP BY scheduled_date, scheduled_time, message_type
           HAVING n > 1"""
    )
    dupes = c.fetchall()
    check("no duplicate scheduled slots", not dupes, f"{len(dupes)} duplicates" if dupes else "")
    conn.close()


async def test_past_due_guard() -> None:
    section("T4. Materializer does not create past-due today slots")
    now = datetime.now(TZ)
    today_iso = now.date().isoformat()
    current_hhmm = now.strftime("%H:%M")

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """SELECT scheduled_time, message_type FROM scheduled_messages
           WHERE status='scheduled' AND created_by='auto' AND scheduled_date=?""",
        (today_iso,),
    )
    today_rows = c.fetchall()
    past_due = [(t, mt) for t, mt in today_rows if t[:5] <= current_hhmm]
    check(
        f"no past-due today rows ({today_iso}, now={current_hhmm})",
        not past_due,
        f"found {past_due}" if past_due else f"{len(today_rows)} upcoming today rows",
    )
    conn.close()


async def test_preview_matches_db() -> None:
    section("T5. compute_week_previews output matches materialized DB rows")
    # For current week, compute previews with EMPTY committed_index, then
    # compare against DB rows. Every preview should have a corresponding row.
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=days_since_sunday)

    previews = compute_week_previews(sunday.isoformat(), {})

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """SELECT scheduled_date, scheduled_time, message_type, text, channel_topic_id
           FROM scheduled_messages
           WHERE status IN ('scheduled','sent')
             AND scheduled_date >= ? AND scheduled_date <= ?""",
        (sunday.isoformat(), (sunday + timedelta(days=6)).isoformat()),
    )
    db_rows = {(r[0], r[1][:5], r[2]): {"text": r[3], "topic": r[4]} for r in c.fetchall()}
    conn.close()

    missing = []
    for p in previews:
        key = (p["date"], p["time"], p["type"])
        if key not in db_rows:
            missing.append(key)

    # Allow missing only if it's a past-due today slot (patched guard)
    now = datetime.now(TZ)
    today_iso = now.date().isoformat()
    current_hhmm = now.strftime("%H:%M")
    real_missing = [
        m for m in missing
        if not (m[0] == today_iso and m[1] <= current_hhmm)
    ]

    check(
        "every upcoming preview has a matching DB row",
        not real_missing,
        f"missing: {real_missing}" if real_missing else f"{len(previews)} previews, {len(missing)} skipped (past-due guard)",
    )


async def test_committed_rows_preserved() -> None:
    section("T6. User-committed rows survive rematerialize")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """SELECT id, scheduled_date, scheduled_time, message_type, created_by
           FROM scheduled_messages
           WHERE status='scheduled' AND created_by NOT IN ('auto')
           AND scheduled_date >= date('now')"""
    )
    committed_before = c.fetchall()
    conn.close()

    # Call materialize_forward directly — should leave non-auto rows alone.
    db = Database()
    await db.init()
    try:
        await materialize_forward(db)
    finally:
        await db.close()

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        """SELECT id, scheduled_date, scheduled_time, message_type, created_by
           FROM scheduled_messages
           WHERE status='scheduled' AND created_by NOT IN ('auto')
           AND scheduled_date >= date('now')"""
    )
    committed_after = c.fetchall()
    conn.close()

    check(
        "non-auto committed rows untouched",
        committed_before == committed_after,
        f"{len(committed_before)} before, {len(committed_after)} after",
    )


async def test_reload_purge_and_refill() -> None:
    section("T7. Reload purges future auto rows and refills idempotently")
    from bot.scheduler.materializer import purge_future_auto_rows, materialize_forward
    db = Database()
    await db.init()
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM scheduled_messages "
            "WHERE status='scheduled' AND created_by='auto' AND scheduled_date >= date('now')"
        )
        auto_before = c.fetchone()[0]
        conn.close()

        purged = await purge_future_auto_rows(db)
        inserted = await materialize_forward(db)

        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM scheduled_messages "
            "WHERE status='scheduled' AND created_by='auto' AND scheduled_date >= date('now')"
        )
        auto_after = c.fetchone()[0]
        conn.close()

        check(
            "purge cancelled all future auto rows",
            purged == auto_before,
            f"purged={purged} vs before={auto_before}",
        )
        check(
            "materialize restored equivalent row count",
            inserted == auto_after,
            f"inserted={inserted}, auto_after={auto_after}",
        )
        check(
            "count stable (inserted == before)",
            inserted == auto_before,
            f"before={auto_before} inserted={inserted}",
        )
    finally:
        await db.close()


async def test_feature_flag_honored() -> None:
    section("T8. Feature flag disable removes future auto rows on rematerialize")
    settings_path = REPO / "config" / "settings.yaml"
    backup = settings_path.read_text()
    try:
        # Mutate: disable morning_prompt
        import yaml
        data = yaml.safe_load(backup)
        original_morning = data["features"]["morning_prompt"]
        data["features"]["morning_prompt"] = {"enabled": False, "groups": ["main"]}
        settings_path.write_text(yaml.safe_dump(data, allow_unicode=True))

        # Clear settings cache by re-importing get_settings (it reads fresh each call)
        check("is_feature_enabled('morning_prompt') == False after disable",
              is_feature_enabled("morning_prompt") is False)

        # Rematerialize with flag off
        from bot.scheduler.materializer import purge_future_auto_rows, materialize_forward
        db = Database()
        await db.init()
        try:
            await purge_future_auto_rows(db)
            await materialize_forward(db)

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                """SELECT COUNT(*) FROM scheduled_messages
                   WHERE status='scheduled' AND created_by='auto'
                   AND message_type='morning' AND scheduled_date >= date('now')"""
            )
            morning_rows_disabled = c.fetchone()[0]
            conn.close()

            check("no morning rows materialized when feature disabled",
                  morning_rows_disabled == 0,
                  f"found {morning_rows_disabled}")
        finally:
            await db.close()
    finally:
        # Restore settings.yaml
        settings_path.write_text(backup)
        check("is_feature_enabled('morning_prompt') == True after restore",
              is_feature_enabled("morning_prompt") is True)

        # Re-materialize to restore morning rows
        db = Database()
        await db.init()
        try:
            from bot.scheduler.materializer import purge_future_auto_rows, materialize_forward
            await purge_future_auto_rows(db)
            await materialize_forward(db)

            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute(
                """SELECT COUNT(*) FROM scheduled_messages
                   WHERE status='scheduled' AND created_by='auto'
                   AND message_type='morning' AND scheduled_date >= date('now')"""
            )
            morning_rows_restored = c.fetchone()[0]
            conn.close()

            check("morning rows re-materialized after restore",
                  morning_rows_restored > 0,
                  f"restored {morning_rows_restored} rows")
        finally:
            await db.close()


async def test_old_cron_jobs_gone() -> None:
    section("T9. Old text-content cron jobs removed")
    import ast
    src = (REPO / "bot" / "scheduler" / "jobs.py").read_text()
    for forbidden in ("send_morning_prompt", "send_evening_prompt", "send_discussion_prompt"):
        check(f"bot/scheduler/jobs.py does not reference {forbidden}",
              forbidden not in src)

    # Confirm old handler functions are gone from source files
    goals_src = (REPO / "bot" / "handlers" / "goals.py").read_text()
    check("send_morning_prompt removed from goals.py", "async def send_morning_prompt" not in goals_src)
    check("send_evening_prompt removed from goals.py", "async def send_evening_prompt" not in goals_src)

    disc_src = (REPO / "bot" / "handlers" / "discussions.py").read_text()
    check("send_discussion_prompt removed from discussions.py", "async def send_discussion_prompt" not in disc_src)

    # Commitment shim (no longer needed) is gone
    check("bot/utils/commitment.py deleted", not (REPO / "bot" / "utils" / "commitment.py").exists())


async def test_running_bot_has_new_schedule() -> None:
    section("T10. Running bot has correct APScheduler job set")
    log_text = (REPO / "data" / "bot.log").read_text()
    # Look for the latest "Scheduled N cron jobs" line
    lines = [ln for ln in log_text.splitlines() if "cron jobs via JobQueue" in ln]
    check("latest startup logged new message and matching job count",
          bool(lines) and "cron jobs via JobQueue (text content → materializer)" in lines[-1],
          lines[-1] if lines else "no match")

    # No cron should exist for morning_prompt etc. after startup
    recent = log_text.splitlines()[-2000:]
    for forbidden in ('Added job "morning_prompt"', 'Added job "evening_prompt"', 'Added job "discussion_prompt'):
        found_after = [ln for ln in recent if forbidden in ln]
        check(f"no {forbidden!r} added post-migration",
              not found_after,
              f"{len(found_after)} occurrences" if found_after else "")


async def main() -> None:
    now = datetime.now(TZ)
    print(f"Robotnik sync test suite — {now.strftime('%Y-%m-%d %H:%M %A')} {TZ}")
    print(f"DB: {DB_PATH}")

    await test_single_source_imports()
    await test_day_of_week_mapping()
    await test_no_duplicate_slots()
    await test_past_due_guard()
    await test_preview_matches_db()
    await test_committed_rows_preserved()
    await test_reload_purge_and_refill()
    await test_feature_flag_honored()
    await test_old_cron_jobs_gone()
    await test_running_bot_has_new_schedule()

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    print(f"\n{'=' * 60}")
    print(f"RESULT: {passed} passed, {failed} failed")
    if failed:
        print("\nFailures:")
        for name, ok, detail in results:
            if not ok:
                print(f"  ✗ {name} — {detail}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
