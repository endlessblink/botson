"""The public community page (/welcome): what a stranger sees and, above all, what never shows
(street addresses, event join links, organizers, phone numbers, pending or test events, any link to /cal),
the join buttons, the search-engine switch, escaping and empty states. Nothing real is sent."""

import asyncio
import html
import json
import re

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from bot.database.db import Database
from bot.utils import community_page as cpage
from bot.utils import config, events_calendar
from bot.utils.copy import load_copy, load_copy_block
from dashboard import app as dash

MAIN, TEST = -1001, -1002
TG = "https://t.me/+AbCdEf123"
WA = "https://chat.whatsapp.com/AbCdEf123"


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUP_ID", str(MAIN))
    monkeypatch.setenv("TEST_GROUP_ID", str(TEST))
    monkeypatch.setenv("BOTSON_COMMUNITY_PAGE_PATH", str(tmp_path / "community_page.json"))
    monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "https://botson.example")
    fixed = events_calendar.local_now().replace(year=2026, month=11, day=26, hour=12, minute=0)
    monkeypatch.setattr(events_calendar, "local_now", lambda: fixed)


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "c.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


async def event(db, title, day, time="20:00", *, place=None, desc="", cover=None, chat=MAIN, request=None,
                link=None, organizer=None, requester_name="דנה כהן", active=True):
    eid = await db.create_event(title=title, description=desc, event_date=day, event_time=time, location=place,
                                created_by=0, cover_path=cover)
    await db.update_event(eid, message_id=5, tg_chat_id=chat)
    if not active:
        await db._db.execute("UPDATE events SET active = 0 WHERE id = ?", (eid,))
    if request:
        await db._db.execute(
            "INSERT INTO event_requests (token, platform, requester_id, requester_name, created_at, expires_at, status, "
            "title, event_date, event_time, link, organizer, event_id) VALUES (?, 'whatsapp', '972541234567@c.us', ?, "
            "'2026-11-01', '2026-11-02', ?, ?, ?, ?, ?, ?, ?)",
            (f"tok{eid}", requester_name, request, title, day, time, link, organizer, eid))
    await db._db.commit()
    return eid


def http_request(path="/welcome", session=None):
    scope = {"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": [], "app": dash.app}
    if session is not None:
        scope["session"] = session
    return Request(scope)


async def page(db, month=None):
    r = await dash.community_page(http_request(), month, db)
    return r, r.body.decode()


def save(**kw):
    values = {"telegram_url": "", "whatsapp_url": "", "indexable": False, **kw}
    assert cpage.save_settings(**values) == []


# ── What a stranger sees, and what never shows ──

def test_shows_upcoming_published_events_only(tmp_path):
    async def body(db):
        await event(db, "ערב משחקי לוח", "2026-12-03", place="בית קפה נחת, רחוב הרצל 12, תל אביב", request="approved")
        await event(db, "רק בקבוצת הבדיקה", "2026-12-03", chat=TEST)
        await event(db, "ממתין לאישור", "2026-12-03", request="submitted")
        await event(db, "נמחק", "2026-12-03", active=False)
        await event(db, "רחוק מדי", "2027-06-01")
        _, text = await page(db)  # nothing left this month: the next events are shown
        assert "ערב משחקי לוח" in text and "תל אביב" in text
        assert "20:00" in text and load_copy("community_page", "date", d=3, m=12) in text
        for hidden in ("רק בקבוצת הבדיקה", "ממתין לאישור", "נמחק"):
            assert hidden not in text, hidden
    run(tmp_path, body)


def test_never_shows_addresses_links_names_phones_or_descriptions(tmp_path):
    async def body(db):
        await event(db, "פיקניק", "2026-12-12", place="פארק הירקון, שדרות רוקח 2, תל אביב",
                    desc="מתקשרים ל-054-1234567 או כותבים ל-maya@example.com", request="approved",
                    link="https://chat.whatsapp.com/EVENTLINK999", organizer="מאיה לוי", requester_name="יוסי ישראלי")
        _, text = await page(db)
        for hidden in ("שדרות רוקח", "רוקח 2", "פארק הירקון", "EVENTLINK999", "מאיה לוי", "יוסי ישראלי",
                       "054-1234567", "972541234567", "maya@example.com", "מתקשרים"):
            assert hidden not in text, hidden
        assert 'href="/cal' not in text  # the public page never leads to the old calendar address
        assert "<script" not in text and "googleapis" not in text
        assert not re.search(r'(?:src|href|url)\(?=?"?https?://(?!botson\.example)', text)  # nothing loads from elsewhere
    run(tmp_path, body)


