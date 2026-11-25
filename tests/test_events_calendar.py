"""Month calendar picture: the model, which events count, the endpoints, and drawing edge cases."""

import asyncio
import io
from datetime import date, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from PIL import Image

from bot.database.db import Database
from bot.utils import events_calendar as ec
from dashboard import app as dash

TOKEN = "agent-secret-value"
MAIN, TEST = -100, -200
NOW = datetime(2026, 12, 3, 12, 0)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", TOKEN)
    monkeypatch.setenv("GROUP_ID", str(MAIN))


def ev(day, time, title, month=12, **kw):
    return {"title": title, "event_date": f"2026-{month:02d}-{day:02d}", "event_time": time, **kw}


def days(model):
    return {d.day: d for week in model.weeks for d in week}


def req(*, agent=False, session=False, token=TOKEN):
    return SimpleNamespace(headers={"authorization": f"Bearer {token}"} if agent else {},
                           session={"authenticated": session})


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "c.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


def size(png):
    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    img = Image.open(io.BytesIO(png))
    img.load()
    return img.size


# ── Model ──

def test_december_2026_weekdays_and_flags():
    m = ec.build_month(2026, 12, [], NOW)
    assert all(len(w) == 7 for w in m.weeks) and len(m.weeks) == 5
    assert m.weeks[0][2].day == date(2026, 12, 1)  # Sunday-first: index 2 is Tuesday
    assert m.weeks[0][6].day == date(2026, 12, 5) and m.weeks[0][6].weekend  # Saturday
    d = days(m)
    assert [x for x in d if d[x].is_today] == [date(2026, 12, 3)]
    assert d[date(2026, 12, 2)].is_past and not d[date(2026, 12, 3)].is_past
    assert not d[date(2026, 11, 29)].in_month and d[date(2026, 12, 31)].in_month
    assert m.count == 0 and m.next_event is None


def test_events_sorted_within_the_day_with_past_flags_and_next():
    m = ec.build_month(2026, 12, [
        ev(9, "21:30", "b"), ev(9, "20:00", "a", organizer="Yael"), ev(9, None, "no time"),
        ev(1, "19:30", "old"), ev(3, "11:00", "started"), ev(3, "13:00", "soon"), ev(5, "9:00", "other month", month=11),
    ], NOW)
    d = days(m)
    assert [e.title for e in d[date(2026, 12, 9)].events] == ["no time", "a", "b"]
    assert d[date(2026, 12, 9)].events[1].organizer == "Yael"
    assert d[date(2026, 12, 1)].events[0].past
    assert [e.past for e in d[date(2026, 12, 3)].events] == [True, False]
    assert m.count == 6 and m.next_event.title == "soon"


def test_next_event_looks_ahead_only_from_the_current_month():
    events = [ev(1, "10:00", "done"), {"title": "jan", "event_date": "2027-01-05", "event_time": "20:00"}]
    assert ec.build_month(2026, 12, events, NOW).next_event.title == "jan"
    assert ec.build_month(2026, 11, events, NOW).next_event is None


def test_untimed_event_today_is_not_past_and_bad_rows_are_survivable():
    m = ec.build_month(2026, 12, [ev(3, "", "today"), {"title": "x", "event_date": "soon"}, ev(4, "99:99", "odd")], NOW)
    assert m.count == 2 and m.next_event.title == "today"
    assert days(m)[date(2026, 12, 4)].events[0].start is None


@pytest.mark.parametrize("year,month,rows", [(2026, 2, 4), (2026, 12, 5), (2026, 8, 6)])
def test_week_rows(year, month, rows):
    assert len(ec.build_month(year, month, [], NOW).weeks) == rows


def test_now_in_another_timezone_is_read_as_local_wall_clock():
    assert ec.local_now().tzinfo.key == "Asia/Jerusalem"
    assert ec.parse_month(None, NOW) == (2026, 12) and ec.parse_month("2027-01", NOW) == (2027, 1)


# ── Which events count ──

