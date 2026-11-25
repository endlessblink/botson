"""Events shared between the Telegram group and the WhatsApp group.

An event created on the dashboard can also go to WhatsApp: the event card as
text, then a sign-up poll. Botty receives WhatsApp poll votes from WAHA and
forwards them to Botson, which stores them per event. The Telegram card,
reminders and the dashboard then show WhatsApp sign-ups as counts only —
WhatsApp members' names never appear in the Telegram group.
"""
from __future__ import annotations

import logging

from . import whatsapp_sender
from .copy import load_copy

logger = logging.getLogger(__name__)

NAMES_MARKER = "\n\n✅ מגיעים"  # noqa: hardcoded-content (marker of the existing Telegram RSVP names block)


def stanza_of(msg_id: str | None) -> str:
    """WhatsApp message key without the chat/sender parts (same as Botty's stanzaOf)."""
    value = str(msg_id or "")
    parts = value.split("_")
    return parts[2] if len(parts) >= 3 else value


def poll_options() -> list[str]:
    return [load_copy("whatsapp", k) for k in ("event_poll_yes", "event_poll_maybe", "event_poll_no")]


def status_for_option(option: str | None) -> str | None:
    """Map a WhatsApp poll answer to 'yes' / 'maybe'; anything else clears the sign-up."""
    yes, maybe, _no = poll_options()
    option = str(option or "").strip()
    if option == yes:
        return "yes"
    if option == maybe:
        return "maybe"
    return None


async def publish_to_whatsapp(db, event_id: int, target: str, card_text: str, image_url: str | None = None) -> str:
    """Send the event card (with its picture, when there is one) and its sign-up poll
    to WhatsApp. Returns the poll key."""
    chat_id = whatsapp_sender.resolve_chat_id(target)
    sent = None
    if image_url:
        try:
            sent = await whatsapp_sender.send_image(chat_id, image_url, card_text)
        except Exception:
            logger.warning("member_events: WhatsApp image failed, sending the text only", exc_info=True)
    if not sent:
        await whatsapp_sender.send_text(chat_id, card_text)
    poll_id = await whatsapp_sender.send_poll(chat_id, load_copy("whatsapp", "event_poll_question"), poll_options())
    stanza = stanza_of(poll_id)
    if not stanza:
        raise RuntimeError("WhatsApp did not return a poll id")
    await db.set_event_whatsapp_poll(event_id, target, stanza)
    return stanza


async def whatsapp_line(db, event: dict) -> str:
    """The WhatsApp count line for a shared event, or '' for a Telegram-only one."""
    if not event or not event.get("wa_target"):
        return ""
    counts = await db.get_event_whatsapp_counts(event["id"])
    return load_copy("whatsapp", "event_whatsapp_counts", yes=counts["yes"], maybe=counts["maybe"])


def _wa_marker() -> str:
    template = load_copy("whatsapp", "event_whatsapp_counts")
    return "\n\n" + template.split("{", 1)[0]


def strip_rsvp_blocks(text: str) -> str:
    """The card without the sign-up blocks this module and the RSVP handler append."""
    text = str(text or "")
    for marker in (NAMES_MARKER, _wa_marker()):
        text = text.split(marker, 1)[0]
    return text


# ── Member-created events ─────────────────────────────────────────────────


def member_cfg() -> dict:
    from .config import get_settings

    return (get_settings() or {}).get("member_events") or {}


def _has_word(low: str, words) -> bool:
    import re

    for w in words or []:
        w = str(w).lower()
        if w.isascii():
            if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", low):
                return True
        elif w in low:
            return True
    return False


def asks_for_event(text: str | None, *, private: bool = False) -> bool:
    """True when the message asks to create an event (the caller checks the bot was tagged)."""
    cfg = member_cfg()
    low = str(text or "").lower()
    return bool(
        cfg.get("enabled")
        and _has_word(low, cfg.get("event_words"))
        and (private or _has_word(low, cfg.get("create_words")))
    )


def _israel_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Jerusalem"))


