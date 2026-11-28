"""Event posts pinned in the WhatsApp group too (bot/utils/event_pins.py, sync_whatsapp).
Every WAHA call goes to a fake HTTP server; every Telegram call is faked."""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import httpx
import pytest

import bot.handlers.calendar as calendar_handler
from bot.database.db import Database
from bot.utils import event_pins as ep
from bot.utils import shared_events, topic_guard, whatsapp_sender
from dashboard import app as dash
from tests.test_event_pins import FakeBot

MAIN, TEST, TOPIC = -1001, -1002, 341
WA_MAIN, WA_TEST = "111@g.us", "222@g.us"
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=ZoneInfo("Asia/Jerusalem"))
TODAY = NOW.date()
DAY, WEEK, MONTH = 86400, 604800, 2592000
CFG = {"enabled": True, "days_before": 7, "test_group_in_test_mode": True,
       "whatsapp": {"enabled": True, "max_pins": 3, "durations_s": [DAY, WEEK, MONTH]}}


def msg(n, chat=WA_MAIN):
    return f"true_{chat}_3EB0{n:04d}_972500000000@c.us"


def esc(m):
    return m.replace("@", "%40")


class FakeWaha:
    """Answers like WAHA; records (action, chat, message id as it arrived in the path, body)."""

    def __init__(self):
        self.calls, self.fail, self.status = [], None, 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.fail:
            raise self.fail
        parts = request.url.raw_path.decode().split("/")  # /api/default/chats/<chat>/messages/<msg>/<action>
        self.calls.append((parts[-1], parts[4], parts[-2], json.loads(request.content.decode() or "{}")))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"}, request=request)
        return httpx.Response(200, json={"success": True}, request=request)

    def pins(self):
        return [(c[2], c[3].get("duration")) for c in self.calls if c[0] == "pin"]

    def unpins(self):
        return [c[2] for c in self.calls if c[0] == "unpin"]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GROUP_ID", str(MAIN))
    monkeypatch.setenv("TEST_GROUP_ID", str(TEST))
    monkeypatch.setenv("WHATSAPP_WAHA_URL", "http://waha.test")
    monkeypatch.setenv("WHATSAPP_WAHA_API_KEY", "k")
    monkeypatch.setenv("WHATSAPP_WAHA_SESSION", "default")
    monkeypatch.setenv("WHATSAPP_GROUP_ID", WA_MAIN)
    monkeypatch.setenv("WHATSAPP_TEST_GROUP_ID", WA_TEST)
    monkeypatch.setattr(topic_guard, "GROUP_ID", MAIN)
    monkeypatch.setattr(topic_guard, "TEST_GROUP_ID", TEST)
    monkeypatch.setattr(ep, "pins_cfg", lambda: CFG)
    monkeypatch.setattr(ep, "_today", lambda: TODAY)
    monkeypatch.setattr(ep, "_now", lambda: NOW)
    monkeypatch.setattr(shared_events, "default_mode", lambda: "real")
    ep._warned.clear()
    ep._wa_warned.clear()


@pytest.fixture
def waha(monkeypatch):
    fake = FakeWaha()
    real = httpx.AsyncClient
    monkeypatch.setattr(whatsapp_sender.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(fake.handler), **kw))
    return fake


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "w.db"))
        await db.init()
        await db.upsert_verified_forum_topic(TOPIC, "עדכונים", "welcome", "test")
        await db.set_handler_routing("events_publish", TOPIC)
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


async def event(db, days_ahead, *, wa=None, target="whatsapp", tg=None):
    eid = await db.create_event(title="ערב", description="", event_date=(TODAY + timedelta(days=days_ahead)).isoformat(),
                                event_time="20:00", location=None, created_by=0)
    if tg:
        await db.update_event(eid, message_id=tg, tg_chat_id=MAIN, topic_id=TOPIC)
    await db.set_event_whatsapp_poll(eid, target, "stanza")
    await db.set_event_whatsapp_message(eid, wa)
    return eid


def at(days, hour=12):
    return NOW.replace(hour=hour) + timedelta(days=days)


# ── window, duration, id escaping ──