def test_only_events_published_to_the_real_groups_count(tmp_path):
    async def body(db):
        async def add(title, *, created_by=0, **fields):
            eid = await db.create_event(title=title, description="", event_date="2026-12-09", event_time="20:00",
                                        location=None, created_by=created_by)
            if fields:
                await db.update_event(eid, **fields)
            return eid

        async def request(status, event_id=None, **fields):
            rid = await db.create_event_request(token=f"t{status}{event_id}", platform="whatsapp", requester_id="1",
                                                requester_name="x", created_at="a", expires_at="b")
            await db.update_event_request(rid, status=status, event_id=event_id, title=status,
                                          event_date="2026-12-09", **fields)

        real = await add("member real", message_id=1, tg_chat_id=MAIN, wa_target="whatsapp")
        await request("approved", real, organizer="Yael")
        await request("submitted")
        await request("rejected")
        await add("dashboard real", message_id=2, tg_chat_id=MAIN)
        await add("real whatsapp only", message_id=3, tg_chat_id=TEST, wa_target="whatsapp")
        await add("slash command", created_by=555, message_id=4)
        await add("test groups", message_id=5, tg_chat_id=TEST, wa_target="whatsapp_test")
        await add("test telegram", message_id=6, tg_chat_id=TEST)
        await add("saved, not published")
        await add("destination unknown", message_id=7)
        await add("deleted", message_id=8, tg_chat_id=MAIN, active=0)
        pulled = await add("request not approved", message_id=9, tg_chat_id=MAIN)
        await request("rejected", pulled)
        await add("planner card", created_by=555, message_id=10)
        await db._db.execute("INSERT INTO scheduled_messages (text, message_type, scheduled_date, scheduled_time, "
                             "status, sent_message_id) VALUES ('x', 'event', '2026-12-01', '10:00', 'sent', 10)")
        rows = {r["title"]: r for r in await db.get_published_events(MAIN)}
        assert set(rows) == {"member real", "dashboard real", "real whatsapp only", "slash command"}
        assert rows["member real"]["organizer"] == "Yael" and rows["dashboard real"]["organizer"] is None
    run(tmp_path, body)


# ── Endpoints ──

def test_agent_endpoint_needs_the_token_and_a_good_month(tmp_path):
    async def body(db):
        for r in (req(), req(agent=True, token="wrong"), req(session=True)):
            with pytest.raises(HTTPException) as e:
                await dash.agent_events_calendar(r, None, db)
            assert e.value.status_code == 401
        for bad in ("2026-13", "2026-1", "december", "2026-12-01", "0001-01", "2026-12\n"):
            with pytest.raises(HTTPException) as e:
                await dash.agent_events_calendar(req(agent=True), bad, db)
            assert e.value.status_code == 400
        for month in (None, "2026-12"):
            res = await dash.agent_events_calendar(req(agent=True), month, db)
            w, h = size(res.body)
            assert res.media_type == "image/png" and w == 1080 and 900 < h < 2500
    run(tmp_path, body)


def test_dashboard_picture_needs_a_login(tmp_path):
    async def body(db):
        for r in (req(), req(agent=True)):
            with pytest.raises(HTTPException) as e:
                await dash.events_calendar_png(r, None, db)
            assert e.value.status_code == 401
        assert size((await dash.events_calendar_png(req(session=True), "2026-12", db)).body)[0] == 1080
    run(tmp_path, body)


# ── Drawing ──

def test_edge_cases_draw_without_errors():
    crowded = [ev(9, f"{h}:00", "מפגש") for h in (18, 19, 20, 21)]
    tricky = [
        ev(4, "20:00", "🎉 Party 2026: ערב Karaoke 👩‍👩‍👧 (חלק 2)", location="בר ABC " * 30, organizer="נועה " * 30),
        ev(10, None, "מילה " * 60), ev(11, "20:00", "א" * 300), ev(12, "20:00", "🎉"), ev(13, "20:00", ""),
        ev(14, "20:00", "{braces} %s ‮\x00 שָׁלוֹם"),
    ]
    for year, month, events in [(2026, 12, []), (2026, 12, crowded), (2026, 12, tricky), (2026, 2, []), (2026, 8, [])]:
        assert size(ec.render_png(ec.build_month(year, month, events, NOW)))[0] == 1080


