"""Full event text from a screenshot, and "where" as in person / online (Google Meet, Zoom...).
Every AI, Telegram and WhatsApp call is faked."""

import json
import re
from unittest.mock import AsyncMock, MagicMock, patch

import bot.handlers.calendar as calendar_handler
from bot.utils import community_page, event_place, shared_events, whatsapp_sender
from bot.utils.copy import load_copy
from dashboard import app as dash
from dashboard import event_picture as ep
from tests.test_event_picture import MEMBER, env, page, req, run  # noqa: F401  (env: the shared fixture)

MEET = "https://meet.google.com/abc-defg-hij"
LONG = "\n".join(f"שורה {i}: מביאים משחק, מצטרפים מהקישור, ונשארים לשיחה אחרי." for i in range(25))  # ~1300 chars


def form(**over):
    base = {"title": "ערב משחקים", "event_date": "2099-01-01", "event_time": "20:00"}
    return shared_events.clean_form_fields({**base, **over})


# ── 1. the full text ──

def test_the_reader_is_told_to_copy_the_whole_text_and_the_limit_comes_from_config():
    prompt = ep._prompt()
    assert "a few words" not in prompt
    assert "copied in full, word for word" in prompt and "Never shorten, summarize or rewrite" in prompt
    limit = shared_events.member_cfg()["max_description_chars"]
    assert limit >= 1500 and str(limit) in prompt and "{max_desc}" not in prompt


def test_a_long_description_is_kept_whole():
    assert 1000 < len(LONG) <= shared_events.member_cfg()["max_description_chars"]
    fields, errors = form(description=LONG)
    assert fields["description"] == LONG and not errors
    assert ep.clean_reply({"title": "x", "event_date": "2099-01-01", "start_time": "20:00", "description": LONG})["description"] == LONG


def test_a_long_card_with_a_picture_goes_out_as_picture_then_text(tmp_path, monkeypatch):
    (tmp_path / "covers").mkdir()
    (tmp_path / "covers" / "a.png").write_bytes(b"png")
    monkeypatch.setattr(calendar_handler, "_media_dir", lambda: tmp_path)
    sent = []

    async def fake_send(bot, db, method, **kw):
        sent.append((method, kw))
        return MagicMock(message_id=len(sent))

    monkeypatch.setattr(calendar_handler, "safe_send", fake_send)
    import asyncio

    msg = asyncio.run(calendar_handler.send_message_with_optional_cover(
        MagicMock(), db=None, chat_id=-1, text="x" * 2000, cover_path="covers/a.png", message_thread_id=3))
    assert [m for m, _ in sent] == ["send_photo", "send_message"]
    assert "caption" not in sent[0][1] and sent[1][1]["text"] == "x" * 2000 and msg.message_id == 2
    sent.clear()
    asyncio.run(calendar_handler.send_message_with_optional_cover(
        MagicMock(), db=None, chat_id=-1, text="short", cover_path="covers/a.png", message_thread_id=3))
    assert [m for m, _ in sent] == ["send_photo"] and sent[0][1]["caption"] == "short"


# ── 2. in person / online, platform from config ──

def test_platform_names_come_from_the_link_host():
    assert event_place.platform_for(MEET) == "Google Meet"
    assert event_place.platform_for("https://us02web.zoom.us/j/123") == "Zoom"
    assert event_place.platform_for("https://teams.microsoft.com/l/meetup-join/x") == "Microsoft Teams"
    assert event_place.platform_for("https://discord.gg/abc") == "Discord"
    assert event_place.platform_for("https://example.com/call") is None
    assert event_place.platform_for("https://meet.google.com.evil.example/x") is None
    assert event_place.place_label("online", MEET, None) == load_copy("member_events", "place_online_platform", platform="Google Meet")
    assert event_place.place_label("online", "https://example.com/call", None) == load_copy("member_events", "place_online")
    assert event_place.place_label("in_person", None, "בית קפה, יפו") == "בית קפה, יפו"


def test_online_needs_a_real_link_and_switching_back_keeps_both_values():
    fields, errors = form(place_type="online", online_link="")
    assert errors.get("online_link") == "form_error_online_link"
    fields, errors = form(place_type="online", online_link="javascript:alert(1)")
    assert errors.get("online_link") == "form_error_online_link"
    fields, errors = form(place_type="in_person", location="סטודיו ביפו", online_link=MEET)
    assert not errors and fields["place_type"] == "in_person"
    assert fields["location"] == "סטודיו ביפו" and fields["online_link"] == MEET  # nothing lost
    fields, errors = form(place_type="online", location="סטודיו ביפו", online_link=MEET)
    assert not errors and fields["place_type"] == "online" and fields["location"] == "סטודיו ביפו"
    assert form(place_type="nonsense")[0]["place_type"] == "in_person"


# ── 3. picture and old habits land as online ──

def test_a_bare_meeting_address_typed_as_the_place_is_online():
    fields, errors = form(location=MEET)
    assert not errors and fields["place_type"] == "online" and fields["online_link"] == MEET and fields["location"] == ""


def test_the_reader_sets_online_from_the_picture():
    base = {"title": "ערב משחקים", "event_date": "2099-01-01", "start_time": "20:00"}
    for answer in ({"online_link": MEET}, {"location": MEET}, {"link": MEET}):
        out = ep.clean_reply({**base, **answer})
        assert out["place_type"] == "online" and out["online_link"] == MEET, answer
        assert "link" not in out and "location" not in out
    out = ep.clean_reply({**base, "location": "בר, חיפה", "link": "https://tickets.example.com/x"})
    assert "place_type" not in out and out["location"] == "בר, חיפה"


