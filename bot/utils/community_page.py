"""The community's public page (`/welcome`): front door and events calendar in one.

Open to anyone, so it shows far less than the published event cards:

- Events: the published events (Database.get_published_events), one month at a time (/welcome =
  this month, /welcome/2026-12 = that month; the old /cal addresses redirect here), reduced to title,
  day, time, picture, a city/area, the cleaned description and the event's own outside link (tickets,
  sign-up) when it is a safe https:// address and not a chat invite. Never the stored location text,
  chat invite links, organizer or requester names, phone numbers or member ids. The Google Calendar link and the
  calendar file carry the same reduction (calendar_entry).
- Place: never echoed. The stored location is matched against the config list of known cities and
  areas; only the canonical name from that list is shown, otherwise nothing at all.
- Title: links, e-mail addresses, @handles and phone-like numbers are removed before showing.
- Join: two links set by the operator (Telegram, WhatsApp), shown only when they are https:// invite
  links on an allowed host. Search engines: one switch, default off.

Numbers and lists live in settings.yaml `community_page`, wording in `copy.community_page`.
The operator's join links and the search switch are kept in data/community_page.json (deploys
reset tracked files, so a runtime value must never live under config/).
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

PATH = "/welcome"
PLATFORMS = ("telegram", "whatsapp")
_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_PATH = _REPO_ROOT / "data" / "community_page.json"


# ── Settings ────────────────────────────────────────────────

def base_cfg() -> dict:
    from .config import settings_shared

    return (settings_shared() or {}).get("community_page") or {}


def runtime_path() -> Path:
    return Path(os.environ.get("BOTSON_COMMUNITY_PAGE_PATH") or DEFAULT_RUNTIME_PATH)


def _runtime() -> dict:
    path = runtime_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        logger.warning("community_page: could not read %s", path, exc_info=True)
        return {}


def settings() -> dict:
    """The page's switches: config defaults, overridden by what the operator saved."""
    base = base_cfg()
    saved = _runtime()
    join = dict(base.get("join") or {})
    for p in PLATFORMS:
        if isinstance(saved.get(f"{p}_url"), str):
            join[f"{p}_url"] = saved[f"{p}_url"]
    indexable = saved["indexable"] if isinstance(saved.get("indexable"), bool) else bool(base.get("indexable", False))
    return {"join": join, "indexable": indexable}


def valid_join_link(platform: str, url: str) -> bool:
    """An https:// invite link on one of the hosts allowed for that platform, nothing else."""
    url = str(url or "").strip()
    if not url or len(url) > 300 or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    hosts = [str(h).lower() for h in ((base_cfg().get("join_hosts") or {}).get(platform) or [])]
    return (parts.scheme == "https" and not parts.username and not parts.password and parts.port is None
            and (parts.hostname or "").lower() in hosts and bool(parts.path.strip("/")))


def join_links() -> list[dict]:
    """The join buttons to show, in a fixed order; an empty or invalid link shows no button."""
    join = settings()["join"]
    out = []
    for p in PLATFORMS:
        url = str(join.get(f"{p}_url") or "").strip()
        if valid_join_link(p, url):
            out.append({"platform": p, "url": url})
    return out


def is_indexable() -> bool:
    return settings()["indexable"]


def save_settings(*, telegram_url: str, whatsapp_url: str, indexable: bool) -> list[str]:
    """Save the operator's values. Returns the platforms whose link was refused (nothing saved then)."""
    values = {"telegram_url": str(telegram_url or "").strip(), "whatsapp_url": str(whatsapp_url or "").strip()}
    bad = [p for p in PLATFORMS if values[f"{p}_url"] and not valid_join_link(p, values[f"{p}_url"])]
    if bad:
        return bad
    path = runtime_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as f:
        json.dump({**values, "indexable": bool(indexable)}, f, ensure_ascii=False, indent=2)
        tmp = f.name
    os.replace(tmp, path)
    return []


# ── Reducing an event for strangers ─────────────────────────

# Any letter or digit, the Hebrew block included (built from code points, not typed letters).
_LETTER = "0-9A-Za-z" + chr(0x0590) + "-" + chr(0x05FF)


def _prefix() -> str:
    """Optional one-letter prefix before a name (config place_prefix_letters, e.g. the letters for in/to/from)."""
    letters = re.escape(str(base_cfg().get("place_prefix_letters") or ""))
    return f"[{letters}]?" if letters else ""


def _alias_re(alias: str) -> re.Pattern:
    return re.compile(rf"(?<![{_LETTER}]){_prefix()}{re.escape(alias)}(?![{_LETTER}])", re.IGNORECASE)


