"""A streak milestone is announced once, not on every post made the same day."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from bot.handlers import goals


def _run(before, after):
    db = MagicMock()
    db.upsert_member = AsyncMock()
    db.update_streak = AsyncMock()
    db.add_points = AsyncMock(return_value=0)
    db.log_activity = AsyncMock()
    db.get_streak = AsyncMock(side_effect=[{"current": before}, {"current": after}])
    message = SimpleNamespace(message_thread_id=1, reply_to_message=None, reply_text=AsyncMock())
    update = SimpleNamespace(message=message, effective_chat=SimpleNamespace(id=1),
                             effective_user=SimpleNamespace(id=5, first_name="a", username="a"))
    context = SimpleNamespace(bot_data={"db": db}, bot=SimpleNamespace(send_message=AsyncMock()))
    return update, context, message


def _patch(monkeypatch):
    monkeypatch.setattr(goals, "is_feature_enabled", lambda *a, **k: True)
    monkeypatch.setattr(goals, "_get_goals_topic_id", lambda: 1)
    monkeypatch.setattr(goals, "get_settings", lambda: {"gamification": {"streak_milestones": [7]}})
    monkeypatch.setattr(goals, "get_points", lambda *_: 0)
    monkeypatch.setattr(goals, "check_level_up", lambda *_: None)
    monkeypatch.setattr(goals, "load_copy", lambda *a, **k: "msg")


def test_milestone_sent_when_streak_reaches_it(monkeypatch):
    _patch(monkeypatch)
    update, context, message = _run(6, 7)
    asyncio.run(goals.track_goals_participation(update, context))
    message.reply_text.assert_awaited_once()


def test_second_post_same_day_does_not_repeat_it(monkeypatch):
    _patch(monkeypatch)
    update, context, message = _run(7, 7)
    asyncio.run(goals.track_goals_participation(update, context))
    message.reply_text.assert_not_awaited()
