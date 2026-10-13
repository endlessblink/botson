"""Stops the bot from posting the same text twice in the same place within minutes.

A handler bug (one celebration per message instead of per event) once sent the
same congratulation twice. This sits under every send path, so a repeat is
dropped no matter which handler caused it.
"""
import logging
import time

from telegram.ext import ExtBot

from .config import get_settings

logger = logging.getLogger(__name__)
_recent: dict[tuple, tuple[float, object]] = {}
_installed = False


def _settings() -> tuple[int, int]:
    cfg = get_settings().get("bot") or {}
    return int(cfg.get("duplicate_send_window_seconds", 600)), int(cfg.get("duplicate_send_min_chars", 20))


def install_duplicate_guard() -> None:
    global _installed
    if _installed:
        return
    original = ExtBot.send_message

    async def guarded(self, chat_id, text, *args, **kwargs):
        window, min_chars = _settings()
        key = (chat_id, kwargs.get("message_thread_id"), text)
        now = time.monotonic()
        # Messages with buttons are answers to someone's request (e.g. /arcade), never repeats.
        if window > 0 and isinstance(text, str) and len(text) >= min_chars and not kwargs.get("reply_markup"):
            for k in [k for k, (t, _) in _recent.items() if now - t > window]:
                del _recent[k]
            hit = _recent.get(key)
            if hit:
                logger.warning("Dropped duplicate send to chat %s within %ss: %.40s", chat_id, window, text)
                return hit[1]
        message = await original(self, chat_id, text, *args, **kwargs)
        _recent[key] = (now, message)
        return message

    ExtBot.send_message = guarded
    _installed = True
