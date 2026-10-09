"""Deliver Botson content to a WhatsApp group through Botty's WAHA session.

Botson stays the brain (generation, approval, quality gates, scheduling);
Botty's WhatsApp account only delivers. Configuration comes from env:

  WHATSAPP_WAHA_URL      base URL of Botty's WAHA (e.g. http://127.0.0.1:3051)
  WHATSAPP_WAHA_API_KEY  WAHA API key (secret; never committed)
  WHATSAPP_WAHA_SESSION  WAHA session name (default "default")
  WHATSAPP_GROUP_ID      target group JID, e.g. 1203...@g.us
  WHATSAPP_TEST_GROUP_ID optional test group JID

Rows opt in with scheduled_messages.target_group = "whatsapp" or
"whatsapp_test". Nothing here runs for Telegram rows.
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)

WHATSAPP_TARGETS = {"whatsapp": "WHATSAPP_GROUP_ID", "whatsapp_test": "WHATSAPP_TEST_GROUP_ID"}

# Types that carry over to WhatsApp in phase 1. Live games, RSVP buttons and
# forum-topic features have no WhatsApp equivalent yet.
SUPPORTED_TYPES = {"morning", "evening", "discussion", "custom", "poll"}


class WhatsAppNotConfigured(RuntimeError):
    pass


def is_whatsapp_target(target: str | None) -> bool:
    return str(target or "") in WHATSAPP_TARGETS


def resolve_chat_id(target: str) -> str:
    env_name = WHATSAPP_TARGETS.get(target)
    chat_id = (os.getenv(env_name, "") if env_name else "").strip()
    if not chat_id.endswith("@g.us"):
        raise WhatsAppNotConfigured(f"no WhatsApp group id for target '{target}'")
    return chat_id


def _config() -> tuple[str, str, str]:
    base = os.getenv("WHATSAPP_WAHA_URL", "").strip().rstrip("/")
    key = os.getenv("WHATSAPP_WAHA_API_KEY", "").strip()
    session = os.getenv("WHATSAPP_WAHA_SESSION", "default").strip() or "default"
    if not base or not key:
        raise WhatsAppNotConfigured("WHATSAPP_WAHA_URL / WHATSAPP_WAHA_API_KEY not set")
    return base, key, session


async def _post(path: str, payload: dict) -> str | None:
    base, key, session = _config()
    body = {"session": session, **payload}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(f"{base}{path}", json=body, headers={"X-Api-Key": key})
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        return None
    msg_id = data.get("id") if isinstance(data, dict) else None
    if isinstance(msg_id, dict):
        msg_id = msg_id.get("_serialized")
    return str(msg_id) if msg_id else None


async def send_text(chat_id: str, text: str) -> str | None:
    return await _post("/api/sendText", {"chatId": chat_id, "text": text})


async def send_poll(chat_id: str, question: str, options: list[str]) -> str | None:
    return await _post(
        "/api/sendPoll",
        {"chatId": chat_id, "poll": {"name": question, "options": options, "multipleAnswers": False}},
    )
