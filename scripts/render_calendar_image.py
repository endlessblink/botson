"""Render a Google Calendar-style month view to PNG and send it to Sherlocks Den.

Uses Playwright + Chromium to screenshot an HTML/Tailwind calendar where each
day cell shows the activity *inside* the cell (not as a separate text block).
Hebrew RTL, dark mode to match Telegram's dark theme.

Usage: .venv/bin/python3 scripts/render_calendar_image.py
"""

from __future__ import annotations

import asyncio
import calendar as cal
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path
from collections import defaultdict

from dotenv import load_dotenv
from playwright.async_api import async_playwright
from telegram import Bot

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
load_dotenv(REPO / ".env")

TEST = -1003747545764
HEB_DOW = ["א", "ב", "ג", "ד", "ה", "ו", "ש"]
HEB_MONTHS = {1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל",
              5: "מאי", 6: "יוני", 7: "יולי", 8: "אוגוסט",
              9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר"}

# emoji + tailwind color classes per type
TYPE_STYLE = {
    "morning":    ("🌞", "בוקר",  "bg-amber-500/20 text-amber-200 border-amber-500/40"),
    "evening":    ("🌙", "ערב",   "bg-indigo-500/20 text-indigo-200 border-indigo-500/40"),
    "discussion": ("💬", "שיחה",  "bg-emerald-500/20 text-emerald-200 border-emerald-500/40"),
    "weekly":     ("📊", "סיכום", "bg-violet-500/20 text-violet-200 border-violet-500/40"),
    "event":      ("🎉", "אירוע", "bg-rose-500/20 text-rose-200 border-rose-500/40"),
}


def load_activity():
    today = date.today()
    year, month = today.year, today.month
    last_day = cal.monthrange(year, month)[1]
    month_end = date(year, month, last_day)

    conn = sqlite3.connect("data/bot.db")
    conn.row_factory = sqlite3.Row
    sched = conn.execute(
        """SELECT scheduled_date, scheduled_time, message_type FROM scheduled_messages
           WHERE status='scheduled' AND scheduled_date BETWEEN ? AND ?
           ORDER BY scheduled_date, scheduled_time""",
        (today.isoformat(), month_end.isoformat()),
    ).fetchall()
    events = conn.execute(
        """SELECT event_date, event_time, title FROM events
           WHERE active=1 AND event_date BETWEEN ? AND ?
           ORDER BY event_date, event_time""",
        (today.isoformat(), month_end.isoformat()),
    ).fetchall()
    conn.close()

    by_day: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for r in sched:
        emoji, label, css = TYPE_STYLE.get(r["message_type"], ("📌", r["message_type"], "bg-zinc-700 text-zinc-200 border-zinc-600"))
        by_day[r["scheduled_date"]].append((emoji, (r["scheduled_time"] or "")[:5], css))
    for r in events:
        emoji, label, css = TYPE_STYLE["event"]
        by_day[r["event_date"]].append((emoji, (r["event_time"] or "")[:5] or "—", css))
    return today, by_day


def build_html(today: date, by_day: dict) -> str:
    year, month = today.year, today.month
    cal.setfirstweekday(cal.SUNDAY)
    weeks = cal.monthcalendar(year, month)

    # Build header (RTL — Sunday on the right visually)
    header_cells = "".join(
        f'<div class="text-center font-semibold text-zinc-400 text-sm py-2">{d}</div>'
        for d in HEB_DOW
    )

    # Build week rows
    week_rows = []
    for week in weeks:
        cells = []
        for day in week:
            if day == 0:
                cells.append('<div class="bg-zinc-950/40 rounded-md min-h-[110px]"></div>')
                continue
            iso = date(year, month, day).isoformat()
            items = by_day.get(iso, [])
            is_today = (day == today.day)
            is_past = day < today.day

            # Day number (top-right in RTL)
            day_num_class = (
                "bg-blue-500 text-white font-bold rounded-full w-7 h-7 flex items-center justify-center text-sm"
                if is_today else
                ("text-zinc-600 text-sm font-medium px-1" if is_past else "text-zinc-200 text-sm font-medium px-1")
            )

            # Event chips inside the cell
            chip_html = ""
            for emoji, time_str, css in items[:4]:
                chip_html += (
                    f'<div class="{css} border text-[11px] rounded px-1.5 py-0.5 mb-0.5 truncate leading-tight">'
                    f'{emoji} {time_str}</div>'
                )
            if len(items) > 4:
                chip_html += '<div class="text-zinc-500 text-[10px] px-1">…</div>'

            bg_class = "bg-zinc-900/80" if not is_past else "bg-zinc-950/30"
            border_class = "ring-2 ring-blue-500/60" if is_today else "border border-zinc-800"

            cells.append(
                f'<div class="{bg_class} {border_class} rounded-md min-h-[110px] p-1.5 flex flex-col gap-0.5">'
                f'  <div class="flex justify-end mb-1"><span class="{day_num_class}">{day}</span></div>'
                f'  <div class="flex flex-col">{chip_html}</div>'
                f'</div>'
            )
        week_rows.append(f'<div class="grid grid-cols-7 gap-1.5">{"".join(cells)}</div>')

    weeks_html = "\n".join(week_rows)

    # Legend
    legend_items = []
    for key, (emoji, label, css) in TYPE_STYLE.items():
        if key == "event":
            continue
        legend_items.append(
            f'<div class="{css} border text-xs rounded px-2 py-1">{emoji} {label}</div>'
        )
    legend = "".join(legend_items)

    return f"""<!doctype html>
<html dir="rtl" lang="he">
<head>
<meta charset="utf-8">
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body {{ font-family: 'Segoe UI', 'Heebo', system-ui, -apple-system, sans-serif; }}
</style>
</head>
<body class="bg-zinc-950 text-zinc-100 p-4">
  <div class="max-w-5xl mx-auto">
    <div class="flex items-center justify-between mb-3">
      <div class="flex gap-2">{legend}</div>
      <h1 class="text-2xl font-bold">📅 {HEB_MONTHS[month]} {year}</h1>
    </div>
    <div class="grid grid-cols-7 gap-1.5 mb-1">{header_cells}</div>
    <div class="flex flex-col gap-1.5">
      {weeks_html}
    </div>
  </div>
</body>
</html>"""


async def main():
    today, by_day = load_activity()
    html = build_html(today, by_day)

    out_html = REPO / "data" / "calendar_demo.html"
    out_png = REPO / "data" / "calendar_demo.png"
    out_html.write_text(html, encoding="utf-8")

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1100, "height": 900},
                                      device_scale_factor=2)
        await page.goto(f"file://{out_html}")
        # Wait for tailwind CDN to apply styles
        await page.wait_for_load_state("networkidle")
        # Screenshot just the calendar container, not the whole viewport
        await page.locator(".max-w-5xl").screenshot(path=str(out_png))
        await browser.close()

    print(f"Wrote {out_png}")

    bot = Bot(os.getenv("BOT_TOKEN"))
    # Delete previous demo (#64)
    try:
        await bot.delete_message(chat_id=TEST, message_id=64)
        print("deleted msg 64")
    except Exception as e:
        print(f"delete 64: {e}")

    with open(out_png, "rb") as f:
        m = await bot.send_photo(
            chat_id=TEST,
            photo=f,
            caption="📅 לוח אירועים — תצוגה מלאה (כל יום עם הפעילות בתוכו)",
        )
    print(f"Sent photo {m.message_id}")


if __name__ == "__main__":
    asyncio.run(main())
