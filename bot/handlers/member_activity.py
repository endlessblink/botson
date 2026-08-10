"""Member activity reporting and reversible cleanup opt-ins."""

from __future__ import annotations

from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from ..database.db import Database
from ..utils.config import get_settings
from ..utils.copy import load_copy
from ..utils.helpers import is_admin


def _settings() -> dict:
    return get_settings().get("member_activity", {})


async def activity_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type not in {"group", "supergroup"}:
        await update.message.reply_text(load_copy("member_activity", "group_only"))
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(load_copy("member_activity", "not_authorized"))
        return
    days = int(_settings().get("activity_window_days", 90))
    db: Database = context.bot_data["db"]
    report = await db.get_member_activity_report(
        update.effective_chat.id,
        window_days=days,
        limit=int(_settings().get("report_member_limit", 50)),
    )
    candidate_names = ", ".join(
        member["display_name"] for member in report["candidates"]
    ) or load_copy("member_activity", "none")
    await update.message.reply_text(
        load_copy("member_activity", "report").format(
            days=days,
            total=report["total"],
            active=report["active"],
            inactive=report["total"] - report["active"],
            candidates=report["candidate_count"],
            candidate_names=candidate_names,
        )
    )


async def member_cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.effective_chat or not update.effective_user:
        return
    if update.effective_chat.type not in {"group", "supergroup"}:
        await update.message.reply_text(load_copy("member_activity", "group_only"))
        return
    if not is_admin(update.effective_user.id):
        await update.message.reply_text(load_copy("member_activity", "not_authorized"))
        return
    cfg = _settings()
    days = int(cfg.get("activity_window_days", 90))
    grace_days = int(cfg.get("grace_period_days", 14))
    deadline = datetime.now() + timedelta(days=grace_days)
    db: Database = context.bot_data["db"]
    campaign_id = await db.create_member_cleanup_campaign(
        update.effective_chat.id,
        deadline_at=deadline.strftime("%Y-%m-%d %H:%M:%S"),
        activity_window_days=days,
    )
    report = await db.get_member_activity_report(
        update.effective_chat.id, window_days=days, campaign_id=campaign_id
    )
    await update.message.reply_text(
        load_copy("member_activity", "campaign_prompt").format(
            days=days, grace_days=grace_days, total=report["total"],
        ),
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                load_copy("member_activity", "keep_button"),
                callback_data=f"cleanup:keep:{campaign_id}",
            )
        ]]),
    )


async def cleanup_optin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.from_user or not query.message:
        return
    try:
        _, action, raw_campaign_id = query.data.split(":", 2)
        campaign_id = int(raw_campaign_id)
    except (AttributeError, ValueError):
        return
    if action != "keep":
        return
    db: Database = context.bot_data["db"]
    await db.record_member_cleanup_optin(campaign_id, query.message.chat_id, query.from_user.id)
    await query.answer(load_copy("member_activity", "optin_saved"), show_alert=True)


def register(app) -> None:
    app.add_handler(CommandHandler("activity_report", activity_report_command))
    app.add_handler(CommandHandler("member_cleanup", member_cleanup_command))
    app.add_handler(CallbackQueryHandler(cleanup_optin_callback, pattern=r"^cleanup:keep:\d+$"))
