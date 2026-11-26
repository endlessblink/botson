"""Members ask to see the month's event calendar (Telegram handler, Botty's endpoints,
the Events page "send now" control). Every Telegram, WhatsApp and Botson call is faked."""

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import bot.handlers.calendar  # noqa: F401  (loads telegram.ext before telegram.Bot is patched)
from bot.database.db import Database
from bot.handlers import calendar_request as cr
from bot.handlers import event_requests
from bot.utils import calendar_share as cs
from bot.utils import config, events_calendar, shared_events, topic_guard, whatsapp_sender
from bot.utils.copy import load_copy
from dashboard import app as dash

TOKEN = "agent-secret-value"
MAIN, TEST = -1001, -1002
EVENTS_TOPIC, CORNER = 341, 4037
OPERATOR = 999
MEMBER_WA = "5@lid"
FAKE_PNG = b"\x89PNG fake"


async def _v(value):
    return value


def req(payload=None, *, agent=True, session=False):
    return SimpleNamespace(headers={"authorization": f"Bearer {TOKEN}"} if agent else {},
                           json=lambda: _v(payload or {}), session={"authenticated": True} if session else {},
                           state=SimpleNamespace())


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for k, v in {"BOTSON_AGENT_API_TOKEN": TOKEN, "BOT_TOKEN": "123:test", "GROUP_ID": str(MAIN),
                 "TEST_GROUP_ID": str(TEST), "WHATSAPP_WAHA_URL": "http://127.0.0.1:1", "WHATSAPP_WAHA_API_KEY": "k",
                 "WHATSAPP_GROUP_ID": "111@g.us", "WHATSAPP_TEST_GROUP_ID": "222@g.us"}.items():
        monkeypatch.setenv(k, v)
    for module in (topic_guard, cr):
        monkeypatch.setattr(module, "GROUP_ID", MAIN)
        monkeypatch.setattr(module, "TEST_GROUP_ID", TEST)
    monkeypatch.setattr(cr, "ADMIN_IDS", [OPERATOR])
    monkeypatch.setattr(config, "ADMIN_IDS", [OPERATOR])
    monkeypatch.setattr(events_calendar, "month_png", AsyncMock(return_value=FAKE_PNG))
    monkeypatch.setattr(whatsapp_sender, "group_member_ids", AsyncMock(return_value=[MEMBER_WA]))
    monkeypatch.setattr(whatsapp_sender, "phone_for_lid", AsyncMock(return_value=None))
    monkeypatch.setattr(whatsapp_sender, "lid_for_phone", AsyncMock(return_value=None))
    monkeypatch.setattr(whatsapp_sender, "send_image_data", AsyncMock(side_effect=AssertionError("unfaked WhatsApp send")))


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "c.db"))
        await db.init()
        await db.upsert_verified_forum_topic(EVENTS_TOPIC, "מצטרפים חדשים + עדכונים", "welcome", "test")
        await db.upsert_verified_forum_topic(CORNER, "הפינה של בוטסון", "botson_corner", "test")
        await db.set_handler_routing("events_publish", EVENTS_TOPIC)
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


async def publish_event(db, title="ערב בדיקה", day="15"):
    eid = await db.create_event(title=title, description="", event_date=f"{cs.month_for(False)}-{day}",
                                event_time="20:00", location=None, created_by=0)
    await db.update_event(eid, message_id=5, tg_chat_id=MAIN)
    return eid


async def age_posts(db, days):
    async with db._db.execute("SELECT platform, chat_id, posted_at FROM calendar_posts") as cur:
        rows = [dict(r) for r in await cur.fetchall()]
    from datetime import datetime, timedelta
    for r in rows:
        older = (datetime.fromisoformat(r["posted_at"]) - timedelta(days=days)).isoformat()
        await db._db.execute("UPDATE calendar_posts SET posted_at = ? WHERE platform = ? AND chat_id = ?",
                             (older, r["platform"], r["chat_id"]))
    await db._db.commit()


