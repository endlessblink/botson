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


async def publish_to_whatsapp(db, event_id: int, target: str, card_text: str) -> str:
    """Send the event card and its sign-up poll to WhatsApp. Returns the poll key."""
    chat_id = whatsapp_sender.resolve_chat_id(target)
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


def asks_for_event(text: str | None) -> bool:
    """True when the message asks to create an event (the caller checks the bot was tagged)."""
    cfg = member_cfg()
    low = str(text or "").lower()
    return bool(
        cfg.get("enabled")
        and _has_word(low, cfg.get("event_words"))
        and _has_word(low, cfg.get("create_words"))
    )


def _israel_now():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("Asia/Jerusalem"))


def format_card(*, title: str, description: str | None, event_date: str, event_time: str | None,
                location: str | None, link: str | None, by: str | None, image_url: str | None = None) -> str:
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
    if link or image_url or by:
        lines.append("")
    if link:
        lines.append(load_copy("member_events", "card_link", link=link))
    if image_url:
        lines.append(load_copy("member_events", "card_image", url=image_url))
    if by:
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


async def create_request_link(db, *, platform: str, requester_id: str, requester_name: str) -> dict:
    """A personal, expiring form link for a member, or a 'limit' answer."""
    import secrets
    from datetime import timedelta

    from .config import PUBLIC_DASHBOARD_URL

    cfg = member_cfg()
    minutes = int(cfg.get("link_minutes", 60))
    now = _israel_now()
    if not PUBLIC_DASHBOARD_URL:
        raise RuntimeError("PUBLIC_DASHBOARD_URL is not set")
    mine = await db.list_event_requests(platform=platform, requester_id=str(requester_id),
                                        since=now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
    live = [r for r in mine if r["status"] == "open" and r["expires_at"] > now.isoformat()]
    if live:
        token = live[0]["token"]
    elif len(mine) >= int(cfg.get("max_links_per_day", 3)):
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
                       by=req.get("requester_name"), image_url=image_url)


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


def targets_for(mode: str | None) -> tuple[str, str]:
    """(telegram group, whatsapp group) for a destination mode: 'real' or 'test'.
    No mode means the operator's default from settings."""
    mode = mode or str(member_cfg().get("default_mode") or "test")
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
    card = request_card(req)
    sent = await send_message_with_optional_cover(bot, db=db, chat_id=chat_id, text=card,
                                                  message_thread_id=topic_id, cover_path=req.get("cover_path"))
    await bot.edit_message_reply_markup(chat_id=chat_id, message_id=sent.message_id, reply_markup=rsvp_markup(event_id))
    await db.update_event(event_id, message_id=sent.message_id, tg_chat_id=chat_id, topic_id=topic_id, card_text=card)
    await db.update_event_request(request_id, status="approved", event_id=event_id)
    await db.log_activity("event", f"member event {event_id} approved: {req['title'][:60]}", target_channel=str(topic_id or chat_id))
    result = {"status": "ok", "event_id": event_id}
    if wa_target:
        try:
            await publish_to_whatsapp(db, event_id, wa_target, request_card(req, image_url=_media_url(req.get("cover_path"))))
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