def test_height_follows_the_number_of_weeks():
    h = [size(ec.render_png(ec.build_month(2026, m, [], NOW)))[1] for m in (2, 12, 8)]
    assert h[0] < h[1] < h[2]


def test_wrapping_stays_inside_the_room_and_drops_unknown_glyphs():
    p = ec._Painter(ec.calendar_cfg())
    for text in ("מילה " * 60, "א" * 300, "Supercalifragilisticexpialidocious"):
        lines = p.wrap(text, "title", 100, 3)
        assert len(lines) == 3 and lines[-1].endswith("…")
        assert all(p.width(line, "title") <= 100 for line in lines)
    assert p.clean("🎉 ערב 2026 Party!") == "ערב 2026 Party!"


def test_config_and_copy_are_complete():
    from bot.utils.copy import load_copy_block

    copy = load_copy_block("events_calendar")
    assert (len(copy["month_names"]), len(copy["day_letters"]), len(copy["day_names"])) == (12, 7, 7)
    cfg = ec.calendar_cfg()
    assert set(cfg["fonts"]) == set(cfg["line_heights"])


# ── Event pictures ──

def _cover(folder, name="covers/a.png", color=(0, 90, 200), mode="RGB"):
    from PIL import Image

    (folder / "covers").mkdir(parents=True, exist_ok=True)
    Image.new(mode, (800, 600), color).save(folder / name)
    return name


def _pixels(png, color):
    import io
    from PIL import Image

    im = Image.open(io.BytesIO(png)).convert("RGB")
    return sum(n for n, c in im.getcolors(im.width * im.height) if c == color)


def test_a_cover_shows_in_its_day_and_next_to_the_details(tmp_path):
    cover = _cover(tmp_path)
    row = ev(9, "20:00", "Vinyl night", location="Bar", organizer="Yael")
    plain = ec.render_png(ec.build_month(2026, 12, [row], NOW), tmp_path)
    drawn = ec.render_png(ec.build_month(2026, 12, [{**row, "cover_path": cover}], NOW), tmp_path)
    sp = ec.calendar_cfg()["spacing"]
    assert _pixels(plain, (0, 90, 200)) == 0
    assert _pixels(drawn, (0, 90, 200)) > sp["next_cover_w"] * sp["next_cover_h"] * 0.9 + 100 * sp["chip_cover_h"] * 0.9


def test_a_missing_broken_or_outside_cover_is_left_out(tmp_path):
    media = tmp_path / "media"
    (media / "covers").mkdir(parents=True)
    (media / "covers" / "bad.png").write_bytes(b"not a picture")
    _cover(tmp_path, "covers/secret.png")
    row = ev(9, "20:00", "x")
    plain = ec.render_png(ec.build_month(2026, 12, [row], NOW), media)
    for path in ("covers/none.png", "covers/bad.png", "../covers/secret.png", str(tmp_path / "covers/secret.png"), ".", "a\x00b"):
        assert ec.render_png(ec.build_month(2026, 12, [{**row, "cover_path": path}], NOW), media) == plain
    assert ec.render_png(ec.build_month(2026, 12, [{**row, "cover_path": "covers/secret.png"}], NOW)) == plain


def test_upcoming_events_get_detail_rows_up_to_the_limit():
    events = [ev(d, "20:00", f"e{d}") for d in range(5, 20)]
    model = ec.build_month(2026, 12, events, NOW)
    assert [e.title for e in model.upcoming][0] == model.next_event.title and len(model.upcoming) > 6
    few = size(ec.render_png(ec.build_month(2026, 12, events[:1], NOW)))[1]
    many = size(ec.render_png(model))[1]
    assert few < many < 4000


def test_the_published_events_carry_their_cover(tmp_path):
    async def body(db):
        eid = await db.create_event(title="x", description="", event_date="2026-12-09", event_time="20:00", location=None,
                                    created_by=0, cover_path="covers/a.png")
        await db.update_event(eid, message_id=1, tg_chat_id=MAIN)
        assert (await db.get_published_events(MAIN))[0]["cover_path"] == "covers/a.png"
    run(tmp_path, body)