def test_window_duration_and_unpin_the_day_after(tmp_path, waha):
    async def body(db):
        eid = await event(db, 10, wa=msg(1))
        assert (await ep.sync_whatsapp(db))["pinned"] == []
        await ep.sync_whatsapp(db, now=at(2))  # 8 days before: not yet
        assert waha.calls == []
        await ep.sync_whatsapp(db, now=at(3))  # 7 days before, 7.5 days left: the 30-day pin
        assert waha.pins() == [(esc(msg(1)), MONTH)]
        assert waha.calls[0][1] == WA_MAIN and "@" not in waha.calls[0][2]  # the message id is URL-escaped
        await ep.sync_whatsapp(db, now=at(10, hour=23))  # still pinned on the day itself
        assert len(waha.calls) == 1
        assert (await ep.sync_whatsapp(db, now=at(11, hour=0)))["unpinned"] == [eid]
        assert waha.unpins() == [esc(msg(1))]
        assert not (await db.list_event_wa_pins())[eid]["pinned"]
    run(tmp_path, body)


def test_a_near_event_takes_the_shortest_duration_that_covers_its_day(tmp_path, waha):
    async def body(db):
        await event(db, 0, wa=msg(1))
        await event(db, 3, wa=msg(2))
        await ep.sync_whatsapp(db)
        assert sorted(d for _, d in waha.pins()) == [DAY, WEEK]
    run(tmp_path, body)


def test_running_twice_changes_nothing(tmp_path, waha):
    async def body(db):
        await event(db, 2, wa=msg(1))
        await ep.sync_whatsapp(db)
        assert await ep.sync_whatsapp(db) == {"pinned": [], "unpinned": []}
        assert len(waha.calls) == 1
    run(tmp_path, body)


# ── the 3-pin limit ──

def test_only_the_three_nearest_are_pinned_and_a_freed_slot_goes_to_the_next(tmp_path, waha):
    async def body(db):
        ids = [await event(db, d, wa=msg(d)) for d in (5, 1, 3, 2, 6)]
        await ep.sync_whatsapp(db)
        assert sorted(m for m, _ in waha.pins()) == sorted(esc(msg(d)) for d in (1, 2, 3))
        waha.calls.clear()
        # The day-1 event is over: unpinned first, then the day-5 event takes its slot in the same check.
        assert await ep.sync_whatsapp(db, now=at(2)) == {"pinned": [ids[0]], "unpinned": [ids[1]]}
        assert [c[0] for c in waha.calls] == ["unpin", "pin"]
    run(tmp_path, body)


def test_when_the_unpin_fails_the_next_event_waits_for_a_later_check(tmp_path, waha):
    async def body(db):
        ids = [await event(db, d, wa=msg(d)) for d in (1, 2, 3, 5)]
        await ep.sync_whatsapp(db)
        waha.calls.clear()
        waha.status = 500
        assert await ep.sync_whatsapp(db, now=at(2)) == {"pinned": [], "unpinned": []}
        assert waha.pins() == []  # never a fourth pin over the limit
        waha.status = 200
        assert await ep.sync_whatsapp(db, now=at(2, hour=13)) == {"pinned": [ids[3]], "unpinned": [ids[0]]}
    run(tmp_path, body)


def test_the_operators_own_pin_comes_before_the_nearest(tmp_path, waha):
    async def body(db):
        far = await event(db, 20, wa=msg(20))
        for d in (1, 2, 3):
            await event(db, d, wa=msg(d))
        r = await ep.set_manual(FakeBot(), db, far, True)
        assert r["status"] == "ok" and r["whatsapp_status"] == "ok"
        assert sorted(m for m, _ in waha.pins()) == sorted(esc(msg(d)) for d in (1, 2, 20))
        assert dict(waha.pins())[esc(msg(20))] == MONTH
    run(tmp_path, body)


def test_a_pin_longer_than_thirty_days_is_renewed_when_it_runs_out(tmp_path, waha):
    async def body(db):
        eid = await event(db, 45, wa=msg(1))
        await ep.set_manual(FakeBot(), db, eid, True)
        await ep.sync_whatsapp(db, now=at(29))
        assert len(waha.pins()) == 1
        await ep.sync_whatsapp(db, now=at(30, hour=13))
        assert len(waha.pins()) == 2  # WhatsApp dropped it after 30 days: pinned again
    run(tmp_path, body)


# ── which posts ──

def test_old_events_without_a_whatsapp_id_are_skipped(tmp_path, waha):
    async def body(db):
        await event(db, 2, wa=None)
        await event(db, 2, wa="")
        assert await ep.sync_whatsapp(db) == {"pinned": [], "unpinned": []}
        assert waha.calls == []
    run(tmp_path, body)


