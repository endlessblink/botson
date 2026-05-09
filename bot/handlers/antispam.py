# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Anti-spam handler with silent background moderation."""

import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from ..database.db import Database
from ..utils.config import ADMIN_IDS, GROUP_ID, get_settings, get_spam_patterns, is_feature_enabled
from ..utils.helpers import is_admin, is_bot_user

logger = logging.getLogger(__name__)

# In-memory tracking for duplicate detection
# {user_id: [(message_text, timestamp), ...]}
_message_history: dict[int, list[tuple[str, float]]] = defaultdict(list)

# Whitelisted patterns (in-memory, loaded from config + admin additions)
_whitelist: list[str] = []

# Compiled spam patterns
_spam_patterns: list[re.Pattern] = []


def _init_patterns():
    """Initialize spam patterns from config."""
    global _spam_patterns, _whitelist
    try:
        patterns = get_spam_patterns()
        _spam_patterns = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in patterns]
        from ..utils.config import load_yaml
        data = load_yaml("spam_patterns.yaml")
        _whitelist = data.get("whitelist", [])
    except Exception as e:
        logger.error("Failed to load spam patterns: %s", e)


def _is_whitelisted(text: str) -> bool:
    """Check if text matches any whitelist pattern."""
    for pattern in _whitelist:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _count_links(text: str) -> int:
    """Count URLs in text."""
    url_pattern = re.compile(r'https?://\S+|www\.\S+|t\.me/\S+', re.IGNORECASE)
    return len(url_pattern.findall(text))


async def _notify_admins(context: ContextTypes.DEFAULT_TYPE, message: str):
    """Send notification to admins via DM."""
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=f"🛡️ Anti-Spam:\n{message}")
        except Exception as e:
            logger.error("Failed to notify admin %d: %s", admin_id, e)


def _is_trusted_member(member: dict | None, trusted_days: int) -> bool:
    """True if member has been in the group longer than trusted_days."""
    if not member or trusted_days <= 0:
        return False
    joined_str = member.get("joined_at")
    if not joined_str:
        return False
    try:
        joined = datetime.fromisoformat(joined_str)
    except (ValueError, TypeError):
        return False
    return (datetime.now() - joined).days >= trusted_days


async def _delete_and_log(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    rule: str,
    action: str,
    is_trusted: bool = False,
):
    """Log spam detection, and optionally delete/mute (if dry_run is off and not trusted)."""
    msg = update.message
    if not msg:
        return

    user = update.effective_user
    user_name = user.first_name if user else "Unknown"
    user_id = user.id if user else 0
    msg_text = msg.text or msg.caption or "[media]"

    settings = get_settings()
    dry_run = settings.get("antispam", {}).get("dry_run", True)
    skip_action = dry_run or is_trusted

    # Delete the message (only if enforcement is active for this user)
    if not skip_action:
        try:
            await msg.delete()
        except Exception as e:
            logger.error("Failed to delete message: %s", e)

    # Log to database
    db: Database = context.bot_data["db"]
    await db.log_spam(user_id, msg_text[:500], rule, action)
    await db.log_activity("spam", f"זוהה ספאם: {rule}", user_id)

    # Notify admins
    if is_trusted:
        mode_tag = "🟢 [TRUSTED — NOT DELETED] "
    elif dry_run:
        mode_tag = "🔍 [DRY RUN] "
    else:
        mode_tag = ""
    log_msg = (
        f"{mode_tag}🛡️ Anti-Spam\n"
        f"👤 {user_name} (ID: {user_id})\n"
        f"📜 Rule: {rule}\n"
        f"⚡ Action: {action}\n"
        f"💬 Message: {msg_text[:200]}\n"
        f"🕐 {datetime.now(ZoneInfo('Asia/Jerusalem')).strftime('%Y-%m-%d %H:%M:%S')}"
    )
    await _notify_admins(context, log_msg)
    logger.info(
        "Spam %s: rule=%s action=%s user=%d trusted=%s",
        "detected (skipped)" if skip_action else "action",
        rule, action, user_id, is_trusted,
    )


