"""The public calendar page (/cal): which events show, escaping, Google and calendar-file links,
deep links, no-index headers, empty states, and the caption line leading to it. Nothing real is sent."""

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


def http_request(path="/cal", query=""):
    return Request({"type": "http", "method": "GET", "path": path, "query_string": query.encode(), "headers": [],
                    "app": dash.app})


async def page(db, month=None, e=None, d=None):
    q = "&".join(f"{k}={v}" for k, v in (("e", e), ("d", d)) if v)
    r = await dash.public_calendar(http_request(query=q), month, e, d, db)
    return r, r.body.decode()


# ── What shows ──

def test_only_published_events_show_and_only_card_fields(tmp_path):
    async def body(db):
        await event(db, "ערב שפורסם", "2026-11-28", request="approved", link="https://example.com/join?utm_source=x",
                    organizer="מאיה")
        await event(db, "רק בקבוצת הבדיקה", "2026-11-28", chat=TEST)
        await event(db, "ממתין לאישור", "2026-11-28", request="submitted")
        await event(db, "נמחק", "2026-11-28", active=False)
        _, text = await page(db)
        assert "ערב שפורסם" in text
        for hidden in ("רק בקבוצת הבדיקה", "ממתין לאישור", "נמחק", "972500000001", "utm_source"):
            assert hidden not in text, hidden
        assert "באחריות מאיה" in text
    run(tmp_path, body)


def test_everything_typed_is_escaped_and_bad_links_are_dropped(tmp_path):
    async def body(db):
        await event(db, '<script>alert(1)</script> "ערב"', "2026-11-28", place="<b>בר</b>",
                    desc="<img src=x onerror=alert(1)>", request="approved", link="javascript:alert(1)")
        _, text = await page(db, e=1)
        assert "<script>alert(1)" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text
        assert "<img src=x" not in text and "<b>בר</b>" not in text
        assert "javascript:" not in text
        assert load_copy("calendar_page", "join") not in text  # no usable link, no join button
    run(tmp_path, body)


def test_the_join_link_opens_safely_in_a_new_tab(tmp_path):
    async def body(db):
        await event(db, "פיקניק", "2026-11-28", request="approved", link="https://example.com/join")
        _, text = await page(db, e=1)
        assert re.search(r'<a class="btn primary" href="https://example.com/join" target="_blank" rel="noopener noreferrer">', text)
    run(tmp_path, body)


# ── Google Calendar and the calendar file ──

EV = {"id": 7, "title": "סדנה, קרמיקה; ערב", "date": date(2026, 11, 28), "time": "17:00", "place": "יפו, סטודיו",
      "description": "שורה ראשונה\nשורה שנייה", "cover_url": None, "link": "https://example.com/j", "who": "תמר"}


def test_google_link_is_googles_own_prefilled_screen():
    u = urlsplit(cp.google_url(EV))
    assert (u.scheme, u.netloc, u.path) == ("https", "calendar.google.com", "/calendar/render")
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert q["action"] == "TEMPLATE" and q["text"] == EV["title"] and q["ctz"] == "Asia/Jerusalem"
    assert q["dates"] == "20261128T170000/20261128T190000"  # 2 hours from config
    assert q["location"] == EV["place"]
    assert q["details"].startswith("שורה ראשונה") and "https://example.com/j" in q["details"]
    allday = {k: v[0] for k, v in parse_qs(urlsplit(cp.google_url({**EV, "time": None})).query).items()}
    assert allday["dates"] == "20261128/20261129"
    assert "location" not in parse_qs(urlsplit(cp.google_url({**EV, "place": None})).query)


def test_the_length_comes_from_config(monkeypatch):
    monkeypatch.setattr(cp, "page_cfg", lambda: {"event_length_minutes": 90})
    assert parse_qs(urlsplit(cp.google_url(EV)).query)["dates"] == ["20261128T170000/20261128T183000"]


def test_calendar_file_is_a_correct_standard_file():
    text = cp.ics_text(EV, host="botson.example", now=datetime(2026, 11, 26, 10, 0, tzinfo=timezone.utc))
    assert text.endswith("\r\n") and "\n" not in text.replace("\r\n", "")
    lines = text.split("\r\n")
    assert all(len(line.encode()) <= 75 for line in lines)
    unfolded = text.replace("\r\n ", "")
    assert "BEGIN:VCALENDAR" in unfolded and "VERSION:2.0" in unfolded and "BEGIN:VTIMEZONE" in unfolded
    assert "TZID:Asia/Jerusalem" in unfolded
    assert "UID:event-7@botson.example" in unfolded and "DTSTAMP:20261126T100000Z" in unfolded
    assert "DTSTART;TZID=Asia/Jerusalem:20261128T170000" in unfolded
    assert "DTEND;TZID=Asia/Jerusalem:20261128T190000" in unfolded
    assert "SUMMARY:סדנה\\, קרמיקה\\; ערב" in unfolded
    assert "LOCATION:יפו\\, סטודיו" in unfolded
    assert "DESCRIPTION:שורה ראשונה\\nשורה שנייה\\n\\n" in unfolded
    assert "URL:https://example.com/j" in unfolded
    allday = cp.ics_text({**EV, "time": None, "place": None, "link": None, "description": None})
    assert "DTSTART;VALUE=DATE:20261128" in allday and "DTEND;VALUE=DATE:20261129" in allday
    assert "LOCATION" not in allday and "URL:" not in allday


