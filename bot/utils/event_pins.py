"""Pin event posts in the Telegram group around their date, silently.

Rules (settings.yaml `event_pins`):
- A published event's post is pinned from `days_before` days before its date until the end of
  its date (Israel time), and unpinned the day after. Several events may be pinned at once.
- Only the bot's own event posts of events really published to the community (the calendar's
  rule, Database.get_published_events). Test-group posts only while the live / test switch is
  on test and `test_group_in_test_mode` is on. A deleted or cancelled event is unpinned.
- The operator may pin or unpin one by hand (dashboard); that choice holds until the event is
  over. `enabled: false` stops the automatic pinning; manual choices still apply.
- Pins are kept in their own table (event_pins), apart from the general "replace the previous
  pin" ledger (bot_pins in pin_manager.py), so neither rule ever removes the other's pins.
- Every run compares what should be pinned with what is recorded, so running it twice changes
  nothing and a restart picks up where it left off.

WhatsApp (settings `event_pins.whatsapp`, sync_whatsapp below) follows the same window and the
same manual choice, with its own state (table event_wa_pins), so one side failing never blocks
or undoes the other:
- Only the bot's own event post (the card, never its poll) in the WhatsApp group the event went
  to: the main group, and the test group only under the same test-mode rule as Telegram. Events
  published before the post's id was stored have no id and are skipped.
- WhatsApp pins always carry a duration (24 hours / 7 days / 30 days). Each pin takes the
  shortest duration from `durations_s` that lasts until the end of the event's day, and is still
  unpinned explicitly the day after; a pin that outlives the longest duration is pinned again
  when it runs out.
- WhatsApp keeps at most `max_pins` pins in a chat (3). When more events are in the window, the
  operator's own pins come first, then the nearest events. Unpins run before pins, so when an
  earlier event ends its slot goes to the next event in the same check; if that unpin fails, the
  slot stays taken and the next event waits for a later check.
- `whatsapp.enabled: false` stops every WhatsApp pin call; pins already placed then run out by
  their own duration.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)

_warned: set[str] = set()  # one clear log line per problem per process, not one per run
_NOT_PINNED = ("not pinned", "message to unpin not found", "message not found", "message_id_invalid")


def pins_cfg() -> dict:
    from .config import get_settings

    return (get_settings() or {}).get("event_pins") or {}


def _today() -> date:
    from .events_calendar import local_now

    return local_now().date()


def in_window(event_date: date, today: date, days_before: int) -> bool:
    return event_date - timedelta(days=days_before) <= today <= event_date


async def candidates(db) -> list[dict]:
    """The event posts that may be pinned: {id, date, chat_id, message_id, topic_id}."""
    from .shared_events import default_mode

    group = int(os.getenv("GROUP_ID", "0") or 0)
    out = []
    for e in await db.get_published_events(group):
        chat = e.get("tg_chat_id")
        if not e.get("message_id") or (chat is not None and int(chat) != group):
            continue  # a WhatsApp-only event, or not a post of the bot in the group
        out.append({"id": int(e["id"]), "date": e["event_date"], "chat_id": group,
                    "message_id": int(e["message_id"]), "topic_id": e.get("topic_id")})
    test = int(os.getenv("TEST_GROUP_ID", "0") or 0)
    if test and pins_cfg().get("test_group_in_test_mode", True) and default_mode() == "test":
        for e in await db.get_test_group_events(test):
            out.append({"id": int(e["id"]), "date": e["event_date"], "chat_id": test,
                        "message_id": int(e["message_id"]), "topic_id": e.get("topic_id")})
    for c in out:
        try:
            c["date"] = date.fromisoformat(str(c["date"]))
        except ValueError:
            c["date"] = None
    return [c for c in out if c["date"]]


async def _topic(db, c: dict) -> int | None:
    if c.get("topic_id") is not None:
        return int(c["topic_id"])
    routing = await db.get_handler_routing("events_publish")
    return routing["play_topic_id"] if routing else None


async def _pin(bot, db, c: dict) -> bool:
    from .topic_guard import verify_send_target

    try:
        await verify_send_target(db, chat_id=c["chat_id"], message_thread_id=await _topic(db, c))
        await bot.pin_chat_message(chat_id=c["chat_id"], message_id=c["message_id"], disable_notification=True)
    except Exception as e:
        _warn_once(e, c)
        return False
    await db.save_event_pin(c["id"], chat_id=c["chat_id"], message_id=c["message_id"], pinned=True)
    logger.info("event_pins: pinned event %s (message %s)", c["id"], c["message_id"])
    return True


async def _unpin(bot, db, event_id: int, row: dict) -> bool:
    try:
        await bot.unpin_chat_message(chat_id=int(row["chat_id"]), message_id=int(row["message_id"]))
    except Exception as e:
        if not any(m in str(e).lower() for m in _NOT_PINNED):
            _warn_once(e, {"id": event_id})
            return False
    await db.save_event_pin(event_id, chat_id=int(row["chat_id"]), message_id=int(row["message_id"]), pinned=False)
    logger.info("event_pins: unpinned event %s (message %s)", event_id, row["message_id"])
    return True


def _warn_once(e: Exception, c: dict) -> None:
    text = str(e).lower()
    key = "rights" if ("rights" in text or "not enough" in text or "administrator" in text) else f"{type(e).__name__}:{text[:80]}"
    if key in _warned:
        return
    _warned.add(key)
    if key == "rights":
        logger.warning("event_pins: the bot has no right to pin messages in the group; nothing pinned (%s)", e)
    else:
        logger.warning("event_pins: pin/unpin failed for event %s: %s", c.get("id"), e)


def _wanted(c: dict, row: dict | None, today: date, auto: bool, days: int) -> bool:
    if today > c["date"]:
        return False  # over: always unpinned
    manual = (row or {}).get("manual")
    if manual == "pin":
        return True
    if manual == "unpin":
        return False
    return auto and in_window(c["date"], today, days)


async def sync(bot, db, *, today: date | None = None) -> dict:
    """Bring the pins in line with the rules. Safe to run any number of times."""
    cfg = pins_cfg()
    today = today or _today()
    auto, days = bool(cfg.get("enabled", True)), int(cfg.get("days_before", 7))
    rows = await db.list_event_pins()
    done = {"pinned": [], "unpinned": []}
    wanted = set()
    for c in await candidates(db):
        row = rows.get(c["id"])
        if not _wanted(c, row, today, auto, days):
            continue
        wanted.add(c["id"])
        same = row and row["pinned"] and int(row["message_id"]) == c["message_id"]
        if not same and await _pin(bot, db, c):
            done["pinned"].append(c["id"])
    for event_id, row in rows.items():
        if row["pinned"] and event_id not in wanted and await _unpin(bot, db, event_id, row):
            done["unpinned"].append(event_id)
    return done


async def set_manual(bot, db, event_id: int, pin: bool) -> dict:
    """The operator pins or unpins one event's post by hand, in both groups where it was posted.

    status follows Telegram (or WhatsApp for an event with no Telegram post); whatsapp_status is
    "ok" / "failed", or None when there is no WhatsApp post or WhatsApp pinning is off."""
    choice = "pin" if pin else "unpin"
    c = next((x for x in await candidates(db) if x["id"] == event_id), None)
    row = (await db.list_event_pins()).get(event_id)
    wa_on = bool(wa_cfg().get("enabled", False))
    w = next((x for x in await wa_candidates(db) if x["id"] == event_id), None) if wa_on else None
    w_row = (await db.list_event_wa_pins()).get(event_id) if wa_on else None
    if not (c or row or w or w_row):
        return {"status": "not_found"}
    out = {"changes": {}}
    if c or row:
        chat = c["chat_id"] if c else int(row["chat_id"])
        msg = c["message_id"] if c else int(row["message_id"])
        await db.save_event_pin(event_id, chat_id=chat, message_id=msg, manual=choice)
        out["changes"]["telegram"] = await sync(bot, db)
        out["pinned"] = bool((await db.list_event_pins())[event_id]["pinned"])
    out["whatsapp_status"] = None
    if w or w_row:
        await db.save_event_wa_pin(event_id, chat_id=(w or w_row)["chat_id"], message_id=(w or w_row)["message_id"],
                                   manual=choice)
        try:
            out["changes"]["whatsapp"] = await sync_whatsapp(db)
            out["whatsapp_pinned"] = bool((await db.list_event_wa_pins())[event_id]["pinned"])
        except Exception:
            logger.exception("event_pins: WhatsApp manual %s failed for event %s", choice, event_id)
            out["whatsapp_pinned"] = None
        out["whatsapp_status"] = "ok" if out["whatsapp_pinned"] == pin else "failed"
    side = out["pinned"] if "pinned" in out else out.get("whatsapp_pinned")
    out["status"] = "ok" if side == pin else "failed"
    return out


# ── WhatsApp ──

_wa_warned: set[str] = set()


def wa_cfg() -> dict:
    return pins_cfg().get("whatsapp") or {}


def _now() -> datetime:
    from .events_calendar import local_now

    return local_now()


async def wa_candidates(db) -> list[dict]:
    """The bot's WhatsApp event posts that may be pinned: {id, date, chat_id, message_id}."""
    from . import whatsapp_sender
    from .shared_events import default_mode

    targets = ["whatsapp"]
    if pins_cfg().get("test_group_in_test_mode", True) and default_mode() == "test":
        targets.append("whatsapp_test")
    out = []
    for target in targets:
        try:
            chat = whatsapp_sender.resolve_chat_id(target)
        except whatsapp_sender.WhatsAppNotConfigured:
            continue
        for e in await db.get_whatsapp_event_posts(target):
            msg = str(e["wa_message_id"])
            # A serialized id names its chat ("true_<chat>_<id>"): a post of another group is not ours.
            if msg.count("_") >= 2 and msg.split("_", 2)[1] != chat:
                continue
            try:
                day = date.fromisoformat(str(e["event_date"]))
            except ValueError:
                continue
            out.append({"id": int(e["id"]), "date": day, "chat_id": chat, "message_id": msg})
    return out