async def check_spam(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Main spam detection handler. Runs on every message."""
    msg = update.message
    if not msg or not update.effective_user:
        return

    user = update.effective_user

    if not is_feature_enabled("antispam", update.effective_chat.id):
        return

    # Skip admins and bots
    if is_admin(user.id) or is_bot_user(user):
        return

    text = msg.text or msg.caption or ""

    # Skip if whitelisted
    if text and _is_whitelisted(text):
        return

    settings = get_settings()
    spam_settings = settings.get("antispam", {})
    db: Database = context.bot_data["db"]

    # Ensure member exists in DB and check trusted status
    member = await db.get_member(user.id)
    trusted_days = spam_settings.get("trusted_member_days", 30)
    is_trusted = _is_trusted_member(member, trusted_days)

    # ── Block check: reject messages from blocked users ──
    # Explicitly blocked users override trusted status — block always wins.
    if await db.is_blocked(user.id):
        await _delete_and_log(update, context, "user_blocked", "delete + ban (blocked user)")
        if not spam_settings.get("dry_run", True):
            try:
                await context.bot.ban_chat_member(
                    chat_id=msg.chat_id,
                    user_id=user.id,
                )
            except Exception as e:
                logger.error("Failed to ban blocked user %d: %s", user.id, e)
        return

    # ── Rule 1: Forwarded from unknown channels ──
    if msg.forward_origin:
        await _delete_and_log(update, context, "forwarded_unknown", "delete + warn", is_trusted=is_trusted)
        if not spam_settings.get("dry_run", True) and not is_trusted:
            try:
                await context.bot.send_message(
                    chat_id=user.id,
                    text="⚠️ ההודעה שהעברת נמחקה. העברת הודעות מערוצים לא מוכרים אינה מותרת בקבוצה.",
                )
            except Exception:
                pass  # User may have blocked the bot
        return

    # ── Rule 2: Links from new members ──
    # By definition only fires for new members, but we still pass is_trusted for consistency.
    if member:
        joined = datetime.fromisoformat(member["joined_at"]) if member.get("joined_at") else None
        new_member_days = spam_settings.get("new_member_days", 7)
        if joined and (datetime.now() - joined).days < new_member_days:
            max_links = spam_settings.get("max_links_new_member", 3)
            if _count_links(text) >= max_links:
                await _delete_and_log(update, context, "new_member_links", "hold for admin", is_trusted=is_trusted)
                await _notify_admins(
                    context,
                    f"🔗 חבר/ה חדש/ה ({user.first_name}) שלח/ה הודעה עם {_count_links(text)} קישורים.\nצריך אישור מנהל.",
                )
                return

    # ── Rule 3: Spam patterns (crypto/betting/adult) ──
    if not _spam_patterns:
        _init_patterns()

    for pattern in _spam_patterns:
        if pattern.search(text):
            await _delete_and_log(update, context, f"pattern: {pattern.pattern[:50]}", "auto-delete", is_trusted=is_trusted)
            return

    # ── Rule 4: Repeated identical messages ──
    now = time.time()
    window = spam_settings.get("duplicate_window_seconds", 60)
    threshold = spam_settings.get("duplicate_threshold", 3)
    mute_minutes = spam_settings.get("mute_duration_minutes", 10)

    # Clean old entries
    _message_history[user.id] = [
        (t, ts) for t, ts in _message_history[user.id] if now - ts < window
    ]

    # Add current message
    if text:
        _message_history[user.id].append((text, now))

        # Count identical messages
        identical = sum(1 for t, _ in _message_history[user.id] if t == text)
        if identical > threshold:
            await _delete_and_log(update, context, "duplicate_messages", f"delete + mute {mute_minutes}min", is_trusted=is_trusted)
            # Mute user (only if not dry_run and not trusted)
            if not spam_settings.get("dry_run", True) and not is_trusted:
                try:
                    until = datetime.now() + timedelta(minutes=mute_minutes)
                    await context.bot.restrict_chat_member(
                        chat_id=msg.chat_id,
                        user_id=user.id,
                        permissions={"can_send_messages": False},
                        until_date=until,
                    )
                except Exception as e:
                    logger.error("Failed to mute user %d: %s", user.id, e)
            return

    # ── Rule 5: New member posting too fast ──
    grace = spam_settings.get("new_member_grace_seconds", 30)
    if member:
        joined = datetime.fromisoformat(member["joined_at"]) if member.get("joined_at") else None
        if joined and (datetime.now() - joined).total_seconds() < grace:
            await _notify_admins(
                context,
                f"⏱️ חבר/ה חדש/ה ({user.first_name}) שלח/ה הודעה תוך {grace} שניות מההצטרפות.\n💬: {text[:200]}",
            )


async def whitelist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add a pattern to the whitelist. /whitelist <pattern>. Admin only."""
    if not update.effective_user or not update.message:
        return

    if not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים לעדכן את הרשימה הלבנה")
        return

    if not context.args:
        await update.message.reply_text("שימוש: /whitelist <pattern>")
        return

    pattern = " ".join(context.args)
    _whitelist.append(pattern)
    await update.message.reply_text(f"✅ התבנית נוספה לרשימה הלבנה: {pattern}")
    logger.info("Whitelist added by admin %d: %s", update.effective_user.id, pattern)


def register(app):
    """Register anti-spam handlers."""
    _init_patterns()
    app.add_handler(CommandHandler("whitelist", whitelist_command))
    # Spam check runs on ALL messages at highest priority
    app.add_handler(
        MessageHandler(filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL, check_spam),
        group=0,
    )
