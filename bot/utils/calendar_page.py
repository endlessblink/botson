"""The public, read-only events calendar page (`/cal`), opened from the calendar picture.

Shows exactly the events the calendar picture shows (Database.get_published_events): only
what was already published to the community, and only what the published card shows
(title, date, time, place, description, picture, the cleaned join link, who is responsible).
No phone numbers, member ids, pending, rejected or test-only events.

Numbers live in settings.yaml `events_calendar.page`, wording in `copy.calendar_page`.
"""
from __future__ import annotations

import calendar as _calendar
import logging
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from .copy import load_copy

logger = logging.getLogger(__name__)

TZ = "Asia/Jerusalem"


def page_cfg() -> dict:
    from .config import get_settings

    return ((get_settings() or {}).get("events_calendar") or {}).get("page") or {}


def page_url(month: str | None = None, event_id: int | None = None) -> str | None:
    """The page's public address, or None when the dashboard has no public address."""
    from . import config

    base = getattr(config, "PUBLIC_DASHBOARD_URL", "")
    if not base:
        return None
    url = f"{base}/cal" + (f"/{month}" if month else "")
    return url + (f"?e={int(event_id)}" if event_id else "")


# ── Events ──────────────────────────────────────────────────

async def page_events(db) -> list[dict]:
    """The published events, reduced to what the published card shows."""
    from .shared_events import post_link, valid_link

    out = []
    for e in await db.get_published_events(int(os.getenv("GROUP_ID", "0") or 0)):
        link = None
        if e.get("link"):
            try:
                link = await post_link(db, e["link"])
            except Exception:
                logger.warning("calendar_page: could not prepare the link of event %s", e.get("id"), exc_info=True)
            if not link or not valid_link(link):
                link = None
        try:
            day = date.fromisoformat(str(e["event_date"]))
        except (TypeError, ValueError):
            continue
        out.append({
            "id": int(e["id"]),
            "title": str(e.get("title") or "").strip(),
            "date": day,
            "time": (str(e.get("event_time") or "").strip()[:5] or None),
            "place": (str(e.get("location") or "").strip() or None),
            "description": (str(e.get("description") or "").strip() or None),
            "cover_url": f"/media/{e['cover_path']}" if e.get("cover_path") else None,
            "link": link,
            "who": (str(e.get("organizer") or e.get("requester_name") or "").strip() or None),
        })
    out.sort(key=lambda x: (x["date"], x["time"] or ""))
    return out


def _start_end(ev: dict) -> tuple[datetime, datetime] | None:
    """Local start and end, or None for an event with no time (an all-day entry)."""
    if not ev.get("time"):
        return None
    try:
        hh, mm = (int(x) for x in ev["time"].split(":")[:2])
        start = datetime(ev["date"].year, ev["date"].month, ev["date"].day, hh, mm, tzinfo=ZoneInfo(TZ))
    except ValueError:
        return None
    return start, start + timedelta(minutes=int(page_cfg().get("event_length_minutes", 120)))


def _details(ev: dict) -> str:
    parts = [ev["description"]] if ev.get("description") else []
    if ev.get("link"):
        parts.append(load_copy("calendar_page", "join_in_calendar", link=ev["link"]))
    return "\n\n".join(parts)