# ── Telegram fakes ──

class FakeBot:
    def __init__(self, member_status="member"):
        self.id, self.username = 77, "thebotstonbot"
        self.ids = iter(range(900, 2000))
        self.photos, self.texts = [], []
        self.get_chat_member = AsyncMock(return_value=SimpleNamespace(status=member_status))

    async def send_photo(self, **kw):
        self.photos.append(kw)
        return SimpleNamespace(message_id=next(self.ids), date=None, caption=kw.get("caption"))

    async def send_message(self, **kw):
        self.texts.append(kw)
        return SimpleNamespace(message_id=next(self.ids), date=None, text=kw.get("text"))


def update(text, *, chat=MAIN, kind="supergroup", thread=CORNER, user=5, tag=True, message_id=50):
    private = kind == "private"
    body = f"@thebotstonbot {text}" if tag and not private else text
    msg = SimpleNamespace(text=body, chat_id=chat, message_thread_id=None if private else thread, message_id=message_id,
                          reply_to_message=None, set_reaction=AsyncMock())
    return SimpleNamespace(effective_message=msg, effective_user=SimpleNamespace(id=user, is_bot=False, first_name="Dana"),
                           effective_chat=SimpleNamespace(id=chat, type=kind))


def ctx(db, bot=None):
    return SimpleNamespace(bot=bot or FakeBot(), bot_data={"db": db})


def link_words(text_kw):
    ents = text_kw.get("entities") or []
    raw = text_kw["text"].encode("utf-16-le")
    return [(raw[e.offset * 2:(e.offset + e.length) * 2].decode("utf-16-le"), e.url) for e in ents]


# ── Words ──

def test_schedule_words_and_what_does_not_count():
    for yes in ('לו"ז', "לו״ז?", "מה הלוז", "מה קורה החודש?", "אירועים קרובים", "מה האירועים הקרובים", "בלו\"ז", "calendar"):
        assert cs.asks_for_calendar(yes), yes
    for no in ("לוזים", "מה קורה?", "תקציר", "ליצור אירוע", 'אני רוצה לקבוע אירוע בלו"ז', "calendars", ""):
        assert not cs.asks_for_calendar(no), no
    assert cs.wants_next_month('לו"ז לחודש הבא') and cs.wants_next_month("מה קורה בחודש הבא")
    assert not cs.wants_next_month('לו"ז')
    this, nxt = cs.month_for(False), cs.month_for(True)
    y, m = map(int, this.split("-"))
    assert nxt == (f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}")


def test_all_new_wording_is_in_config():
    for key in ("caption", "already_posted", "posted_elsewhere", "link_words", "private_limit", "members_only", "failed"):
        assert not str(load_copy("calendar_share", key, month="x", year=1)).startswith("[copy missing")
    cfg = cs.share_cfg()
    assert cfg["group_every_days"] == 3 and cfg["private_per_day"] >= 1 and cfg["words"]


# ── Telegram group ──

def test_group_tag_posts_in_the_events_topic_through_the_guard(tmp_path):
    async def body(db):
        c = ctx(db)
        u = update('לו"ז')
        await cr.handle_calendar_words(u, c)
        u.effective_message.set_reaction.assert_awaited_once_with(cs.share_cfg()["ack_reaction"])
        assert len(c.bot.photos) == 1
        photo = c.bot.photos[0]
        assert photo["chat_id"] == MAIN and photo["message_thread_id"] == EVENTS_TOPIC
        assert photo["photo"] == FAKE_PNG and "reply_to_message_id" not in photo
        assert photo["caption"] == cs.caption(cs.month_for(False))
        # Asked in another topic: the asker is told where it went, with a clickable link.
        assert len(c.bot.texts) == 1 and c.bot.texts[0]["message_thread_id"] == CORNER
        assert c.bot.texts[0]["reply_to_message_id"] == 50
        assert link_words(c.bot.texts[0]) == [(load_copy("calendar_share", "link_words"), "https://t.me/c/1/900")]
        assert "parse_mode" not in c.bot.texts[0]
        post = await db.get_calendar_post("telegram", str(MAIN))
        assert post["message_id"] == "900" and post["thread_id"] == EVENTS_TOPIC
    run(tmp_path, body)