def test_only_the_group_the_event_went_to_and_the_test_group_only_in_test_mode(tmp_path, waha, monkeypatch):
    async def body(db):
        await event(db, 2, wa=msg(1, WA_TEST), target="whatsapp_test")
        await event(db, 2, wa=msg(2, "999@g.us"))  # recorded for the main group, but the id is another chat's
        await ep.sync_whatsapp(db)
        assert waha.calls == []
        monkeypatch.setattr(shared_events, "default_mode", lambda: "test")
        await ep.sync_whatsapp(db)
        assert waha.pins() == [(esc(msg(1, WA_TEST)), WEEK)] and waha.calls[0][1] == WA_TEST
    run(tmp_path, body)


def test_a_deleted_event_is_unpinned(tmp_path, waha):
    async def body(db):
        eid = await event(db, 2, wa=msg(1))
        await ep.sync_whatsapp(db)
        await db.delete_event(eid)
        assert (await ep.sync_whatsapp(db))["unpinned"] == [eid]
    run(tmp_path, body)


def test_the_switch_turns_whatsapp_off_on_its_own(tmp_path, waha, monkeypatch):
    monkeypatch.setattr(ep, "pins_cfg", lambda: {**CFG, "whatsapp": {**CFG["whatsapp"], "enabled": False}})

    async def body(db):
        await event(db, 2, wa=msg(1), tg=501)
        b = FakeBot()
        out = await ep.sync_all(b, db)
        assert waha.calls == [] and out["whatsapp"] == {"pinned": [], "unpinned": []}
        assert [p["message_id"] for p in b.pins] == [501]  # Telegram goes on as before
    run(tmp_path, body)


# ── failure isolation ──

def test_whatsapp_down_logs_once_and_telegram_carries_on(tmp_path, waha, caplog):
    async def body(db):
        eid = await event(db, 2, wa=msg(1), tg=501)
        waha.fail = httpx.ConnectError("connection refused")
        b = FakeBot()
        with caplog.at_level(logging.WARNING, logger="bot.utils.event_pins"):
            await ep.pin_job(SimpleNamespace(bot=b, bot_data={"db": db}))
            await ep.pin_job(SimpleNamespace(bot=b, bot_data={"db": db}))
        assert [p["message_id"] for p in b.pins] == [501]
        assert (await db.list_event_pins())[eid]["pinned"]
        assert eid not in await db.list_event_wa_pins()
        assert sum("cannot be reached" in r.message for r in caplog.records) == 1
        waha.fail = None
        await ep.sync_all(b, db)  # back up: pinned on the next check, Telegram untouched
        assert len(waha.pins()) == 1 and len(b.pins) == 1 and b.unpins == []
    run(tmp_path, body)


def test_no_rights_and_a_gone_message_each_log_one_clear_line(tmp_path, waha, caplog):
    async def body(db):
        await event(db, 2, wa=msg(1))
        with caplog.at_level(logging.WARNING, logger="bot.utils.event_pins"):
            waha.status = 403
            await ep.sync_whatsapp(db)
            await ep.sync_whatsapp(db)
            waha.status = 404
            await ep.sync_whatsapp(db)
            await ep.sync_whatsapp(db)
        lines = [r.message for r in caplog.records]
        assert len(lines) == 2
        assert sum("no admin right" in m for m in lines) == 1 and sum("is gone" in m for m in lines) == 1
        assert all("Telegram pinning is not affected" in m for m in lines)
    run(tmp_path, body)


def test_telegram_failing_never_blocks_whatsapp(tmp_path, waha, monkeypatch):
    async def body(db):
        await event(db, 2, wa=msg(1), tg=501)
        monkeypatch.setattr(ep, "sync", AsyncMock(side_effect=RuntimeError("telegram exploded")))
        out = await ep.sync_all(FakeBot(), db)
        assert out["telegram"] is None and len(waha.pins()) == 1
    run(tmp_path, body)


def test_a_whatsapp_failure_never_undoes_a_telegram_pin(tmp_path, waha):
    async def body(db):
        eid = await event(db, 2, wa=msg(1), tg=501)
        b = FakeBot()
        await ep.sync_all(b, db)
        waha.status = 500
        await db.delete_event(eid)
        await ep.sync_all(b, db)  # WhatsApp unpin fails; Telegram still unpins its own post
        assert not (await db.list_event_pins())[eid]["pinned"] and b.unpins[0]["message_id"] == 501
        assert (await db.list_event_wa_pins())[eid]["pinned"]  # kept as pinned, retried next check
    run(tmp_path, body)


# ── the Events page and publishing ──

