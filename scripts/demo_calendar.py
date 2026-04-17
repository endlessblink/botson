"""One-off demo: render the two calendar UI options in the Sherlocks Den test group.

Sends two messages to chat -1003747545764:
  A) inline-keyboard month grid (option 1: on-demand /calendar)
  B) formatted upcoming-events list, pinned (option 2: pinned auto-update board)

Both are visual mockups — the buttons in A are non-interactive (no handler
registered in the running bot). Pulls real upcoming activity from
`scheduled_messages` (morning/evening/discussion prompts) and the `events`
table (manual /event creations).

Usage: .venv/bin/python3 scripts/demo_calendar.py
"""

from __future__ import annotations

import asyncio
import calendar
import json
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))

load_dotenv()

from bot.database.db import Database  # noqa: E402

TZ = ZoneInfo("Asia/Jerusalem")
TEST_GROUP_ID = -1003747545764
DB_PATH = "data/bot.db"

HEB_DOW = ["א", "ב", "ג", "ד", "ה", "ו", "ש"]  # Sun..Sat
HEB_MONTHS = {
    1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל",
    5: "מאי", 6: "יוני", 7: "יולי", 8: "אוגוסט",
    9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר",
}

# Map message_type → (emoji, Hebrew label)
TYPE_META = {
    "morning":    ("🌞", "בוקר"),
    "evening":    ("🌙", "ערב"),
    "discussion": ("💬", "שיחה"),
    "weekly":     ("📊", "סיכום שבועי"),
    "event":      ("🎉", "אירוע"),
}


def _load_upcoming_scheduled(limit: int = 8) -> list[dict]:
    """Read upcoming scheduled bot prompts directly from the DB."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT scheduled_date, scheduled_time, message_type, text, channel_topic_id
           FROM scheduled_messages
           WHERE status = 'scheduled' AND scheduled_date >= date('now')
           ORDER BY scheduled_date ASC, scheduled_time ASC
           LIMIT ?""",
        (limit,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _build_month_keyboard(year: int, month: int, today_day: int,
                          event_days: set[int]) -> InlineKeyboardMarkup:
    """Build a Sun-first month grid. Today gets brackets, event days get 🔵."""
    calendar.setfirstweekday(calendar.SUNDAY)
    weeks = calendar.monthcalendar(year, month)

    rows: list[list[InlineKeyboardButton]] = []

    # Hebrew weekday header row — labels only, callback is a noop
    rows.append([
        InlineKeyboardButton(d, callback_data="cal_demo_noop") for d in HEB_DOW
    ])

    for week in weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton("·", callback_data="cal_demo_noop"))
                continue
            label = str(day)
            if day in event_days:
                label = f"🔵{day}"
            if day == today_day:
                label = f"[{label}]"
            row.append(InlineKeyboardButton(label, callback_data="cal_demo_noop"))
        rows.append(row)

    return InlineKeyboardMarkup(rows)