def test_asking_again_within_three_days_points_at_the_posted_picture(tmp_path):
    async def body(db):
        c = ctx(db)
        await cr.handle_calendar_words(update('לו"ז', message_id=50), c)
        await cr.handle_calendar_words(update("מה קורה החודש?", message_id=51, user=6), c)
        assert len(c.bot.photos) == 1
        reply = c.bot.texts[-1]
        assert reply["text"].startswith(load_copy("calendar_share", "already_posted"))
        assert reply["reply_to_message_id"] == 51 and link_words(reply)[0][1] == "https://t.me/c/1/900"
        # Asked inside the events topic itself: a reply quoting the picture.
        await cr.handle_calendar_words(update('לו"ז', thread=EVENTS_TOPIC, message_id=52, user=7), c)
        assert c.bot.texts[-1]["reply_to_message_id"] == 900
        assert c.bot.texts[-1]["text"] == load_copy("calendar_share", "already_posted")
        assert len(c.bot.photos) == 1
        # After the three days, a new picture.
        await age_posts(db, 3.1)
        await cr.handle_calendar_words(update('לו"ז', message_id=53), c)
        assert len(c.bot.photos) == 2
    run(tmp_path, body)


def test_a_new_event_or_another_month_makes_a_fresh_post_inside_the_days(tmp_path):
    async def body(db):
        c = ctx(db)
        await cr.handle_calendar_words(update('לו"ז', message_id=50), c)
        await publish_event(db)
        await cr.handle_calendar_words(update('לו"ז', message_id=51), c)
        assert len(c.bot.photos) == 2, "the earlier picture misses the new event"
        await cr.handle_calendar_words(update('לו"ז לחודש הבא', message_id=52), c)
        assert len(c.bot.photos) == 3
        assert c.bot.photos[-1]["caption"] == cs.caption(cs.month_for(True))
        await cr.handle_calendar_words(update('לו"ז לחודש הבא', message_id=53), c)
        assert len(c.bot.photos) == 3, "same month, nothing new: pointed at it"
    run(tmp_path, body)


def test_the_operator_is_never_held_back(tmp_path):
    async def body(db):
        c = ctx(db)
        for i in range(3):
            await cr.handle_calendar_words(update('לו"ז', user=OPERATOR, message_id=60 + i), c)
        assert len(c.bot.photos) == 3
    run(tmp_path, body)


def test_guard_refusal_and_missing_routing_post_nothing_and_say_so(tmp_path):
    async def body(db):
        await db.set_handler_routing("events_publish", 5555)  # not a verified topic
        c = ctx(db)
        await cr.handle_calendar_words(update('לו"ז'), c)
        assert c.bot.photos == []
        assert c.bot.texts[-1]["text"] == load_copy("calendar_share", "failed")
        assert await db.get_calendar_post("telegram", str(MAIN)) is None
        await db.set_handler_routing("events_publish", None)
        await cr.handle_calendar_words(update('לו"ז', message_id=51), c)
        assert c.bot.photos == [] and c.bot.texts[-1]["text"] == load_copy("calendar_share", "failed")
    run(tmp_path, body)


def test_untagged_messages_other_chats_and_event_requests_are_left_alone(tmp_path):
    async def body(db):
        c = ctx(db)
        await cr.handle_calendar_words(update('מה הלו"ז?', tag=False), c)
        await cr.handle_calendar_words(update('לו"ז', chat=-1009), c)
        await cr.handle_calendar_words(update("אני רוצה ליצור אירוע"), c)
        assert c.bot.photos == [] and c.bot.texts == []
    run(tmp_path, body)


