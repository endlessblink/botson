"""Short links in event posts, the picture paste in the form, and the reader's log lines."""

import asyncio
import json
import os
import re
import subprocess
import sys
from datetime import timedelta
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
LONG = "https://tickets.example.com/shows/%D7%A9%D7%9D-%D7%90%D7%99%D7%A8%D7%95%D7%A2-%D7%9C%D7%93%D7%95%D7%92%D7%9E%D7%94/1234567890/?a=1&b=%2F#x"
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


async def open_form(db, who=MEMBER):
    link = await dash.agent_event_request(req({"chat_id": who, "requester": who, "name": "Dana"}), db)
    return link["url"].rsplit("/", 1)[1]


async def look(db, token, link):
    return (await dash.member_event_link_preview(token, req({"link": link}), db))["link"]


async def submitted(db, form=FORM, previewed=True):
    token = await open_form(db)
    if previewed:  # the form always asks before it sends
        await look(db, token, form.get("link"))
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


def test_the_code_in_the_preview_works_at_once_and_is_the_one_in_both_posts(tmp_path):
    async def body(db):
        token = await open_form(db)
        shown = await look(db, token, LONG)
        assert re.fullmatch(r"https://botson\.example/l/[A-Za-z0-9]{8}", shown)
        r = await dash.event_short_link(shown[-8:], db)
        assert r.status_code == 302 and r.headers["location"] == LONG
        _, row, admin = await submitted(db)
        assert LONG in admin.send_message.await_args.kwargs["text"]  # the approver sees where it really goes
        tg, wa = await publish(db, row["id"])
        assert link_lines(tg) == link_lines(wa) == [shown] and LONG not in tg + wa
        assert shown in whatsapp_sender.rtl_text(wa).split("\n")  # no invisible mark on the link line
        assert (await db.get_event_request(request_id=row["id"]))["link"] == LONG
        assert await codes(db) == [{"code": shown[-8:], "url": LONG}]
    run(tmp_path, body)


def test_the_same_link_keeps_one_code_however_often_it_is_typed(tmp_path):
    async def body(db):
        token, other = await open_form(db), await open_form(db, "8@lid")
        seen = {await look(db, t, link) for t in (token, token, other) for link in (LONG, f" {LONG} ", LONG.replace("?a=1", "?utm_source=x&a=1"))}
        assert len(seen) == 1 and len(await codes(db)) == 1
        assert (await db.get_event_request(token=token))["short_links"] == 1
        assert (await db.get_event_request(token=other))["short_links"] == 0  # it already existed
    run(tmp_path, body)


def test_one_form_can_only_make_a_few_codes(tmp_path):
    async def body(db):
        token = await open_form(db)
        links = [f"https://example.com/{i}/" + "a" * 130 for i in range(8)]
        shown = await asyncio.gather(*(look(db, token, link) for link in links))
        assert sum("/l/" in x for x in shown) == 5 and len(await codes(db)) == 5
        refused = links[[i for i, x in enumerate(shown) if "/l/" not in x][0]]
        assert refused in shown  # the cleaned link itself, nothing invented
        made = links[[i for i, x in enumerate(shown) if "/l/" in x][0]]
        assert "/l/" in await look(db, token, made)  # its own links still answer
        _, row, _ = await submitted(db, {**FORM, "link": refused}, previewed=False)
        tg, wa = await publish(db, row["id"])
        assert link_lines(tg) == link_lines(wa) == [refused]  # the post agrees with what was shown
    run(tmp_path, body)


def test_only_an_open_form_can_ask(tmp_path):
    async def body(db):
        _, row, _ = await submitted(db, previewed=False)
        for bad in (row["token"], "unknown", ""):
            with pytest.raises(dash.HTTPException) as e:
                await look(db, bad, LONG)
            assert e.value.status_code == 403
        token = await open_form(db)
        await db.update_event_request((await db.get_event_request(token=token))["id"], expires_at="2000-01-01")
        with pytest.raises(dash.HTTPException):
            await look(db, token, LONG)
        assert await codes(db) == []
        assert await db.draft_short_link(request_id=row["id"], url=LONG, code="abcdefgh", now="2099", cap=5) is None
    run(tmp_path, body)


def test_an_invalid_link_never_gets_a_code(tmp_path):
    async def body(db):
        token = await open_form(db)
        tail = "x" * 180
        for bad in ("javascript:alert(1)" + tail, "data:text/html," + tail, "ftp://example.com/" + tail, "https:// " + tail, "https:///" + tail,
                    "https://example.com/" + "x" * 2001, ["x"], {"a": 1}, None, ""):
            assert await look(db, token, bad) == ""
        for short in ("https://example.com/x", "https://example.com/" + "a" * 100):  # 120 is still short enough
            assert await look(db, token, short) == short
        assert await codes(db) == []
    run(tmp_path, body)


