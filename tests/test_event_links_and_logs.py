"""Short links in event posts, the picture paste in the form, and the reader's log lines."""

import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import bot.handlers.calendar as calendar_handler
from bot.database.db import Database
from bot.utils import config, shared_events, whatsapp_sender
from bot.utils.copy import load_copy
from dashboard import app as dash
from tests.test_event_picture import MEMBER, page, req

REPO = Path(__file__).resolve().parent.parent
LONG = "https://www.facebook.com/events/s/%D7%A9%D7%9D-%D7%90%D7%99%D7%A8%D7%95%D7%A2-%D7%9C%D7%93%D7%95%D7%92%D7%9E%D7%94/1234567890/?a=1&b=%2F#x"
FORM = {"title": "Vinyl night", "event_date": "2099-01-01", "event_time": "20:00", "link": LONG}


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    for k, v in {"BOTSON_AGENT_API_TOKEN": "agent-secret-value", "BOT_TOKEN": "123:test", "GROUP_ID": "-100",
                 "TEST_GROUP_ID": "-200", "WHATSAPP_GROUP_ID": "111@g.us", "WHATSAPP_TEST_GROUP_ID": "222@g.us"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "https://botson.example")
    monkeypatch.setattr(config, "ADMIN_IDS", [999])


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "l.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


async def codes(db):
    async with db._db.execute("SELECT code, url FROM event_short_links") as cur:
        return [dict(r) for r in await cur.fetchall()]


async def submitted(db, form=FORM):
    link = await dash.agent_event_request(req({"chat_id": MEMBER, "requester": MEMBER, "name": "Dana"}), db)
    token = link["url"].rsplit("/", 1)[1]
    admin = MagicMock(send_message=AsyncMock())
    with patch("telegram.Bot", return_value=admin):
        r = await dash.member_event_submit(token, req(form), db)
    return r, await db.get_event_request(token=token), admin


async def publish(db, request_id):
    tg, wa = AsyncMock(return_value=MagicMock(message_id=7)), AsyncMock()
    with patch.object(calendar_handler, "send_message_with_optional_cover", tg), \
            patch.object(whatsapp_sender, "send_text", wa), patch.object(whatsapp_sender, "send_poll", AsyncMock(return_value="true_2@g.us_ABC_x")):
        r = await shared_events.approve_request(MagicMock(edit_message_reply_markup=AsyncMock()), db, request_id, "test")
    assert r["status"] == "ok"
    return tg.await_args.kwargs["text"], wa.await_args.args[1]


def link_lines(text):
    return [line for line in text.split("\n") if "://" in line]


def client(db):
    dash.app.dependency_overrides[dash.get_db] = lambda: db
    return TestClient(dash.app, follow_redirects=False)  # no session, no startup


@pytest.fixture(autouse=True)
def _no_overrides():
    yield
    dash.app.dependency_overrides.clear()


def test_both_posts_carry_the_short_link_and_the_original_is_kept(tmp_path):
    async def body(db):
        _, row, admin = await submitted(db)
        assert LONG in admin.send_message.await_args.kwargs["text"]  # the approver sees where it really goes
        tg, wa = await publish(db, row["id"])
        short = link_lines(tg)
        assert len(short) == 1 and re.fullmatch(r"https://botson\.example/l/[A-Za-z0-9]{8}", short[0])
        assert link_lines(wa) == short and LONG not in tg + wa
        # exactly what goes to WhatsApp: no invisible mark on the link line
        assert short[0] in whatsapp_sender.rtl_text(wa).split("\n")
        assert (await db.get_event_request(request_id=row["id"]))["link"] == LONG
        assert (await codes(db)) == [{"code": short[0][-8:], "url": LONG}]
    run(tmp_path, body)


def test_no_code_exists_before_publishing_and_a_bad_link_never_gets_one(tmp_path):
    async def body(db):
        _, row, _ = await submitted(db)
        await shared_events.reject_request(db, row["id"])
        for bad in ("javascript:alert(1)" + "x" * 90, "ftp://example.com/" + "x" * 90, "https://" + " " + "x" * 90, "https:///" + "x" * 90):
            r, _, _ = await submitted(db, {**FORM, "link": bad})
            assert r.status_code == 422
            assert await shared_events.post_link(db, bad) == bad
        assert await codes(db) == []
    run(tmp_path, body)


def test_the_redirect_is_public_exact_and_survives_glued_marks(tmp_path):
    async def redirect(db):
        short = await shared_events.post_link(db, LONG)
        code = short.rsplit("/", 1)[1]
        loop = asyncio.get_running_loop()
        # the route itself, on this loop's database
        for path_code in (code, code + "\u200f"):
            r = await dash.event_short_link(path_code, db)
            assert r.status_code == 302 and r.headers["location"] == LONG
        for wrong in ("nope1234", "", "../e", code[:-1], code.swapcase()):
            with pytest.raises(dash.HTTPException) as e:
                await dash.event_short_link(wrong, db)
            assert e.value.status_code == 404
        # through the whole app: no login, GET and HEAD
        fake = MagicMock(short_link_url=AsyncMock(return_value=LONG))
        for method in ("get", "head"):
            r = await loop.run_in_executor(None, hit_with(fake), method, f"/l/{code}")
            assert r.status_code == 302 and r.headers["location"] == LONG and r.headers["cache-control"] == "no-store"
    def hit_with(fake):
        return lambda method, path: getattr(client(fake), method)(path)
    run(tmp_path, redirect)


