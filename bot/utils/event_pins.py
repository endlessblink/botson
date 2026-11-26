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
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

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
    """The operator pins or unpins one event's post by hand."""
    c = next((x for x in await candidates(db) if x["id"] == event_id), None)
    row = (await db.list_event_pins()).get(event_id)
    if not c and not row:
        return {"status": "not_found"}
    chat = c["chat_id"] if c else int(row["chat_id"])
    msg = c["message_id"] if c else int(row["message_id"])
    await db.save_event_pin(event_id, chat_id=chat, message_id=msg, manual="pin" if pin else "unpin")
    result = await sync(bot, db)
    pinned = bool((await db.list_event_pins())[event_id]["pinned"])
    return {"status": "ok" if pinned == pin else "failed", "pinned": pinned, "changes": result}


async def pin_job(context) -> None:
    """Repeating job (bot/main.py)."""
    try:
        await sync(context.bot, context.bot_data["db"])
    except Exception:
        logger.exception("event_pins: periodic check failed")
