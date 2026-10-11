"""Reply to explicit Botson mentions with same-topic context and a quality gate."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..scheduler.materializer import _generate_with_claude
from ..utils.config import GROUP_ID, get_settings, load_yaml
from ..utils.operator_anchors import render_learned_rules_block
from ..utils.topic_guard import safe_send
from ..utils.helpers import get_display_name

logger = logging.getLogger(__name__)


def _is_addressed_to_bot(message: Any, bot_id: int, bot_username: str | None) -> str | None:
    reply = getattr(message, "reply_to_message", None)
    reply_user = getattr(reply, "from_user", None)
    if reply_user and getattr(reply_user, "id", None) == bot_id:
        return "reply"
    if bot_username:
        text = (getattr(message, "text", None) or "").strip()
        if re.search(rf"(?<![\w@])@{re.escape(bot_username)}(?!\w)", text, re.IGNORECASE):
            return "mention"
    return None


def _json_object(raw: str | None) -> dict | None:
    try:
        value = json.loads((raw or "").strip())
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _bounded_context(messages: list[dict], limit: int, max_chars: int) -> list[dict]:
    selected = messages[-max(1, limit):]
    result: list[dict] = []
    remaining = max_chars
    for item in reversed(selected):
        text = str(item.get("text") or "").strip()
        if not text or remaining <= 0:
            continue
        # Keep the beginning of a long message so the context remains readable
        # and doesn't turn into a stray fragment.
        text = text[:remaining]
        result.append({
            "speaker": str(item.get("sender_name") or "member")[:80],
            "text": text,
        })
        remaining -= len(text)
    return list(reversed(result))


async def _make_reply(
    *,
    message: Any,
    db: Database,
    settings: dict,
    addressed_by: str,
) -> str | None:
    cfg = settings.get("community_replies") or {}
    contract = load_yaml("community_reply.yaml") or {}
    generator_template = str(contract.get("generator_prompt") or "").strip()
    reviewer_template = str(contract.get("reviewer_prompt") or "").strip()
    if not generator_template or not reviewer_template:
        logger.error("community_replies: prompt contract missing; skipping")
        return None

    now = datetime.now(timezone.utc)
    recent = await db.get_recent_community_messages(
        GROUP_ID,
        since=now - timedelta(hours=max(1, int(cfg.get("context_hours", 24)))),
        limit=300,
    )
    thread_id = int(message.message_thread_id)
    recent = [item for item in recent if item.get("thread_id") == thread_id]
    replied = getattr(message, "reply_to_message", None)
    if (
        replied
        and getattr(getattr(replied, "from_user", None), "is_bot", False)
        and not any(item.get("message_id") == replied.message_id for item in recent)
    ):
        recent.append({
            "sender_name": "Botson",
            "source": "botson",
            "text": getattr(replied, "text", None) or getattr(replied, "caption", None) or "",
        })
    incoming_text = (message.text or message.caption or "").strip()
    user = message.from_user
    recent.append({"sender_name": get_display_name(user), "text": incoming_text})
    context_items = _bounded_context(
        recent,
        int(cfg.get("context_messages", 10)),
        int(cfg.get("context_max_chars", 1800)),
    )
    context_text = json.dumps(context_items, ensure_ascii=False)
    learned_rules = render_learned_rules_block() or "No additional operator rules."
    max_reply_chars = max(40, int(cfg.get("max_reply_chars", 240)))
    prompt = generator_template.format(
        learned_rules=learned_rules,
        context=context_text,
        sender_name=get_display_name(user),
        incoming_text=incoming_text,
        addressed_by=addressed_by,
        max_reply_chars=max_reply_chars,
    )
    candidate_result = _json_object(await _generate_with_claude(prompt))
    if not candidate_result or set(candidate_result) != {"decision", "text"}:
        logger.info("community_replies: generation returned invalid contract")
        return None
    if candidate_result.get("decision") == "skip":
        return None
    if candidate_result.get("decision") != "reply":
        return None
    candidate = candidate_result.get("text")
    if not isinstance(candidate, str):
        return None
    candidate = candidate.strip()
    if not candidate or len(candidate) > max_reply_chars or "\n\n" in candidate:
        return None

    reviewer_prompt = (
        reviewer_template
        + "\n\nSame-topic context (JSON):\n"
        + context_text
        + "\n\nLatest member message:\n"
        + incoming_text
        + "\n\nProposed reply:\n"
        + candidate
        + "\n\nOperator rules:\n"
        + learned_rules
    )
    verdict = _json_object(await _generate_with_claude(reviewer_prompt))
    if (
        not verdict
        or set(verdict) != {"pass", "reason"}
        or verdict.get("pass") is not True
        or not isinstance(verdict.get("reason"), str)
    ):
        logger.info("community_replies: quality review rejected or malformed")
        return None
    return candidate


async def handle_addressed_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    chat = update.effective_chat
    if not message or not user or not chat or chat.id != GROUP_ID:
        return
    if getattr(user, "is_bot", False) or not message.message_thread_id:
        return
    settings = get_settings()
    cfg = settings.get("community_replies") or {}
    if not cfg.get("enabled", False):
        return
    addressed_by = _is_addressed_to_bot(
        message, int(context.bot.id), getattr(context.bot, "username", None)
    )
    if not addressed_by:
        return

    db: Database = context.bot_data["db"]
    try:
        if not await db.claim_community_reply(chat.id, message.message_id):
            logger.info("community_replies: duplicate update skipped message_id=%s", message.message_id)
            return
    except Exception:
        logger.exception("community_replies: could not claim inbound update; failing closed")
        return

    terminal_status = "skipped"
    reply_message_id = None
    try:
        reply_text = await _make_reply(
            message=message,
            db=db,
            settings=settings,
            addressed_by=addressed_by,
        )
        if not reply_text:
            return
        sent = await safe_send(
            context.bot,
            db,
            "send_message",
            chat_id=GROUP_ID,
            message_thread_id=int(message.message_thread_id),
            reply_to_message_id=message.message_id,
            text=reply_text,
        )
        terminal_status = "sent"
        reply_message_id = getattr(sent, "message_id", None)
        logger.info(
            "community_replies: sent reviewed reply to inbound message_id=%s",
            message.message_id,
        )
    except Exception:
        terminal_status = "failed"
        logger.exception(
            "community_replies: failed for inbound message_id=%s", message.message_id
        )
    finally:
        try:
            await db.finish_community_reply(
                chat.id,
                message.message_id,
                status=terminal_status,
                reply_message_id=reply_message_id,
            )
        except Exception:
            logger.exception(
                "community_replies: failed to persist result for inbound message_id=%s",
                message.message_id,
            )


def register(app) -> None:
    """Register the opt-in conversational reply handler after antispam."""
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_addressed_message),
        group=1,
    )
