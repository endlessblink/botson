"""Shared utility functions."""

import logging
from telegram import Update

from .config import ADMIN_IDS

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    """Check if a user is an admin."""
    return user_id in ADMIN_IDS


def is_bot_user(user) -> bool:
    """Check if a Telegram user is a bot."""
    return getattr(user, "is_bot", False)


def get_display_name(user) -> str:
    """Get the best display name for a user."""
    if user.first_name and user.last_name:
        return f"{user.first_name} {user.last_name}"
    return user.first_name or user.username or "חבר/ה"