def test_the_event_form_handler_skips_schedule_requests(tmp_path, monkeypatch):
    link = AsyncMock(side_effect=AssertionError("a form link for a schedule request"))
    monkeypatch.setattr(event_requests, "create_request_link", link)
    monkeypatch.setattr(event_requests, "GROUP_ID", MAIN)

    async def body(db):
        c = ctx(db)
        await event_requests.handle_event_request(update("אירועים קרובים", chat=5, kind="private"), c)
        await event_requests.handle_event_request(update('לו"ז', chat=5, kind="private"), c)
        assert c.bot.texts == []
    run(tmp_path, body)


def test_the_test_group_posts_where_it_was_asked(tmp_path):
    async def body(db):
        c = ctx(db)
        await cr.handle_calendar_words(update('לו"ז', chat=TEST, thread=12), c)
        assert c.bot.photos[0]["chat_id"] == TEST and c.bot.photos[0]["message_thread_id"] == 12
        assert c.bot.photos[0]["reply_to_message_id"] == 50 and c.bot.texts == []
    run(tmp_path, body)


def test_the_calendar_command_works_too(tmp_path):
    async def body(db):
        c = ctx(db)
        await cr.calendar_command(update("/calendar", tag=False), c)
        assert c.bot.photos[0]["message_thread_id"] == EVENTS_TOPIC
    run(tmp_path, body)


# ── Telegram private ──

def test_private_members_any_time_up_to_the_daily_cap(tmp_path):
    async def body(db):
        c = ctx(db)
        cap = cs.share_cfg()["private_per_day"]
        for i in range(cap):
            await cr.handle_calendar_words(update('לו"ז', chat=5, kind="private", message_id=70 + i), c)
        assert len(c.bot.photos) == cap
        assert c.bot.photos[0]["chat_id"] == 5 and c.bot.photos[0].get("message_thread_id") is None
        await cr.handle_calendar_words(update('לו"ז', chat=5, kind="private", message_id=99), c)
        assert len(c.bot.photos) == cap
        assert c.bot.texts[-1]["text"] == load_copy("calendar_share", "private_limit")
        # A private copy does not count as the group's post.
        assert await db.get_calendar_post("telegram", "5") is None
        # Next day the count starts again.
        await db._db.execute("UPDATE calendar_private_sends SET day = '2000-01-01'")
        await db._db.commit()
        await cr.handle_calendar_words(update('לו"ז', chat=5, kind="private", message_id=100), c)
        assert len(c.bot.photos) == cap + 1
    run(tmp_path, body)


def test_private_strangers_and_unreadable_membership(tmp_path):
    async def body(db):
        stranger = ctx(db, FakeBot(member_status="left"))
        await cr.handle_calendar_words(update('לו"ז', chat=8, user=8, kind="private"), stranger)
        assert stranger.bot.photos == [] and stranger.bot.texts[-1]["text"] == load_copy("calendar_share", "members_only")
        broken = FakeBot()
        broken.get_chat_member = AsyncMock(side_effect=RuntimeError("telegram down"))
        bc = ctx(db, broken)
        await cr.handle_calendar_words(update('לו"ז', chat=8, user=8, kind="private"), bc)
        assert broken.photos == [] and broken.texts[-1]["text"] == load_copy("calendar_share", "failed")
        # The operator is not checked and not capped.
        op = ctx(db, FakeBot(member_status="left"))
        for i in range(cs.share_cfg()["private_per_day"] + 2):
            await cr.handle_calendar_words(update('לו"ז', chat=OPERATOR, user=OPERATOR, kind="private", message_id=i + 1), op)
        assert len(op.bot.photos) == cs.share_cfg()["private_per_day"] + 2
    run(tmp_path, body)


