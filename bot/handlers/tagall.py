"""Admin-only Telegram announcement tagging for known group members."""

from __future__ import annotations

import asyncio
import html
import logging
import secrets
import time
from dataclasses import dataclass

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from ..database.db import Database
from ..scheduler.member_sync import sync_chat_members
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


def _announcement_messages(text: str, label: str) -> list[str]:
    output_limit = int(_settings().get("output_chars", 3800))
    message = f"{html.escape(text.strip())}\n\n{html.escape(label.strip())}"
    if len(message) > output_limit:
        return [message[:output_limit]]
    return [message]


async def _eligible_members(
    bot,
    chat_id: int,
    db: Database,
    *,
    verify_live: bool = True,
) -> tuple[list[dict], int]:
    members = await db.get_chat_members_for_tagging(chat_id)
    if not verify_live:
        return members, 0
    semaphore = asyncio.Semaphore(
        max(1, int(_settings().get("membership_check_concurrency", 10)))
    )

    async def check_member(member: dict) -> tuple[dict | None, int]:
        async with semaphore:
            try:
                chat_member = await bot.get_chat_member(chat_id, int(member["user_id"]))
            except Exception as exc:  # noqa: BLE001 - stale users must not abort the batch
                logger.info("tagall: membership check failed for %s: %s", member["user_id"], exc)
                return None, 1
            status = getattr(chat_member, "status", "")
            if status == "restricted" and not getattr(chat_member, "is_member", False):
                return None, 1
            if status not in {"creator", "administrator", "member", "restricted"}:
                return None, 1
            return member, 0

    results = await asyncio.gather(*(check_member(member) for member in members))
    eligible = [member for member, _ in results if member is not None]
    skipped = sum(skipped_count for _, skipped_count in results)
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
    progress = await update.message.reply_text(load_copy("tagall", "syncing"))
    roster_synced = False
    if _settings().get("roster_sync_enabled", True):
        try:
            roster_synced = await asyncio.wait_for(
                sync_chat_members(db, update.effective_chat.id),
                timeout=int(_settings().get("roster_sync_timeout_seconds", 60)),
            )
            roster_synced = roster_synced is not None
        except Exception as exc:  # noqa: BLE001 - known roster remains a safe fallback
            logger.warning("tagall: full roster sync failed: %s", exc)
    await db.upsert_chat_member(
        update.effective_chat.id,
        update.effective_user.id,
        getattr(update.effective_user, "username", None),
        getattr(update.effective_user, "full_name", str(update.effective_user.id)),
    )
    eligible, skipped = await _eligible_members(
        context.bot,
        update.effective_chat.id,
        db,
        verify_live=not roster_synced,
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(load_copy("tagall", "confirm_button"), callback_data=f"tagall:confirm:{token}"),
        InlineKeyboardButton(load_copy("tagall", "cancel_button"), callback_data=f"tagall:cancel:{token}"),
    ]])
    await progress.edit_text(
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
    roster_synced = False
    if _settings().get("roster_sync_enabled", True):
        try:
            roster_synced = await asyncio.wait_for(
                sync_chat_members(db, pending.chat_id),
                timeout=int(_settings().get("roster_sync_timeout_seconds", 60)),
            )
            roster_synced = roster_synced is not None
        except Exception as exc:  # noqa: BLE001 - known roster remains a safe fallback
            logger.warning("tagall: refresh roster sync failed: %s", exc)
    eligible, skipped = await _eligible_members(
        context.bot,
        pending.chat_id,
        db,
        verify_live=not roster_synced,
    )
    if not eligible:
        await query.edit_message_text(load_copy("tagall", "no_members", skipped=skipped))
        return
    label = load_copy("tagall", "all_label")
    sent = 0
    try:
        for message in _announcement_messages(pending.text, label):
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