def public_place(location: str | None) -> str | None:
    """The city/area of an event as one of the configured names, or None. Never the stored text."""
    text = " ".join(str(location or "").split())
    if not text:
        return None
    cfg = base_cfg()
    for word in cfg.get("place_online_words") or []:
        if _alias_re(str(word)).search(text):
            from .copy import load_copy

            return load_copy("community_page", "online")
    streets = [str(w) for w in cfg.get("place_street_words") or []]
    best: tuple[int, str] | None = None
    for place in cfg.get("places") or []:
        name = str((place or {}).get("name") or "").strip()
        if not name:
            continue
        for alias in (place.get("aliases") or [name]):
            for m in _alias_re(str(alias)).finditer(text):
                before = text[:m.start()].rstrip(" ,.-")
                if any(re.search(rf"(?<![{_LETTER}]){_prefix()}{re.escape(w)}$", before) for w in streets):
                    continue  # a street named after a city is not the city
                if best is None or m.start() > best[0]:
                    best = (m.start(), name)
    return best[1] if best else None


_URL = re.compile(r"(?:https?://|www\.)\S+|\b[\w.-]+\.(?:com|co\.il|org|net|il|ly|me|io)\b\S*", re.IGNORECASE)
_EMAIL = re.compile(r"\S+@\S+\.\S+")
_HANDLE = re.compile(r"(?<![\w])@\w+")
_PHONE = re.compile(r"(?<!\d)(?:\+?972[\s-]?|0)\d{1,2}[\s-]?\d{3}[\s-]?\d{4}(?!\d)|(?<!\d)\d{9,}(?!\d)")


def public_title(title: str | None) -> str:
    """The title without links, e-mail addresses, @handles or phone-like numbers."""
    text = str(title or "")
    for rx in (_URL, _EMAIL, _HANDLE, _PHONE):
        text = rx.sub(" ", text)
    text = " ".join(text.split()).strip(" -–—·|,:")
    return text


def public_description(text: str | None, location: str | None = None) -> str:
    """The published description for strangers: the title rules line by line (no links, e-mails,
    @handles or phone numbers), and any line that looks like an address is left out whole: a line
    that repeats the stored location, or has a street word (config place_street_words) with a number."""
    streets = [str(w) for w in base_cfg().get("place_street_words") or []]
    loc = " ".join(str(location or "").split())
    out: list[str] = []
    for raw in str(text or "").splitlines():
        line = " ".join(raw.split())
        if loc and len(loc) >= 4 and loc in line:
            continue
        if re.search(r"\d", line) and any(
                re.search(rf"(?<![{_LETTER}]){_prefix()}{re.escape(w)}(?![{_LETTER}])", line) for w in streets):
            continue
        line = public_title(line) if line else ""
        if line or (out and out[-1]):
            out.append(line)
    return "\n".join(out).strip()


def _host_matches(host: str, pattern: str) -> bool:
    """The host or one of its parent domains matches the pattern ("*" = any one label, e.g. a TLD)."""
    from fnmatch import fnmatchcase

    labels = host.split(".")
    return any(fnmatchcase(".".join(labels[i:]), pattern) for i in range(len(labels) - 1))


def _blocked_host(host: str, path: str = "") -> bool:
    """A chat invite or a map/navigation link (config event_link_blocked_hosts, and
    event_link_blocked_paths for host+path prefixes such as google.*/maps)."""
    cfg = base_cfg()
    hosts = [str(h).lower().strip(".") for h in cfg.get("event_link_blocked_hosts") or [] if str(h).strip(".")]
    if any(_host_matches(host, h) for h in hosts):
        return True
    path = (path or "/").lower()
    for rule in cfg.get("event_link_blocked_paths") or []:
        h, _, prefix = str(rule).lower().partition("/")
        prefix = "/" + prefix
        if h and _host_matches(host, h) and path.startswith(prefix):  # /maps also catches /maps/place/...
            return True
    return False


def public_link(url: str | None) -> str | None:
    """The event's own outside link (tickets, sign-up), or None. Only a plain https:// address with a
    real host, no user, password or port, never an invite into a chat and never a map or navigation
    link, which would give the exact address away (config event_link_blocked_hosts, subdomains
    included, and event_link_blocked_paths): those stay inside the group."""
    url = str(url or "").strip()
    if not url or len(url) > 500 or any(c.isspace() or ord(c) < 32 for c in url) or any(c in url for c in "\"'<>`"):
        return None
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    host = (parts.hostname or "").lower().rstrip(".")
    if (parts.scheme != "https" or parts.username or parts.password or port is not None
            or "." not in host or _blocked_host(host, parts.path)):
        return None
    if re.fullmatch(r"[\d.]+", host) or host.startswith("[") or not re.fullmatch(r"[a-z0-9.-]+", host):
        return None
    return url


_HTTPS = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)


def event_link(link: str | None, description: str | None) -> str | None:
    """The link the organizer gave (the approved request's link), else the first safe https:// link
    written in the description. The description itself is still shown without links."""
    found = public_link(link)
    if found:
        return found
    for m in _HTTPS.finditer(str(description or "")):
        found = public_link(m.group(0).rstrip(".,;:!?)]}"))
        if found:
            return found
    return None


_COVER = re.compile(r"^[A-Za-z0-9_./-]+\.(?:png|jpe?g|webp|gif)$", re.IGNORECASE)


def _cover_url(path: str | None) -> str | None:
    p = str(path or "").strip()
    if not p or ".." in p or p.startswith("/") or not _COVER.match(p):
        return None
    return f"/media/{p}"


