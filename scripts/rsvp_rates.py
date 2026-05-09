#!/usr/bin/env python3
"""Print RSVP counts per recent trivia/emoji warm-up announcement.

Read-only. Answers the practical question: are the warm-up RSVP messages
reaching anyone? Run locally against `data/bot.db`, or copy the prod DB
down and point DB_PATH at it.

Usage:
    python3 scripts/rsvp_rates.py            # last 20 warm-ups, default DB
    DB_PATH=/tmp/prod.db python3 scripts/rsvp_rates.py 40
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys


def main() -> int:
    db_path = os.environ.get("DB_PATH") or "data/bot.db"
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 20

    if not os.path.exists(db_path):
        print(f"DB not found at {db_path}; set DB_PATH=...")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT id, scheduled_date, scheduled_time, channel_topic_id, status,
               poll_options, sent_message_id
        FROM scheduled_messages
        WHERE message_type = 'trivia_warmup_rsvp'
        ORDER BY scheduled_date DESC, scheduled_time DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    if not rows:
        print("No trivia_warmup_rsvp rows found.")
        return 0

    print(f"{'date':<12} {'time':<6} {'topic':<6} {'status':<10} "
          f"{'rsvp':<5} {'thr':<5} {'theme':<25} game_outcome")
    print("-" * 100)

    games_skipped = 0
    games_sent = 0
    rsvp_sum = 0
    threshold_met = 0
    rows_with_threshold = 0

    for r in rows:
        try:
            payload = json.loads(r["poll_options"] or "{}")
        except (json.JSONDecodeError, TypeError):
            payload = {}
        threshold = int(payload.get("min_ready_players") or 0)
        theme = str(payload.get("theme_label") or payload.get("activity_label") or "")[:24]
        marker = str(payload.get("warmup_marker") or "")

        rsvp_n = 0
        if r["status"] == "sent":
            count_row = conn.execute(
                "SELECT COUNT(*) AS n FROM trivia_interest_responses WHERE scheduled_msg_id = ?",
                (int(r["id"]),),
            ).fetchone()
            rsvp_n = int(count_row["n"]) if count_row else 0
            rsvp_sum += rsvp_n
            if threshold > 0:
                rows_with_threshold += 1
                if rsvp_n >= threshold:
                    threshold_met += 1

        outcome = "n/a"
        if marker:
            game_row = conn.execute(
                """
                SELECT status FROM scheduled_messages
                WHERE message_type IN ('trivia_round','emoji_puzzle')
                  AND poll_options LIKE ?
                ORDER BY id DESC LIMIT 1
                """,
                (f'%"{marker}"%',),
            ).fetchone()
            if game_row:
                outcome = game_row["status"]
                if outcome == "skipped":
                    games_skipped += 1
                elif outcome == "sent":
                    games_sent += 1

        print(f"{r['scheduled_date']:<12} {r['scheduled_time'][:5]:<6} "
              f"{r['channel_topic_id'] or 0:<6} {r['status']:<10} "
              f"{rsvp_n:<5} {threshold:<5} {theme:<25} {outcome}")

    print("-" * 100)
    sent_rows = sum(1 for r in rows if r["status"] == "sent")
    if sent_rows:
        avg = rsvp_sum / sent_rows
        print(f"sent warm-ups: {sent_rows}   avg RSVPs: {avg:.2f}   "
              f"threshold met: {threshold_met}/{rows_with_threshold}   "
              f"games sent: {games_sent}   skipped: {games_skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
