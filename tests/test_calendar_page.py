"""The events calendar, now part of the public page /welcome: months, past and empty months, the Google
Calendar link and calendar file (reduced like the page: city only, cleaned text, no join link), the old
/cal addresses redirecting, and the caption line leading to it. Nothing real is sent."""

import asyncio
import html
import re
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from starlette.requests import Request

import bot.handlers.calendar  # noqa: F401
from bot.database.db import Database
from bot.utils import calendar_page as cp
from bot.utils import calendar_share as cs
from bot.utils import config, events_calendar
from bot.utils.copy import load_copy
from dashboard import app as dash

MAIN, TEST = -1001, -1002
TODAY = date(2026, 11, 26)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GROUP_ID", str(MAIN))
    monkeypatch.setenv("TEST_GROUP_ID", str(TEST))
    monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "https://botson.example")
    real_now = events_calendar.local_now
    fixed = real_now().replace(year=2026, month=11, day=26, hour=12, minute=0)
    monkeypatch.setattr(events_calendar, "local_now", lambda: fixed)


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "cal.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


async def event(db, title, day, time="20:00", *, place=None, desc="", cover=None, chat=MAIN, request=None, link=None,
                organizer=None, requester_name="דנה", active=True):
    eid = await db.create_event(title=title, description=desc, event_date=day, event_time=time, location=place,
                                created_by=0, cover_path=cover)
    await db.update_event(eid, message_id=5, tg_chat_id=chat)
    if not active:
        await db._db.execute("UPDATE events SET active = 0 WHERE id = ?", (eid,))
    if request:
        await db._db.execute(
            "INSERT INTO event_requests (token, platform, requester_id, requester_name, created_at, expires_at, status, "
            "title, event_date, event_time, link, organizer, event_id) VALUES (?, 'whatsapp', '972500000001@c.us', ?, "
            "'2026-11-01', '2026-11-02', ?, ?, ?, ?, ?, ?, ?)",
            (f"tok{eid}", requester_name, request, title, day, time, link, organizer, eid))
    await db._db.commit()
    return eid


def http_request(path="/welcome"):
    return Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": [],
                    "app": dash.app})


async def page(db, month=None):
    r = await dash.community_page(http_request(), month, db)
    return r, r.body.decode()


async def opened(db, eid):
    r = await dash.community_event_page(http_request(f"/welcome/e/{eid}"), eid, db)
    return r, r.body.decode()


def q(url):
    return {k: v[0] for k, v in parse_qs(urlsplit(html.unescape(url)).query).items()}


# ── Months ──

def test_this_month_by_default_and_other_months_by_address(tmp_path):
    async def body(db):
        await event(db, "פיקניק", "2026-11-28")
        await event(db, "יוגה", "2026-12-10")
        _, text = await page(db)
        assert "פיקניק" in text and "יוגה" not in text
        assert "נובמבר 2026" in text and 'href="/welcome/2026-10#events"' in text and 'href="/welcome/2026-12#events"' in text
        assert 'href="/welcome#events"' not in text  # already this month
        _, text = await page(db, "2026-12")
        assert "יוגה" in text and "פיקניק" not in text and "דצמבר 2026" in text
        assert 'href="/welcome#events"' in text  # back to this month
        _, text = await page(db, "2026-01")
        assert 'href="/welcome/2025-12#events"' in text
        for bad in ("2026-13", "november", "0001-01"):
            with pytest.raises(HTTPException) as e:
                await page(db, bad)
            assert e.value.status_code == 404
    run(tmp_path, body)