def _day_end(day: date, now: datetime) -> datetime:
    return datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo)


def _duration(day: date, now: datetime) -> int:
    """The shortest allowed pin duration that lasts until the end of the event's day."""
    allowed = sorted(int(d) for d in (wa_cfg().get("durations_s") or [86400, 604800, 2592000]))
    need = (_day_end(day, now) - now).total_seconds()
    return next((d for d in allowed if d >= need), allowed[-1])


def _expired(row: dict, now: datetime) -> bool:
    try:
        return bool(row.get("expires_at")) and datetime.fromisoformat(row["expires_at"]) <= now
    except ValueError:
        return True


def _wa_warn_once(e: Exception, event_id: int) -> None:
    import httpx

    from . import whatsapp_sender

    code = getattr(getattr(e, "response", None), "status_code", None)
    if isinstance(e, whatsapp_sender.WhatsAppNotConfigured):
        key, why = "config", "WhatsApp sending is not set up here"
    elif isinstance(e, httpx.TransportError):
        key, why = "down", "Botty's WhatsApp session cannot be reached"
    elif code in (401, 403):
        key, why = "rights", "WhatsApp refused (no admin right to pin, or a wrong API key)"
    elif code == 404:
        key, why = f"gone:{event_id}", f"the WhatsApp post of event {event_id} is gone"
    else:
        key, why = f"{type(e).__name__}:{code}:{str(e)[:60]}", f"pin/unpin failed for event {event_id}"
    if key in _wa_warned:
        return
    _wa_warned.add(key)
    logger.warning("event_pins: WhatsApp: %s; Telegram pinning is not affected (%s)", why, e)