def test_titles_lose_links_phones_emails_and_handles(tmp_path):
    async def body(db):
        await event(db, "ערב סרט, פרטים 054-7654321 או https://example.com/x @someone a@b.co", "2026-12-05")
        _, text = await page(db)
        assert "ערב סרט, פרטים" in text
        for hidden in ("054-7654321", "example.com", "@someone", "a@b.co"):
            assert hidden not in text, hidden
    run(tmp_path, body)


@pytest.mark.parametrize("location,expected", [
    ("בית קפה נחת, רחוב הרצל 12, תל אביב", "תל אביב"),
    ("דיזנגוף 50 ת\"א", "תל אביב"),
    ("רחוב ירושלים 5, חיפה", "חיפה"),            # a street named after a city is not the city
    ("רחוב ירושלים 5", None),
    ("אצל שרון בבית, שדרות רוטשילד 10", None),    # a first name is not an area, the street is never shown
    ("בזום", "online"),
    ("Zoom", "online"),
    ("ליד הים בבת ים", "בת ים"),
    ("מרכז קהילתי, קריית ביאליק", "הקריות"),
    ("", None),
    (None, None),
    ("הבית של דנה, קומה 3 דירה 7", None),
])
def test_place_is_only_ever_a_listed_city_or_area(location, expected):
    if expected == "online":
        expected = load_copy("community_page", "online")
    assert cpage.public_place(location) == expected


def test_everything_typed_is_escaped(tmp_path):
    async def body(db):
        await event(db, '<script>alert(1)</script> "ערב"', "2026-12-03", cover="covers/../../etc/passwd")
        _, text = await page(db)
        assert "<script>alert(1)" not in text
        assert html.escape("<script>alert(1)</script>").replace("&#x27;", "'") in text or "&lt;script&gt;" in text
        assert "passwd" not in text
    run(tmp_path, body)


# ── Empty states ──

def test_no_upcoming_events_shows_the_empty_state(tmp_path):
    async def body(db):
        _, text = await page(db)
        assert html.escape(load_copy("community_page", "empty_title", month="נובמבר")) in text
        assert html.escape(load_copy("community_page", "members_note")) not in text
    run(tmp_path, body)


def test_events_with_and_without_pictures(tmp_path):
    async def body(db):
        await event(db, "בלי תמונה", "2026-12-03")
        await event(db, "עם תמונה", "2026-12-04", cover="covers/a.png")
        _, text = await page(db)
        stops = re.findall(r'<li class="ev"[^>]*>.*?</li>', text, re.S)
        assert len(stops) == 2
        without, with_pic = stops
        assert "<img" not in without and '<span class="day" aria-hidden="true">3</span>' in without  # date plate
        assert 'src="/media/covers/a.png"' in with_pic and "filter" not in with_pic  # natural colour
    run(tmp_path, body)


def test_a_long_title_stays_whole_and_an_unknown_place_shows_no_city(tmp_path):
    async def body(db):
        long_title = "ערב " * 40
        await event(db, long_title, "2026-12-03", place="הבית של מישהי, קומה 3")
        _, text = await page(db)
        stop = re.search(r'<li class="ev"[^>]*>.*?</li>', text, re.S).group(0)
        assert long_title.strip() in stop and 'class="city"' not in stop and "קומה" not in text
    run(tmp_path, body)


def test_one_join_link_shows_one_button(tmp_path):
    async def body(db):
        save(whatsapp_url=WA)
        _, text = await page(db)
        assert text.count('class="btn"') == 1 and f'href="{WA}"' in text
        assert html.escape(load_copy("community_page", "join_soon")) not in text
    run(tmp_path, body)


def test_the_page_uses_one_font_and_no_print_art():
    from pathlib import Path

    folder = Path(dash.__file__).parent / "static" / "community"
    assert sorted(p.name for p in folder.iterdir()) == ["share.jpg"]
    src = (Path(dash.__file__).parent / "templates" / "public_community.html").read_text(encoding="utf-8")
    assert "/static/community/" not in src and "mask" not in src and "rubik" not in src.lower()


def test_the_month_is_sorted(tmp_path):
    async def body(db):
        for d in ("2026-12-20", "2026-12-01", "2026-12-10"):
            await event(db, f"אירוע {d}", d)
        _, text = await page(db, "2026-12")
        assert text.index("אירוע 2026-12-01") < text.index("אירוע 2026-12-10") < text.index("אירוע 2026-12-20")
    run(tmp_path, body)


# ── Join buttons ──

