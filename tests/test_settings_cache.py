"""settings.yaml is parsed once and reused until the file changes (counted, not timed).

The calendar page and picture used to parse the 74 KB file 37-46 and 10 times per request,
which made the public page take 5-8 seconds on the server.
"""

import asyncio
from pathlib import Path

import pytest
from starlette.requests import Request

import bot.handlers.calendar  # noqa: F401
from bot.database.db import Database
from bot.utils import config, copy as copy_mod, events_calendar
from dashboard import app as dash


@pytest.fixture
def parses(monkeypatch):
    """Count real parses of settings.yaml, starting from an empty cache."""
    box = {"n": 0}
    real = config.load_yaml

    def counting(name):
        if name == "settings.yaml":
            box["n"] += 1
        return real(name)

    monkeypatch.setattr(config, "load_yaml", counting)
    config._SETTINGS_CACHE.clear()
    return box


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "s.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


def _req():
    return Request({"type": "http", "method": "GET", "path": "/welcome", "query_string": b"", "headers": [], "app": dash.app})


def test_the_calendar_page_and_picture_parse_settings_at_most_once(tmp_path, parses, monkeypatch):
    monkeypatch.setenv("GROUP_ID", "-1001")

    async def body(db):
        eid = await db.create_event(title="ערב", description="", event_date="2026-11-28", event_time="20:00",
                                    location="יפו", created_by=0)
        await db.update_event(eid, message_id=5, tg_chat_id=-1001)
        await dash.community_page(_req(), None, db)
        assert parses["n"] <= 1, f"the page parsed settings.yaml {parses['n']} times"
        parses["n"] = 0
        await dash.community_page(_req(), "2026-11", db)
        await dash.community_event_page(_req(), eid, db)
        await dash.community_event_ics(eid, db)
        assert parses["n"] == 0, "an unchanged file is never parsed again"
        await events_calendar.month_png(db, "2027-01", tmp_path)
        assert parses["n"] == 0, "the picture reuses the parsed settings too"
    run(tmp_path, body)


def test_a_changed_file_is_read_again_at_once(tmp_path, monkeypatch, parses):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    f = tmp_path / "settings.yaml"
    f.write_text("copy:\n  t:\n    k: one\n", encoding="utf-8")
    assert copy_mod.load_copy("t", "k") == "one"
    assert copy_mod.load_copy("t", "k") == "one" and parses["n"] == 1
    f.write_text("copy:\n  t:\n    k: two, longer\n", encoding="utf-8")  # a dashboard save or a deploy
    assert copy_mod.load_copy("t", "k") == "two, longer" and parses["n"] == 2
    assert config.get_settings()["copy"]["t"]["k"] == "two, longer"


def test_callers_that_change_settings_never_change_them_for_others(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    config._SETTINGS_CACHE.clear()
    (tmp_path / "settings.yaml").write_text("a:\n  b: 1\ncopy:\n  t:\n    lst: [x]\n", encoding="utf-8")
    mine = config.get_settings()
    mine["a"]["b"] = 99
    assert config.get_settings()["a"]["b"] == 1
    block = copy_mod.load_copy_block("t")
    block["lst"].append("y")
    assert copy_mod.load_copy_block("t") == {"lst": ["x"]}


def test_the_real_settings_load_the_same_with_the_fast_loader():
    import yaml

    raw = (Path(config.__file__).resolve().parents[2] / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert yaml.load(raw, Loader=config._SAFE_LOADER) == yaml.safe_load(raw)
