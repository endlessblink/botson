"""Showing the month calendar picture in a chat, on request (Telegram and WhatsApp).

One set of rules for both platforms (limits in settings.yaml `events_calendar.share`,
wording in `copy.calendar_share`):

- A group gets the picture at most once every `group_every_days` days. Asked sooner,
  the bot answers briefly, pointing at the picture already posted, and suggests a
  private copy. The operator is never held back.
- "Already posted" is only said when it is true: each post remembers a version of
  the month it showed (the month plus every published event in it). When an event is
  added, changed or removed since, or a different month is asked for, the version no
  longer matches and a fresh picture is posted even inside the N days.
- In a private chat any group member may ask at any time, up to `private_per_day`
  pictures a day (Israel day). The operator is not capped. Membership is checked by
  the caller (Telegram: the group's member list; WhatsApp: Botson's member check).

State lives in the database (calendar_posts, calendar_private_sends), so it survives restarts.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime, timedelta

from .copy import load_copy

logger = logging.getLogger(__name__)


def share_cfg() -> dict:
    from .config import get_settings

    return ((get_settings() or {}).get("events_calendar") or {}).get("share") or {}


def _norm(text: str | None) -> str:
    # Abbreviation marks typed different ways (config `quote_marks`) all count as ".
    out = str(text or "").lower()
    for mark in share_cfg().get("quote_marks") or []:
        out = out.replace(str(mark), '"')
    return out


def _has_any(text: str, words) -> bool:
    """Whole words: Latin words alone; a Hebrew word may carry one or two prefix
    letters (config `word_prefixes`), never a suffix."""
    for w in words or []:
        w = _norm(w).strip()
        if not w:
            continue
        if w.isascii():
            pattern = rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])"
        else:
            prefixes = re.escape(str(share_cfg().get("word_prefixes") or ""))
            lead = rf"[{prefixes}]{{0,2}}" if prefixes else ""
            pattern = rf"(?<![\w\"]){lead}{re.escape(w)}(?![\w\"])"
        if re.search(pattern, text):
            return True
    return False


def asks_for_calendar(text: str | None) -> bool:
    """True when the message asks to see the schedule. A request to create an event
    (an event word plus a create word) always goes to the event form instead."""
    from .shared_events import asks_for_event

    cfg = share_cfg()
    if not cfg.get("enabled"):
        return False
    low = _norm(text)
    return _has_any(low, cfg.get("words")) and not asks_for_event(text)


def wants_next_month(text: str | None) -> bool:
    return _has_any(_norm(text), share_cfg().get("next_month_words"))


def month_for(next_month: bool) -> str:
    from .events_calendar import local_now

    now = local_now()
    year, month = now.year, now.month
    if next_month:
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return f"{year:04d}-{month:02d}"


def caption(month: str) -> str:
    year, mon = (int(x) for x in month.split("-"))
    names = load_copy("events_calendar", "month_names")
    name = names[mon - 1] if isinstance(names, list) and len(names) == 12 else month
    return load_copy("calendar_share", "caption", month=name, year=year)


def _more_line(month: str) -> tuple[str, str] | None:
    """(words, address) of the caption's second line, leading to the full calendar page."""
    from .calendar_page import page_url

    url = page_url(month)
    return (load_copy("calendar_share", "caption_more"), url) if url else None


def telegram_caption(month: str) -> tuple[str, list]:
    """Caption + the clickable words leading to the page (no parse mode)."""
    more = _more_line(month)
    return text_with_link(caption(month), *more) if more else (caption(month), [])


def whatsapp_caption(month: str) -> str:
    """Caption + the words, then the bare address on its own line (WhatsApp links only addresses)."""
    more = _more_line(month)
    return f"{caption(month)}\n{more[0]}\n{more[1]}" if more else caption(month)