def test_nobody_can_mint_a_code_over_the_web():
    assert {m for r in dash.app.routes if getattr(r, "path", "").startswith("/l/") for m in r.methods} == {"GET", "HEAD"}


def test_the_same_link_keeps_one_code_and_a_taken_code_is_retried(tmp_path, monkeypatch):
    async def body(db):
        first = await shared_events.post_link(db, LONG)
        assert await shared_events.post_link(db, LONG) == first and len(await codes(db)) == 1
        taken = first.rsplit("/", 1)[1]
        assert await db.short_link_code(LONG + "2", taken, "now") is None
        assert await db.short_link_url(taken) == LONG
    run(tmp_path, body)


def test_short_links_trouble_and_a_missing_address_leave_the_link_alone(tmp_path, monkeypatch):
    async def body(db):
        edge = "https://example.com/" + "a" * 40  # exactly the configured 60
        for same in (None, "", "https://example.com/x", edge):
            assert await shared_events.post_link(db, same) == same
        assert (await shared_events.post_link(db, edge + "a")).startswith("https://botson.example/l/")
        broken = MagicMock(short_link_code=AsyncMock(side_effect=OSError("disk")))
        assert await shared_events.post_link(broken, LONG) == LONG
        monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "")
        assert await shared_events.post_link(db, LONG) == LONG
    run(tmp_path, body)


def test_a_very_long_link_is_whole_or_refused_and_never_cut(tmp_path):
    async def body(db):
        whole = "https://example.com/" + "%D7%A9" * 300
        _, row, _ = await submitted(db, {**FORM, "link": "\u200f" + whole + "\u200f"})
        assert row["link"] == whole
        r, _, _ = await submitted(db, {**FORM, "link": whole + "x" * 400})
        assert r.status_code == 422
    run(tmp_path, body)


def test_the_form_page_previews_a_stand_in_and_takes_long_links(tmp_path):
    async def body(db):
        link = await dash.agent_event_request(req({"chat_id": MEMBER, "requester": MEMBER, "name": "Dana"}), db)
        html = await page(db, link)
        assert 'maxlength="2000"' in html and "https://botson.example/l/xxxxxxxx" in html
        for key in ("form_image_paste", "form_image_hint"):
            assert load_copy("member_events", key) in html
    run(tmp_path, body)


def test_whatsapp_text_keeps_links_free_of_invisible_marks():
    rlm = whatsapp_sender.RLM
    out = whatsapp_sender.rtl_text("שלום\nhttps://a.b/c\nקישור: https://a.b/d\nhttps://a.b/e וגם").split("\n")
    assert out == [f"{rlm}שלום{rlm}", "https://a.b/c", f"{rlm}קישור: https://a.b/d", f"{rlm}https://a.b/e וגם{rlm}", rlm]


def service_log(code: str, prelude: str = "") -> str:
    """What a process that imports the dashboard (as uvicorn does, bare root logger) prints."""
    out = subprocess.run([sys.executable, "-c", f"import logging\n{prelude}\nimport dashboard.app\n{code}"], cwd=REPO, text=True,
                         capture_output=True, timeout=120, env={**os.environ, "PYTHONPATH": str(REPO)})
    assert out.returncode == 0, out.stderr
    return out.stderr + out.stdout


READ = """
import asyncio, json
from dashboard import event_picture as ep
async def codex(*a): return json.dumps({"title": "SECRET-TITLE"})
ep._read_with_codex = codex
asyncio.run(ep.read_event_picture(b"x", "png", codex_bin="codex"))
from bot.utils.cli_home import log_cli_timing
log_cli_timing("codex", "ok", 1.5, "event-picture")
logging.getLogger("bot.utils.freshness").info("NOISE")
logging.getLogger("httpx").info("NOISE")
"""


def test_the_service_log_shows_which_cli_read_the_picture_and_nothing_private():
    log = service_log(READ)
    assert re.search(r"codex read the picture in \d+\.\ds: \{'title': 12\}", log)
    assert "[cli-timing] codex ok in 1.5s (ctx=event-picture)" in log
    assert "SECRET" not in log and "NOISE" not in log


def test_the_lines_are_not_printed_twice_where_logging_is_already_set_up():
    log = service_log(READ, prelude="logging.basicConfig(level=logging.WARNING)")
    assert log.count("codex read the picture") == 1 and log.count("[cli-timing] codex ok") == 1
