# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Inline button polls with vote tracking.

Handles callback_data starting with 'poll_'. Tracks votes in-memory AND
persists every toggle to the `poll_votes` table so:
  - Bot restarts don't wipe vote counts (cache is rehydrated on startup)
  - The dashboard can read poll results to power "Create event from poll"

Options are auto-discovered from the existing buttons on first click,
so no separate registration step is needed.
"""

import logging
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from ..database.db import Database
from ..utils.helpers import get_display_name

logger = logging.getLogger(__name__)

# In-memory cache. Always treated as a write-through cache backed by poll_votes.
# {message_id: {option_key: {user_id: display_name}}}
_votes: dict[int, dict[str, dict[int, str]]] = defaultdict(lambda: defaultdict(dict))

# Option labels per message: {message_id: {option_key: label}}
_labels: dict[int, dict[str, str]] = {}


async def hydrate_from_db(db: Database):
    """Rebuild the in-memory _votes cache from poll_votes on bot startup.

    Without this, a bot restart would show zero votes on the buttons until
    every voter clicked again. Labels are still discovered lazily on first
    click — they're not persisted because they live in scheduled_messages.poll_options.
    """
    rows = await db.load_all_poll_votes()
    for r in rows:
        _votes[r["message_id"]][r["option_key"]][r["user_id"]] = r["display_name"]
    logger.info("polls: hydrated %d votes across %d messages", len(rows), len(_votes))


def _discover_labels(message) -> dict[str, str]:
    """Extract option labels from existing inline keyboard buttons."""
    labels = {}
    if message and message.reply_markup:
        for row in message.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("poll_"):
                    key = btn.callback_data.replace("poll_", "")
                    text = btn.text
                    if text.startswith("🗓️ "):
                        text = text[3:]
                    # Remove vote info: " (N) — names" at the END
                    # Vote counts look like "(1) — Name" — always after the last date/time
                    import re
                    text = re.sub(r'\s*\(\d+\)\s*—.*$', '', text)
                    labels[key] = text.strip()
    return labels


async def handle_poll_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle poll button clicks — toggle vote and update display."""
    query = update.callback_query
    if not query or not query.data or not query.data.startswith("poll_"):
        return

    user = update.effective_user
    if not user:
        return

    msg_id = query.message.message_id
    option_key = query.data.replace("poll_", "")
    user_name = get_display_name(user)

    # Auto-discover labels from existing buttons on first interaction
    if msg_id not in _labels:
        discovered = _discover_labels(query.message)
        logger.info("Discovered labels for msg %d: %s", msg_id, discovered)
        _labels[msg_id] = discovered

    labels = _labels.get(msg_id, {})
    if not labels:
        logger.error("No labels found for msg %d", msg_id)
        await query.answer("שגיאה — לא נמצאו אפשרויות")
        return

    db: Database | None = context.bot_data.get("db")

    # Toggle vote
    if user.id in _votes[msg_id][option_key]:
        del _votes[msg_id][option_key][user.id]
        if db:
            try:
                await db.delete_poll_vote(msg_id, option_key, user.id)
            except Exception as e:  # noqa: BLE001
                logger.error("polls: failed to persist vote removal: %s", e)
        logger.info("User %s removed vote for %s on msg %d", user_name, option_key, msg_id)
        await query.answer("הסרת את הבחירה ✖️")
    else:
        _votes[msg_id][option_key][user.id] = user_name
        if db:
            try:
                await db.set_poll_vote(msg_id, option_key, user.id, user_name)
            except Exception as e:  # noqa: BLE001
                logger.error("polls: failed to persist vote: %s", e)
        logger.info("User %s voted for %s on msg %d", user_name, option_key, msg_id)
        await query.answer("נרשמת! ✅")

    # Rebuild buttons with vote counts and names
    buttons = []
    for key, label in labels.items():
        voters = _votes[msg_id].get(key, {})
        count = len(voters)
        if count > 0:
            names = ", ".join(voters.values())
            btn_text = f"🗓️ {label} ({count}) — {names}"
        else:
            btn_text = f"🗓️ {label}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=f"poll_{key}")])

    logger.info("Updating buttons for msg %d: %s", msg_id, [b[0].text for b in buttons])
    try:
        await query.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(buttons)
        )
        logger.info("Buttons updated successfully")
    except Exception as e:
        logger.error("Failed to update poll buttons: %s", e)


def register(app):
    """Register poll callback handler."""
    app.add_handler(CallbackQueryHandler(handle_poll_vote, pattern=r"^poll_"))