def test_calendar_file_route_only_for_published_events(tmp_path):
    async def body(db):
        ok = await event(db, "פיקניק", "2026-11-28")
        hidden = await event(db, "בדיקה", "2026-11-28", chat=TEST)
        r = await dash.public_calendar_ics(ok, db)
        assert r.media_type.startswith("text/calendar") and f"event-{ok}.ics" in r.headers["content-disposition"]
        assert "noindex" in r.headers["x-robots-tag"]
        assert "UID:event-1@botson.example" in r.body.decode()
        with pytest.raises(HTTPException) as e:
            await dash.public_calendar_ics(hidden, db)
        assert e.value.status_code == 404
    run(tmp_path, body)


# ── Deep links, headers, states ──

def test_deep_links_open_a_month_a_day_and_an_event(tmp_path):
    async def body(db):
        nov = await event(db, "פיקניק", "2026-11-28")
        dec = await event(db, "יוגה", "2026-12-10")
        _, text = await page(db)
        assert "<h1>נובמבר" in text and 'class="sheet' in text and ' open"' not in text
        _, text = await page(db, "2026-12")
        assert "<h1>דצמבר" in text
        _, text = await page(db, e=dec)  # no month given: the event's own month
        assert "<h1>דצמבר" in text and f'id="ev-{dec}"' in text
        assert re.search(rf'class="sheet[^"]* open" id="ev-{dec}"', text)
        _, text = await page(db, "2026-11", d="2026-11-28")
        assert re.search(r'data-daylist="2026-11-28"\s*>', text)  # not hidden
        assert re.search(r'class="day[^"]* has[^"]* sel" href="/cal/2026-11\?d=2026-11-28"', text)
        assert re.search(r'data-upcoming-day="2026-11-28" hidden>', text)  # not listed twice
        for bad in ("2026-13", "november", "0001-01"):
            with pytest.raises(HTTPException) as e:
                await page(db, bad)
            assert e.value.status_code == 404
        assert nov
    run(tmp_path, body)


def test_kept_out_of_search_engines(tmp_path):
    async def body(db):
        r, text = await page(db)
        assert "noindex" in r.headers["x-robots-tag"]
        assert '<meta name="robots" content="noindex' in text
        font = await dash.public_calendar_font()
        assert "noindex" in font.headers["x-robots-tag"]
        assert "<script src=" not in text and "googleapis" not in text  # no third-party script or font
    run(tmp_path, body)


def test_empty_month_all_past_month_and_crowded_day(tmp_path):
    async def body(db):
        _, text = await page(db, "2027-01")
        assert html.escape(load_copy("calendar_page", "empty_title", month="ינואר")) in text
        await event(db, "היה מזמן", "2026-11-05")
        _, text = await page(db)
        assert load_copy("calendar_page", "all_past", month="נובמבר") in text
        for i, t in enumerate(("09:00", "12:00", "17:00", "21:00")):
            await event(db, f"אירוע {i}", "2026-12-12", t)
        _, text = await page(db, "2026-12")
        cell = re.search(r'<a class="day[^"]* has[^"]*" href="/cal/2026-12\?d=2026-12-12".*?</a>', text, re.S).group(0)
        assert cell.count('class="mark"') == 3 and "+1" in cell
    run(tmp_path, body)


def test_long_title_no_picture_no_place_no_link(tmp_path):
    async def body(db):
        long_title = "ערב " * 60
        eid = await event(db, long_title, "2026-11-28", place=None)
        _, text = await page(db, e=eid)
        assert long_title.strip() in text
        sheet = re.search(rf'<div class="sheet[^"]*" id="ev-{eid}".*?</div>\s*</div>', text, re.S).group(0)
        assert "has-cover" not in sheet and "📍" not in sheet and load_copy("calendar_page", "join") not in sheet
        assert load_copy("calendar_page", "google") in sheet and f"/cal/event/{eid}.ics" in sheet
        assert '<span class="tile">' in text
        pic = await event(db, "עם תמונה", "2026-11-29", cover="covers/a.png")
        _, text = await page(db, e=pic)
        assert 'src="/media/covers/a.png"' in text
    run(tmp_path, body)


def test_no_hardcoded_hebrew_in_the_page_template():
    from pathlib import Path

    src = (Path(dash.__file__).parent / "templates" / "public_calendar.html").read_text(encoding="utf-8")
    assert not re.search(r"[֐-׿]", src)


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