# ── Botty's endpoints (WhatsApp) ──

def test_agent_share_rules_for_a_whatsapp_group(tmp_path):
    async def body(db):
        g = {"chat_id": "111@g.us", "requester": MEMBER_WA}
        first = await dash.agent_events_calendar_share(req(g), db)
        assert first["status"] == "post" and base64.b64decode(first["image"]) == FAKE_PNG
        assert first["caption"] == cs.caption(first["month"])
        await dash.agent_events_calendar_posted(req({**g, "message_id": "true_111@g.us_CAL", "month": first["month"],
                                                     "version": first["version"]}), db)
        again = await dash.agent_events_calendar_share(req({**g, "requester": "6@lid"}), db)
        assert again == {"status": "already", "text": load_copy("calendar_share", "already_posted"),
                         "message_id": "true_111@g.us_CAL"}
        assert (await dash.agent_events_calendar_share(req({**g, "operator": True}), db))["status"] == "post"
        assert (await dash.agent_events_calendar_share(req({**g, "next": True}), db))["status"] == "post"
        await publish_event(db)
        assert (await dash.agent_events_calendar_share(req(g), db))["status"] == "post"
    run(tmp_path, body)


def test_agent_share_private_members_cap_and_strangers(tmp_path):
    async def body(db):
        me = {"chat_id": MEMBER_WA, "requester": MEMBER_WA}
        for _ in range(cs.share_cfg()["private_per_day"]):
            r = await dash.agent_events_calendar_share(req(me), db)
            assert r["status"] == "post"
            await dash.agent_events_calendar_posted(req({**me, "message_id": "x", "month": r["month"], "version": r["version"]}), db)
        assert await dash.agent_events_calendar_share(req(me), db) == {"status": "limit", "text": load_copy("calendar_share", "private_limit")}
        stranger = {"chat_id": "666@lid", "requester": "666@lid"}
        assert (await dash.agent_events_calendar_share(req(stranger), db))["status"] == "members_only"
        assert (await dash.agent_events_calendar_share(req({**stranger, "operator": True}), db))["status"] == "post"
        whatsapp_sender.group_member_ids.side_effect = RuntimeError("WAHA down")
        with pytest.raises(HTTPException) as e:
            await dash.agent_events_calendar_share(req(stranger), db)
        assert e.value.status_code == 503
    run(tmp_path, body)


def test_agent_share_needs_the_token_and_a_sane_chat(tmp_path):
    async def body(db):
        with pytest.raises(HTTPException) as e:
            await dash.agent_events_calendar_share(req({"chat_id": "111@g.us", "requester": "5@lid"}, agent=False), db)
        assert e.value.status_code == 401
        for bad in ({"chat_id": "111@g.us"}, {"chat_id": "5@lid", "requester": "6@lid"}, {"chat_id": "x", "requester": "x"}):
            with pytest.raises(HTTPException) as e:
                await dash.agent_events_calendar_share(req(bad), db)
            assert e.value.status_code == 400
        with pytest.raises(HTTPException) as e:
            await dash.agent_events_calendar_posted(req({"chat_id": "111@g.us", "requester": "5@lid", "month": "nope", "version": "1"}), db)
        assert e.value.status_code == 400
    run(tmp_path, body)


def test_agent_share_sends_a_real_picture(tmp_path, monkeypatch):
    monkeypatch.undo()  # the real drawing, once
    async def body(db):
        with patch.object(config, "ADMIN_IDS", [OPERATOR]):
            r = await dash.agent_events_calendar_share(req({"chat_id": "1@lid", "requester": "1@lid", "operator": True}), db)
        assert base64.b64decode(r["image"]).startswith(b"\x89PNG\r\n\x1a\n")
    for k, v in {"BOTSON_AGENT_API_TOKEN": TOKEN, "GROUP_ID": str(MAIN)}.items():
        monkeypatch.setenv(k, v)
    run(tmp_path, body)