def test_the_month_grid_shows_events_inside_their_days(tmp_path, monkeypatch):
    async def body(db):
        a = await event(db, "פיקניק", "2026-11-28", "12:00", cover="covers/a.png")
        b = await event(db, "יוגה", "2026-11-28", "21:00")
        c = await event(db, "ערב שלישי", "2026-11-28", "22:00")
        _, text = await page(db)
        grid = text[text.index('<div class="grid"'):text.index('<ol class="line">')]
        cells = re.findall(r'<div class="cell[^"]*"', grid)
        assert len(cells) == 35  # November 2026 starts on a Sunday: 30 days + 5 blank cells
        assert grid.index('class="cell out"') > grid.index('<span class="n">1<')  # day 1 is the first cell
        day = re.search(r'<div class="cell[^"]*has[^"]*"><span class="n">28</span>.*?</div>', grid, re.S).group(0)
        chips = re.findall(r'<a class="chip" href="/welcome/e/(\d+)">(.*?)</a>', day, re.S)
        assert [int(x[0]) for x in chips] == [a, b]  # the picture's limit: two per day, then "+N"
        assert 'src="/media/covers/a.png"' in chips[0][1] and "פיקניק" in chips[0][1] and "12:00" in chips[0][1]
        assert "<img" not in chips[1][1] and "יוגה" in chips[1][1] and "21:00" in chips[1][1]
        more = load_copy("events_calendar", "more", n=1)
        assert f'<a class="more" href="/welcome/e/{c}">{html.escape(more)}</a>' in day
        assert re.search(r'class="cell[^"]* today[^"]*"><span class="n">26<', grid)
        assert f'id="ev-{c}"' in text  # still ahead, so it also has a big card
        _, empty = await page(db, "2027-01")
        assert 'class="grid"' in empty and html.escape(load_copy("community_page", "empty_title", month="ינואר")) in empty
    run(tmp_path, body)


def cards(text):
    return re.findall(r'<li class="ev[^"]*"[^>]*>.*?</li>', text, re.S)


def test_a_past_event_never_gets_a_big_card(tmp_path):
    async def body(db):
        old = await event(db, "היה מזמן", "2026-11-05")
        soon = await event(db, "עוד מעט", "2026-11-28")
        later = await event(db, "בדצמבר", "2026-12-03")
        _, text = await page(db)  # this month: one past, one ahead
        assert [c for c in cards(text) if "היה מזמן" in c] == []
        assert len(cards(text)) == 1 and "עוד מעט" in cards(text)[0]
        grid = text[text.index('<div class="grid"'):]
        assert f'<a class="chip" href="/welcome/e/{old}">' in grid  # still in the grid, still opens
        assert re.search(r'<div class="cell[^"]* past[^"]*"><span class="n">5</span>', grid)
        assert 'class="was-list"' not in text and html.escape(load_copy("community_page", "past")) not in text
        await db._db.execute("UPDATE events SET event_date = '2026-11-20' WHERE id = ?", (soon,))
        await db._db.commit()
        _, text = await page(db)  # nothing left this month: next month's events are the cards
        assert len(cards(text)) == 1 and "בדצמבר" in cards(text)[0] and f'id="ev-{later}"' in text
        assert html.escape(load_copy("community_page", "all_past", month="נובמבר")) in text
        assert f'<p class="quiet">' in text
    run(tmp_path, body)


def test_a_month_that_is_over_gets_a_compact_list_not_cards(tmp_path):
    async def body(db):
        old = await event(db, "ערב באוקטובר", "2026-10-15", place="חיפה")
        _, text = await page(db, "2026-10")
        assert not any("ערב באוקטובר" in c for c in cards(text))
        listed = re.search(r'<ul class="was-list">.*?</ul>', text, re.S).group(0)
        assert f'href="/welcome/e/{old}"' in listed and "ערב באוקטובר" in listed and "חיפה" in listed
    run(tmp_path, body)


def test_an_empty_month_says_so_and_points_to_what_is_next(tmp_path):
    async def body(db):
        _, text = await page(db, "2027-01")
        assert html.escape(load_copy("community_page", "empty_title", month="ינואר")) in text
        assert html.escape(load_copy("community_page", "upcoming")) not in text
        await event(db, "ערב בפברואר", "2027-02-10")
        _, text = await page(db, "2027-01")
        assert html.escape(load_copy("community_page", "upcoming")) in text and "ערב בפברואר" in text
    run(tmp_path, body)


def test_month_pages_are_kept_out_of_search_engines(tmp_path):
    async def body(db):
        r, text = await page(db, "2026-12")
        assert "noindex" in r.headers["x-robots-tag"] and '<meta name="robots" content="noindex' in text
        assert "<script src=" not in text and "googleapis" not in text
    run(tmp_path, body)


# ── Google Calendar and the calendar file: what they carry ──

EV = {"id": 7, "title": "סדנה, קרמיקה; ערב", "date": date(2026, 11, 28), "time": "17:00", "place": "יפו",
      "description": "שורה ראשונה\nשורה שנייה"}