def _format_event_blocks(manual_events: list[dict],
                         scheduled: list[dict]) -> str:
    """Format upcoming activity (manual events + scheduled bot prompts)."""
    blocks: list[str] = []

    for ev in manual_events:
        yes_list = json.loads(ev.get("rsvp_yes") or "[]")
        maybe_list = json.loads(ev.get("rsvp_maybe") or "[]")
        try:
            d = date.fromisoformat(ev["event_date"])
            date_str = d.strftime("%d/%m/%Y")
        except Exception:
            date_str = ev["event_date"]

        lines = [f"🎉 {ev['title']}"]
        if ev.get("description"):
            lines.append(f"📝 {ev['description']}")
        lines.append(f"📅 {date_str}")
        if ev.get("event_time"):
            lines.append(f"🕐 {ev['event_time']}")
        if ev.get("location"):
            lines.append(f"📍 {ev['location']}")
        lines.append("")
        lines.append(f"✅ מגיעים: {len(yes_list)} | 🤔 אולי: {len(maybe_list)}")
        blocks.append("\n".join(lines))

    for s in scheduled:
        emoji, label = TYPE_META.get(s["message_type"], ("📌", s["message_type"]))
        try:
            d = date.fromisoformat(s["scheduled_date"])
            date_str = d.strftime("%d/%m/%Y")
            dow = HEB_DOW[(d.weekday() + 1) % 7]  # Sun=0..Sat=6
        except Exception:
            date_str = s["scheduled_date"]
            dow = ""

        time_str = (s.get("scheduled_time") or "")[:5]
        text = (s.get("text") or "").strip().replace("\n", " ")
        if len(text) > 90:
            text = text[:87] + "…"

        lines = [
            f"{emoji} {label} — {date_str} ({dow}) {time_str}",
            text,
        ]
        blocks.append("\n".join(lines))

    if not blocks:
        return (
            "אין פעילות מתוכננת.\n\n"
            "─────\n\n"
            "🎉 [דמו] ערב משחקים קהילתי\n"
            "📅 25/04/2026  🕐 20:00\n"
            "📍 הסלון של נועם"
        )

    return "\n\n─────\n\n".join(blocks)


async def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: BOT_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)

    today = date.today()
    year, month = today.year, today.month

    db = Database()
    await db.init()
    try:
        manual_events = await db.get_upcoming_events(limit=5)
    finally:
        await db.close()

    scheduled = _load_upcoming_scheduled(limit=8)

    # Days this month with at least one upcoming activity (for the grid markers)
    event_days: set[int] = set()
    for ev in manual_events:
        try:
            d = date.fromisoformat(ev["event_date"])
            if d.year == year and d.month == month:
                event_days.add(d.day)
        except Exception:
            continue
    for s in scheduled:
        try:
            d = date.fromisoformat(s["scheduled_date"])
            if d.year == year and d.month == month:
                event_days.add(d.day)
        except Exception:
            continue

    bot = Bot(token=token)

    # ── Message A: option 1 — inline-keyboard month grid ──
    header_a = f"📅 {HEB_MONTHS[month]} {year}"
    body_a = (
        f"{header_a}\n\n"
        "תצוגה מקדימה — בגרסה האמיתית, פקודת /calendar תפתח את הלוח הזה,\n"
        "ולחיצה על תאריך תציג את הפעילות באותו יום.\n"
        "🔵 = יום עם פעילות ・ [ ] = היום"
    )
    kb = _build_month_keyboard(year, month, today.day, event_days)
    msg_a = await bot.send_message(
        chat_id=TEST_GROUP_ID,
        text=body_a,
        reply_markup=kb,
    )
    print(f"Message A (calendar grid) sent: message_id={msg_a.message_id}")

    # ── Message B: option 2 — upcoming activity board, pinned ──
    body_b = (
        "📌 פעילות קרובה בקבוצה\n\n"
        f"{_format_event_blocks(manual_events, scheduled)}\n\n"
        "─────\n"
        "תצוגה מקדימה — בגרסה האמיתית ההודעה מוצמדת לראש הצ'אט\n"
        "ומתעדכנת אוטומטית בכל הוספת אירוע או שינוי בלו\"ז."
    )
    msg_b = await bot.send_message(
        chat_id=TEST_GROUP_ID,
        text=body_b,
    )
    print(f"Message B (events board) sent:  message_id={msg_b.message_id}")

    try:
        await bot.pin_chat_message(
            chat_id=TEST_GROUP_ID,
            message_id=msg_b.message_id,
            disable_notification=True,
        )
        print(f"Message B pinned in chat {TEST_GROUP_ID}")
    except Exception as e:
        print(f"WARNING: failed to pin message B: {e}", file=sys.stderr)

    print("\nDone. Take screenshots in Sherlocks Den, then unpin/delete when finished.")


if __name__ == "__main__":
    asyncio.run(main())