async def _wa_pin(db, c: dict, now: datetime) -> bool:
    from . import whatsapp_sender

    seconds = _duration(c["date"], now)
    try:
        await whatsapp_sender.pin_message(c["chat_id"], c["message_id"], seconds)
    except Exception as e:
        _wa_warn_once(e, c["id"])
        return False
    await db.save_event_wa_pin(c["id"], chat_id=c["chat_id"], message_id=c["message_id"], pinned=True,
                               expires_at=(now + timedelta(seconds=seconds)).isoformat())
    logger.info("event_pins: pinned event %s in WhatsApp for %ss", c["id"], seconds)
    return True


async def _wa_unpin(db, event_id: int, row: dict, now: datetime) -> bool:
    from . import whatsapp_sender

    if not _expired(row, now):  # an expired pin is already gone: nothing to ask WhatsApp
        try:
            await whatsapp_sender.unpin_message(row["chat_id"], row["message_id"])
        except Exception as e:
            _wa_warn_once(e, event_id)
            return False
    await db.save_event_wa_pin(event_id, chat_id=row["chat_id"], message_id=row["message_id"], pinned=False,
                               expires_at=None)
    logger.info("event_pins: unpinned event %s in WhatsApp", event_id)
    return True


async def sync_whatsapp(db, *, now: datetime | None = None) -> dict:
    """Bring the WhatsApp pins in line with the rules. Safe to run any number of times."""
    cfg = wa_cfg()
    done = {"pinned": [], "unpinned": []}
    if not cfg.get("enabled", False):
        return done
    now = now or _now()
    today = now.date()
    auto = bool(pins_cfg().get("enabled", True))
    days = int(pins_cfg().get("days_before", 7))
    limit = int(cfg.get("max_pins", 3))
    rows = await db.list_event_wa_pins()
    wanted = [c for c in await wa_candidates(db) if _wanted(c, rows.get(c["id"]), today, auto, days)]
    wanted.sort(key=lambda c: ((rows.get(c["id"]) or {}).get("manual") != "pin", c["date"], c["id"]))
    keep, per_chat = [], {}
    for c in wanted:  # the operator's pins first, then the nearest events, up to the limit per chat
        if per_chat.get(c["chat_id"], 0) < limit:
            per_chat[c["chat_id"]] = per_chat.get(c["chat_id"], 0) + 1
            keep.append(c)
    keep_ids = {c["id"] for c in keep}
    taken = {}  # pins WhatsApp still holds for us, per chat
    for event_id, row in rows.items():
        if not row["pinned"]:
            continue
        if event_id in keep_ids and not _expired(row, now):
            taken[row["chat_id"]] = taken.get(row["chat_id"], 0) + 1
        elif event_id not in keep_ids:
            if await _wa_unpin(db, event_id, row, now):
                done["unpinned"].append(event_id)
            elif not _expired(row, now):
                taken[row["chat_id"]] = taken.get(row["chat_id"], 0) + 1
    for c in keep:
        row = rows.get(c["id"])
        if row and row["pinned"] and row["message_id"] == c["message_id"] and not _expired(row, now):
            continue
        if taken.get(c["chat_id"], 0) >= limit:
            continue  # a slot is still held by a pin that could not be removed; a later check retries
        if await _wa_pin(db, c, now):
            taken[c["chat_id"]] = taken.get(c["chat_id"], 0) + 1
            done["pinned"].append(c["id"])
    return done


async def sync_all(bot, db) -> dict:
    """Telegram and WhatsApp, each on its own: a failure on one side never stops the other."""
    out = {}
    for side, call in (("telegram", lambda: sync(bot, db)), ("whatsapp", lambda: sync_whatsapp(db))):
        try:
            out[side] = await call()
        except Exception:
            logger.exception("event_pins: %s check failed", side)
            out[side] = None
    return out


async def pin_job(context) -> None:
    """Repeating job (bot/main.py)."""
    await sync_all(context.bot, context.bot_data["db"])