def google_url(ev: dict) -> str:
    """Google Calendar's own prefilled new-event screen. Nothing signs in through us."""
    times = _start_end(ev)
    if times:
        dates = "/".join(t.strftime("%Y%m%dT%H%M%S") for t in times)
    else:
        dates = f"{ev['date']:%Y%m%d}/{ev['date'] + timedelta(days=1):%Y%m%d}"
    params = {"action": "TEMPLATE", "text": ev["title"], "dates": dates, "ctz": TZ}
    if ev.get("place"):
        params["location"] = ev["place"]
    details = _details(ev)
    if details:
        params["details"] = details
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def _ics_escape(text: str) -> str:
    return (str(text).replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Lines of at most 75 bytes, continued with a space (RFC 5545), never splitting a character."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


# Israel since 2013: summer time from the Friday before the last Sunday of March, 02:00,
# until the last Sunday of October, 02:00.
_VTIMEZONE = [
    "BEGIN:VTIMEZONE", f"TZID:{TZ}",
    "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0300", "TZNAME:IDT", "DTSTART:20130329T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=3;BYMONTHDAY=23,24,25,26,27,28,29;BYDAY=FR", "END:DAYLIGHT",
    "BEGIN:STANDARD", "TZOFFSETFROM:+0300", "TZOFFSETTO:+0200", "TZNAME:IST", "DTSTART:20131027T020000",
    "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
    "END:VTIMEZONE",
]


def ics_text(ev: dict, *, host: str = "botson", now: datetime | None = None) -> str:
    """A standard calendar file (Apple, Outlook and others) for one event."""
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    times = _start_end(ev)
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Botson//Community calendar//HE", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", *_VTIMEZONE, "BEGIN:VEVENT", f"UID:event-{int(ev['id'])}@{host}", f"DTSTAMP:{stamp}"]
    if times:
        lines += [f"DTSTART;TZID={TZ}:{times[0]:%Y%m%dT%H%M%S}", f"DTEND;TZID={TZ}:{times[1]:%Y%m%dT%H%M%S}"]
    else:
        lines += [f"DTSTART;VALUE=DATE:{ev['date']:%Y%m%d}", f"DTEND;VALUE=DATE:{ev['date'] + timedelta(days=1):%Y%m%d}"]
    lines.append(f"SUMMARY:{_ics_escape(ev['title'])}")
    if ev.get("place"):
        lines.append(f"LOCATION:{_ics_escape(ev['place'])}")
    details = _details(ev)
    if details:
        lines.append(f"DESCRIPTION:{_ics_escape(details)}")
    if ev.get("link"):
        lines.append(f"URL:{ev['link']}")
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


# ── The page model ──────────────────────────────────────────

def day_name(d: date) -> str:
    return load_copy("events_calendar", "day_names")[(d.weekday() + 1) % 7]


def when_text(ev: dict) -> str:
    months = load_copy("events_calendar", "month_names")
    days = load_copy("events_calendar", "day_names")
    d = ev["date"]
    fmt = dict(day=days[(d.weekday() + 1) % 7], date=d.day, month=months[d.month - 1], time=ev.get("time") or "")
    return load_copy("calendar_page", "when" if ev.get("time") else "when_no_time", **fmt)


def build_page(events: list[dict], *, year: int, month: int, today: date, open_event: int | None = None,
               selected_day: date | None = None) -> dict:
    """Everything the template draws for one month. Pure: no database, no clock."""
    months = load_copy("events_calendar", "month_names")
    in_month = [e for e in events if e["date"].year == year and e["date"].month == month]
    by_day: dict[date, list[dict]] = {}
    for e in in_month:
        by_day.setdefault(e["date"], []).append(e)
    first_dow = (date(year, month, 1).weekday() + 1) % 7  # Sunday = 0
    n = _calendar.monthrange(year, month)[1]
    cells = []
    for i in range(first_dow):
        cells.append({"date": date(year, month, 1) - timedelta(days=first_dow - i), "out": True})
    for day in range(1, n + 1):
        d = date(year, month, day)
        cells.append({"date": d, "out": False, "count": len(by_day.get(d, [])), "past": d < today,
                      "today": d == today, "sat": d.weekday() == 5, "selected": d == selected_day})
    while len(cells) % 7:
        cells.append({"date": cells[-1]["date"] + timedelta(days=1), "out": True})
    limit = int(page_cfg().get("upcoming_count", 4))
    upcoming = [e for e in events if e["date"] >= today][:limit]
    prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    shown = {e["id"]: e for e in in_month + upcoming}
    opened = next((e for e in events if e["id"] == open_event), None) if open_event else None
    if opened:
        shown[opened["id"]] = opened
    return {
        "year": year, "month": month, "month_key": f"{year:04d}-{month:02d}", "month_name": months[month - 1],
        "prev_key": f"{prev_m[0]:04d}-{prev_m[1]:02d}", "next_key": f"{next_m[0]:04d}-{next_m[1]:02d}",
        "cells": cells, "days": sorted(by_day.items()), "count": len(in_month),
        "all_past": bool(in_month) and all(e["date"] < today for e in in_month),
        "upcoming": upcoming, "sheets": sorted(shown.values(), key=lambda e: (e["date"], e["time"] or "")),
        "open_event": opened["id"] if opened else None, "selected_day": selected_day, "today": today,
    }