def format_card(*, title: str, description: str | None, event_date: str, event_time: str | None,
                location: str | None, link: str | None, by: str | None, image_url: str | None = None,
                organizer: str | None = None) -> str:
    from datetime import date

    d = date.fromisoformat(event_date)
    days = load_copy("member_events", "day_names")
    day = days[(d.weekday() + 1) % 7] if isinstance(days, list) and len(days) == 7 else ""
    lines = [f"📅 {title}",
             load_copy("member_events", "card_when", day=day, date=f"{d.day}.{d.month}", time=event_time or "")]
    if location:
        lines.append(load_copy("member_events", "card_where", location=location))
    if description:
        lines += ["", description]
    organizer = str(organizer or "").strip()
    if link or image_url or by or organizer:
        lines.append("")
    if link:
        lines.append(load_copy("member_events", "card_link", link=link))
    if image_url:
        lines.append(load_copy("member_events", "card_image", url=image_url))
    # Opened for someone else: the post names the person responsible, not the opener.
    if organizer:
        lines.append(load_copy("member_events", "card_organizer", name=organizer))
    elif by:
        lines.append(load_copy("member_events", "card_by", name=by))
    return "\n".join(lines)


def norm_id(raw) -> str:
    """One spelling per person: '972501234567@c.us', '+972 50-123-4567' and a bare
    Telegram user id all reduce to digits (or the lower-cased id when it isn't a number)."""
    head = str(raw or "").split("@")[0].strip().lower()
    digits = "".join(ch for ch in head if ch.isdigit())
    return digits if digits and len(digits) >= len([c for c in head if c.isalnum()]) else head


async def is_trusted(db, platform: str, requester_id: str) -> bool:
    """Trusted members publish without approval, up to a daily cap; past it they go
    through the normal one-tap approval."""
    if not await db.is_trusted_creator(platform, norm_id(requester_id)):
        return False
    since = _israel_now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    mine = await db.list_event_requests(platform=platform, requester_id=str(requester_id), since=since)
    published = [r for r in mine if r["status"] in ("approved", "publishing")]
    return len(published) < int(member_cfg().get("trusted_max_per_day", 5))


async def add_trusted_by_phone(db, phone: str, name: str) -> dict:
    """Trust a WhatsApp member by phone number. Botty may see the same person as
    a number or as a hidden WhatsApp id, so both spellings are stored."""
    from .whatsapp_sender import lid_for_phone

    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if digits.startswith("0"):
        digits = "972" + digits[1:]
    if len(digits) < 10:
        raise ValueError("phone number looks too short")
    ids = [digits]
    try:
        lid = await lid_for_phone(digits)
        if lid:
            ids.append(norm_id(lid))
    except Exception:
        logger.warning("member_events: could not look up the WhatsApp id for %s", digits, exc_info=True)
    for i in ids:
        await db.add_trusted_creator("whatsapp", i, name, _israel_now().isoformat())
    return {"phone": digits, "ids": ids}


async def _is_privileged(db, platform: str, requester_id: str) -> bool:
    """The operator and trusted co-admins are not held to the members' daily form limit."""
    from .config import ADMIN_IDS

    if platform == "telegram" and str(requester_id).isdigit() and int(requester_id) in ADMIN_IDS:
        return True
    return await db.is_trusted_creator(platform, norm_id(requester_id))


async def person_ids(requester: str) -> list[str]:
    """Every known spelling of a WhatsApp person (phone and hidden id), their own first."""
    from . import whatsapp_sender

    own = norm_id(requester)
    ids = [own]
    try:
        other = await (whatsapp_sender.phone_for_lid(own) if str(requester).endswith("@lid")
                       else whatsapp_sender.lid_for_phone(own))
        if other and norm_id(other) not in ids:
            ids.append(norm_id(other))
    except Exception:
        logger.warning("member_events: could not look up the other WhatsApp id", exc_info=True)
    return ids


