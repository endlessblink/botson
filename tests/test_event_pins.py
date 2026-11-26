"""Event posts pinned silently around their date (bot/utils/event_pins.py). Every Telegram call is faked."""

import asyncio
import logging
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

import bot.handlers.calendar  # noqa: F401
from bot.database.db import Database
from bot.utils import event_pins as ep
from bot.utils import shared_events, topic_guard
from bot.utils.pin_manager import pin_replacing_previous
from dashboard import app as dash

MAIN, TEST, TOPIC = -1001, -1002, 341
TODAY = date(2026, 11, 26)


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GROUP_ID", str(MAIN))
    monkeypatch.setenv("TEST_GROUP_ID", str(TEST))
    monkeypatch.setattr(topic_guard, "GROUP_ID", MAIN)
    monkeypatch.setattr(topic_guard, "TEST_GROUP_ID", TEST)
    monkeypatch.setattr(ep, "pins_cfg", lambda: {"enabled": True, "days_before": 7, "test_group_in_test_mode": True})
    monkeypatch.setattr(ep, "_today", lambda: TODAY)
    monkeypatch.setattr(shared_events, "default_mode", lambda: "real")
    ep._warned.clear()


class FakeBot:
    def __init__(self, pin_error=None, unpin_error=None):
        self.pins, self.unpins = [], []
        self.pin_error, self.unpin_error = pin_error, unpin_error

    async def pin_chat_message(self, **kw):
        if self.pin_error:
            raise self.pin_error
        self.pins.append(kw)

    async def unpin_chat_message(self, **kw):
        if self.unpin_error:
            raise self.unpin_error
        self.unpins.append(kw)


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "p.db"))
        await db.init()
        await db.upsert_verified_forum_topic(TOPIC, "עדכונים", "welcome", "test")
        await db.set_handler_routing("events_publish", TOPIC)
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


async def post(db, days_ahead, *, message_id, chat=MAIN, topic=TOPIC, title="ערב"):
    eid = await db.create_event(title=title, description="", event_date=(TODAY + timedelta(days=days_ahead)).isoformat(),
                                event_time="20:00", location=None, created_by=0)
    await db.update_event(eid, message_id=message_id, tg_chat_id=chat, topic_id=topic)
    return eid


def test_near_event_is_pinned_silently_and_a_second_run_changes_nothing(tmp_path):
    async def body(db):
        eid = await post(db, 3, message_id=501)
        b = FakeBot()
        assert (await ep.sync(b, db))["pinned"] == [eid]
        assert b.pins == [{"chat_id": MAIN, "message_id": 501, "disable_notification": True}]
        assert (await ep.sync(b, db)) == {"pinned": [], "unpinned": []}
        assert len(b.pins) == 1 and b.unpins == []
    run(tmp_path, body)


def test_far_event_waits_until_seven_days_before_and_is_unpinned_the_day_after(tmp_path):
    async def body(db):
        eid = await post(db, 10, message_id=502)
        day = TODAY + timedelta(days=10)
        b = FakeBot()
        await ep.sync(b, db)
        assert b.pins == []
        await ep.sync(b, db, today=day - timedelta(days=8))
        assert b.pins == []
        await ep.sync(b, db, today=day - timedelta(days=7))
        assert [p["message_id"] for p in b.pins] == [502]
        await ep.sync(b, db, today=day)
        assert b.unpins == []  # still pinned on the day itself
        assert (await ep.sync(b, db, today=day + timedelta(days=1)))["unpinned"] == [eid]
        assert b.unpins == [{"chat_id": MAIN, "message_id": 502}]
        assert not (await db.list_event_pins())[eid]["pinned"]
    run(tmp_path, body)


def test_several_events_are_pinned_together(tmp_path):
    async def body(db):
        await post(db, 1, message_id=601)
        await post(db, 5, message_id=602)
        await post(db, 30, message_id=603)
        b = FakeBot()
        await ep.sync(b, db)
        assert sorted(p["message_id"] for p in b.pins) == [601, 602]
    run(tmp_path, body)


def test_a_deleted_event_is_unpinned(tmp_path):
    async def body(db):
        eid = await post(db, 2, message_id=701)
        b = FakeBot()
        await ep.sync(b, db)
        await db.delete_event(eid)
        assert (await ep.sync(b, db))["unpinned"] == [eid]
        assert b.unpins == [{"chat_id": MAIN, "message_id": 701}]
    run(tmp_path, body)


def test_only_published_posts_in_the_real_group(tmp_path, monkeypatch):
    async def body(db):
        await post(db, 2, message_id=801, chat=TEST, topic=None, title="בדיקה")
        await post(db, 2, message_id=802, topic=9999)  # a topic the guard does not know
        b = FakeBot()
        await ep.sync(b, db)
        assert b.pins == []
        monkeypatch.setattr(shared_events, "default_mode", lambda: "test")  # test mode is being exercised
        await ep.sync(b, db)
        assert [(p["chat_id"], p["message_id"]) for p in b.pins] == [(TEST, 801)]
    run(tmp_path, body)


