"""WhatsApp quiet-group question: Botson reuses a Telegram post members answered."""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi import HTTPException

from bot.database.db import Database
from dashboard import app as dash

TOKEN = "agent-secret-value"
ADMIN = 999
CFG = {"whatsapp_quiet_question": {
    "enabled": True, "source_types": ["discussion"], "min_repliers": 3,
    "lookback_days": 120, "max_per_day": 1, "skip_if_contains": ["ערוץ"],
}}


def request(query="", payload=None):
    return SimpleNamespace(
        headers={"authorization": f"Bearer {TOKEN}", "content-type": "application/json"},
        query_params={"dry_run": "1"} if query == "dry" else {},
        json=lambda: _v(payload or {"target": "whatsapp"}),
        session={}, state=SimpleNamespace(),
    )


async def _v(value):
    return value


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("BOTSON_AGENT_API_TOKEN", TOKEN)
    monkeypatch.setattr(dash, "get_settings", lambda: CFG)
    monkeypatch.setattr(dash, "ADMIN_IDS", [ADMIN])


async def sent_post(db, text, repliers, *, mtype="discussion", target="main"):
    today = datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
    row = await db.create_scheduled_message(text, mtype, 4037, target, today, "10:00")
    await db.mark_message_sent(row, 1000 + row)
    for uid in repliers:
        await db._db.execute(
            "INSERT INTO prompt_replies (scheduled_msg_id, user_id, replied_at) VALUES (?, ?, ?)",
            (row, uid, "2026-10-10 10:00:00"))
    await db._db.commit()
    return row


def run(tmp_path, body):
    async def scenario():
        db = Database(str(tmp_path / "q.db"))
        await db.init()
        try:
            return await body(db)
        finally:
            await db.close()
    return asyncio.run(scenario())


def test_picks_the_most_answered_question_and_schedules_it_for_whatsapp_once_a_day(tmp_path):
    async def body(db):
        await sent_post(db, "איזה סרט הייתם רואים שוב רק בשביל הפסקול שלו?", [1, 2, 3])
        best = await sent_post(db, "איזה מאכל הייתם מוחקים מהעולם אם הייתם יכולים?", [1, 2, 3, 4, 5])
        out = await dash.whatsapp_quiet_question(request(), db)
        assert out["source_id"] == best and out["repliers"] == 5
        rows = await db.get_scheduled_messages("2020-01-01", "2030-01-01")
        wa = [r for r in rows if r["target_group"] == "whatsapp"]
        assert len(wa) == 1 and wa[0]["message_type"] == "custom" and wa[0]["status"] == "scheduled"
        assert wa[0]["created_by"] == "whatsapp-quiet"
        with pytest.raises(HTTPException) as again:
            await dash.whatsapp_quiet_question(request(), db)
        assert again.value.status_code == 409
    run(tmp_path, body)


def test_never_repeats_a_question_whatsapp_already_had(tmp_path):
    async def body(db):
        text = "איזה מאכל הייתם מוחקים מהעולם אם הייתם יכולים?"
        await sent_post(db, text, [1, 2, 3, 4])
        today = datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
        await db.create_scheduled_message(text, "custom", None, "whatsapp", "2026-09-01", "12:00")
        out = await dash.whatsapp_quiet_question(request("dry"), db)
        assert out["candidates"] == [] and out["bank"] == []
        with pytest.raises(HTTPException) as none:
            await dash.whatsapp_quiet_question(request(), db)
        assert none.value.status_code == 404
        assert today
        # A test-group run doesn't use the question up for the real group.
        test_out = await dash.whatsapp_quiet_question(request(payload={"target": "whatsapp_test"}), db)
        assert test_out["text"] == text
    run(tmp_path, body)