def test_no_join_links_means_no_buttons_and_the_soon_line(tmp_path):
    async def body(db):
        _, text = await page(db)
        assert html.escape(load_copy("community_page", "join_soon")) in text
        assert "t.me" not in text and "chat.whatsapp.com" not in text
    run(tmp_path, body)


def test_join_buttons_appear_only_when_configured(tmp_path):
    async def body(db):
        save(telegram_url=TG)
        _, text = await page(db)
        assert f'href="{TG}"' in text and html.escape(load_copy("community_page", "join_telegram")) in text
        assert "chat.whatsapp.com" not in text and html.escape(load_copy("community_page", "join_soon")) not in text
        save(telegram_url=TG, whatsapp_url=WA)
        _, text = await page(db)
        assert f'href="{TG}"' in text and f'href="{WA}"' in text
        assert text.index(TG) < text.index(WA)
    run(tmp_path, body)


@pytest.mark.parametrize("platform,url", [
    ("telegram", "http://t.me/+abc"),
    ("telegram", "https://evil.example/t.me/+abc"),
    ("telegram", "https://t.me.evil.example/+abc"),
    ("telegram", "javascript:alert(1)"),
    ("telegram", "https://t.me/"),
    ("whatsapp", "https://wa.me/972541234567"),
    ("whatsapp", "https://user:pw@chat.whatsapp.com/x"),
    ("whatsapp", "https://chat.whatsapp.com/x y"),
])
def test_bad_join_links_are_refused(platform, url):
    assert not cpage.valid_join_link(platform, url)
    assert cpage.save_settings(**{"telegram_url": "", "whatsapp_url": "", "indexable": False, f"{platform}_url": url}) == [platform]
    assert not cpage.runtime_path().exists()


def test_a_bad_link_in_the_file_never_reaches_the_page(tmp_path):
    async def body(db):
        cpage.runtime_path().write_text(json.dumps({"telegram_url": "javascript:alert(1)", "whatsapp_url": WA}))
        _, text = await page(db)
        assert "javascript:" not in text and f'href="{WA}"' in text
    run(tmp_path, body)


def test_dashboard_settings_card_shows_the_saved_values(tmp_path):
    async def body(db):
        save(telegram_url=TG, indexable=True)
        r = await dash.settings_page(http_request("/settings", session={"authenticated": True}), db)
        text = r.body.decode()
        assert 'id="community-page-card"' in text and f'value="{TG}"' in text
        assert re.search(r'id="cp-index" type="checkbox" checked', text)
        assert html.escape(load_copy("community_page_admin", "title")) in text
    run(tmp_path, body)


def test_saving_needs_login():
    async def go():
        with pytest.raises(HTTPException) as e:
            await dash.update_community_page(http_request("/api/settings/community-page", session={}))
        return e.value.status_code
    assert asyncio.run(go()) == 401


# ── Search engines: one switch ──

def test_hidden_from_search_engines_by_default(tmp_path):
    async def body(db):
        r, text = await page(db)
        assert "noindex" in r.headers["x-robots-tag"]
        assert '<meta name="robots" content="noindex' in text
        assert 'property="og:title"' in text and 'property="og:image" content="https://botson.example/static/community/share.jpg"' in text
        assert '<link rel="canonical" href="https://botson.example/welcome">' in text
    run(tmp_path, body)


def test_switch_on_lets_search_engines_in(tmp_path):
    async def body(db):
        save(indexable=True)
        r, text = await page(db)
        assert "x-robots-tag" not in r.headers
        assert '<meta name="robots" content="index, follow">' in text and "noindex" not in text
    run(tmp_path, body)


def test_saved_values_live_outside_config(tmp_path):
    save(telegram_url=TG, indexable=True)
    assert "config" not in cpage.runtime_path().parts[-2:]
    assert cpage.settings() == {"join": {"telegram_url": TG, "whatsapp_url": ""}, "indexable": True}


# ── Page hygiene ──

def test_no_hardcoded_hebrew_and_no_link_to_cal_in_the_template():
    from pathlib import Path

    src = (Path(dash.__file__).parent / "templates" / "public_community.html").read_text(encoding="utf-8")
    assert not re.search(r"[֐-׿]", src)
    assert "/cal" not in src and "<script" not in src


def test_fonts_are_served_from_the_page_and_nothing_else():
    async def go():
        r = await dash.community_page_font("heebo.ttf")
        assert r.media_type == "font/ttf"
        with pytest.raises(HTTPException):
            await dash.community_page_font("rubik-black.ttf")
        with pytest.raises(HTTPException):
            await dash.community_page_font("../../.env")
    asyncio.run(go())


