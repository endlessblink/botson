"""Inline button polls with vote tracking.

Handles callback_data starting with 'poll_'. Tracks votes in-memory
and updates button text with voter names and counts.

Options are auto-discovered from the existing buttons on first click,
so no separate registration step is needed.
"""

import logging
from collections import defaultdict

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler

from ..utils.helpers import get_display_name

logger = logging.getLogger(__name__)

# In-memory vote storage: {message_id: {option_key: {user_id: display_name}}}
_votes: dict[int, dict[str, dict[int, str]]] = defaultdict(lambda: defaultdict(dict))

# Store original option labels: {message_id: {option_key: label}}
_labels: dict[int, dict[str, str]] = {}


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

    # Toggle vote
    if user.id in _votes[msg_id][option_key]:
        del _votes[msg_id][option_key][user.id]
        logger.info("User %s removed vote for %s on msg %d", user_name, option_key, msg_id)
        await query.answer("הסרת את הבחירה ✖️")
    else:
        _votes[msg_id][option_key][user.id] = user_name
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
