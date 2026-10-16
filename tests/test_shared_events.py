"""Events shared between Telegram and WhatsApp: one event, sign-ups from both apps."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from bot.database.db import Database
from bot.utils import shared_events, whatsapp_sender
from bot.utils.copy import load_copy
from dashboard import app as dash

TOKEN = "agent-secret-value"
POLL_ID = "true_111@g.us_3EB0ABCDEF_155@lid"


async def _v(value):
    return value


def req(payload, *, agent=False):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {TOKEN}"} if agent else {},
        json=lambda: _v(payload), session={"authenticated": True}, state=SimpleNamespace(),
    )


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {
        "BOTSON_AGENT_API_TOKEN": TOKEN, "BOT_TOKEN": "123:test", "GROUP_ID": "-100", "TEST_GROUP_ID": "-200",
        "WHATSAPP_WAHA_URL": "http://127.0.0.1:3051", "WHATSAPP_WAHA_API_KEY": "k",
        "WHATSAPP_GROUP_ID": "111@g.us", "WHATSAPP_TEST_GROUP_ID": "222@g.us",
    }.items():
        monkeypatch.setenv(k, v)


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "e.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


async def create(db, whatsapp_target="whatsapp"):
    bot = MagicMock()
    bot.edit_message_reply_markup = AsyncMock()
    text, poll = AsyncMock(return_value="wa-card"), AsyncMock(return_value=POLL_ID)
    with patch("telegram.Bot", return_value=bot), \
         patch("bot.handlers.calendar.send_message_with_optional_cover", new=AsyncMock(return_value=SimpleNamespace(message_id=77))), \
         patch.object(whatsapp_sender, "send_text", new=text), \
         patch.object(whatsapp_sender, "send_poll", new=poll):
        result = await dash.create_event(req({
            "title": "ערב משחקי קופסה", "description": "מביאים חטיפים", "event_date": "2026-10-22",
            "event_time": "20:00", "location": "תל אביב", "whatsapp_target": whatsapp_target,
        }), db)
    return result, text, poll


def test_event_goes_to_both_apps_with_a_whatsapp_signup_poll(tmp_path):
    async def body(db):
        result, text, poll = await create(db)
        assert result["status"] == "ok" and result["whatsapp_poll"] == "3EB0ABCDEF"
        assert text.await_args.args[0] == "111@g.us" and "ערב משחקי קופסה" in text.await_args.args[1]
        chat, question, options = poll.await_args.args
        assert chat == "111@g.us" and question == load_copy("whatsapp", "event_poll_question")
        assert options == shared_events.poll_options() and len(options) == 3
        event = await db.get_event(result["event_id"])
        assert (event["wa_target"], event["tg_chat_id"], event["message_id"]) == ("whatsapp", -100, 77)
    run(tmp_path, body)


def test_telegram_only_event_never_touches_whatsapp(tmp_path):
    async def body(db):
        result, text, poll = await create(db, whatsapp_target="")
        assert "whatsapp_poll" not in result
        text.assert_not_awaited(); poll.assert_not_awaited()
        assert await shared_events.whatsapp_line(db, await db.get_event(result["event_id"])) == ""
    run(tmp_path, body)


def test_whatsapp_votes_are_counted_and_shown_on_the_telegram_card_without_names(tmp_path):
    async def body(db):
        result, _, _ = await create(db)
        yes, maybe, no = shared_events.poll_options()
        tg = MagicMock()
        tg.edit_message_text = AsyncMock()
        with patch("telegram.Bot", return_value=tg):
            vote = lambda voter, option: dash.whatsapp_event_vote(req({"poll_id": POLL_ID, "voter": voter, "option": option}, agent=True), db)
            await vote("a@lid", yes)
            await vote("b@lid", yes)
            await vote("c@lid", maybe)
            r = await vote("b@lid", no)  # changed their mind
            assert r["whatsapp"] == {"yes": 1, "maybe": 1}
            r = await vote("c@lid", "")  # vote removed
            assert r["whatsapp"] == {"yes": 1, "maybe": 0}
        card = tg.edit_message_text.await_args.kwargs
        assert card["chat_id"] == -100 and card["message_id"] == 77
        assert load_copy("whatsapp", "event_whatsapp_counts", yes=1, maybe=0) in card["text"]
        assert "@lid" not in card["text"] and card["text"].startswith("📅 ערב משחקי קופסה")
    run(tmp_path, body)


def test_other_polls_and_unauthenticated_calls_are_refused(tmp_path):
    async def body(db):
        await create(db)
        with pytest.raises(HTTPException) as e:
            await dash.whatsapp_event_vote(req({"poll_id": "true_1@g.us_OTHER_x", "voter": "a@lid", "option": "x"}, agent=True), db)
        assert e.value.status_code == 404
        with pytest.raises(HTTPException) as e:
            await dash.whatsapp_event_vote(req({"poll_id": POLL_ID, "voter": "a@lid", "option": "x"}), db)
        assert e.value.status_code == 401
    run(tmp_path, body)


def test_bad_whatsapp_target_is_rejected_before_anything_is_created(tmp_path):
    async def body(db):
        with pytest.raises(HTTPException):
            await create(db, whatsapp_target="telegram")
        assert await db.get_all_events() == []
    run(tmp_path, body)


def test_card_blocks_are_replaced_not_stacked():
    wa = load_copy("whatsapp", "event_whatsapp_counts", yes=2, maybe=1)
    once = shared_events.card_with_rsvps("📅 ערב", ["נועם"], [], wa)
    twice = shared_events.card_with_rsvps(once, ["נועם", "דנה"], [], load_copy("whatsapp", "event_whatsapp_counts", yes=3, maybe=1))
    assert twice.count("📱") == 1 and twice.count("✅ מגיעים") == 1 and "(2): נועם, דנה" in twice
    assert shared_events.card_with_rsvps(twice, [], [], "") == "📅 ערב"