def test_the_form_opens_on_the_right_choice(tmp_path):
    async def body(db):
        link = await dash.agent_event_request(req({"chat_id": MEMBER, "requester": MEMBER, "name": "דנה"}), db)
        html = await page(db, link)
        assert re.search(r'data-place="in_person" aria-checked="true"', html)
        assert re.search(r'id="place-on" hidden', html)
        token = link["url"].rsplit("/", 1)[1]
        row = await db.get_event_request(token=token)
        draft = {"title": "ערב משחקים", "event_date": "2099-01-01", "start_time": "20:00", "online_link": MEET}
        await db.update_event_request(row["id"], prefill=json.dumps(draft))
        html = await page(db, link)
        assert re.search(r'data-place="online" aria-checked="true"', html)
        assert f'value="{MEET}"' in html and 'id="place-in" hidden' in html
        assert '"Google Meet"' in html  # the platform list reaches the page (from config)
    run(tmp_path, body)


# ── 5. downstream ──

def _req(**over):
    return {"title": "ערב משחקים", "description": "", "event_date": "2099-01-01", "event_time": "20:00",
            "location": None, "link": None, "requester_name": "דנה", "organizer": None,
            "place_type": "online", "online_link": MEET, **over}


def test_the_posts_say_online_with_the_platform_and_carry_the_link_for_members():
    label = load_copy("member_events", "place_online_platform", platform="Google Meet")
    where = load_copy("member_events", "card_where_online", location=label)
    wa = shared_events.request_card(_req())
    assert where in wa.split("\n") and MEET in wa
    tg, links = shared_events.telegram_card(**shared_events._request_fields(_req()))
    assert where in tg.split("\n") and links[0]["url"] == MEET
    both = shared_events.request_card(_req(link="https://tickets.example.com/x"))
    assert load_copy("member_events", "card_online_link", link=MEET) in both.split("\n")
    in_person = shared_events.request_card(_req(place_type="in_person", online_link=None, location="בר, חיפה"))
    assert load_copy("member_events", "card_where", location="בר, חיפה") in in_person.split("\n")


def test_publishing_keeps_the_label_never_the_link_and_public_readers_show_online(tmp_path, monkeypatch):
    monkeypatch.setenv("GROUP_ID", "-100")
    label = load_copy("member_events", "place_online_platform", platform="Google Meet")

    async def body(db):
        link = await dash.agent_event_request(req({"chat_id": MEMBER, "requester": MEMBER, "name": "דנה"}), db)
        row = await db.get_event_request(token=link["url"].rsplit("/", 1)[1])
        await db.update_event_request(row["id"], status="submitted", title="ערב משחקים", event_date="2099-01-01",
                                      event_time="20:00", place_type="online", online_link=MEET)
        tg = AsyncMock(return_value=MagicMock(message_id=7))
        with patch.object(calendar_handler, "send_message_with_optional_cover", tg), \
                patch.object(whatsapp_sender, "send_text", AsyncMock()), \
                patch.object(whatsapp_sender, "send_poll", AsyncMock(return_value="true_2@g.us_ABC_x")):
            r = await shared_events.approve_request(MagicMock(edit_message_reply_markup=AsyncMock(),
                                                              pin_chat_message=AsyncMock()), db, row["id"], "real")
        assert r["status"] in ("ok", "partial")
        async with db._db.execute("SELECT location, place_type, description FROM events WHERE id = ?",
                                  (r["event_id"],)) as cur:
            loc, kind, desc = await cur.fetchone()
        assert loc == label and kind == "online" and MEET not in f"{loc} {desc}"  # the group post (card) has it, readers don't
        events = await community_page.public_events(db, __import__("datetime").date(2026, 11, 28))
        assert [e["place"] for e in events] == [label] and MEET not in json.dumps(events, default=str)
        assert community_page.public_place(loc) == label
    run(tmp_path, body)


def test_meeting_links_never_reach_public_pages():
    for url in (MEET, "https://us02web.zoom.us/j/1", "https://zoom.us/j/1", "https://teams.microsoft.com/l/x",
                "https://teams.live.com/meet/1", "https://meet.jit.si/x", "https://whereby.com/x",
                "https://acme.webex.com/meet/x", "https://discord.gg/x"):
        assert community_page.public_link(url) is None, url
        assert community_page.event_link(None, f"מצטרפים כאן {url}") is None, url
    assert community_page.public_link("https://tickets.example.com/x") == "https://tickets.example.com/x"
    assert community_page.public_place(MEET) == load_copy("member_events", "place_online_platform", platform="Google Meet")


def test_old_events_stay_in_person(tmp_path):
    async def body(db):
        eid = await db.create_event(title="ערב", description="", event_date="2099-01-01", event_time="20:00",
                                    location="בר, חיפה", created_by=0)
        async with db._db.execute("SELECT place_type FROM events WHERE id = ?", (eid,)) as cur:
            assert (await cur.fetchone())[0] == "in_person"
        link = await dash.agent_event_request(req({"chat_id": MEMBER, "requester": MEMBER, "name": "דנה"}), db)
        row = await db.get_event_request(token=link["url"].rsplit("/", 1)[1])
        assert row["place_type"] == "in_person" and row["online_link"] is None
    run(tmp_path, body)