async def public_events(db, today: date) -> list[dict]:
    """Every published event, reduced to what a stranger may see, in date order."""
    from .copy import load_copy

    show_pictures = bool(base_cfg().get("show_pictures", True))
    days = load_copy("events_calendar", "day_names")
    out = []
    for e in await db.get_published_events(int(os.getenv("GROUP_ID", "0") or 0)):
        try:
            day = date.fromisoformat(str(e.get("event_date")))
        except (TypeError, ValueError):
            continue
        title = public_title(e.get("title"))
        if not title:
            continue
        hhmm = str(e.get("event_time") or "").strip()[:5]
        if not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            hhmm = ""
        day_name = days[(day.weekday() + 1) % 7] if isinstance(days, list) and len(days) == 7 else ""
        out.append({
            "id": int(e.get("id") or 0),
            "description": public_description(e.get("description"), e.get("location")),
            "title": title,
            "date": day,
            "past": day < today,
            "date_text": load_copy("community_page", "date", d=day.day, m=day.month),
            "when": (load_copy("community_page", "when", day=day_name, time=hhmm) if hhmm
                     else load_copy("community_page", "when_no_time", day=day_name)),
            "time": hhmm,
            "place": public_place(e.get("location")),
            "cover_url": _cover_url(e.get("cover_path")) if show_pictures else None,
            "link": event_link(e.get("link"), e.get("description")),
        })
    out.sort(key=lambda x: (x["date"], x["time"]))
    return out


def month_view(events: list[dict], *, year: int, month: int, today: date) -> dict:
    """One month of the page. The grid holds every event of the month (past ones dimmed). The big
    cards are only for events still ahead: the month's own, or, when it has none left, the next ones
    after it. A month that is already over gets a compact one-line list of what happened instead."""
    from .copy import load_copy

    months = load_copy("events_calendar", "month_names")
    in_month = [e for e in events if (e["date"].year, e["date"].month) == (year, month)]
    prev_m = (year - 1, 12) if month == 1 else (year, month - 1)
    next_m = (year + 1, 1) if month == 12 else (year, month + 1)
    ahead = [e for e in in_month if not e["past"]]
    later = []
    if not ahead:
        limit = int(base_cfg().get("upcoming_count", 4))
        later = [e for e in events if not e["past"] and (e["date"].year, e["date"].month) > (year, month)][:limit]
    return {
        "cells": _cells(in_month, year=year, month=month, today=today),
        "year": year, "month": month, "key": f"{year:04d}-{month:02d}",
        "name": months[month - 1] if isinstance(months, list) and len(months) == 12 else f"{month}",
        "prev_key": f"{prev_m[0]:04d}-{prev_m[1]:02d}", "next_key": f"{next_m[0]:04d}-{next_m[1]:02d}",
        "is_current": (year, month) == (today.year, today.month),
        "events": in_month, "all_past": bool(in_month) and not ahead,
        "cards": ahead or later, "cards_later": not ahead and bool(later),
        "past_list": [e for e in in_month if e["past"]] if (year, month) < (today.year, today.month) else [],
    }


def _cells(in_month: list[dict], *, year: int, month: int, today: date) -> list[dict]:
    """The square month grid, Sunday first: one cell per day (blank cells before day 1 and after the
    last day). Like the calendar picture, a day shows its events inside the cell (picture, name, time),
    up to events_calendar.max_chips_per_day; the rest become a "+N" link to the next hidden event's page."""
    import calendar as _calendar

    by_day: dict[date, list[dict]] = {}
    for e in in_month:
        by_day.setdefault(e["date"], []).append(e)
    first_dow = (date(year, month, 1).weekday() + 1) % 7  # Sunday = 0
    from .events_calendar import calendar_cfg

    per_day = max(1, int(calendar_cfg().get("max_chips_per_day", 2)))  # same as the calendar picture
    cells: list[dict] = [{"out": True} for _ in range(first_dow)]
    for n in range(1, _calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, n)
        evs = by_day.get(d, [])
        cells.append({"out": False, "day": n, "events": evs, "shown": evs[:per_day], "more": max(0, len(evs) - per_day),
                      "today": d == today, "past": d < today, "sat": d.weekday() == 5})
    while len(cells) % 7:
        cells.append({"out": True})
    return cells


async def public_event(db, today: date, event_id: int) -> dict | None:
    """One published event, reduced like the page shows it, or None."""
    for ev in await public_events(db, today):
        if ev["id"] == event_id:
            return ev
    return None


def calendar_entry(ev: dict) -> dict:
    """What the Google Calendar link and the calendar file carry: the public title, the city or area
    only, the cleaned description and the event's own safe outside link (the same one the page shows).
    No address, chat invite, organizer or contact."""
    return {"id": ev["id"], "title": ev["title"], "date": ev["date"], "time": ev["time"] or None,
            "place": ev.get("place"), "description": ev.get("description") or None, "link": ev.get("link")}


def page_url() -> str | None:
    from . import config

    base = getattr(config, "PUBLIC_DASHBOARD_URL", "")
    return f"{base}{PATH}" if base else None
