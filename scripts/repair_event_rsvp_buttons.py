"""One-shot repair: fix RSVP buttons on already-posted events.

Earlier dashboard versions sent events with `callback_data="rsvp_yes"` /
`"rsvp_maybe"` (no event_id). The bot's handler in bot/handlers/events.py
expects `rsvp_yes_{id}` / `rsvp_maybe_{id}` — without the suffix the click
fires but the handler can't extract the event_id, so buttons appear inert.

This script edits each active event's reply_markup with the correct
callback_data. The message text itself is unchanged.

Usage:  sudo -u botson .venv/bin/python3 scripts/repair_event_rsvp_buttons.py
        (run on the VPS where /opt/robotnik/.env has BOT_TOKEN)
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

try:
    from dotenv import load_dotenv
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup


def _markup(event_id: int, yes_count: int, maybe_count: int) -> InlineKeyboardMarkup:
    yes = f"✅ מגיע/ה ({yes_count})" if yes_count else "✅ מגיע/ה"
    maybe = f"🤔 אולי ({maybe_count})" if maybe_count else "🤔 אולי"
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(yes, callback_data=f"rsvp_yes_{event_id}"),
        InlineKeyboardButton(maybe, callback_data=f"rsvp_maybe_{event_id}"),
    ]])


async def main():
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        print("ERROR: BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    main_group = int(os.getenv("GROUP_ID", "0"))
    test_group = int(os.getenv("TEST_GROUP_ID", "0"))

    db_path = os.getenv("DB_PATH", str(_REPO / "data" / "bot.db"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, title, message_id, topic_id, rsvp_yes, rsvp_maybe
           FROM events WHERE active = 1 AND message_id IS NOT NULL"""
    ).fetchall()
    conn.close()

    if not rows:
        print("No active events with message_id — nothing to repair.")
        return

    print(f"Repairing {len(rows)} event(s)…")
    bot = Bot(bot_token)
    for r in rows:
        eid = r["id"]
        msg_id = r["message_id"]
        yes_n = len(json.loads(r["rsvp_yes"] or "[]"))
        maybe_n = len(json.loads(r["rsvp_maybe"] or "[]"))
        # Try main group first; if that fails, try test group.
        for chat_id in (main_group, test_group):
            if not chat_id:
                continue
            try:
                await bot.edit_message_reply_markup(
                    chat_id=chat_id, message_id=msg_id,
                    reply_markup=_markup(eid, yes_n, maybe_n),
                )
                print(f"  ✓ event #{eid} ({r['title'][:40]}) — fixed buttons "
                      f"in chat {chat_id} msg {msg_id}")
                break
            except Exception as e:
                # 'message to edit not found' is the expected miss; print others.
                if "message to edit" not in str(e).lower():
                    print(f"  ! event #{eid} chat {chat_id} msg {msg_id}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
