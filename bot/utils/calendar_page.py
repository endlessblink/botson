"""Calendar entries for the community's public page (`/welcome`, which is also the events calendar).

The page hands one event at a time to Google Calendar (its own prefilled screen) or as a standard
calendar file. Both carry only what the page shows: community_page.calendar_entry reduces the
event first (public title, city or area only, cleaned description; no address, join link, organizer
or contact). page_url() is the address the calendar picture's caption links to; the old /cal
addresses there redirect to the matching month of /welcome.

Numbers live in settings.yaml `events_calendar.page`.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

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
    return str(ev.get("description") or "")


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
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"
