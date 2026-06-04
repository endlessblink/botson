#!/usr/bin/env python3
"""Read-only generation health check for cron/Watchpost.

Exit codes:
  0 = ok, or degraded with --allow-degraded
  1 = degraded
  2 = failed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

VENV_PYTHON = REPO_ROOT / ".venv" / "bin" / "python"
if (
    VENV_PYTHON.exists()
    and Path(sys.executable) != VENV_PYTHON
    and not os.environ.get("BOTSON_GENERATION_HEALTH_REEXEC")
):
    os.environ["BOTSON_GENERATION_HEALTH_REEXEC"] = "1"
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON), str(Path(__file__).resolve()), *sys.argv[1:]])


async def _run(args: argparse.Namespace) -> dict:
    from bot.database.db import Database
    from bot.utils.config import DB_PATH
    from dashboard.app import run_generation_health_check

    db = Database(args.db or DB_PATH)
    await db.init()
    try:
        return await run_generation_health_check(
            db,
            include_planner=args.planner,
            min_suggestions=args.min_suggestions,
        )
    finally:
        await db.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Check Botson generation/provider health.")
    parser.add_argument("--planner", action="store_true", help="also run a read-only Planner dry run")
    parser.add_argument("--min-suggestions", type=int, default=6, help="minimum Planner dry-run suggestions")
    parser.add_argument("--timeout-seconds", type=int, default=180, help="hard timeout for the whole check")
    parser.add_argument("--allow-degraded", action="store_true", help="exit 0 when fallback providers were needed")
    parser.add_argument("--db", default="", help="override DB path")
    args = parser.parse_args()

    try:
        result = asyncio.run(asyncio.wait_for(_run(args), timeout=max(1, int(args.timeout_seconds))))
    except TimeoutError:
        result = {
            "status": "failed",
            "ok": False,
            "degraded": False,
            "checks": {
                "timeout": {
                    "status": "failed",
                    "timeout_seconds": max(1, int(args.timeout_seconds)),
                }
            },
        }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    if result.get("status") == "ok":
        return 0
    if result.get("status") == "degraded":
        return 0 if args.allow_degraded else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