async def is_known_member(db, ids: list[str]) -> bool:
    """A trusted co-admin, or someone in one of Botson's WhatsApp groups. Raises when
    no configured group could be read and the person was not found (never guess)."""
    from . import whatsapp_sender

    for i in ids:
        if await db.is_trusted_creator("whatsapp", i):
            return True
    failure: Exception | None = None
    for target in ("whatsapp", "whatsapp_test"):
        try:
            members = await whatsapp_sender.group_member_ids(whatsapp_sender.resolve_chat_id(target))
        except whatsapp_sender.WhatsAppNotConfigured:
            continue
        except Exception as exc:
            failure = exc
            continue
        if {norm_id(m) for m in members} & set(ids):
            return True
    if failure:
        raise RuntimeError("could not read the WhatsApp group members") from failure
    return False


def valid_link(link: str) -> bool:
    import re
    from urllib.parse import urlsplit

    if not re.fullmatch(r"https?://\S+", link):
        return False
    try:
        return bool(urlsplit(link).hostname)
    except ValueError:
        return False


def clean_link(link: str) -> str:
    """The same address without what makes it long: a site's own short form where
    `member_events.link_cleaning` has a rule, and tracking parameters. Text only, nothing is
    opened. Anything unclear, or a result that isn't a valid link: the link as given."""
    import re
    from fnmatch import fnmatchcase
    from urllib.parse import unquote_plus, urlsplit

    cfg = member_cfg().get("link_cleaning") or {}
    try:
        if not valid_link(link):
            return link
        parts = urlsplit(link)
        host = parts.netloc.lower()  # with a port or a user name no site rule applies
        params = [x for x in parts.query.split("&") if x]
        name = lambda x: unquote_plus(x.split("=", 1)[0]).lower()
        for rule in cfg.get("site_rules") or []:
            found = re.fullmatch(rule["path"], parts.path) if host in rule["hosts"] else None
            if found:
                kept = [x for x in params if name(x) in (rule.get("keep_params") or [])]
                out = rule["to"].format(*found.groups()) + ("?" + "&".join(kept) if kept else "")
                return out if valid_link(out) else link
        drop = [str(x) for x in cfg.get("tracking_params") or []]
        for entry in cfg.get("site_tracking_params") or []:
            if host in entry["hosts"]:
                drop += entry["params"]
        kept = [x for x in params if not any(fnmatchcase(name(x), d) for d in drop)]
        base, _, fragment = link.partition("#")
        base, _, query = base.partition("?")
        # Untouched parameters keep their exact spelling and order.
        query = query if len(kept) == len(params) else "&".join(kept)
        out = base + (f"?{query}" if query else "") + (f"#{fragment}" if fragment else "")
        return out if valid_link(out) else link
    except Exception:
        logger.warning("member_events: could not clean a link, keeping it as it is", exc_info=True)
        return link


def needs_short_link(link: str) -> bool:
    """Last resort: the cleaned link is still too long to sit in a post."""
    from .config import PUBLIC_DASHBOARD_URL

    cfg = member_cfg().get("short_link") or {}
    short_len = len(f"{PUBLIC_DASHBOARD_URL}/l/") + int(cfg.get("code_chars", 8))
    return bool(cfg.get("enabled") and PUBLIC_DASHBOARD_URL and valid_link(link)
                and len(link) > max(int(cfg.get("min_chars", 120)), short_len))


async def preview_link(db, req: dict, link: str) -> str:
    """The link as the post will carry it, for an open form: cleaned, and when that is still too
    long a real, working /l/<code>. Past the form's cap, or on any trouble: the cleaned link."""
    import secrets
    import string

    from .config import PUBLIC_DASHBOARD_URL

    link = clean_link(link)
    if not needs_short_link(link):
        return link
    cfg = member_cfg().get("short_link") or {}
    try:
        for _ in range(3):
            # Letters and digits only: a code ending in "-" or "_" can fall off the address.
            code = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(int(cfg.get("code_chars", 8))))
            code = await db.draft_short_link(request_id=req["id"], url=link, code=code, now=_israel_now().isoformat(),
                                             cap=int(cfg.get("max_per_form", 5)))
            if code:
                return f"{PUBLIC_DASHBOARD_URL}/l/{code}"
    except Exception:
        logger.warning("member_events: could not shorten a link, showing it as it is", exc_info=True)
    return link


