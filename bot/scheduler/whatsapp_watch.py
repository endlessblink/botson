"""Tell the operator on Telegram when Botty's WhatsApp login drops, and when it is back.

Botty's own health check only posts to a Discord channel. This job watches the
same WAHA session from Botson, tries one automatic restart per outage, and DMs
the admins a link to the dashboard page that shows a working QR code.
"""
from __future__ import annotations

import logging
import time

from telegram.ext import ContextTypes

from ..utils import config
from ..utils.config import get_settings
from ..utils.copy import load_copy
from ..utils.whatsapp_sender import WhatsAppNotConfigured, restart_session, session_info

logger = logging.getLogger(__name__)

_state = {"bad": 0, "alerted_at": 0.0, "restarted": False, "down_since": 0.0}


def watch_cfg() -> dict:
    return (get_settings() or {}).get("whatsapp_watch") or {}


def connect_url() -> str:
    return f"{config.PUBLIC_DASHBOARD_URL}/whatsapp"


async def _dm_admins(bot, text: str) -> None:
    for admin_id in config.ADMIN_IDS:
        try:
            await bot.send_message(chat_id=admin_id, text=text, disable_web_page_preview=True)
        except Exception as exc:
            logger.error("whatsapp_watch: could not DM admin %s: %s", admin_id, exc)


async def check_whatsapp_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = watch_cfg()
    if not cfg.get("enabled"):
        return
    try:
        status = str((await session_info()).get("status") or "UNKNOWN")
    except WhatsAppNotConfigured:
        return
    except Exception as exc:
        status = "UNREACHABLE"
        logger.warning("whatsapp_watch: WAHA not reachable: %s", exc)

    if status == "WORKING":
        if _state["alerted_at"]:
            minutes = max(1, int((time.time() - _state["down_since"]) / 60))
            await _dm_admins(context.bot, load_copy("whatsapp_watch", "recovered", minutes=minutes))
        _state.update(bad=0, alerted_at=0.0, restarted=False, down_since=0.0)
        return

    _state["bad"] += 1
    if _state["bad"] < int(cfg.get("bad_checks", 2)):
        return
    _state["down_since"] = _state["down_since"] or time.time()
    # One automatic restart per outage: it fixes a crashed engine, and a
    # logged-out phone simply turns into "waiting for a QR scan".
    if not _state["restarted"] and status in {"FAILED", "STOPPED", "UNREACHABLE"}:
        _state["restarted"] = True
        try:
            await restart_session()
            logger.info("whatsapp_watch: restarted the WhatsApp session (was %s)", status)
            return
        except Exception as exc:
            logger.warning("whatsapp_watch: restart failed: %s", exc)
    repeat_s = float(cfg.get("repeat_hours", 3)) * 3600
    if _state["alerted_at"] and time.time() - _state["alerted_at"] < repeat_s:
        return
    _state["alerted_at"] = time.time()
    key = "down_scan" if status == "SCAN_QR_CODE" else "down"
    await _dm_admins(context.bot, load_copy("whatsapp_watch", key, status=status, url=connect_url()))
