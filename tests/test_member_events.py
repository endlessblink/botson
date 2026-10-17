"""Members create events through a personal form link; the operator approves; both groups get it."""

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import bot.handlers.calendar  # noqa: F401  (loads telegram.ext before telegram.Bot is patched)
from bot.database.db import Database
from bot.utils import config, shared_events, whatsapp_sender
from bot.utils.copy import load_copy
from dashboard import app as dash

TOKEN = "agent-secret-value"
PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 32 + b"IEND\xaeB`\x82").decode()


async def _v(value):
    return value


def req(payload=None, *, agent=False, session=False):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {TOKEN}"} if agent else {},
        json=lambda: _v(payload or {}), session={"authenticated": session}, state=SimpleNamespace(),
    )


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k, v in {
        "BOTSON_AGENT_API_TOKEN": TOKEN, "BOT_TOKEN": "123:test", "GROUP_ID": "-100", "TEST_GROUP_ID": "-200",
        "WHATSAPP_WAHA_URL": "http://127.0.0.1:3051", "WHATSAPP_WAHA_API_KEY": "k",
        "WHATSAPP_GROUP_ID": "111@g.us", "WHATSAPP_TEST_GROUP_ID": "222@g.us",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "https://botson.example")
    monkeypatch.setattr(config, "ADMIN_IDS", [999])
    monkeypatch.setattr(dash, "COVERS_DIR", tmp_path)
    monkeypatch.setattr(dash, "MEDIA_DIR", tmp_path.parent)


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "m.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


FORM = {"title": "ערב משחקי קופסה", "event_date": "2099-01-01", "event_time": "20:00",
        "location": "תל אביב", "description": "מביאים חטיפים", "link": "https://example.com/x"}


async def whatsapp_link(db, requester="5@lid", name="דנה"):
    return await dash.agent_event_request(req({"chat_id": "111@g.us", "requester": requester, "name": name}, agent=True), db)


async def submit(db, token, form=None):
    admin_bot = MagicMock(send_message=AsyncMock(), edit_message_reply_markup=AsyncMock())
    with patch("telegram.Bot", return_value=admin_bot):
        r = await dash.member_event_submit(token, req(form or FORM), db)
    return r, admin_bot


def test_asking_for_an_event_needs_both_an_event_word_and_a_create_word():
    assert shared_events.asks_for_event("@bot אפשר ליצור אירוע?")
    assert shared_events.asks_for_event("בוטסון, מפגש חדש בבקשה")
    assert not shared_events.asks_for_event("@bot מה האירוע הבא?")
    assert not shared_events.asks_for_event("@bot ליצור סיכום")
    assert shared_events.asks_for_event("היי אני רוצה להרים איוונט")
    assert shared_events.asks_for_event("@bot can we organize a meetup?")
    assert not shared_events.asks_for_event("how to prevent the news")


def test_whatsapp_member_gets_a_personal_link_reused_until_it_expires_and_a_daily_cap(tmp_path):
    async def body(db):
        a = await whatsapp_link(db)
        assert a["status"] == "ok" and a["url"].startswith("https://botson.example/e/")
        assert (await whatsapp_link(db))["url"] == a["url"]  # same open link, no new one
        with pytest.raises(HTTPException) as e:
            await dash.agent_event_request(req({"chat_id": "999@g.us", "requester": "5@lid"}, agent=True), db)
        assert e.value.status_code == 403
        for i in range(3):
            r = await db.get_event_request(token=a["url"].rsplit("/", 1)[1]) if i == 0 else None
            if r:
                await db.update_event_request(r["id"], status="submitted")
            if i:
                await db.create_event_request(token=f"t{i}", platform="whatsapp", requester_id="5@lid", requester_name="",
                                              created_at=shared_events._israel_now().isoformat(), expires_at="2000-01-01")
        assert (await whatsapp_link(db))["status"] == "limit"
    run(tmp_path, body)


def test_form_validates_then_is_single_use_and_dms_the_admin_with_buttons(tmp_path):
    async def body(db):
        token = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        bad, _ = await submit(db, token, {"title": "", "event_date": "2000-01-01", "event_time": "25:00", "link": "ftp://x"})
        assert bad.status_code == 422
        r, admin = await submit(db, token, {**FORM, "image": f"data:image/png;base64,{PNG}"})
        assert r == {"status": "ok"}
        sent = admin.send_message.await_args.kwargs
        assert sent["chat_id"] == 999 and "ערב משחקי קופסה" in sent["text"] and "דנה" in sent["text"]
        assert load_copy("member_events", "platform_whatsapp") in sent["text"]
        assert [b.callback_data for b in sent["reply_markup"].inline_keyboard[0]][0].startswith("evreq_ok_")
        again, _ = await submit(db, token)
        assert again.status_code == 410
        saved = await db.get_event_request(token=token)
        assert saved["status"] == "submitted" and saved["cover_path"].endswith(".png")
    run(tmp_path, body)