def test_an_unpublished_code_stops_and_a_published_one_stays(tmp_path, monkeypatch):
    async def body(db):
        token = await open_form(db)
        code = (await look(db, token, LONG))[-8:]
        real = shared_events._israel_now()
        monkeypatch.setattr(shared_events, "_israel_now", lambda: real + timedelta(hours=25))
        with pytest.raises(dash.HTTPException):
            await dash.event_short_link(code, db)
        assert (await shared_events.post_link(db, LONG)).endswith(code)  # approved a day later: works again
        monkeypatch.setattr(shared_events, "_israel_now", lambda: real + timedelta(days=900))
        assert (await dash.event_short_link(code, db)).headers["location"] == LONG
    run(tmp_path, body)


def test_codes_already_live_keep_working_after_the_update(tmp_path):
    async def body(db):
        await db._db.execute("INSERT INTO event_short_links (code, url, created_at) VALUES ('oldcode1', ?, '2020-01-01')", (LONG,))
        assert (await dash.event_short_link("oldcode1", db)).headers["location"] == LONG
    run(tmp_path, body)


def test_the_redirect_is_public_and_survives_glued_marks(tmp_path):
    def hit(fake, method, path):
        return getattr(client(fake), method)(path)

    async def body(db):
        code = (await look(db, await open_form(db), LONG))[-8:]
        assert (await dash.event_short_link(code + "\u200f", db)).headers["location"] == LONG
        for wrong in ("nope1234", "", "../e", code[:-1], code.swapcase()):
            with pytest.raises(dash.HTTPException) as e:
                await dash.event_short_link(wrong, db)
            assert e.value.status_code == 404
        fake = MagicMock(short_link_url=AsyncMock(return_value=LONG))
        for method in ("get", "head"):
            r = await asyncio.get_running_loop().run_in_executor(None, hit, fake, method, f"/l/{code}")
            assert r.status_code == 302 and r.headers["location"] == LONG and r.headers["cache-control"] == "no-store"
    run(tmp_path, body)


def test_trouble_or_a_missing_address_leaves_the_cleaned_link(tmp_path, monkeypatch):
    async def body(db):
        broken = MagicMock(draft_short_link=AsyncMock(side_effect=OSError("disk")), publish_short_link=AsyncMock(side_effect=OSError("disk")))
        taken = MagicMock(draft_short_link=AsyncMock(return_value=None))
        assert await shared_events.preview_link(broken, {"id": 1}, LONG) == LONG and await shared_events.post_link(broken, LONG) == LONG
        assert await shared_events.preview_link(taken, {"id": 1}, LONG) == LONG
        for same in (None, "", "https://example.com/x"):
            assert await shared_events.post_link(db, same) == same
        monkeypatch.setattr(config, "PUBLIC_DASHBOARD_URL", "")
        assert await shared_events.preview_link(db, {"id": 1}, LONG) == LONG
    run(tmp_path, body)


def test_nobody_can_mint_a_code_over_the_web():
    assert {m for r in dash.app.routes if getattr(r, "path", "").startswith("/l/") for m in r.methods} == {"GET", "HEAD"}


def test_a_very_long_link_is_whole_or_refused_and_never_cut(tmp_path):
    async def body(db):
        whole = "https://example.com/" + "%D7%A9" * 300
        _, row, _ = await submitted(db, {**FORM, "link": "\u200f" + whole + "\u200f"})
        assert row["link"] == whole
        r, _, _ = await submitted(db, {**FORM, "link": whole + "x" * 400})
        assert r.status_code == 422
    run(tmp_path, body)


def test_the_form_page_takes_long_links_and_holds_no_stand_in(tmp_path):
    async def body(db):
        link = await dash.agent_event_request(req({"chat_id": MEMBER, "requester": MEMBER, "name": "Dana"}), db)
        html = await page(db, link)
        assert 'maxlength="2000"' in html and "/l/" not in html and "xxxx" not in html
        for key in ("form_image_paste", "form_image_hint"):
            text = load_copy("member_events", key)
            assert text in html or json.dumps(text)[1:-1] in html
    run(tmp_path, body)


FB = "https://www.facebook.com/events/1499006433289623/"


