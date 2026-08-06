"""Admin-only Telegram announcement tagging for known group members."""

from __future__ import annotations

import html
import logging
import secrets
import time
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from ..database.db import Database
from ..utils.config import get_settings
from ..utils.copy import load_copy
from ..utils.helpers import is_admin

logger = logging.getLogger(__name__)

_PENDING: dict[str, "PendingTag"] = {}
_LAST_SENT: dict[int, float] = {}


@dataclass(frozen=True)
class PendingTag:
    requester_id: int
    chat_id: int
    message_thread_id: int | None
    text: str
    created_at: float


def _settings() -> dict:
    return get_settings().get("tagall", {})


def _mention(user_id: int, display_name: str) -> str:
    label = html.escape(display_name or str(user_id))
    return f'<a href="tg://user?id={user_id}">{label}</a>'


def _chunks(items: list[str], limit: int) -> list[str]:
    result: list[str] = []
    current: list[str] = []
    current_len = 0
    for item in items:
        extra = len(item) if not current else len(item) + 1
        if current and current_len + extra > limit:
            result.append(" ".join(current))
            current = []
            current_len = 0
        current.append(item)
        current_len += len(item) if len(current) == 1 else len(item) + 1
    if current:
        result.append(" ".join(current))
    return result


def _first_chunk(items: list[str], limit: int) -> tuple[str, int]:
    if not items:
        return "", 0
    selected: list[str] = []
    length = 0
    for item in items:
        extra = len(item) if not selected else len(item) + 1
        if selected and length + extra > limit:
            break
        selected.append(item)
        length += extra
    return " ".join(selected), len(selected)


def _announcement_messages(text: str, mentions: list[str]) -> list[str]:
    output_limit = int(_settings().get("output_chars", 3800))
    announcement = html.escape(text.strip())
    if not mentions:
        return [announcement]
    first_limit = max(1, output_limit - len(announcement) - 2)
    first_chunk, used = _first_chunk(mentions, first_limit)
    return [f"{announcement}\n\n{first_chunk}"] + _chunks(mentions[used:], output_limit)


async def _eligible_members(bot, chat_id: int, db: Database) -> tuple[list[dict], int]:
    eligible: list[dict] = []
    skipped = 0
    for member in await db.get_members_for_tagging():
        try:
            chat_member = await bot.get_chat_member(chat_id, int(member["user_id"]))
        except Exception as exc:  # noqa: BLE001 - stale users must not abort the batch
            logger.info("tagall: membership check failed for %s: %s", member["user_id"], exc)
            skipped += 1
            continue
        status = getattr(chat_member, "status", "")
        if status == "restricted" and not getattr(chat_member, "is_member", False):
            skipped += 1
            continue
        if status not in {"creator", "administrator", "member", "restricted"}:
            skipped += 1
            continue
        eligible.append(member)
    return eligible, skipped


async def tagall_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Preview an announcement and the known members it can tag."""
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    if update.effective_chat.type not in {"group", "supergroup"}:
        await update.message.reply_text(load_copy("tagall", "group_only"))
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(load_copy("tagall", "not_authorized"))
        return
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(load_copy("tagall", "missing_message"))
        return
    if len(text) > int(_settings().get("message_chars", 2000)):
        await update.message.reply_text(load_copy("tagall", "message_too_long"))
        return
    token = secrets.token_urlsafe(8)
    _PENDING[token] = PendingTag(
        requester_id=update.effective_user.id,
        chat_id=update.effective_chat.id,
        message_thread_id=getattr(update.message, "message_thread_id", None),
        text=text,
        created_at=time.monotonic(),
    )
    db: Database = context.bot_data["db"]
    eligible, skipped = await _eligible_members(context.bot, update.effective_chat.id, db)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(load_copy("tagall", "confirm_button"), callback_data=f"tagall:confirm:{token}"),
        InlineKeyboardButton(load_copy("tagall", "cancel_button"), callback_data=f"tagall:cancel:{token}"),
    ]])
    await update.message.reply_text(
        load_copy("tagall", "preview", eligible=len(eligible), skipped=skipped),
        reply_markup=keyboard,
    )


async def tagall_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm or cancel a pending tag-all preview."""
    query = update.callback_query
    if not query or not query.from_user:
        return
    await query.answer()
    try:
        _, action, token = query.data.split(":", 2)
    except (AttributeError, ValueError):
        return
    pending = _PENDING.pop(token, None)
    ttl = int(_settings().get("preview_ttl_seconds", 600))
    query_chat_id = getattr(getattr(query, "message", None), "chat_id", pending.chat_id if pending else None)
    if (
        not pending
        or query.from_user.id != pending.requester_id
        or query_chat_id != pending.chat_id
        or time.monotonic() - pending.created_at > ttl
    ):
        await query.edit_message_text(load_copy("tagall", "expired"))
        return
    if action == "cancel":
        await query.edit_message_text(load_copy("tagall", "cancelled"))
        return
    now = time.monotonic()
    cooldown = int(_settings().get("cooldown_seconds", 300))
    if now - _LAST_SENT.get(pending.chat_id, 0) < cooldown:
        await query.edit_message_text(load_copy("tagall", "cooldown"))
        return
    db: Database = context.bot_data["db"]
    eligible, skipped = await _eligible_members(context.bot, pending.chat_id, db)
    if not eligible:
        await query.edit_message_text(load_copy("tagall", "no_members", skipped=skipped))
        return
    mentions = [_mention(int(row["user_id"]), row.get("display_name") or row.get("username") or "") for row in eligible]
    sent = 0
    try:
        for message in _announcement_messages(pending.text, mentions):
            await context.bot.send_message(
                chat_id=pending.chat_id,
                message_thread_id=pending.message_thread_id,
                text=message,
                parse_mode="HTML",
            )
            sent += 1
    except Exception as exc:  # noqa: BLE001 - report partial sends without a false success
        logger.error("tagall: send failed after %s messages: %s", sent, exc)
        await query.edit_message_text(load_copy("tagall", "send_failed", sent=sent))
        return
    _LAST_SENT[pending.chat_id] = now
    await query.edit_message_text(load_copy("tagall", "sent", tagged=len(eligible), skipped=skipped, messages=sent))


def register(app):
    """Register the Telegram tag-all command."""
    app.add_handler(CommandHandler("tagall", tagall_command))
    app.add_handler(CallbackQueryHandler(tagall_callback, pattern=r"^tagall:"))