def test_no_right_to_pin_is_logged_once_and_nothing_breaks(tmp_path, caplog):
    async def body(db):
        eid = await post(db, 2, message_id=901)
        b = FakeBot(pin_error=RuntimeError("Bad Request: not enough rights to manage pinned messages in the chat"))
        with caplog.at_level(logging.WARNING, logger="bot.utils.event_pins"):
            assert await ep.sync(b, db) == {"pinned": [], "unpinned": []}
            await ep.sync(b, db)
            await ep.pin_job(SimpleNamespace(bot=b, bot_data={"db": db}))
        assert sum("no right to pin" in r.message for r in caplog.records) == 1
        assert eid not in await db.list_event_pins()
    run(tmp_path, body)


def test_a_restart_picks_up_from_the_database(tmp_path):
    async def body(db):
        eid = await post(db, 2, message_id=1001)
        await ep.sync(FakeBot(), db)
        await db.close()
        fresh = Database(str(tmp_path / "p.db"))
        await fresh.init()
        try:
            b = FakeBot()
            await ep.sync(b, fresh)
            assert b.pins == [] and b.unpins == []  # already pinned, nothing to redo
            # Unpinned by hand in Telegram meanwhile: unpinning later still finishes cleanly.
            b2 = FakeBot(unpin_error=RuntimeError("Bad Request: message is not pinned"))
            assert (await ep.sync(b2, fresh, today=TODAY + timedelta(days=3)))["unpinned"] == [eid]
        finally:
            await fresh.close()
        await db.init()
    run(tmp_path, body)


def test_the_previous_pin_rule_and_event_pins_never_touch_each_other(tmp_path):
    async def body(db):
        eid = await post(db, 2, message_id=1101)
        b = FakeBot()
        await ep.sync(b, db)
        await pin_replacing_previous(b, db, chat_id=MAIN, topic_id=TOPIC, message_id=1200)
        await pin_replacing_previous(b, db, chat_id=MAIN, topic_id=TOPIC, message_id=1201)
        assert [u["message_id"] for u in b.unpins] == [1200]  # only the general pin was replaced
        await ep.sync(b, db, today=TODAY + timedelta(days=3))
        assert [u["message_id"] for u in b.unpins] == [1200, 1101]  # the event pin goes, 1201 stays
        assert eid in await db.list_event_pins()
    run(tmp_path, body)


def test_switch_off_stops_automatic_pins_but_manual_still_works(tmp_path, monkeypatch):
    monkeypatch.setattr(ep, "pins_cfg", lambda: {"enabled": False, "days_before": 7})

    async def body(db):
        eid = await post(db, 2, message_id=1301)
        b = FakeBot()
        await ep.sync(b, db)
        assert b.pins == []
        r = await ep.set_manual(b, db, eid, True)
        assert r["status"] == "ok" and r["pinned"] and b.pins[0]["message_id"] == 1301
        assert (await ep.sync(b, db, today=TODAY + timedelta(days=3)))["unpinned"] == [eid]  # over: unpinned anyway
    run(tmp_path, body)


def test_dashboard_manual_pin_and_unpin_hold(tmp_path):
    async def body(db):
        eid = await post(db, 2, message_id=1401)
        b = FakeBot()
        req = lambda pin: SimpleNamespace(session={"authenticated": True}, json=AsyncMock(return_value={"pin": pin}))
        with patch("telegram.Bot", return_value=b):
            assert (await dash.event_pin(eid, req(True), db))["pinned"] is True
            r = await dash.event_pin(eid, req(False), db)
            assert r["pinned"] is False and b.unpins[-1]["message_id"] == 1401
            await ep.sync(b, db)
            assert len(b.pins) == 1, "the operator's unpin holds against the automatic rule"
            with pytest.raises(HTTPException) as e:
                await dash.event_pin(99999, req(True), db)
            assert e.value.status_code == 404
            with pytest.raises(HTTPException) as e:
                await dash.event_pin(eid, SimpleNamespace(session={}, json=AsyncMock()), db)
            assert e.value.status_code == 401
    run(tmp_path, body)


def test_deleting_from_the_dashboard_unpins_at_once(tmp_path):
    async def body(db):
        eid = await post(db, 2, message_id=1501)
        b = FakeBot()
        await ep.sync(b, db)
        with patch("telegram.Bot", return_value=b):
            await dash.delete_event(eid, SimpleNamespace(session={"authenticated": True}), db)
        assert b.unpins == [{"chat_id": MAIN, "message_id": 1501}]
    run(tmp_path, body)


def test_config_defaults():
    from bot.utils.config import get_settings

    cfg = get_settings()["event_pins"]
    assert cfg["enabled"] is True and cfg["days_before"] == 7 and cfg["check_minutes"] >= 1
