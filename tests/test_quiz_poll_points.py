"""Guess polls: members whose only pick was the right answer get points."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bot.database.db import Database
from bot.handlers import calendar
from bot.handlers.polls import award_quiz_points

OPTIONS = ["A", "B", "C", "D"]


def _poll_row(message_id=8779):
    return {"id": 875, "message_type": "poll", "status": "sent",
            "poll_options": json.dumps(OPTIONS), "sent_message_id": message_id}


async def _db(tmp_path):
    db = Database(str(tmp_path / "quiz.db"))
    await db.init()
    return db


def run(tmp_path, body):
    async def scenario():
        db = await _db(tmp_path)
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


def test_only_sole_correct_pick_scores_and_is_idempotent(tmp_path):
    award = AsyncMock()

    async def body(db):
        await db.set_poll_vote(8779, "0", 1, "Right")          # correct only
        await db.set_poll_vote(8779, "1", 2, "Wrong")          # wrong
        await db.set_poll_vote(8779, "0", 3, "Hedger")         # ticked everything
        await db.set_poll_vote(8779, "1", 3, "Hedger")
        with patch("bot.handlers.levels.award_and_check_level", new=award), \
             patch("bot.utils.scoring.get_points", return_value=5):
            first = await award_quiz_points(db, SimpleNamespace(bot=AsyncMock()),
                                            poll_row=_poll_row(), correct_option="A")
            second = await award_quiz_points(db, SimpleNamespace(bot=AsyncMock()),
                                             poll_row=_poll_row(), correct_option="A")

        member = await db.get_member(1)
        return first, second, member

    first, second, member = run(tmp_path, body)
    assert member is not None
    assert first == ["Right"]
    assert second == []
    award.assert_awaited_once()
    assert award.call_args.args[2:] == (1, "Right", 5)


def test_unknown_answer_or_unsent_poll_is_refused(tmp_path):
    async def body(db):
        with pytest.raises(ValueError):
            await award_quiz_points(db, SimpleNamespace(bot=None), poll_row=_poll_row(), correct_option="Z")
        with pytest.raises(ValueError):
            await award_quiz_points(db, SimpleNamespace(bot=None), poll_row=_poll_row(0), correct_option="A")
    run(tmp_path, body)


def test_reveal_row_marker_triggers_scoring_and_winner_reply():
    db = SimpleNamespace(get_scheduled_message=AsyncMock(return_value=_poll_row()))
    msg = {"id": 874, "channel_topic_id": 54,
           "poll_options": json.dumps({"quiz_answer_for": 875, "correct_option": "A"})}
    send = AsyncMock()
    with patch("bot.handlers.polls.award_quiz_points", new=AsyncMock(return_value=["Right"])) as award, \
         patch.object(calendar, "safe_send", new=send), \
         patch.object(calendar, "load_copy", return_value="winners"):
        asyncio.run(calendar._award_quiz_reveal(AsyncMock(), db, msg, -100, 9000))
    award.assert_awaited_once()
    assert send.call_args.kwargs["reply_to_message_id"] == 9000
    assert send.call_args.kwargs["message_thread_id"] == 54


def test_plain_rows_and_reveal_without_winners_send_nothing():
    send = AsyncMock()
    with patch.object(calendar, "safe_send", new=send), \
         patch("bot.handlers.polls.award_quiz_points", new=AsyncMock(return_value=[])):
        asyncio.run(calendar._award_quiz_reveal(AsyncMock(), SimpleNamespace(), {"poll_options": None}, -100, 1))
        db = SimpleNamespace(get_scheduled_message=AsyncMock(return_value=_poll_row()))
        msg = {"poll_options": {"quiz_answer_for": 875, "correct_option": "A"}}
        asyncio.run(calendar._award_quiz_reveal(AsyncMock(), db, msg, -100, 1))
    send.assert_not_awaited()


def test_marker_dict_is_not_read_as_poll_options():
    assert calendar._parse_poll_options(json.dumps({"quiz_answer_for": 1, "correct_option": "A"})) == []