# ── Events page control ──

def _send(db, mode, shown_mode=None, month=None):
    return dash.events_calendar_send(req({"month": month or cs.month_for(False), "mode": shown_mode or mode},
                                         agent=False, session=True), db)


def test_dashboard_send_in_test_mode_goes_to_the_test_groups(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_events, "default_mode", lambda: "test")
    wa = AsyncMock(return_value="true_222@g.us_CAL")
    monkeypatch.setattr(whatsapp_sender, "send_image_data", wa)

    async def body(db):
        tg = FakeBot()
        with patch("telegram.Bot", return_value=tg):
            r = await _send(db, "test")
        assert r["results"] == {"telegram": {"ok": True}, "whatsapp": {"ok": True}}
        assert tg.photos[0]["chat_id"] == TEST and tg.photos[0].get("message_thread_id") is None
        assert wa.await_args.args[0] == "222@g.us" and wa.await_args.args[1] == FAKE_PNG
        assert (await db.get_calendar_post("whatsapp", "222@g.us"))["message_id"] == "true_222@g.us_CAL"
    run(tmp_path, body)


def test_dashboard_send_in_live_mode_goes_to_the_real_groups_and_counts_as_the_post(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_events, "default_mode", lambda: "real")
    monkeypatch.setattr(whatsapp_sender, "send_image_data", AsyncMock(side_effect=RuntimeError("WAHA 500")))

    async def body(db):
        tg = FakeBot()
        with patch("telegram.Bot", return_value=tg):
            r = await _send(db, "real")
        assert r["results"]["telegram"] == {"ok": True}
        assert r["results"]["whatsapp"]["ok"] is False and "WAHA 500" in r["results"]["whatsapp"]["error"]
        assert tg.photos[0]["chat_id"] == MAIN and tg.photos[0]["message_thread_id"] == EVENTS_TOPIC
        # A member asking right after is pointed at it.
        c = ctx(db)
        await cr.handle_calendar_words(update('לו"ז'), c)
        assert c.bot.photos == [] and c.bot.texts[-1]["text"].startswith(load_copy("calendar_share", "already_posted"))
    run(tmp_path, body)


def test_dashboard_send_refuses_when_the_switch_changed_or_no_login(tmp_path, monkeypatch):
    monkeypatch.setattr(shared_events, "default_mode", lambda: "real")

    async def body(db):
        tg = FakeBot()
        with patch("telegram.Bot", return_value=tg):
            with pytest.raises(HTTPException) as e:
                await _send(db, "real", shown_mode="test")
            assert e.value.status_code == 409
            with pytest.raises(HTTPException) as e:
                await dash.events_calendar_send(req({"month": "2026-11", "mode": "real"}, agent=True), db)
            assert e.value.status_code == 401
            with pytest.raises(HTTPException) as e:
                await _send(db, "real", month="2026-13")
            assert e.value.status_code == 400
        assert tg.photos == []
    run(tmp_path, body)


def test_events_page_says_where_it_will_post():
    import jinja2

    src = (dash.Path(dash.__file__).parent / "templates" / "events.html").read_text(encoding="utf-8")
    assert "calendar_copy.send_where_real" in src and "calendar_copy.send_where_test" in src
    env = jinja2.Environment()
    block = src[src.index('<p id="calendar-send-where"'):]
    block = block[:block.index("</p>") + 4]
    copy = {"send_where_real": load_copy("events_calendar", "send_where_real"),
            "send_where_test": load_copy("events_calendar", "send_where_test")}
    real = env.from_string(block).render(member_default_mode="real", calendar_copy=copy, calendar_topic="מצטרפים חדשים")
    test = env.from_string(block).render(member_default_mode="test", calendar_copy=copy, calendar_topic="x")
    assert "מצטרפים חדשים" in real and copy["send_where_test"] in test and "{topic}" not in real