def test_google_link_is_googles_own_prefilled_screen():
    u = urlsplit(cp.google_url(EV))
    assert (u.scheme, u.netloc, u.path) == ("https", "calendar.google.com", "/calendar/render")
    g = q(cp.google_url(EV))
    assert g["action"] == "TEMPLATE" and g["text"] == EV["title"] and g["ctz"] == "Asia/Jerusalem"
    assert g["dates"] == "20261128T170000/20261128T190000"  # 2 hours from config
    assert g["location"] == "יפו" and g["details"] == "שורה ראשונה\nשורה שנייה"
    assert q(cp.google_url({**EV, "time": None}))["dates"] == "20261128/20261129"
    assert "location" not in q(cp.google_url({**EV, "place": None}))
    assert "details" not in q(cp.google_url({**EV, "description": None}))


def test_the_length_comes_from_config(monkeypatch):
    monkeypatch.setattr(cp, "page_cfg", lambda: {"event_length_minutes": 90})
    assert q(cp.google_url(EV))["dates"] == "20261128T170000/20261128T183000"


def test_calendar_file_is_a_correct_standard_file():
    text = cp.ics_text(EV, host="botson.example", now=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc))
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    assert all(len(line.encode()) <= 75 for line in text.split("\r\n"))
    unfolded = text.replace("\r\n ", "")
    assert "BEGIN:VCALENDAR" in unfolded and "VERSION:2.0" in unfolded and "TZID:Asia/Jerusalem" in unfolded
    assert "UID:event-7@botson.example" in unfolded and "DTSTAMP:20261126T100000Z" in unfolded
    assert "DTSTART;TZID=Asia/Jerusalem:20261128T170000" in unfolded
    assert "DTEND;TZID=Asia/Jerusalem:20261128T190000" in unfolded
    assert "SUMMARY:סדנה\\, קרמיקה\\; ערב" in unfolded and "LOCATION:יפו" in unfolded
    assert "DESCRIPTION:שורה ראשונה\\nשורה שנייה" in unfolded and "URL:" not in unfolded
    allday = cp.ics_text({**EV, "time": None, "place": None, "description": None})
    assert "DTSTART;VALUE=DATE:20261128" in allday and "LOCATION" not in allday and "DESCRIPTION" not in allday


PRIVATE = ("הרצל 12", "רחוב הרצל", "פארק הירקון", "054-1234567", "maya@example.com", "@maya", "EVENTLINK999",
           "chat.whatsapp.com", "REQLINK", "מאיה לוי", "יוסי ישראלי", "972500000001")


def test_the_calendar_entries_carry_only_the_public_reduction(tmp_path):
    async def body(db):
        eid = await event(db, "פיקניק @maya 054-1234567", "2026-12-12", "17:00",
                          place="פארק הירקון, רחוב הרצל 12, תל אביב",
                          desc="פיקניק אחרי הצהריים.\nנפגשים ברחוב הרצל 12\nפרטים: 054-1234567 maya@example.com\n"
                               "https://chat.whatsapp.com/EVENTLINK999",
                          request="approved", link="https://chat.whatsapp.com/REQLINK", organizer="מאיה לוי",
                          requester_name="יוסי ישראלי")
        _, one = await opened(db, eid)
        gurl = html.unescape(re.search(r'class="gbtn" href="([^"]+)"', one).group(1))
        g = q(gurl)
        assert g["text"] == "פיקניק" and g["location"] == "תל אביב" and g["details"].startswith("פיקניק אחרי הצהריים.")
        ics = (await dash.community_event_ics(eid, db)).body.decode().replace("\r\n ", "")
        assert "SUMMARY:פיקניק" in ics and "LOCATION:תל אביב" in ics and "URL:" not in ics
        plain_url = urlsplit(gurl).query and parse_qs(urlsplit(gurl).query)
        decoded = " ".join(v[0] for v in plain_url.values())
        for hidden in PRIVATE:
            assert hidden not in decoded and hidden not in ics and hidden not in one, hidden
        assert f'href="/welcome/e/{eid}.ics"' in one
    run(tmp_path, body)


def test_calendar_file_only_for_published_events(tmp_path):
    async def body(db):
        ok = await event(db, "פיקניק", "2026-11-28")
        hidden = await event(db, "בדיקה", "2026-11-28", chat=TEST)
        r = await dash.community_event_ics(ok, db)
        assert r.media_type.startswith("text/calendar") and f"event-{ok}.ics" in r.headers["content-disposition"]
        assert "noindex" in r.headers["x-robots-tag"]
        with pytest.raises(HTTPException) as e:
            await dash.community_event_ics(hidden, db)
        assert e.value.status_code == 404
    run(tmp_path, body)