@pytest.mark.parametrize("given", [
    "https://www.facebook.com/events/s/%D7%A9%D7%9D-%D7%90%D7%99%D7%A8%D7%95%D7%A2-%D7%9C/1499006433289623/",
    "https://www.facebook.com/events/s/%D7%A9%D7%9C/1499006433289623", "https://facebook.com/events/1499006433289623",
    "https://m.facebook.com/events/1499006433289623/?acontext=%7B%22a%22%3A1%7D&ref=share", "http://web.facebook.com/events/1499006433289623?mibextid=x",
    "https://www.facebook.com/events/some-venue/some-title/1499006433289623/?ref=newsfeed#x", "https://WWW.Facebook.com/events/some-title/1499006433289623/",
    "https://www.facebook.com/events/s/2026/1499006433289623/", "https://www.facebook.com/events/s/שם-אירוע/1499006433289623/", FB,
])
def test_facebook_event_links_become_their_short_real_address(given):
    assert shared_events.clean_link(given) == FB


@pytest.mark.parametrize("same", [
    "https://www.facebook.com/events/1499006433289623/permalink/1499006433289999/", "https://www.facebook.com/events/s/title/", "https://www.facebook.com/events/",
    "https://www.facebook.com/groups/1499006433289623/", "https://www.facebook.com/events/123/", "https://www.facebook.com/share/abc123/",
    "https://evil.example/www.facebook.com/events/1499006433289623/", "https://www.facebook.com.evil.example/events/1499006433289623/",
    "https://www.facebook.com@evil.example/events/1499006433289623/", "https://www.facebook.com:8443/events/1499006433289623/",
    "https://example.com/p?b=2&a=1&a=1&x=%2F+%20&ref=home&si=7#part", "https://example.com/a?utm=1&xfbclid=2", "https://example.com/a;b?q=a%26utm_source=1",
    "https://example.com/a?b=", "javascript:alert(1)", "ftp://example.com/?utm_source=x", "not a link", "https://[broken/?utm_source=1", "",
])
def test_a_link_without_a_rule_or_with_anything_unclear_is_left_alone(same):
    assert shared_events.clean_link(same) == same


@pytest.mark.parametrize("given,want", [
    ("https://example.com/p?utm_source=a&id=7&fbclid=z&UTM_Medium=b", "https://example.com/p?id=7"),
    ("https://example.com/p?gclid=1#tickets", "https://example.com/p#tickets"), ("https://example.com/p?", "https://example.com/p"),
    ("https://example.com/p#", "https://example.com/p"), ("https://example.com/p?igshid=1&x=%D7%A9&y", "https://example.com/p?x=%D7%A9&y"),
    ("https://youtu.be/abc?si=tr&t=30", "https://youtu.be/abc?t=30"), ("https://www.instagram.com/p/x/?igsh=1", "https://www.instagram.com/p/x/"),
    ("https://m.facebook.com/events/1499006433289623/?event_time_id=99&ref=x", FB + "?event_time_id=99"),
])
def test_tracking_is_dropped_and_the_rest_kept_exactly(given, want):
    assert shared_events.clean_link(given) == want


def test_a_bad_rule_in_config_keeps_the_original_link():
    for rule in ({"hosts": ["a.b"], "path": "(", "to": "x"}, {"hosts": ["a.b"], "path": "/(x)", "to": "javascript:{0}"}, {"hosts": ["a.b"], "path": "/(x)", "to": "https://{3}"}):
        with patch.object(shared_events, "member_cfg", return_value={"link_cleaning": {"site_rules": [rule]}}):
            assert shared_events.clean_link("https://a.b/x") == "https://a.b/x"


def test_the_preview_and_both_posts_show_the_same_cleaned_link(tmp_path):
    async def body(db):
        typed = "https://www.facebook.com/events/s/%D7%A9%D7%9C/1499006433289623/?fbclid=1"
        opened = await dash.agent_event_request(req({"chat_id": MEMBER, "requester": MEMBER, "name": "Dana"}), db)
        token = opened["url"].rsplit("/", 1)[1]
        seen = await dash.member_event_link_preview(token, req({"link": typed}), db)
        assert seen == {"link": FB}
        _, row, _ = await submitted(db, {**FORM, "link": typed})
        tg, wa = await publish(db, row["id"])
        assert link_lines(tg) == link_lines(wa) == [FB]
        assert (await db.get_event_request(request_id=row["id"]))["link"] == typed and await codes(db) == []
        with pytest.raises(dash.HTTPException) as e:  # a used form answers nothing
            await dash.member_event_link_preview(token, req({"link": typed}), db)
        assert e.value.status_code == 403
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