async def post_link(db, link: str | None) -> str | None:
    """The link for the published post: exactly what the form preview showed. Codes are never
    made here, only looked up and kept for good."""
    from .config import PUBLIC_DASHBOARD_URL

    link = clean_link(link) if link else link
    if not link or not needs_short_link(link):
        return link
    try:
        code = await db.publish_short_link(link)
        return f"{PUBLIC_DASHBOARD_URL}/l/{code}" if code else link
    except Exception:
        logger.warning("member_events: could not look up a short link, posting it as it is", exc_info=True)
        return link


async def short_link_target(db, code: str) -> str | None:
    from datetime import timedelta

    hours = float((member_cfg().get("short_link") or {}).get("draft_hours", 24))
    url = await db.short_link_url(code, (_israel_now() - timedelta(hours=hours)).isoformat())
    return url if url and valid_link(url) else None


def clean_form_fields(data: dict) -> tuple[dict, dict]:
    """The event form's trimming and checks, shared by the form submit and the picture
    reader. Returns (fields, errors); an error value is the copy key to show."""
    import re
    import unicodedata
    from datetime import date

    cfg = member_cfg()
    # Invisible marks picked up while copying would break the address.
    link = "".join(ch for ch in str(data.get("link") or "") if unicodedata.category(ch) != "Cf").strip()
    fields = {
        "title": str(data.get("title") or "").strip(),
        "event_date": str(data.get("event_date") or "").strip(),
        "event_time": str(data.get("event_time") or "").strip(),
        "location": str(data.get("location") or "").strip()[:120],
        "description": str(data.get("description") or "").strip()[: int(cfg.get("max_description_chars", 400))],
        "link": link,
        # Optional: who is responsible, when the form is opened for someone else. One line, a name only.
        "organizer": " ".join(str(data.get("organizer") or "").split())[:60],
    }
    errors = {}
    if not fields["title"] or len(fields["title"]) > int(cfg.get("max_title_chars", 80)):
        errors["title"] = "form_error_title"
    try:
        if date.fromisoformat(fields["event_date"]) < _israel_now().date():
            raise ValueError
    except ValueError:
        errors["event_date"] = "form_error_date"
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", fields["event_time"]):
        errors["event_time"] = "form_error_time"
    # Too long is refused, never cut: half an address is a dead link.
    if link and (len(link) > int(cfg.get("max_link_chars", 2000)) or not valid_link(link)):
        errors["link"] = "form_error_link"
    return fields, errors