def test_a_past_event_opens_without_calendar_buttons(tmp_path):
    async def body(db):
        eid = await event(db, "היה מזמן", "2026-11-05")
        _, one = await opened(db, eid)
        assert "היה מזמן" in one and '<a class="gbtn"' not in one and '.ics"' not in one
        assert 'href="/welcome/2026-11#events"' in one
    run(tmp_path, body)


# ── The old calendar addresses ──

@pytest.mark.parametrize("args,target", [
    ((None, None), "/welcome"),
    (("2026-12", None), "/welcome/2026-12"),
    (("2026-12", 5), "/welcome/e/5"),
    ((None, 5), "/welcome/e/5"),
    (("nonsense", None), "/welcome"),
])
def test_old_cal_addresses_redirect_to_the_one_page(args, target):
    r = asyncio.run(dash.public_calendar(*args))
    assert r.status_code == 302 and r.headers["location"] == target


def test_old_calendar_file_address_redirects():
    r = asyncio.run(dash.public_calendar_ics(5))
    assert r.status_code == 302 and r.headers["location"] == "/welcome/e/5.ics"


def test_the_old_calendar_template_and_copy_are_gone():
    from pathlib import Path

    from bot.utils.copy import load_copy_block

    assert not (Path(dash.__file__).parent / "templates" / "public_calendar.html").exists()
    assert not load_copy_block("calendar_page", default={})
    for gone in ("page_events", "build_page", "when_text", "day_name"):
        assert not hasattr(cp, gone), gone


# ── Captions under the calendar picture ──

def test_captions_lead_to_the_page_on_both_platforms(monkeypatch):
    month = "2026-11"
    words = load_copy("calendar_share", "caption_more")
    text, entities = cs.telegram_caption(month)
    assert text == f"{cs.caption(month)}\n{words}"
    raw = text.encode("utf-16-le")
    (e,) = entities
    assert raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le") == words
    assert e.url == "https://botson.example/cal/2026-11"
    wa = cs.whatsapp_caption(month)
    assert wa.split("\n") == [cs.caption(month), words, "https://botson.example/cal/2026-11"]
    from bot.utils import whatsapp_sender
    assert "https://botson.example/cal/2026-11" in whatsapp_sender.rtl_text(wa).split("\n")  # bare, no mark
    monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "")
    assert cs.telegram_caption(month) == (cs.caption(month), []) and cs.whatsapp_caption(month) == cs.caption(month)


def test_bots_and_dashboard_send_the_caption_with_the_page_line(tmp_path, monkeypatch):
    from bot.handlers import calendar_request as cr
    from bot.utils import shared_events, topic_guard, whatsapp_sender
    from tests.test_calendar_share import FakeBot, ctx, req, update

    for module in (topic_guard, cr):
        monkeypatch.setattr(module, "GROUP_ID", MAIN)
        monkeypatch.setattr(module, "TEST_GROUP_ID", TEST)
    monkeypatch.setattr(events_calendar, "month_png", AsyncMock(return_value=b"PNG"))
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", "agent-secret-value")
    monkeypatch.setenv("WHATSAPP_TEST_GROUP_ID", "222@g.us")

    async def body(db):
        await db.upsert_verified_forum_topic(341, "עדכונים", "welcome", "test")
        await db.set_handler_routing("events_publish", 341)
        c = ctx(db)
        await cr.handle_calendar_words(update('לו"ז', chat=5, kind="private"), c)
        photo = c.bot.photos[0]
        assert photo["caption"].endswith(load_copy("calendar_share", "caption_more"))
        assert photo["caption_entities"][0].url == "https://botson.example/cal/2026-11"
        share = await dash.agent_events_calendar_share(req({"chat_id": "1@lid", "requester": "1@lid", "operator": True}), db)
        assert share["caption"].endswith("\nhttps://botson.example/cal/2026-11")
        monkeypatch.setattr(shared_events, "default_mode", lambda: "test")
        wa = AsyncMock(return_value="x")
        monkeypatch.setattr(whatsapp_sender, "send_image_data", wa)
        tg = FakeBot()
        with patch("telegram.Bot", return_value=tg):
            await dash.events_calendar_send(SimpleNamespace(session={"authenticated": True}, headers={},
                                                            json=AsyncMock(return_value={"month": "2026-11", "mode": "test"})), db)
        assert tg.photos[0]["caption_entities"][0].url == "https://botson.example/cal/2026-11"
        assert wa.await_args.args[4].endswith("\nhttps://botson.example/cal/2026-11")
    run(tmp_path, body)