def test_only_proven_text_posts_from_telegram_count(tmp_path):
    async def body(db):
        await sent_post(db, "שאלה שרק שניים ענו עליה, מה דעתכם על חורף?", [1, 2])
        await sent_post(db, "שאלה שרק האדמין ענה עליה הרבה פעמים, מה איתכם?", [ADMIN, 1, 2])
        await sent_post(db, "מה הסרט האחרון שראיתם בערוץ הסרטים שלנו?", [1, 2, 3])
        await sent_post(db, "שאלה מהבוקר שלא ברשימת הסוגים, מה שתיתם?", [1, 2, 3], mtype="morning")
        await sent_post(db, "שאלה שכבר נשלחה בוואטסאפ וענו עליה שם, מה איתכם?", [1, 2, 3], target="whatsapp")
        out = await dash.whatsapp_quiet_question(request("dry"), db)
        assert out["candidates"] == []
    run(tmp_path, body)


def test_rejects_non_whatsapp_targets_and_missing_token(tmp_path):
    async def body(db):
        with pytest.raises(HTTPException) as bad:
            await dash.whatsapp_quiet_question(request(payload={"target": "main"}), db)
        assert bad.value.status_code == 400
        anon = request()
        anon.headers = {"content-type": "application/json"}
        with pytest.raises(HTTPException) as anon_err:
            await dash.whatsapp_quiet_question(anon, db)
        assert anon_err.value.status_code == 401
    run(tmp_path, body)


def test_production_config_is_enabled_with_sane_limits():
    cfg = dash.load_yaml("settings.yaml")["whatsapp_quiet_question"] if hasattr(dash, "load_yaml") else None
    assert cfg and cfg["enabled"] and cfg["max_per_day"] == 1 and cfg["min_repliers"] >= 2
    assert json.dumps(cfg)


def test_niqqud_is_removed_but_maqaf_stays(tmp_path):
    async def body(db):
        await sent_post(db, "מתוך כל הבָּיוֹגְרָפְיוֹת שראיתם ב־2026, איזו גרמה לכם לגלול?", [1, 2, 3])
        out = await dash.whatsapp_quiet_question(request(), db)
        assert out["text"] == "מתוך כל הביוגרפיות שראיתם ב־2026, איזו גרמה לכם לגלול?"
        rows = await db.get_scheduled_messages("2020-01-01", "2030-01-01")
        assert [r["text"] for r in rows if r["target_group"] == "whatsapp"] == [out["text"]]
    run(tmp_path, body)


def test_bank_questions_follow_once_proven_ones_run_out(tmp_path, monkeypatch):
    bank = ["שאלה ראשונה מהבנק על טלפורטר למאדים, נכנסים?", "שאלה שנייה מהבנק על מכונת חוויות, נכנסים?"]
    monkeypatch.setattr(dash, "get_settings", lambda: {"whatsapp_quiet_question": {
        **CFG["whatsapp_quiet_question"], "bank": bank, "max_per_day": 5}})

    async def body(db):
        proven = await sent_post(db, "איזה מאכל הייתם מוחקים מהעולם אם הייתם יכולים?", [1, 2, 3])
        first = await dash.whatsapp_quiet_question(request(), db)
        assert first["source_id"] == proven
        second = await dash.whatsapp_quiet_question(request(), db)
        assert second["text"] == bank[0] and second["source_id"] is None
        third = await dash.whatsapp_quiet_question(request(), db)
        assert third["text"] == bank[1]
        with pytest.raises(HTTPException) as empty:
            await dash.whatsapp_quiet_question(request(), db)
        assert empty.value.status_code == 404
    run(tmp_path, body)


def test_production_bank_has_no_niqqud_or_telegram_only_wording():
    import re
    cfg = dash.load_yaml("settings.yaml")["whatsapp_quiet_question"]
    assert cfg["bank"]
    for q in cfg["bank"]:
        assert not re.search("[\u0591-\u05BD\u05BF\u05C1\u05C2\u05C4\u05C5\u05C7]", q), q
        assert not any(f in q for f in cfg["skip_if_contains"]), q
