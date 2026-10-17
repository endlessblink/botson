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
    return await dash.agent_event_request(req({"chat_id": requester, "requester": requester, "name": name}, agent=True), db)


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
    assert shared_events.asks_for_event("איוונט", private=True) and not shared_events.asks_for_event("איוונט")


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


def test_a_link_with_invisible_marks_glued_on_by_whatsapp_still_opens(tmp_path):
    async def body(db):
        token = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        found = await db.get_event_request(token=token + "\u200f")
        assert found and found["token"] == token
    run(tmp_path, body)


def test_asking_again_refreshes_the_opener_name_on_the_open_link(tmp_path):
    async def body(db):
        first = await whatsapp_link(db, name="בדיקה")
        again = await whatsapp_link(db, name="דנה")
        assert again["url"] == first["url"]
        saved = await db.get_event_request(token=first["url"].rsplit("/", 1)[1])
        assert saved["requester_name"] == "דנה"
    run(tmp_path, body)


def test_a_trusted_member_without_a_whatsapp_name_is_still_named_on_the_card(tmp_path):
    async def body(db):
        await db.add_trusted_creator("whatsapp", "5", "נועם", "2026-10-17T00:00:00")
        link = await whatsapp_link(db, name="")
        saved = await db.get_event_request(token=link["url"].rsplit("/", 1)[1])
        assert saved["requester_name"] == "נועם"
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


def test_co_admin_added_by_number_can_open_the_form_in_a_private_chat_and_strangers_get_a_form_that_waits_for_approval(tmp_path):
    async def body(db):
        with patch.object(whatsapp_sender, "lid_for_phone", new=AsyncMock(return_value="145543674253364@lid")):
            added = await shared_events.add_trusted_by_phone(db, "+972 54-564-7477", "עמרי")
        assert added["ids"] == ["972545647477", "145543674253364"]
        for spelling in ("972545647477@c.us", "145543674253364@lid"):
            r = await dash.agent_event_request(req({"chat_id": spelling, "requester": spelling, "name": "עמרי"}, agent=True), db)
            assert r["status"] == "ok"
        stranger = await dash.agent_event_request(req({"chat_id": "972500000000@c.us", "requester": "972500000000@c.us"}, agent=True), db)
        assert stranger["status"] == "ok"                           # anyone may ask for a form privately
        assert not await shared_events.is_trusted(db, "whatsapp", "972500000000@c.us")  # but publishing still needs approval
        with patch.object(shared_events, "member_cfg", return_value={"trusted_max_per_day": 0}):
            assert not await shared_events.is_trusted(db, "whatsapp", "972545647477@c.us")  # daily cap reached
        await db.remove_trusted_creator("whatsapp", "972545647477")
        assert await db.list_trusted_creators() == []  # both spellings leave together
    run(tmp_path, body)


def test_operator_switch_decides_where_trusted_events_go(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "bot.db"))
    assert shared_events.default_mode() == "test"            # the shipped default is the safe one
    shared_events.set_default_mode("real")
    assert shared_events.targets_for(None) == ("main", "whatsapp")
    shared_events.set_default_mode("test")
    assert shared_events.targets_for(None) == ("test", "whatsapp_test")
    with pytest.raises(ValueError):
        shared_events.set_default_mode("everywhere")


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


def test_form_checkbox_sets_live_or_test_for_trusted_only(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "bot.db"))

    async def body(db):
        stranger = (await whatsapp_link(db, requester="9@lid"))["url"].rsplit("/", 1)[1]
        page = await dash.member_event_form(stranger, req({}), db)
        assert b'id="live"' not in page.body                      # strangers never see the switch
        await submit(db, stranger, {**FORM, "live": True})
        assert shared_events.default_mode() == "test"              # and cannot flip it
        first = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        await submit(db, first)
        await shared_events.trust_requester(db, (await db.get_event_request(token=first))["id"])
        await db.update_event_request((await db.get_event_request(token=first))["id"], status="approved")
        second = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        page = await dash.member_event_form(second, req({}), db)
        assert b'id="live"' in page.body and b'id="live" checked' not in page.body
        tg = AsyncMock(return_value=SimpleNamespace(message_id=9))
        with patch("bot.handlers.calendar.send_message_with_optional_cover", new=tg), \
             patch.object(whatsapp_sender, "send_text", new=AsyncMock(return_value="x")), \
             patch.object(whatsapp_sender, "send_poll", new=AsyncMock(return_value="true_222@g.us_P3_x")):
            await submit(db, second, {**FORM, "live": True})
        assert shared_events.default_mode() == "real"
        third = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        page = await dash.member_event_form(third, req({}), db)
        assert b'id="live" checked' in page.body                   # the form remembers the switch
    run(tmp_path, body)


def test_saved_template_shows_up_on_the_next_form(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "bot.db"))

    async def body(db):
        first = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        await submit(db, first)
        rid = (await db.get_event_request(token=first))["id"]
        await shared_events.trust_requester(db, rid)
        await db.update_event_request(rid, status="approved")
        second = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        tg = AsyncMock(return_value=SimpleNamespace(message_id=9))
        with patch("bot.handlers.calendar.send_message_with_optional_cover", new=tg), \
             patch.object(whatsapp_sender, "send_text", new=AsyncMock(return_value="x")), \
             patch.object(whatsapp_sender, "send_poll", new=AsyncMock(return_value="true_222@g.us_P3_x")):
            await submit(db, second, {**FORM, "title": "ערב פוקר", "save_template": True})
        third = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
        page = await dash.member_event_form(third, req({}), db)
        import json
        assert json.dumps("ערב פוקר").encode() in page.body.replace(b"\\u", b"\\u") or "ערב פוקר".encode() in page.body
        assert b'id="save-tpl"' in page.body
    run(tmp_path, body)


