"""The WhatsApp watchdog DMs the admins once when Botty drops and once when it is back."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import bot.handlers.calendar  # noqa: F401
from bot.scheduler import whatsapp_watch as w
from bot.utils import config


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setattr(config, "ADMIN_IDS", [999])
    monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "https://botson.example")
    monkeypatch.setattr(w, "watch_cfg", lambda: {"enabled": True, "bad_checks": 2, "repeat_hours": 3})
    w._state.update(bad=0, alerted_at=0.0, restarted=False, down_since=0.0)


def tick(status, bot):
    ctx = MagicMock(bot=bot)
    with patch.object(w, "session_info", new=AsyncMock(return_value={"status": status})), \
         patch.object(w, "restart_session", new=AsyncMock()) as restart:
        asyncio.run(w.check_whatsapp_session(ctx))
    return restart


def test_failed_session_is_restarted_once_then_alerted_with_the_link_and_recovery_is_announced():
    bot = MagicMock(send_message=AsyncMock())
    assert tick("FAILED", bot).await_count == 0 and bot.send_message.await_count == 0   # first bad check: wait
    assert tick("FAILED", bot).await_count == 1 and bot.send_message.await_count == 0   # second: auto-restart
    tick("SCAN_QR_CODE", bot)
    sent = bot.send_message.await_args.kwargs
    assert sent["chat_id"] == 999 and "https://botson.example/whatsapp" in sent["text"]
    tick("SCAN_QR_CODE", bot)
    assert bot.send_message.await_count == 1                                            # no spam while still down
    tick("WORKING", bot)
    assert bot.send_message.await_count == 2 and "דקות" in bot.send_message.await_args.kwargs["text"]
    tick("WORKING", bot)
    assert bot.send_message.await_count == 2                                            # recovery announced once