def test_approval_publishes_to_both_groups_once(tmp_path):
    async def body(db):
        token = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        await submit(db, token)
        rid = (await db.get_event_request(token=token))["id"]
        bot = MagicMock(edit_message_reply_markup=AsyncMock())
        tg = AsyncMock(return_value=SimpleNamespace(message_id=55))
        text, poll = AsyncMock(return_value="x"), AsyncMock(return_value="true_111@g.us_POLL1_x")
        with patch("bot.handlers.calendar.send_message_with_optional_cover", new=tg), \
             patch.object(whatsapp_sender, "send_text", new=text), patch.object(whatsapp_sender, "send_poll", new=poll), \
             patch.object(db, "get_handler_routing", new=AsyncMock(return_value={"play_topic_id": 341})):
            first = await shared_events.approve_request(bot, db, rid, "real")
            second = await shared_events.approve_request(bot, db, rid, "real")
        assert first["status"] == "ok" and second["status"] == "already_decided"
        card = tg.await_args.kwargs["text"]
        assert tg.await_args.kwargs["chat_id"] == -100 and tg.await_args.kwargs["message_thread_id"] == 341
        assert "דנה" in card and "https://example.com/x" in card and "תל אביב" in card
        assert text.await_args.args[0] == "111@g.us" and poll.await_count == 1
        event = await db.get_event(first["event_id"])
        assert event["wa_poll_stanza"] == "POLL1" and event["card_text"] == card
        assert (await db.get_event_request(request_id=rid))["status"] == "approved"
    run(tmp_path, body)


def test_test_mode_posts_only_to_the_two_test_groups(tmp_path):
    async def body(db):
        token = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        await submit(db, token)
        rid = (await db.get_event_request(token=token))["id"]
        bot = MagicMock(edit_message_reply_markup=AsyncMock())
        tg = AsyncMock(return_value=SimpleNamespace(message_id=7))
        text, poll = AsyncMock(return_value="x"), AsyncMock(return_value="true_222@g.us_POLL2_x")
        with patch("bot.handlers.calendar.send_message_with_optional_cover", new=tg), \
             patch.object(whatsapp_sender, "send_text", new=text), patch.object(whatsapp_sender, "send_poll", new=poll):
            assert (await shared_events.approve_request(bot, db, rid, "test"))["status"] == "ok"
        assert tg.await_args.kwargs["chat_id"] == -200 and tg.await_args.kwargs["message_thread_id"] is None
        assert text.await_args.args[0] == "222@g.us"
    run(tmp_path, body)


def test_trusted_member_events_publish_without_waiting_for_approval(tmp_path):
    async def body(db):
        first = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        await submit(db, first)
        rid = (await db.get_event_request(token=first))["id"]
        await shared_events.trust_requester(db, rid)
        assert await shared_events.is_trusted(db, "whatsapp", "5@lid")
        await db.update_event_request(rid, status="approved")
        second = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        tg = AsyncMock(return_value=SimpleNamespace(message_id=9))
        with patch("bot.handlers.calendar.send_message_with_optional_cover", new=tg), \
             patch.object(whatsapp_sender, "send_text", new=AsyncMock(return_value="x")), \
             patch.object(whatsapp_sender, "send_poll", new=AsyncMock(return_value="true_222@g.us_P3_x")):
            r, admin = await submit(db, second)
        assert r == {"status": "ok"} and tg.await_count == 1
        assert (await db.get_event_request(token=second))["status"] == "approved"
        assert admin.send_message.await_args.kwargs["reply_markup"] is None
        await db.remove_trusted_creator("whatsapp", "5")
        assert not await shared_events.is_trusted(db, "whatsapp", "5@lid")
    run(tmp_path, body)


def test_co_admin_added_by_number_can_open_the_form_in_a_private_chat_but_strangers_cannot(tmp_path):
    async def body(db):
        with patch.object(whatsapp_sender, "lid_for_phone", new=AsyncMock(return_value="145543674253364@lid")):
            added = await shared_events.add_trusted_by_phone(db, "+972 54-564-7477", "עמרי")
        assert added["ids"] == ["972545647477", "145543674253364"]
        for spelling in ("972545647477@c.us", "145543674253364@lid"):
            r = await dash.agent_event_request(req({"chat_id": spelling, "requester": spelling, "name": "עמרי"}, agent=True), db)
            assert r["status"] == "ok"
        with pytest.raises(HTTPException) as e:
            await dash.agent_event_request(req({"chat_id": "972500000000@c.us", "requester": "972500000000@c.us"}, agent=True), db)
        assert e.value.status_code == 403
        with patch.object(shared_events, "member_cfg", return_value={"trusted_max_per_day": 0}):
            assert not await shared_events.is_trusted(db, "whatsapp", "972545647477@c.us")  # daily cap reached
        await db.remove_trusted_creator("whatsapp", "972545647477")
        assert await db.list_trusted_creators() == []  # both spellings leave together
    run(tmp_path, body)


def test_rejected_requests_are_never_published(tmp_path):
    async def body(db):
        token = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        await submit(db, token)
        rid = (await db.get_event_request(token=token))["id"]
        assert await shared_events.reject_request(db, rid)
        assert (await shared_events.approve_request(MagicMock(), db, rid))["status"] == "already_decided"
        assert await db.get_all_events() == []
    run(tmp_path, body)


def test_expired_link_shows_the_expired_page(tmp_path):
    async def body(db):
        await db.create_event_request(token="old", platform="telegram", requester_id="1", requester_name="x",
                                      created_at="2000-01-01", expires_at="2000-01-01T01:00:00")
        page = await dash.member_event_form("old", req(), db)
        assert load_copy("member_events", "form_expired_title") in page.body.decode()
        r, _ = await submit(db, "old")
        assert r.status_code == 410
    run(tmp_path, body)