def test_templates_can_be_updated_and_deleted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "bot.db"))
    shared_events.save_template(title="ערב פוקר", description="א")
    shared_events.save_template(title="ערב פוקר", description="ב")
    names = [t["name"] for t in shared_events.load_templates()]
    assert names.count("ערב פוקר") == 1
    shared_events.save_template(title="פוקר חדש", description="ג", replaces="ערב פוקר")
    names = [t["name"] for t in shared_events.load_templates()]
    assert "פוקר חדש" in names and "ערב פוקר" not in names
    shipped = [t["name"] for t in shared_events.member_cfg().get("templates") or []]
    for n in shipped + ["פוקר חדש"]:
        shared_events.delete_template(n)
    assert shared_events.load_templates() == []


def test_trusted_people_are_not_held_to_the_daily_form_limit(tmp_path):
    async def body(db):
        for _ in range(3):
            tok = (await whatsapp_link(db))["url"].rsplit("/", 1)[1]
            await db.update_event_request((await db.get_event_request(token=tok))["id"], status="submitted")
        assert (await whatsapp_link(db))["status"] == "limit"
        await shared_events.trust_requester(db, (await db.get_event_request(token=tok))["id"])
        assert (await whatsapp_link(db))["status"] == "ok"
    run(tmp_path, body)


def test_saving_over_a_shipped_template_does_not_duplicate_and_delete_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "bot.db"))
    name = shared_events.member_cfg()["templates"][0]["name"]
    shared_events.save_template(title=name, description="חדש")
    same = [t for t in shared_events.load_templates() if t["name"] == name]
    assert len(same) == 1 and same[0]["description"] == "חדש"
    shared_events.delete_template(name)
    assert all(t["name"] != name for t in shared_events.load_templates())


def test_template_edge_cases(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "bot.db"))
    names = lambda: [t["name"] for t in shared_events.load_templates()]
    shipped = names()
    # nothing to save without a title
    assert shared_events.save_template(title="") is False and shared_events.save_template(title="   ") is False
    assert names() == shipped
    # spaces and letter case do not make a second copy
    shared_events.save_template(title="Poker  Night")
    shared_events.save_template(title="  poker night ")
    assert sum(1 for n in names() if n.casefold().replace("  ", " ").strip() == "poker night") == 1
    # deleting by a differently spelled name removes it; deleting nothing or something unknown is harmless
    shared_events.delete_template("POKER NIGHT")
    shared_events.delete_template("")
    shared_events.delete_template("never existed")
    assert not any("poker" in n.lower() for n in names())
    # saving again after deleting brings it back
    shared_events.save_template(title="Poker Night")
    assert "Poker Night" in names()
    # a shipped template can be deleted and re-saved
    s0 = shipped[0]
    shared_events.delete_template(s0)
    assert s0 not in names()
    shared_events.save_template(title=s0, description="x")
    assert names().count(s0) == 1
    # renaming the template the form started from replaces it, even a shipped one
    shared_events.save_template(title="שם חדש", replaces=s0)
    assert "שם חדש" in names() and s0 not in names()
    # replacing a name that does not exist just saves the new one
    shared_events.save_template(title="עוד אחד", replaces="לא קיים")
    assert "עוד אחד" in names()
    # oversized or odd input is trimmed, bad time dropped, markup stays plain text
    shared_events.save_template(title="<b>" + "א" * 500, description="ב" * 5000, location="ג" * 500,
                                time="25:99", link="https://x/" + "ד" * 900)
    big = [t for t in shared_events.load_templates() if t["name"].startswith("<b>")][0]
    cfg = shared_events.member_cfg()
    assert len(big["name"]) <= cfg["max_title_chars"] and len(big["description"]) <= cfg["max_description_chars"]
    assert len(big["location"]) <= 120 and len(big["link"]) <= 300 and big["time"] == ""
    # a damaged file never breaks the form
    shared_events._templates_file().write_text("{not json", encoding="utf-8")
    assert isinstance(shared_events.load_templates(), list)
    shared_events._templates_file().write_text('"just a string"', encoding="utf-8")
    assert isinstance(shared_events.load_templates(), list)
    # the list is capped, oldest dropped
    for i in range(45):
        shared_events.save_template(title=f"t{i}")
    assert sum(1 for n in names() if n.startswith("t") and n[1:].isdigit()) == cfg.get("max_saved_templates", 30)
    assert "t44" in names() and "t0" not in names()


def test_tagging_in_a_group_points_to_a_private_chat_instead_of_posting_the_link(tmp_path, monkeypatch):
    monkeypatch.setenv("WHATSAPP_GROUP_ID", "111@g.us")

    async def body(db):
        r = await dash.agent_event_request(req({"chat_id": "111@g.us", "requester": "5@lid", "name": "דנה"}, agent=True), db)
        assert r["status"] == "private_only" and "url" not in r and "http" not in r["text"]
        assert await db.list_event_requests(platform="whatsapp", requester_id="5@lid", since="2000-01-01") == []
    run(tmp_path, body)