async def month_version(db, month: str) -> str:
    """Changes whenever what the month's picture shows about events changes."""
    events = await db.get_published_events(int(os.getenv("GROUP_ID", "0") or 0))
    shown = [[e.get("id"), e.get("title"), e.get("event_date"), e.get("event_time"), e.get("location"),
              e.get("cover_path"), e.get("organizer")]
             for e in events if str(e.get("event_date") or "").startswith(month)]
    raw = json.dumps([month, shown], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _today() -> str:
    from .events_calendar import local_now

    return local_now().date().isoformat()


async def decide(db, *, platform: str, chat_id: str, private: bool, operator: bool,
                 user_ids: list[str], month: str) -> dict:
    """What to do with one request: {"action": "post"|"already"|"limit", ...}.

    "post" carries the version to record after sending; "already" carries the
    earlier message (message_id, thread_id) and the reply text; "limit" the reply text.
    """
    from .events_calendar import local_now

    cfg = share_cfg()
    version = await month_version(db, month)
    if private:
        cap = int(cfg.get("private_per_day", 3))
        if not operator and await db.calendar_private_count(platform=platform, user_ids=user_ids, day=_today()) >= cap:
            return {"action": "limit", "text": load_copy("calendar_share", "private_limit")}
        return {"action": "post", "version": version, "month": month}
    last = await db.get_calendar_post(platform, chat_id)
    if last and not operator and last.get("version") == version:
        try:
            posted = datetime.fromisoformat(last["posted_at"])
        except (TypeError, ValueError):
            posted = None
        days = float(cfg.get("group_every_days", 3))
        if posted and local_now() - posted < timedelta(days=days):
            return {"action": "already", "message_id": last["message_id"], "thread_id": last.get("thread_id"),
                    "text": load_copy("calendar_share", "already_posted")}
    return {"action": "post", "version": version, "month": month}


async def record(db, *, platform: str, chat_id: str, private: bool, user_id: str, operator: bool,
                 message_id, thread_id: int | None, month: str, version: str) -> None:
    """Remember a picture that went out: the group's latest post, or one private copy."""
    from .events_calendar import local_now

    if private:
        if not operator:
            await db.add_calendar_private_send(platform=platform, user_id=user_id, day=_today())
        return
    await db.set_calendar_post(platform=platform, chat_id=chat_id, message_id=str(message_id), thread_id=thread_id,
                               month=month, version=version, posted_at=local_now().isoformat())


async def picture(db, month: str) -> bytes:
    from ..handlers.calendar import _media_dir
    from .events_calendar import month_png

    return await month_png(db, month, _media_dir())


# ── Telegram ────────────────────────────────────────────────

async def telegram_events_topic(db) -> int | None:
    routing = await db.get_handler_routing("events_publish")
    return routing["play_topic_id"] if routing else None


async def send_telegram_picture(bot, db, *, chat_id: int, thread_id: int | None, png: bytes, text: str,
                                reply_to: int | None = None, entities: list | None = None):
    """Through the topic guard, like every Telegram send."""
    from .topic_guard import safe_send

    extra = {"reply_to_message_id": reply_to} if reply_to else {}
    if entities:
        extra["caption_entities"] = entities
    return await safe_send(bot, db, "send_photo", chat_id=chat_id, message_thread_id=thread_id,
                           photo=png, caption=text, **extra)


def telegram_message_link(chat_id: int, message_id, thread_id: int | None = None) -> str | None:
    """t.me link members can open; only for supergroups (-100…)."""
    raw = str(chat_id)
    if not raw.startswith("-100"):
        return None
    return f"https://t.me/c/{raw[4:]}/{message_id}"


def text_with_link(text: str, words: str, url: str | None) -> tuple[str, list]:
    """text + a new line holding clickable words (no parse mode: nothing else becomes a link)."""
    from telegram import MessageEntity

    if not url:
        return text, []
    full = f"{text}\n{words}"
    offset = len(f"{text}\n".encode("utf-16-le")) // 2
    length = len(words.encode("utf-16-le")) // 2
    return full, [MessageEntity(type=MessageEntity.TEXT_LINK, offset=offset, length=length, url=url)]