def test_the_manual_pin_on_the_events_page_applies_to_both_groups(tmp_path, waha):
    async def body(db):
        eid = await event(db, 20, wa=msg(1), tg=1401)
        b = FakeBot()
        req = lambda pin: SimpleNamespace(session={"authenticated": True}, json=AsyncMock(return_value={"pin": pin}))
        with patch("telegram.Bot", return_value=b):
            r = await dash.event_pin(eid, req(True), db)
            assert r["pinned"] is True and r["whatsapp_pinned"] is True and r["whatsapp_status"] == "ok"
            assert b.pins[-1]["message_id"] == 1401 and waha.pins() == [(esc(msg(1)), MONTH)]
            r = await dash.event_pin(eid, req(False), db)
            assert r["pinned"] is False and r["whatsapp_pinned"] is False
            assert waha.unpins() == [esc(msg(1))]
            await ep.sync_all(b, db)
            assert len(waha.pins()) == 1, "the operator's unpin holds in WhatsApp too"
            waha.status = 403
            r = await dash.event_pin(eid, req(True), db)
            assert r["status"] == "ok" and r["whatsapp_status"] == "failed"  # the page then says WhatsApp failed
    run(tmp_path, body)


def test_publishing_stores_the_post_id_and_pins_a_near_event_at_once(tmp_path, waha, monkeypatch):
    monkeypatch.setattr(whatsapp_sender, "send_text", AsyncMock(return_value=msg(7)))
    monkeypatch.setattr(whatsapp_sender, "send_poll", AsyncMock(return_value=f"true_{WA_MAIN}_POLL_x"))

    async def body(db):
        rid = await db.create_event_request(token="t1", platform="whatsapp", requester_id="5@lid", requester_name="דנה",
                                            created_at=NOW.isoformat(), expires_at=at(1).isoformat())
        row = {"id": rid}
        await db.update_event_request(rid, status="submitted", title="ערב",
                                      event_date=(TODAY + timedelta(days=2)).isoformat(), event_time="20:00")
        tg = AsyncMock(return_value=SimpleNamespace(message_id=7))
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock(), pin_chat_message=AsyncMock(),
                              unpin_chat_message=AsyncMock())
        with patch.object(calendar_handler, "send_message_with_optional_cover", tg):
            r = await shared_events.approve_request(bot, db, row["id"], "real")
        assert r["status"] == "ok"
        assert (await db.get_event(r["event_id"]))["wa_message_id"] == msg(7)
        assert waha.pins() == [(esc(msg(7)), WEEK)]
    run(tmp_path, body)


def test_settings_have_the_whatsapp_switch_and_limits():
    from bot.utils.config import get_settings
    from bot.utils.copy import load_copy_block

    cfg = get_settings()["event_pins"]["whatsapp"]
    assert cfg["enabled"] is True and cfg["max_pins"] == 3
    assert sorted(cfg["durations_s"]) == [DAY, WEEK, MONTH]
    copy = load_copy_block("event_pins", default={})
    assert copy.get("wa_pinned") and copy.get("wa_not_pinned") and copy.get("failed_whatsapp")


def test_the_events_page_shows_both_pin_states():
    import jinja2

    from bot.utils.copy import load_copy_block

    src = (dash.Path(dash.__file__).parent / "templates" / "events.html").read_text(encoding="utf-8")
    block = src[src.index("{% set has_wa"):]
    block = block[:block.index("{% endif %}\n                    </td>") + len("{% endif %}")]
    copy = load_copy_block("event_pins", default={})
    page = lambda e, wa_on=True, wa=None, tg=None: jinja2.Environment().from_string(block).render(
        e=e, wa_pins_on=wa_on, wa_pins=wa or {}, event_pins=tg or {}, pins_copy=copy, pins_auto=True,
        today_iso="2026-11-26")
    both = {"id": 1, "active": 1, "message_id": 5, "wa_message_id": msg(1), "event_date": "2026-11-28"}
    html = page(both, wa={1: {"pinned": 1}}, tg={1: {"pinned": 0}})
    assert copy["wa_pinned"] in html and copy["not_pinned"] in html
    assert copy["wa_not_pinned"] in page(both) and copy["wa_pinned"] not in page(both)
    assert copy["wa_not_pinned"] not in page(both, wa_on=False)  # WhatsApp pinning off: no WhatsApp line
    wa_only = {**both, "message_id": None}
    html = page(wa_only, wa={1: {"pinned": 1}})
    assert copy["wa_pinned"] in html and copy["unpin"] in html and copy["not_pinned"] not in html
