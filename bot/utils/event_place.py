"""Where an event happens: in person (a place) or online (a meeting link).

Stored canonical: place_type is "in_person" or "online"; an online event keeps its meeting
address in online_link and its in-person place, if any was typed, in location (so switching
back in the form loses nothing). The platform's name (Google Meet, Zoom...) is recognised from
the link's host with the list in settings.yaml `member_events.online_platforms`.

The published text never needs the link to say where: place_label() gives
"אונליין · Google Meet" (wording in copy.member_events). Public pages show only that label;
the meeting link itself is for members, in the group post.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

IN_PERSON, ONLINE = "in_person", "online"


def _platforms() -> list[dict]:
    from .config import settings_shared

    return (((settings_shared() or {}).get("member_events") or {}).get("online_platforms")) or []


def _host(url: str) -> str:
    try:
        return (urlsplit(str(url or "").strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def platform_names() -> list[str]:
    return [str(p.get("name") or "").strip() for p in _platforms()]


def platform_for(url: str | None) -> str | None:
    """The meeting platform's name for a link (subdomains count), else None."""
    host = _host(url or "")
    if not host:
        return None
    for p in _platforms():
        for h in p.get("hosts") or []:
            h = str(h).lower().strip(".")
            if h and (host == h or host.endswith("." + h)):
                return str(p.get("name") or "").strip() or None
    return None


def is_meeting_link(text: str | None) -> bool:
    """A bare web address of a known meeting platform (what people paste into the place field)."""
    t = str(text or "").strip()
    return bool(re.fullmatch(r"https?://\S+", t)) and platform_for(t) is not None


def normalize(fields: dict) -> dict:
    """place_type made canonical; a bare meeting address typed as the place becomes the online link."""
    out = dict(fields)
    kind = str(out.get("place_type") or "").strip()
    kind = ONLINE if kind == ONLINE else IN_PERSON
    online = str(out.get("online_link") or "").strip()
    location = str(out.get("location") or "").strip()
    if kind == IN_PERSON and is_meeting_link(location):
        kind, online, location = ONLINE, online or location, ""
    if kind == ONLINE and location and location == online:
        location = ""
    out.update(place_type=kind, online_link=online, location=location)
    return out


def place_label(place_type: str | None, online_link: str | None, location: str | None) -> str | None:
    """What a post says under "where": the place, or "online · <platform>"."""
    from .copy import load_copy

    if place_type == ONLINE:
        platform = platform_for(online_link)
        return (load_copy("member_events", "place_online_platform", platform=platform) if platform
                else load_copy("member_events", "place_online"))
    location = str(location or "").strip()
    if is_meeting_link(location):  # an older event with a bare meeting address as its place
        return place_label(ONLINE, location, None)
    return location or None
