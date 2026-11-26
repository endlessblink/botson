"""The community's public front-door page ("the bulletin", `/welcome`).

Open to anyone, so it shows far less than the members' calendar (`/cal`):

- Events: the same published events as the calendar (Database.get_published_events), upcoming only,
  reduced to title, day, time, picture and a city/area. Never the stored location text, the
  description, join links, organizer or requester names, phone numbers or member ids.
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
from datetime import date, timedelta
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


_COVER = re.compile(r"^[A-Za-z0-9_./-]+\.(?:png|jpe?g|webp|gif)$", re.IGNORECASE)


def _cover_url(path: str | None) -> str | None:
    p = str(path or "").strip()
    if not p or ".." in p or p.startswith("/") or not _COVER.match(p):
        return None
    return f"/media/{p}"


async def public_events(db, today: date) -> list[dict]:
    """Upcoming published events, reduced to what a stranger may see, soonest first."""
    from .copy import load_copy

    cfg = base_cfg()
    horizon = today + timedelta(days=int(cfg.get("lookahead_days", 90)))
    show_pictures = bool(cfg.get("show_pictures", True))
    days = load_copy("events_calendar", "day_names")
    out = []
    for e in await db.get_published_events(int(os.getenv("GROUP_ID", "0") or 0)):
        try:
            day = date.fromisoformat(str(e.get("event_date")))
        except (TypeError, ValueError):
            continue
        if not (today <= day <= horizon):
            continue
        title = public_title(e.get("title"))
        if not title:
            continue
        hhmm = str(e.get("event_time") or "").strip()[:5]
        if not re.fullmatch(r"\d{1,2}:\d{2}", hhmm):
            hhmm = ""
        day_name = days[(day.weekday() + 1) % 7] if isinstance(days, list) and len(days) == 7 else ""
        out.append({
            "title": title,
            "date": day,
            "date_text": load_copy("community_page", "date", d=day.day, m=day.month),
            "when": (load_copy("community_page", "when", day=day_name, time=hhmm) if hhmm
                     else load_copy("community_page", "when_no_time", day=day_name)),
            "time": hhmm,
            "place": public_place(e.get("location")),
            "cover_url": _cover_url(e.get("cover_path")) if show_pictures else None,
        })
    out.sort(key=lambda x: (x["date"], x["time"]))
    return out[: int(cfg.get("max_events", 8))]


def columns() -> list[dict]:
    """The regular columns ("what we do"), wording from copy."""
    from .copy import load_copy

    out = []
    for c in base_cfg().get("columns") or []:
        art, key = str((c or {}).get("art") or ""), str((c or {}).get("key") or "")
        if not re.fullmatch(r"[a-z]+", art) or not re.fullmatch(r"[a-z_]+", key):
            continue
        out.append({"art": art, "title": load_copy("community_page", f"col_{key}_title"),
                    "text": load_copy("community_page", f"col_{key}_text")})
    return out


def page_url() -> str | None:
    from . import config

    base = getattr(config, "PUBLIC_DASHBOARD_URL", "")
    return f"{base}{PATH}" if base else None
