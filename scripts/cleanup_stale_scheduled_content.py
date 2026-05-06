#!/usr/bin/env python3
"""Dry-run cleanup for stale future scheduled content.

Default mode prints matching rows only. Use `--apply --action cancel` to mark
future matching rows as cancelled after reviewing the dry-run output.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bot.utils.freshness import stale_reasons_for_row  # noqa: E402


def _row_dict(cursor: sqlite3.Cursor, row: tuple) -> dict:
    return {desc[0]: row[idx] for idx, desc in enumerate(cursor.description)}


def find_stale_rows(db_path: str, *, from_date: str | None = None) -> list[dict]:
    from_date = from_date or date.today().isoformat()
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            """SELECT id, text, message_type, scheduled_date, scheduled_time,
                      status, created_by, channel_topic_id, poll_options
                 FROM scheduled_messages
                WHERE status IN ('scheduled', 'draft')
                  AND scheduled_date >= ?
                ORDER BY scheduled_date, scheduled_time, id""",
            (from_date,),
        )
        matches = []
        for raw in cur.fetchall():
            row = _row_dict(cur, raw)
            reasons = stale_reasons_for_row(row)
            if reasons:
                row["reasons"] = reasons
                matches.append(row)
        return matches
    finally:
        conn.close()


def cancel_rows(db_path: str, ids: list[int]) -> int:
    if not ids:
        return 0
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        placeholders = ",".join("?" for _ in ids)
        cur.execute(
            f"UPDATE scheduled_messages SET status = 'cancelled', error_message = 'stale-content-cleanup' WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=os.getenv("DB_PATH", str(REPO / "data" / "bot.db")))
    parser.add_argument("--from-date", default=date.today().isoformat())
    parser.add_argument("--apply", action="store_true", help="Actually mutate matching rows")
    parser.add_argument("--action", choices=("cancel",), default="cancel")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    args = parser.parse_args(argv)

    rows = find_stale_rows(args.db, from_date=args.from_date)
    if args.json:
        print(json.dumps({"db": args.db, "from_date": args.from_date, "matches": rows}, ensure_ascii=False, indent=2))
    else:
        print(f"DB: {args.db}")
        print(f"From date: {args.from_date}")
        print(f"Matches: {len(rows)}")
        for row in rows:
            text = str(row.get("text") or "").replace("\n", " ")[:120]
            print(
                f"#{row['id']} {row['scheduled_date']} {row['scheduled_time']} "
                f"{row['message_type']} {row['status']} · {', '.join(row['reasons'])} · {text}"
            )

    if not args.apply:
        if not args.json:
            print("Dry-run only. Re-run with --apply --action cancel after review.")
        return 0

    changed = cancel_rows(args.db, [int(row["id"]) for row in rows])
    if not args.json:
        print(f"Cancelled rows: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