def test_events_come_right_after_the_title_and_the_old_blocks_are_gone(tmp_path):
    async def body(db):
        await event(db, "ערב משחקים", "2026-12-03")
        _, text = await page(db)
        assert text.index("</h1>") < text.index('id="events"') < text.index('id="join"')
        assert html.escape(load_copy("community_page", "lead")) in text
        assert 'class="route"' not in text and 'class="cols"' not in text
        assert not hasattr(cpage, "columns")
        for key in ("columns_title", "columns_kicker", "col_trivia_title", "col_emoji_title", "channels", "channels_label", "body"):
            assert key not in (cpage.base_cfg() or {}) and key not in load_copy_block("community_page", default={})
    run(tmp_path, body)


def test_no_channel_row_even_with_verified_topics(tmp_path):
    """Channels exist only in Telegram, so the page (read by WhatsApp members too) lists none."""
    async def body(db):
        eid = await event(db, "ערב משחקים", "2026-12-03")
        await db.upsert_verified_forum_topic(54, "סרטים סדרות וכו", "movies", "dot")
        for _, text in (await page(db), await opened(db, eid)):
            assert 'class="chips"' not in text and 'class="chan"' not in text and "סרטים סדרות וכו" not in text
        assert not hasattr(cpage, "channels")
    run(tmp_path, body)


# ── An opened event (/welcome/e/<id>) ──

async def opened(db, eid):
    r = await dash.community_event_page(http_request(f"/welcome/e/{eid}"), eid, db)
    return r, r.body.decode()


def test_cards_open_their_event_and_the_details_show_the_published_description(tmp_path):
    async def body(db):
        eid = await event(db, "ערב משחקי לוח", "2026-12-03", place="בית קפה נחת, רחוב הרצל 12, תל אביב",
                          desc="מביאים משחק אחד.\nיש גם פיצה טבעונית.", cover="covers/a.png")
        _, text = await page(db)
        assert f'href="/welcome/e/{eid}"' in text
        r, one = await opened(db, eid)
        assert r.headers["x-robots-tag"].startswith("noindex") and 'content="noindex' in one
        assert "ערב משחקי לוח" in one and "תל אביב" in one and "20:00" in one
        assert "מביאים משחק אחד." in one and "יש גם פיצה טבעונית." in one
        assert 'src="/media/covers/a.png"' in one
        assert html.escape(load_copy("community_page", "members_note")) in one
        assert 'href="/cal' not in one and "<script" not in one
    run(tmp_path, body)


def test_the_details_never_show_address_contacts_names_or_event_links(tmp_path):
    async def body(db):
        eid = await event(db, "פיקניק", "2026-12-12", place="פארק הירקון, שדרות רוקח 2, תל אביב",
                          desc="פיקניק אחרי הצהריים.\nנפגשים ברחוב הרצל 12 ליד הכניסה\n"
                               "פארק הירקון, שדרות רוקח 2, תל אביב\nפרטים: 054-1234567, maya@example.com, @maya\n"
                               "קבוצה: https://chat.whatsapp.com/EVENTLINK999",
                          request="approved", link="https://chat.whatsapp.com/REQLINK", organizer="מאיה לוי",
                          requester_name="יוסי ישראלי")
        _, one = await opened(db, eid)
        assert "פיקניק אחרי הצהריים." in one
        for hidden in ("הרצל 12", "רוקח", "פארק הירקון", "054-1234567", "maya@example.com", "@maya", "EVENTLINK999",
                       "chat.whatsapp.com", "REQLINK", "מאיה לוי", "יוסי ישראלי", "972541234567"):
            assert hidden not in one, hidden
    run(tmp_path, body)


def test_an_empty_description_shows_no_description_block(tmp_path):
    async def body(db):
        eid = await event(db, "ערב בלי תיאור", "2026-12-03")
        _, one = await opened(db, eid)
        assert 'class="desc"' not in one and "ערב בלי תיאור" in one
    run(tmp_path, body)


@pytest.mark.parametrize("kind", ["test_group", "pending", "deleted", "missing"])
def test_only_published_events_open(tmp_path, kind):
    async def body(db):
        eid = {
            "test_group": lambda: event(db, "x", "2026-12-03", chat=TEST),
            "pending": lambda: event(db, "x", "2026-12-03", request="submitted"),
            "deleted": lambda: event(db, "x", "2026-12-03", active=False),
        }.get(kind)
        eid = await eid() if eid else 999
        with pytest.raises(HTTPException) as err:
            await opened(db, eid)
        assert err.value.status_code == 404
    run(tmp_path, body)