async def create_request_link(db, *, platform: str, requester_id: str, requester_name: str,
                              fresh: bool = False, unlimited: bool = False) -> dict:
    """A personal, expiring form link for a member, or a 'limit' answer.

    fresh: always a new form (each picture fills its own). unlimited: the operator."""
    import secrets
    from datetime import timedelta

    from .config import PUBLIC_DASHBOARD_URL

    cfg = member_cfg()
    minutes = int(cfg.get("link_minutes", 60))
    now = _israel_now()
    if not str(requester_name or "").strip():  # the card names who opened it: use the name the operator gave them
        for t in await db.list_trusted_creators():
            if t["platform"] == platform and t["user_id"] == norm_id(requester_id) and t.get("name"):
                requester_name = t["name"]
                break
    if not PUBLIC_DASHBOARD_URL:
        raise RuntimeError("PUBLIC_DASHBOARD_URL is not set")
    mine = await db.list_event_requests(platform=platform, requester_id=str(requester_id),
                                        since=now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
    live = [r for r in mine if r["status"] == "open" and r["expires_at"] > now.isoformat()
            # a form filled from a picture belongs to that picture: a plain request gets an empty one
            and not r.get("prefill") and not fresh]
    if live:
        token = live[0]["token"]
        name = str(requester_name or "").strip()[:60]
        if name and name != live[0].get("requester_name"):  # the card says who opened it: keep that current
            await db.update_event_request(live[0]["id"], requester_name=name)
    elif len(mine) >= int(cfg.get("max_links_per_day", 3)) and not unlimited and not await _is_privileged(db, platform, requester_id):
        return {"status": "limit", "text": load_copy("member_events", "limit_reply")}
    else:
        token = secrets.token_urlsafe(16)
        await db.create_event_request(
            token=token, platform=platform, requester_id=str(requester_id),
            requester_name=str(requester_name or "").strip()[:60],
            created_at=now.isoformat(), expires_at=(now + timedelta(minutes=minutes)).isoformat(),
        )
    url = f"{PUBLIC_DASHBOARD_URL}/e/{token}"
    return {"status": "ok", "url": url, "minutes": minutes,
            "text": load_copy("member_events", "link_reply", minutes=minutes), "button": load_copy("member_events", "link_button")}


def request_is_open(req: dict | None) -> bool:
    return bool(req and req["status"] == "open" and req["expires_at"] > _israel_now().isoformat())


def request_card(req: dict, *, image_url: str | None = None) -> str:
    return format_card(title=req["title"], description=req.get("description"), event_date=req["event_date"],
                       event_time=req.get("event_time"), location=req.get("location"), link=req.get("link"),
                       by=req.get("requester_name"), image_url=image_url, organizer=req.get("organizer"))


def _media_url(cover_path: str | None) -> str | None:
    from .config import PUBLIC_DASHBOARD_URL

    return f"{PUBLIC_DASHBOARD_URL}/media/{cover_path}" if cover_path and PUBLIC_DASHBOARD_URL else None


def rsvp_markup(event_id: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    yes, maybe, _no = poll_options()
    return InlineKeyboardMarkup([[InlineKeyboardButton(yes, callback_data=f"rsvp_yes_{event_id}"),
                                  InlineKeyboardButton(maybe, callback_data=f"rsvp_maybe_{event_id}")]])


def decision_markup(request_id: int):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    return InlineKeyboardMarkup([
        [InlineKeyboardButton(load_copy("member_events", "approve_button"), callback_data=f"evreq_ok_{request_id}_real")],
        [InlineKeyboardButton(load_copy("member_events", "approve_test_button"), callback_data=f"evreq_ok_{request_id}_test")],
        [InlineKeyboardButton(load_copy("member_events", "approve_trust_button"), callback_data=f"evreq_ok_{request_id}_trust")],
        [InlineKeyboardButton(load_copy("member_events", "reject_button"), callback_data=f"evreq_no_{request_id}")],
    ])


async def notify_admins_of_request(bot, req: dict, *, auto_published: bool = False) -> int:
    """DM each admin the submitted event with approve / reject buttons, or a
    plain note when a trusted member's event already went out."""
    from .config import ADMIN_IDS

    platform = load_copy("member_events", f"platform_{req['platform']}")
    text = load_copy("member_events", "admin_request", name=req.get("requester_name") or "?",
                     platform=platform, card=request_card(req))
    if auto_published:
        text = load_copy("member_events", "auto_published_note", name=req.get("requester_name") or "?",
                         platform=platform, card=request_card(req))
    delivered = 0
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, reply_markup=None if auto_published else decision_markup(req["id"]),
                                   disable_web_page_preview=True)
            delivered += 1
        except Exception as exc:
            logger.error("member_events: could not DM admin %s: %s", admin_id, exc)
    return delivered


def _templates_file():
    from pathlib import Path

    from . import config

    return Path(config.DB_PATH).parent / "member_events_templates.json"


def _tname(raw) -> str:
    """Template names compare without surrounding spaces, repeated spaces or letter case."""
    return " ".join(str(raw or "").split()).casefold()


def _read_templates() -> dict:
    import json

    try:
        data = json.loads(_templates_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing or damaged file: start clean, never crash the form
        data = {}
    if isinstance(data, list):  # first version stored just the list
        data = {"saved": data}
    if not isinstance(data, dict):
        data = {}
    saved = data.get("saved") if isinstance(data.get("saved"), list) else []
    removed = data.get("removed") if isinstance(data.get("removed"), list) else []
    return {"saved": [t for t in saved if isinstance(t, dict) and _tname(t.get("name"))],
            "removed": [_tname(n) for n in removed if _tname(n)]}


def _write_templates(data: dict) -> None:
    import json
    import os

    path = _templates_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # a crash mid-write never leaves a half file


def load_templates() -> list[dict]:
    """Templates from settings.yaml plus the ones people saved from the form, minus the
    ones they deleted (kept in the data folder so a deploy never brings them back).
    One template per name: a saved copy replaces the shipped one."""
    data = _read_templates()
    saved_names = {_tname(t["name"]) for t in data["saved"]}
    shipped = [t for t in member_cfg().get("templates") or [] if _tname(t.get("name")) not in saved_names]
    seen: set = set()
    out = []
    for t in shipped + data["saved"]:
        key = _tname(t.get("name"))
        if not key or key in data["removed"] or key in seen:
            continue
        seen.add(key)
        out.append(t)
    return out


def save_template(*, title: str, description: str = "", location: str = "", time: str = "", link: str = "",
                  replaces: str = "") -> bool:
    """Remember a form as a reusable template named after its title. Same name replaces it,
    and `replaces` updates the template the form was started from even if it was renamed.
    Returns False when there is nothing worth saving (no title)."""
    import re

    cfg = member_cfg()
    name = " ".join(str(title or "").split())[: int(cfg.get("max_title_chars", 80))]
    if not name:
        return False
    link = str(link or "").strip()
    data = _read_templates()
    if replaces and _tname(replaces) != _tname(name):
        data["saved"] = [t for t in data["saved"] if _tname(t["name"]) != _tname(replaces)]
        if _tname(replaces) not in data["removed"]:
            data["removed"].append(_tname(replaces))
    entry = {"name": name, "title": name,
             "description": str(description or "").strip()[: int(cfg.get("max_description_chars", 400))],
             "location": str(location or "").strip()[:120],
             "time": time if re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(time or "")) else "",
             "link": link if len(link) <= int(cfg.get("max_link_chars", 2000)) else ""}
    data["saved"] = [t for t in data["saved"] if _tname(t["name"]) != _tname(name)] + [entry]
    data["saved"] = data["saved"][-int(cfg.get("max_saved_templates", 30)):]
    data["removed"] = [n for n in data["removed"] if n != _tname(name)]
    _write_templates(data)
    return True


def delete_template(name: str) -> None:
    key = _tname(name)
    if not key:
        return
    data = _read_templates()
    data["saved"] = [t for t in data["saved"] if _tname(t["name"]) != key]
    if key not in data["removed"]:
        data["removed"].append(key)
    _write_templates(data)


def _mode_file():
    from pathlib import Path

    from . import config

    return Path(config.DB_PATH).parent / "member_events_mode.txt"


def default_mode() -> str:
    """Where trusted members' events go: the operator's switch on the Events page
    (kept in the data folder so a deploy never resets it), else settings.yaml."""
    try:
        saved = _mode_file().read_text(encoding="utf-8").strip()
        if saved in ("real", "test"):
            return saved
    except OSError:
        pass
    return "real" if str(member_cfg().get("default_mode")) == "real" else "test"


def set_default_mode(mode: str) -> None:
    if mode not in ("real", "test"):
        raise ValueError("mode must be real or test")
    path = _mode_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(mode, encoding="utf-8")


async def can_publish_directly(db, req: dict) -> bool:
    """True for trusted co-admins (and the operator): their form publishes without approval."""
    from .config import ADMIN_IDS

    if req["platform"] == "telegram" and str(req["requester_id"]).isdigit() and int(req["requester_id"]) in ADMIN_IDS:
        return True
    return await is_trusted(db, req["platform"], req["requester_id"])


def apply_mode_command(text: str | None) -> str | None:
    """A private "go live" / "test mode" / "which mode" message from an allowed person.
    Returns the reply to send, or None when the text is not a mode command."""
    words = member_cfg().get("mode_words") or {}
    t = (text or "").lower()
    has = lambda key: any(str(w).lower() in t for w in words.get(key) or [])
    for mode in ("real", "test"):
        if has(mode):
            set_default_mode(mode)
            return load_copy("member_events", f"mode_set_{mode}")
    if has("status"):
        return load_copy("member_events", f"mode_status_{default_mode()}")
    return None


def targets_for(mode: str | None) -> tuple[str, str]:
    """(telegram group, whatsapp group) for a destination mode: 'real' or 'test'.
    No mode means the operator's default from settings."""
    mode = mode or default_mode()
    return ("test", "whatsapp_test") if mode == "test" else ("main", "whatsapp")


async def approve_request(bot, db, request_id: int, mode: str | None = None) -> dict:
    """Publish an approved member event to Telegram (card + sign-up buttons) and
    WhatsApp (card + sign-up poll). Safe against double taps."""
    import os

    from ..handlers.calendar import send_message_with_optional_cover

    req = await db.get_event_request(request_id=request_id)
    if not req or not await db.update_event_request(request_id, expect_status="submitted",
                                                    status="publishing", decided_at=_israel_now().isoformat()):
        return {"status": "already_decided"}
    tg_target, wa_target = targets_for(mode)
    event_id = await db.create_event(
        title=req["title"], description=req.get("description") or "", event_date=req["event_date"],
        event_time=req.get("event_time"), location=req.get("location"), created_by=0,
        cover_path=req.get("cover_path"),
    )
    chat_id = int(os.getenv("TEST_GROUP_ID" if tg_target == "test" else "GROUP_ID", "0"))
    topic_id = None
    if tg_target != "test":
        routing = await db.get_handler_routing("events_publish")
        topic_id = routing["play_topic_id"] if routing else None
    # Only the posts carry the cleaned address; the request keeps the original link.
    posted = {**req, "link": await post_link(db, req.get("link"))}
    card = request_card(posted)
    sent = await send_message_with_optional_cover(bot, db=db, chat_id=chat_id, text=card,
                                                  message_thread_id=topic_id, cover_path=req.get("cover_path"))
    await bot.edit_message_reply_markup(chat_id=chat_id, message_id=sent.message_id, reply_markup=rsvp_markup(event_id))
    await db.update_event(event_id, message_id=sent.message_id, tg_chat_id=chat_id, topic_id=topic_id, card_text=card)
    await db.update_event_request(request_id, status="approved", event_id=event_id)
    await db.log_activity("event", f"member event {event_id} approved: {req['title'][:60]}", target_channel=str(topic_id or chat_id))
    result = {"status": "ok", "event_id": event_id}
    if wa_target:
        try:
            await publish_to_whatsapp(db, event_id, wa_target, request_card(posted), _media_url(req.get("cover_path")))
        except Exception as exc:
            logger.exception("member_events: WhatsApp publish failed for event %s", event_id)
            result.update(status="partial", whatsapp_error=str(exc)[:200])
    return result


async def reject_request(db, request_id: int) -> bool:
    return await db.update_event_request(request_id, expect_status="submitted", status="rejected",
                                         decided_at=_israel_now().isoformat())


def card_with_rsvps(base: str, yes_names: list[str], maybe_names: list[str], wa_line: str) -> str:
    text = strip_rsvp_blocks(base)
    if yes_names or maybe_names:
        maybe_label = load_copy("whatsapp", "event_rsvp_maybe_label")
        text += (
            f"{NAMES_MARKER} ({len(yes_names)}): {', '.join(yes_names) if yes_names else '—'}\n"
            f"{maybe_label} ({len(maybe_names)}): {', '.join(maybe_names) if maybe_names else '—'}"
        )
    if wa_line:
        text += f"\n\n{wa_line}"
    return text


async def trust_requester(db, request_id: int) -> None:
    """Remember the person behind a request so their next events skip approval."""
    req = await db.get_event_request(request_id=request_id)
    if req:
        await db.add_trusted_creator(req["platform"], norm_id(req["requester_id"]), req.get("requester_name") or "",
                                     _israel_now().isoformat())
