"""Botson Dashboard — FastAPI backend for managing the bot."""

import asyncio
import json
import logging
import os
import secrets
import signal
from pathlib import Path

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.propagate = False

import yaml
from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot.database.db import Database
from bot.utils.config import DB_PATH, get_settings, get_prompts, get_spam_patterns, get_topic_rules, load_yaml
from bot.utils.levels import get_level, get_progress

RELOAD_FLAG = Path(__file__).parent.parent / "data" / "reload"


def _signal_bot_reload():
    """Create a reload flag file that the bot watches for schedule reload."""
    try:
        RELOAD_FLAG.parent.mkdir(parents=True, exist_ok=True)
        RELOAD_FLAG.write_text("reload")
        return True
    except Exception:
        return False


def _load_settings_file() -> dict:
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _save_settings_file(settings: dict) -> None:
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)


def _parse_aliases_input(raw) -> list[str]:
    if isinstance(raw, list):
        items = raw
    else:
        text = str(raw or "")
        items = text.replace("\n", ",").split(",")
    seen = set()
    result = []
    for item in items:
        cleaned = str(item).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _puzzle_group_label(chat_id: int) -> str:
    main_group = int(os.getenv("GROUP_ID", "0") or 0)
    test_group = int(os.getenv("TEST_GROUP_ID", "0") or 0)
    if chat_id == main_group:
        return "קבוצה ראשית"
    if chat_id == test_group:
        return "Sherlocks Den"
    return str(chat_id)


def _winner_summary_text(raw: str | None) -> str:
    try:
        items = json.loads(raw or "[]")
    except Exception:
        items = []
    if not items:
        return "—"
    parts = []
    for item in items[:3]:
        name = item.get("display_name", "חבר/ה")
        wins = item.get("wins", 0)
        parts.append(f"{name} ({wins})")
    return ", ".join(parts)

app = FastAPI(title="Botson Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("DASHBOARD_SECRET", secrets.token_hex(32)))

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
CONFIG_DIR = Path(__file__).parent.parent / "config"
MEDIA_DIR = Path(os.getenv("MEDIA_DIR", str(Path(__file__).parent.parent / "media"))).resolve()
COVERS_DIR = MEDIA_DIR / "covers"
COVERS_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

# Dashboard password from env
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "botson-admin")

# DB instance (initialized on startup)
_db: Database | None = None


@app.on_event("startup")
async def startup():
    global _db
    _db = Database(DB_PATH)
    await _db.init()


@app.on_event("shutdown")
async def shutdown():
    if _db:
        await _db.close()


def get_db() -> Database:
    return _db


def require_auth(request: Request):
    """Check if user is authenticated."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=303, headers={"Location": "/login"})


# ── Auth ─────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, name="login.html", context={"error": None})


@app.post("/login")
async def login(request: Request, password: str = Form(...)):
    if password == DASHBOARD_PASSWORD:
        request.session["authenticated"] = True
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request, name="login.html", context={"error": "סיסמה שגויה"})


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)


# ── Dashboard Pages ──────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    leaders = await db.get_leaderboard(5)
    for m in leaders:
        lvl = get_level(m["karma_points"])
        m["level"] = lvl["level"]
        m["level_tag"] = lvl["tag"]
        m["level_emoji"] = lvl["emoji"]
    top_streaks = await db.get_top_streaks(5)
    upcoming_events = await db.get_upcoming_events(5)
    trivia_leaders = await db.get_trivia_leaderboard(5)
    settings = get_settings()

    return templates.TemplateResponse(request, name="index.html", context={
        "leaders": leaders,
        "streaks": top_streaks,
        "events": upcoming_events,
        "trivia_leaders": trivia_leaders,
        "settings": settings,
    })


# ── Settings API ─────────────────────────────────────────

@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    return templates.TemplateResponse(request, name="settings.html", context={
        "settings": settings,
    })


@app.post("/api/settings/topics")
async def update_topics(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    if "topics" not in settings:
        settings["topics"] = {}
    settings["topics"].update(data)

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


@app.post("/api/settings/antispam")
async def update_antispam(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    if "antispam" not in settings:
        settings["antispam"] = {}
    settings["antispam"].update(data)

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


@app.post("/api/settings/schedule")
async def update_schedule(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    if "schedule" not in settings:
        settings["schedule"] = {}
    settings["schedule"].update(data)

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)

    # Auto-reload bot schedule
    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


@app.post("/api/reload-schedule")
async def reload_schedule(request: Request):
    """Manually trigger bot schedule reload via SIGHUP."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    reloaded = _signal_bot_reload()
    if reloaded:
        return {"status": "ok", "message": "Bot schedule reloaded"}
    return {"status": "error", "message": "Bot not running or PID not found"}


# ── Prompts API ──────────────────────────────────────────

@app.get("/prompts", response_class=HTMLResponse)
async def prompts_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    prompts = get_prompts()
    discussions = {}
    try:
        discussions = load_yaml("discussions.yaml")
    except Exception:
        pass
    settings = get_settings()
    forum_topics = await db.get_forum_topics()

    return templates.TemplateResponse(request, name="prompts.html", context={
        "prompts": prompts,
        "discussions": discussions,
        "settings": settings,
        "forum_topics": forum_topics,
    })


@app.post("/api/prompts/save")
async def save_prompts(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    prompt_type = data.get("type")  # "goals" or "discussions"
    content = data.get("content")

    if prompt_type == "goals":
        path = CONFIG_DIR / "prompts.yaml"
    elif prompt_type == "discussions":
        path = CONFIG_DIR / "discussions.yaml"
    else:
        raise HTTPException(status_code=400, detail="Invalid type")

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(content, f, allow_unicode=True, default_flow_style=False)

    # Prompt pools drive the materializer — reload so future auto rows pick
    # up the fresh content.
    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


# ── Forum Topics API ─────────────────────────────────────

@app.get("/api/topics/forum")
async def get_forum_topics(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    topics = await db.get_forum_topics()
    return {"topics": topics}


@app.post("/api/topics/forum")
async def add_forum_topic(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    data = await request.json()
    topic_id = data.get("topic_id")
    name = data.get("name", "").strip()
    if not topic_id or not name:
        raise HTTPException(status_code=400, detail="topic_id and name required")
    await db.upsert_forum_topic(int(topic_id), name)
    return {"status": "ok"}


# ── Bot Control API ──────────────────────────────────────

@app.post("/api/bot/reload")
async def reload_bot_config(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    pid_file = Path(__file__).parent.parent / "data" / "bot.pid"
    if not pid_file.exists():
        raise HTTPException(status_code=503, detail="Bot PID file not found")

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGHUP)
        return {"status": "ok", "message": f"SIGHUP sent to bot (PID {pid})"}
    except ProcessLookupError:
        raise HTTPException(status_code=503, detail="Bot process not running")


@app.post("/api/bot/send-prompt")
async def send_prompt_now(request: Request, db: Database = Depends(get_db)):
    """Trigger a prompt send via the bot."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    prompt_type = data.get("type")  # "morning", "evening", "discussion"

    if prompt_type not in ("morning", "evening", "discussion"):
        raise HTTPException(status_code=400, detail="Invalid type")

    # Get the prompt
    if prompt_type in ("morning", "evening"):
        prompt = await db.get_random_prompt(prompt_type)
        if not prompt:
            raise HTTPException(status_code=404, detail="No prompts in pool")

        # Send via bot
        from telegram import Bot
        bot = Bot(os.getenv("BOT_TOKEN", ""))
        group_id = int(os.getenv("GROUP_ID", "0"))
        goals_topic = os.getenv("GOALS_TOPIC_ID", "")

        kwargs = {"chat_id": group_id, "text": prompt}
        if goals_topic:
            kwargs["message_thread_id"] = int(goals_topic)

        try:
            await bot.send_message(**kwargs)
            await db.log_activity("goals", f"שלח הודעת {prompt_type} (ידני)", target_channel="goals")
            return {"status": "ok", "prompt": prompt}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    elif prompt_type == "discussion":
        # Pick random category with topic ID
        settings = get_settings()
        topic_ids = settings.get("topics", {}).get("discussions", {})
        discussions_data = {}
        try:
            discussions_data = load_yaml("discussions.yaml")
        except Exception:
            pass

        available = [
            cat for cat in discussions_data
            if cat in topic_ids and topic_ids[cat] and discussions_data[cat]
        ]

        if not available:
            raise HTTPException(status_code=404, detail="No categories with topic IDs and prompts")

        import random
        category = random.choice(available)
        prompt = random.choice(discussions_data[category])
        topic_id = topic_ids[category]

        from telegram import Bot
        bot = Bot(os.getenv("BOT_TOKEN", ""))
        group_id = int(os.getenv("GROUP_ID", "0"))

        try:
            await bot.send_message(
                chat_id=group_id,
                text=f"💬 {prompt}",
                message_thread_id=topic_id,
            )
            await db.log_activity("discussion", f"שלח שאלה לדיון ({category}) (ידני)", target_channel=category)
            return {"status": "ok", "prompt": prompt, "category": category}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/bot/send-message")
async def send_message_to_topic(request: Request, db: Database = Depends(get_db)):
    """Send a custom message to a topic via the bot."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    text = data.get("text", "").strip()
    topic_id = data.get("topic_id")
    target = data.get("target", "main")  # "main" or "test"
    cover_path = data.get("cover_path")
    message_type = data.get("message_type")
    poll_options = data.get("poll_options")
    poll_duration = data.get("poll_duration")

    if not text:
        raise HTTPException(status_code=400, detail="Message text required")

    from telegram import Bot
    from bot.handlers.calendar import (
        send_message_with_optional_cover,
        send_poll_message,
        _parse_poll_options,
    )
    bot = Bot(os.getenv("BOT_TOKEN", ""))

    if target == "test":
        group_id = int(os.getenv("TEST_GROUP_ID", "0"))
    else:
        group_id = int(os.getenv("GROUP_ID", "0"))

    if not group_id:
        raise HTTPException(status_code=400, detail=f"No {target} group ID configured")

    try:
        opts = _parse_poll_options(poll_options)
        if message_type == "poll" and len(opts) >= 2:
            msg = await send_poll_message(
                bot,
                chat_id=group_id,
                question=text,
                options=opts,
                message_thread_id=int(topic_id) if topic_id else None,
                duration_hours=poll_duration,
                cover_path=cover_path,
            )
        else:
            msg = await send_message_with_optional_cover(
                bot,
                chat_id=group_id,
                text=text,
                message_thread_id=int(topic_id) if topic_id else None,
                cover_path=cover_path,
            )
        await db.log_activity("manual_send", f"שלח הודעה ידנית ({'טסט' if target == 'test' else 'ראשית'})", target_channel=str(topic_id or "general"))
        return {"status": "ok", "message_id": msg.message_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/bot/logs")
async def get_bot_logs(request: Request, lines: int = 50):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    log_file = Path(__file__).parent.parent / "data" / "bot.log"
    if not log_file.exists():
        return {"lines": [], "file": str(log_file)}

    # Read last N lines efficiently
    with open(log_file, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines

    return {"lines": [l.rstrip() for l in tail], "total": len(all_lines)}


@app.post("/api/bot/restart")
async def restart_bot(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    pid_file = Path(__file__).parent.parent / "data" / "bot.pid"
    if not pid_file.exists():
        raise HTTPException(status_code=503, detail="Bot PID file not found")

    pid = int(pid_file.read_text().strip())
    try:
        os.kill(pid, signal.SIGTERM)
        return {"status": "ok", "message": f"SIGTERM sent to bot (PID {pid}). Supervisor will restart it."}
    except ProcessLookupError:
        raise HTTPException(status_code=503, detail="Bot process not running")


# ── Spam API ─────────────────────────────────────────────

@app.get("/spam", response_class=HTMLResponse)
async def spam_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    # Get recent spam log
    async with db._db.execute(
        "SELECT * FROM spam_log ORDER BY timestamp DESC LIMIT 50"
    ) as cursor:
        rows = await cursor.fetchall()
        spam_log = [dict(r) for r in rows]

    patterns = get_spam_patterns()
    spam_data = load_yaml("spam_patterns.yaml")
    whitelist = spam_data.get("whitelist", [])
    settings = get_settings()

    return templates.TemplateResponse(request, name="spam.html", context={
        "spam_log": spam_log,
        "patterns": patterns,
        "whitelist": whitelist,
        "settings": settings,
    })


@app.post("/api/spam/patterns")
async def update_spam_patterns(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    path = CONFIG_DIR / "spam_patterns.yaml"

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


# ── Moderation / Topic Routing (Phase 0 observation) ─────

@app.get("/moderation", response_class=HTMLResponse)
async def moderation_page(request: Request, db: Database = Depends(get_db)):
    """Off-topic observation dashboard. Phase 0: read-only data view."""
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    tr_config = settings.get("topic_routing") or {}
    days = int(tr_config.get("observation_days", 14))

    rules = get_topic_rules()
    rules_by_id = {int(r["topic_id"]): r for r in rules if r.get("topic_id")}

    topics = await db.get_forum_topics()
    topic_name_by_id = {int(t["topic_id"]): t["name"] for t in topics}

    counts = await db.get_topic_observation_counts(days=days)
    # pivot into {topic_id: {on, off, unknown, no_rule, total}}
    pivot: dict[int, dict] = {}
    for row in counts:
        tid = int(row["from_topic_id"]) if row["from_topic_id"] is not None else 0
        label = row["fit_label"]
        n = row["n"]
        bucket = pivot.setdefault(tid, {"on": 0, "off": 0, "unknown": 0, "no_rule": 0, "total": 0})
        bucket[label] = bucket.get(label, 0) + n
        bucket["total"] += n

    # Build per-topic summary rows
    topic_rows = []
    seen_ids = set()
    for tid, rule in rules_by_id.items():
        b = pivot.get(tid, {"on": 0, "off": 0, "unknown": 0, "no_rule": 0, "total": 0})
        topic_rows.append({
            "topic_id": tid,
            "name": rule.get("name_he") or topic_name_by_id.get(tid, f"Topic {tid}"),
            "category_key": rule.get("category_key", ""),
            "on": b["on"],
            "off": b["off"],
            "unknown": b["unknown"],
            "no_rule": b["no_rule"],
            "total": b["total"],
            "off_pct": round(100 * b["off"] / b["total"], 1) if b["total"] else 0,
        })
        seen_ids.add(tid)
    # Also include topics that have observations but no rule yet
    for tid, b in pivot.items():
        if tid in seen_ids or tid == 0:
            continue
        topic_rows.append({
            "topic_id": tid,
            "name": topic_name_by_id.get(tid, f"Topic {tid}"),
            "category_key": "(no rule)",
            "on": b["on"],
            "off": b["off"],
            "unknown": b["unknown"],
            "no_rule": b["no_rule"],
            "total": b["total"],
            "off_pct": round(100 * b["off"] / b["total"], 1) if b["total"] else 0,
        })
    topic_rows.sort(key=lambda r: (-r["off"], -r["total"]))

    # Recent off-topic observations — top 30 most recent 'off' labels
    all_obs = await db.get_topic_observations(days=days, limit=500)
    recent_off = []
    for o in all_obs:
        if o["fit_label"] != "off":
            continue
        try:
            hits = json.loads(o["keyword_hits"] or "{}")
        except Exception:
            hits = {}
        recent_off.append({
            "timestamp": o["timestamp"],
            "user_id": o["user_id"],
            "from_topic_id": o["from_topic_id"],
            "from_topic_name": topic_name_by_id.get(int(o["from_topic_id"] or 0), f"Topic {o['from_topic_id']}"),
            "suggested_topic_id": o["suggested_topic_id"],
            "suggested_topic_name": topic_name_by_id.get(int(o["suggested_topic_id"] or 0), "—") if o["suggested_topic_id"] else "—",
            "off_matches": hits.get("off", []),
        })
        if len(recent_off) >= 30:
            break

    # Totals
    totals = {"on": 0, "off": 0, "unknown": 0, "no_rule": 0, "total": 0}
    for b in pivot.values():
        for k in totals:
            totals[k] += b.get(k, 0)

    return templates.TemplateResponse(request, name="moderation.html", context={
        "settings": settings,
        "tr_config": tr_config,
        "observation_days": days,
        "topic_rows": topic_rows,
        "recent_off": recent_off,
        "totals": totals,
        "rules_count": len(rules_by_id),
    })


@app.post("/api/moderation/settings")
async def update_moderation_settings(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        current = yaml.safe_load(f) or {}

    tr = current.setdefault("topic_routing", {})
    if "enabled" in data:
        tr["enabled"] = bool(data["enabled"])
    if "mode" in data and data["mode"] in ("observe", "soft", "strict"):
        tr["mode"] = data["mode"]
    if "observation_days" in data:
        try:
            tr["observation_days"] = max(1, min(90, int(data["observation_days"])))
        except (TypeError, ValueError):
            pass

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(current, f, allow_unicode=True, default_flow_style=False, sort_keys=True)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


# ── Levels API ───────────────────────────────────────────

@app.get("/levels", response_class=HTMLResponse)
async def levels_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    leaders = await db.get_leaderboard(50)
    for m in leaders:
        lvl = get_level(m["karma_points"])
        m["level"] = lvl["level"]
        m["level_tag"] = lvl["tag"]
        m["level_emoji"] = lvl["emoji"]
    settings = get_settings()
    return templates.TemplateResponse(request, name="levels.html", context={
        "leaders": leaders,
        "settings": settings,
    })


@app.post("/api/levels/reset")
async def reset_levels(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.reset_points()
    return {"status": "ok"}


# ── Public Calendar Mini App ─────────────────────────────

_CAL_HEB_DOW = ["א", "ב", "ג", "ד", "ה", "ו", "ש"]
_CAL_HEB_MONTHS = {1: "ינואר", 2: "פברואר", 3: "מרץ", 4: "אפריל",
                   5: "מאי", 6: "יוני", 7: "יולי", 8: "אוגוסט",
                   9: "ספטמבר", 10: "אוקטובר", 11: "נובמבר", 12: "דצמבר"}
_CAL_TYPE_STYLE = {
    "morning":    {"emoji": "🌞", "label": "בוקר",  "css": "bg-amber-500/20 text-amber-200 border-amber-500/40"},
    "evening":    {"emoji": "🌙", "label": "ערב",   "css": "bg-indigo-500/20 text-indigo-200 border-indigo-500/40"},
    "discussion": {"emoji": "💬", "label": "שיחה",  "css": "bg-emerald-500/20 text-emerald-200 border-emerald-500/40"},
    "weekly":     {"emoji": "📊", "label": "סיכום", "css": "bg-violet-500/20 text-violet-200 border-violet-500/40"},
    "event":      {"emoji": "🎉", "label": "אירוע", "css": "bg-rose-500/20 text-rose-200 border-rose-500/40"},
}


def _parse_month(qs: str | None) -> tuple[int, int]:
    """Parse ?month=YYYY-MM into (year, month). Default = current."""
    from datetime import date as _date
    if qs:
        try:
            y, m = qs.split("-")
            yi, mi = int(y), int(m)
            if 1 <= mi <= 12 and 2020 <= yi <= 2099:
                return yi, mi
        except (ValueError, AttributeError):
            pass
    today = _date.today()
    return today.year, today.month


@app.get("/calendar", response_class=HTMLResponse)
async def calendar_mini_app(request: Request, month: str | None = None,
                            db: Database = Depends(get_db)):
    """Public interactive calendar Mini App.

    Query params:
      ?month=YYYY-MM   defaults to current month

    Each cell is tappable — the bottom-sheet panel opens with full event text
    for that day. Top header has month nav (prev/next/today).
    """
    import calendar as _cal
    from datetime import date as _date
    from collections import defaultdict

    year, month_num = _parse_month(month)
    today = _date.today()
    last_day = _cal.monthrange(year, month_num)[1]
    month_start = _date(year, month_num, 1).isoformat()
    month_end = _date(year, month_num, last_day).isoformat()

    async with db._db.execute(
        """SELECT scheduled_date, scheduled_time, message_type, text
           FROM scheduled_messages
           WHERE status='scheduled' AND scheduled_date BETWEEN ? AND ?
           ORDER BY scheduled_date, scheduled_time""",
        (month_start, month_end),
    ) as cur:
        sched_rows = await cur.fetchall()

    async with db._db.execute(
        """SELECT event_date, event_time, title, description, location,
                  rsvp_yes, rsvp_maybe
           FROM events
           WHERE active=1 AND event_date BETWEEN ? AND ?
           ORDER BY event_date, event_time""",
        (month_start, month_end),
    ) as cur:
        event_rows = await cur.fetchall()

    def _short(s: str, n: int = 60) -> str:
        s = (s or "").strip().replace("\n", " ")
        return s if len(s) <= n else s[:n - 1] + "…"

    by_day = defaultdict(list)
    for r in sched_rows:
        meta = _CAL_TYPE_STYLE.get(r["message_type"], _CAL_TYPE_STYLE["event"])
        full = (r["text"] or "").strip()
        by_day[r["scheduled_date"]].append({
            "emoji": meta["emoji"],
            "css": meta["css"],
            "label": meta["label"],
            "time": (r["scheduled_time"] or "")[:5],
            "type": r["message_type"],
            "text": full,
            "short": _short(full),
        })
    for r in event_rows:
        meta = _CAL_TYPE_STYLE["event"]
        rsvp_yes = 0
        rsvp_maybe = 0
        try:
            rsvp_yes = len(json.loads(r["rsvp_yes"] or "[]"))
            rsvp_maybe = len(json.loads(r["rsvp_maybe"] or "[]"))
        except (TypeError, ValueError):
            pass
        text = r["title"] or ""
        if r["description"]:
            text += "\n" + r["description"]
        if r["location"]:
            text += f"\n📍 {r['location']}"
        text += f"\n\n✅ {rsvp_yes} מגיעים · 🤔 {rsvp_maybe} אולי"
        by_day[r["event_date"]].append({
            "emoji": meta["emoji"],
            "css": meta["css"],
            "label": meta["label"],
            "time": (r["event_time"] or "")[:5] or "—",
            "type": "event",
            "text": text,
            "short": _short(r["title"] or ""),
        })

    _cal.setfirstweekday(_cal.SUNDAY)
    raw_weeks = _cal.monthcalendar(year, month_num)
    weeks = []
    days_data: dict[str, list] = {}
    for week in raw_weeks:
        row = []
        for day in week:
            if day == 0:
                row.append(None)
            else:
                iso = _date(year, month_num, day).isoformat()
                items = by_day.get(iso, [])
                days_data[iso] = items
                row.append({
                    "day": day,
                    "iso": iso,
                    "is_today": iso == today.isoformat(),
                    "is_past": iso < today.isoformat(),
                    "chips": items,
                })
        weeks.append(row)

    # Prev/next month
    prev_year, prev_month = (year - 1, 12) if month_num == 1 else (year, month_num - 1)
    next_year, next_month = (year + 1, 1) if month_num == 12 else (year, month_num + 1)

    legend = {k: v for k, v in _CAL_TYPE_STYLE.items() if k != "event"}

    return templates.TemplateResponse(request, name="calendar.html", context={
        "year": year,
        "month_num": month_num,
        "month_name": _CAL_HEB_MONTHS[month_num],
        "heb_dow": _CAL_HEB_DOW,
        "heb_months": _CAL_HEB_MONTHS,
        "weeks": weeks,
        "legend": legend,
        "days_json": json.dumps(days_data, ensure_ascii=False),
        "today_iso": today.isoformat(),
        "prev_url": f"/calendar?month={prev_year:04d}-{prev_month:02d}",
        "next_url": f"/calendar?month={next_year:04d}-{next_month:02d}",
        "today_url": "/calendar",
    })


# ── Polls API (read-only — feeds the Events "from poll" picker) ──

@app.get("/api/polls")
async def list_polls_api(request: Request, db: Database = Depends(get_db)):
    """Recent inline-button polls + per-option vote counts.

    Used by the Events page "Create from poll" tab to let the admin pick a
    closed poll's winning option as the basis for a new event. Each entry
    decodes the original `poll_options` JSON (option labels) and merges it
    with the live vote counts from `poll_votes`.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    polls = await db.list_recent_polls(limit=30)
    out = []
    for p in polls:
        try:
            labels = json.loads(p.get("poll_options") or "[]")
        except (TypeError, ValueError):
            labels = []
        options = []
        for idx, label in enumerate(labels):
            key = str(idx)
            row = p["options_with_counts"].get(key) or {"count": 0, "voters": ""}
            options.append({
                "key": key,
                "label": label,
                "count": row["count"],
                "voters": row.get("voters") or "",
            })
        out.append({
            "schedule_id": p["schedule_id"],
            "message_id": p["message_id"],
            "text": p["text"],
            "sent_at": p["sent_at"],
            "topic_id": p.get("channel_topic_id"),
            "target_group": p.get("target_group"),
            "cover_path": p.get("cover_path"),
            "total_votes": p["total_votes"],
            "options": options,
        })
    return {"polls": out}


# ── Events API ───────────────────────────────────────────

@app.get("/events", response_class=HTMLResponse)
async def events_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    events = await db.get_all_events()
    for e in events:
        e["rsvp_yes_count"] = len(json.loads(e.get("rsvp_yes", "[]")))
        e["rsvp_maybe_count"] = len(json.loads(e.get("rsvp_maybe", "[]")))
    settings = get_settings()
    forum_topics = await db.get_forum_topics()

    return templates.TemplateResponse(request, name="events.html", context={
        "events": events,
        "settings": settings,
        "forum_topics": forum_topics,
    })


def _format_event_message(title: str, description: str | None,
                          event_date: str, event_time: str | None,
                          location: str | None) -> str:
    """Hebrew event card text. Same shape as Telegram's pinned event preview."""
    lines = [f"📅 *{title}*"]
    when_parts = []
    if event_date:
        when_parts.append(event_date)
    if event_time:
        when_parts.append(event_time)
    if when_parts:
        lines.append("🕒 " + " · ".join(when_parts))
    if location:
        lines.append(f"📍 {location}")
    if description:
        lines.append("")
        lines.append(description)
    return "\n".join(lines)


def _event_rsvp_markup():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ מגיע/ה", callback_data="rsvp_yes"),
        InlineKeyboardButton("🤔 אולי", callback_data="rsvp_maybe"),
    ]])


@app.post("/api/events/create")
async def create_event(request: Request, db: Database = Depends(get_db)):
    """Create event, post to Telegram (with optional cover + topic + pin), persist message_id.

    Body fields:
      title, description, event_date, event_time, location  — required basics
      cover_path                                            — optional media path
      auto_pin (bool)                                       — pin in Telegram
      topic_id (int)                                        — forum topic to post into
      target_group ('main' | 'test')                        — chat to post to
      source_poll_message_id, source_poll_option_key        — provenance for from-poll events
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    title = data["title"]
    description = data.get("description", "")
    event_date = data["event_date"]
    event_time = data.get("event_time")
    location = data.get("location")
    cover_path = data.get("cover_path")
    auto_pin = bool(data.get("auto_pin"))
    topic_id = data.get("topic_id")
    target_group = data.get("target_group", "main")
    source_poll_message_id = data.get("source_poll_message_id")
    source_poll_option_key = data.get("source_poll_option_key")

    event_id = await db.create_event(
        title=title, description=description, event_date=event_date,
        event_time=event_time, location=location, created_by=0,
        cover_path=cover_path, auto_pin=auto_pin, topic_id=topic_id,
        source_poll_message_id=source_poll_message_id,
        source_poll_option_key=source_poll_option_key,
    )

    # Post to Telegram. Reuse calendar.send_message_with_optional_cover so the
    # photo+caption layout matches scheduled-message sends.
    from telegram import Bot
    from bot.handlers.calendar import send_message_with_optional_cover

    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        return {"status": "ok", "event_id": event_id, "warning": "BOT_TOKEN missing — event not posted"}

    chat_id = int(os.getenv("TEST_GROUP_ID", "0") if target_group == "test" else os.getenv("GROUP_ID", "0"))
    if not chat_id:
        return {"status": "ok", "event_id": event_id, "warning": f"no chat_id for target_group={target_group}"}

    bot = Bot(bot_token)
    text = _format_event_message(title, description, event_date, event_time, location)

    try:
        sent = await send_message_with_optional_cover(
            bot, chat_id=chat_id, text=text,
            message_thread_id=int(topic_id) if topic_id else None,
            cover_path=cover_path,
        )
        # Attach RSVP buttons (separate edit_reply_markup avoids needing to thread
        # markup through the photo/text helper).
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=sent.message_id,
                reply_markup=_event_rsvp_markup(),
            )
        except Exception as e:
            logger.warning("[events] failed to attach RSVP buttons: %s", e)

        if auto_pin:
            try:
                await bot.pin_chat_message(
                    chat_id=chat_id, message_id=sent.message_id,
                    disable_notification=True,
                )
            except Exception as e:
                logger.warning("[events] failed to pin event %d: %s", event_id, e)

        await db.update_event(event_id, message_id=sent.message_id)
        await db.log_activity("event", f"יצר אירוע: {title[:60]}", target_channel=str(topic_id or "main"))
        return {"status": "ok", "event_id": event_id, "message_id": sent.message_id}
    except Exception as e:
        logger.exception("[events] failed to post event %d to telegram", event_id)
        return {"status": "partial", "event_id": event_id, "error": str(e)}


@app.post("/api/events/{event_id}/delete")
async def delete_event(event_id: int, request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.delete_event(event_id)
    return {"status": "ok"}


# ── Blocked Users ────────────────────────────────────────

@app.get("/blocked", response_class=HTMLResponse)
async def blocked_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    blocked = await db.get_blocked_users()
    return templates.TemplateResponse(request, name="blocked.html", context={
        "blocked": blocked,
    })


@app.post("/api/blocked/add")
async def add_blocked_user(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    user_id = data.get("user_id")
    reason = data.get("reason", "")

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")

    await db.block_user(int(user_id), blocked_by="dashboard", reason=reason)

    # Also ban from Telegram group
    try:
        from telegram import Bot
        bot = Bot(os.getenv("BOT_TOKEN", ""))
        group_id = int(os.getenv("GROUP_ID", "0"))
        if group_id:
            await bot.ban_chat_member(chat_id=group_id, user_id=int(user_id))
    except Exception as e:
        logger.warning("Failed to ban user %s from Telegram: %s", user_id, e)

    return {"status": "ok"}


@app.post("/api/blocked/{user_id}/remove")
async def remove_blocked_user(user_id: int, request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.unblock_user(user_id)

    # Also unban from Telegram group (allows them to rejoin)
    try:
        from telegram import Bot
        bot = Bot(os.getenv("BOT_TOKEN", ""))
        group_id = int(os.getenv("GROUP_ID", "0"))
        if group_id:
            await bot.unban_chat_member(chat_id=group_id, user_id=user_id)
    except Exception as e:
        logger.warning("Failed to unban user %d from Telegram: %s", user_id, e)

    return {"status": "ok"}


# ── Trivia API ───────────────────────────────────────────

@app.get("/trivia", response_class=HTMLResponse)
async def trivia_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    leaders = await db.get_trivia_leaderboard(50)
    questions = []
    try:
        data = load_yaml("trivia.yaml")
        questions = data.get("questions", [])
    except Exception:
        pass
    settings = get_settings()

    return templates.TemplateResponse(request, name="trivia.html", context={
        "leaders": leaders,
        "questions": questions,
        "settings": settings,
    })


@app.get("/puzzles", response_class=HTMLResponse)
async def puzzles_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    puzzles = await db.list_emoji_puzzles()
    sessions = await db.get_recent_emoji_sessions(20)
    stats = await db.get_emoji_round_stats()

    for puzzle in puzzles:
        try:
            alias_list = json.loads(puzzle.get("aliases") or "[]")
        except Exception:
            alias_list = []
        puzzle["aliases_list"] = alias_list
        puzzle["aliases_text"] = ", ".join(alias_list)

    for session in sessions:
        session["group_label"] = _puzzle_group_label(int(session.get("chat_id") or 0))
        session["winner_summary_text"] = _winner_summary_text(session.get("winner_summary"))

    return templates.TemplateResponse(request, name="puzzles.html", context={
        "settings": settings,
        "puzzles": puzzles,
        "sessions": sessions,
        "stats": stats,
    })


@app.post("/api/puzzles/create")
async def create_puzzle(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    emoji_prompt = str(data.get("emoji_prompt") or "").strip()
    answer_he = str(data.get("answer_he") or "").strip()
    answer_en = str(data.get("answer_en") or "").strip()
    if not emoji_prompt or not answer_he or not answer_en:
        raise HTTPException(status_code=400, detail="emoji_prompt, answer_he, answer_en required")

    aliases = _parse_aliases_input(data.get("aliases"))
    puzzle_id = await db.create_emoji_puzzle(
        emoji_prompt=emoji_prompt,
        answer_he=answer_he,
        answer_en=answer_en,
        aliases=json.dumps(aliases, ensure_ascii=False),
        difficulty=int(data.get("difficulty", 2) or 2),
        media_type=str(data.get("media_type") or "movie").strip() or "movie",
    )
    if "enabled" in data:
        await db.update_emoji_puzzle(puzzle_id, enabled=1 if data.get("enabled") else 0)
    return {"status": "ok", "id": puzzle_id}


@app.patch("/api/puzzles/{puzzle_id}")
async def update_puzzle(puzzle_id: int, request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    fields = {}
    for key in ("emoji_prompt", "answer_he", "answer_en", "media_type"):
        if key in data:
            fields[key] = str(data.get(key) or "").strip()
    if "difficulty" in data:
        fields["difficulty"] = int(data.get("difficulty") or 2)
    if "enabled" in data:
        fields["enabled"] = 1 if data.get("enabled") else 0
    if "aliases" in data:
        fields["aliases"] = json.dumps(_parse_aliases_input(data.get("aliases")), ensure_ascii=False)

    if not fields:
        return {"status": "ok"}

    changed = await db.update_emoji_puzzle(puzzle_id, **fields)
    if not changed and not await db.get_emoji_puzzle(puzzle_id):
        raise HTTPException(status_code=404, detail="puzzle not found or no changes")
    return {"status": "ok"}


@app.delete("/api/puzzles/{puzzle_id}")
async def delete_puzzle(puzzle_id: int, request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    changed = await db.delete_emoji_puzzle(puzzle_id)
    if not changed:
        raise HTTPException(status_code=404, detail="puzzle not found")
    return {"status": "ok"}


@app.post("/api/puzzles/schedule")
async def save_puzzle_schedule(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings = _load_settings_file()
    settings.setdefault("features", {})
    settings.setdefault("schedule", {})
    settings.setdefault("gamification", {})

    settings["features"]["emoji_puzzle"] = {
        "enabled": bool(data.get("enabled", False)),
        "groups": [str(g) for g in data.get("groups", []) if str(g) in ("main", "test")],
    }
    settings["schedule"]["emoji_puzzle"] = {
        "days": [int(d) for d in data.get("days", [])],
        "time": str(data.get("time") or "22:00").strip() or "22:00",
        "puzzle_count": int(data.get("puzzle_count") or 5),
        "interval_minutes": int(data.get("interval_minutes") or 6),
        "intro_offset_seconds": int(data.get("intro_offset_seconds") or 60),
        "wrap_offset_seconds": int(data.get("wrap_offset_seconds") or 420),
    }
    settings["gamification"]["emoji_puzzle_winner"] = int(data.get("emoji_puzzle_winner") or 5)

    _save_settings_file(settings)
    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


@app.post("/api/puzzles/run-now")
async def run_puzzles_now(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    target = str(data.get("target") or "test").strip()
    from bot.handlers.emoji_puzzle import resolve_emoji_target, start_emoji_night

    chat_id, thread_id = resolve_emoji_target(target)
    if not chat_id:
        raise HTTPException(status_code=400, detail=f"Unknown target '{target}'")

    ctx = type("EmojiCtx", (), {})()
    ctx.bot_data = {"db": db}
    session_id = await start_emoji_night(ctx, chat_id, thread_id, force=True)
    if not session_id:
        raise HTTPException(status_code=409, detail="Could not start session")
    return {"status": "ok", "session_id": session_id, "target": target}


@app.post("/api/trivia/questions")
async def save_trivia_questions(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    path = CONFIG_DIR / "trivia.yaml"

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump({"questions": data["questions"]}, f, allow_unicode=True, default_flow_style=False)

    return {"status": "ok"}


@app.post("/api/trivia/reset")
async def reset_trivia(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.reset_trivia_scores()
    return {"status": "ok"}


TRIVIA_ROUND_TRIGGER = Path(__file__).parent.parent / "data" / "trivia_round_trigger.json"
TRIVIA_ROUND_STOP = Path(__file__).parent.parent / "data" / "trivia_round_stop"


@app.post("/api/trivia/round/start")
async def start_trivia_round(request: Request):
    """Write a trigger file that the bot's trigger_watcher picks up within ~10s."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    target = (data.get("target") or "test").lower()
    pre_roll_s = int(data.get("pre_roll_s", 30))
    pre_roll_s = max(5, min(3600, pre_roll_s))

    main_group = int(os.getenv("GROUP_ID", "0"))
    test_group = int(os.getenv("TEST_GROUP_ID", "0"))
    chat_id = test_group if target == "test" else main_group
    if not chat_id:
        raise HTTPException(status_code=400, detail=f"No chat id for target '{target}'")

    payload = {"chat_id": chat_id, "pre_roll_s": pre_roll_s, "thread_id": None}
    TRIVIA_ROUND_TRIGGER.parent.mkdir(parents=True, exist_ok=True)
    TRIVIA_ROUND_TRIGGER.write_text(json.dumps(payload), encoding="utf-8")
    logger.info("trivia_round: trigger written target=%s pre_roll=%ss chat=%s", target, pre_roll_s, chat_id)
    return {"status": "ok", "chat_id": chat_id, "pre_roll_s": pre_roll_s}


@app.post("/api/trivia/round/stop")
async def stop_trivia_round(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    TRIVIA_ROUND_STOP.parent.mkdir(parents=True, exist_ok=True)
    TRIVIA_ROUND_STOP.write_text("stop")
    return {"status": "ok"}


# ── Generate API ─────────────────────────────────────────

COMMUNITY_CONTEXT = """קהילת "אלהוריים וזה" — קהילת צ'ילדפרי (ללא ילדים מבחירה) בטלגרם.
הקהילה היא חמה, תומכת ומהנה. השפה עברית. התוכן רלוונטי לאורח חיים של מבוגרים, צמיחה אישית, וחיזוק הקשר הקהילתי."""


def build_generation_prompt(field: str, mode: str, existing: str, category: str, instructions: str = "") -> str:
    # Single-item rewrite mode — used by the weekplan modal
    if mode == "rewrite":
        type_he = {"morning": "הודעת בוקר", "evening": "הודעת ערב", "discussion": "שאלה לדיון"}.get(field, "הודעה")
        base = f"""שכתב את ה{type_he} הבאה בעברית. שמור על הרעיון המרכזי אבל הפוך אותה לטובה יותר — יותר מעניינת, טבעית ומזמינה. פלט: רק ההודעה החדשה, בלי הסברים, בלי מרכאות, בלי מספור.

ההודעה המקורית:
{existing}"""
        if instructions:
            base += f"\n\nהוראות נוספות: {instructions}"
        return base

    # Single-item fresh generate — weekplan modal, one prompt only
    if mode == "single":
        if field == "morning":
            base = f"""צור הודעת בוקר אחת מעוררת השראה בעברית עבור {COMMUNITY_CONTEXT}

ההודעה צריכה להיות שורה או שתיים, לפתוח באמוג'י רלוונטי, לעודד את חברי הקהילה לבוקר טוב.
הטון: חם, מעודד, קליל.
פלט: רק ההודעה, בלי מספור, בלי מרכאות, בלי הסברים."""
        elif field == "evening":
            base = f"""צור הודעת ערב אחת רפלקטיבית בעברית עבור {COMMUNITY_CONTEXT}

ההודעה צריכה להיות שורה או שתיים, לפתוח באמוג'י רלוונטי, לעודד חשיבה על היום שעבר.
הטון: רגוע, מחבק, מעודד רפלקציה.
פלט: רק ההודעה, בלי מספור, בלי מרכאות, בלי הסברים."""
        elif field == "discussion":
            base = f"""צור שאלה אחת לדיון בקטגוריה "{category}" בעברית עבור {COMMUNITY_CONTEXT}

השאלה צריכה להיות שורה אחת, מעוררת שיחה ומעניינת.
הטון: סקרני, פתוח, מזמין.
פלט: רק השאלה, בלי מספור, בלי מרכאות, בלי הסברים."""
        else:
            base = f"צור תוכן בעברית עבור {COMMUNITY_CONTEXT}"
        if instructions:
            base += f"\n\nהוראות נוספות: {instructions}"
        return base

    count = "5-8" if mode == "append" else "15-20"

    if field == "morning":
        base = f"""צור {count} הודעות בוקר מעוררות השראה בעברית עבור {COMMUNITY_CONTEXT}

כל הודעה צריכה להיות שורה אחת, לפתוח באמוג'י רלוונטי, ולעודד את חברי הקהילה לבוקר טוב.
הטון: חם, מעודד, קליל. אל תחזור על אמוג'ים.
פלט: רק את ההודעות, שורה אחת לכל הודעה, בלי מספור ובלי הסברים."""

    elif field == "evening":
        base = f"""צור {count} הודעות ערב רפלקטיביות בעברית עבור {COMMUNITY_CONTEXT}

כל הודעה צריכה להיות שורה אחת, לפתוח באמוג'י רלוונטי, ולעודד חשיבה על היום שעבר.
הטון: רגוע, מחבק, מעודד רפלקציה. אל תחזור על אמוג'ים.
פלט: רק את ההודעות, שורה אחת לכל הודעה, בלי מספור ובלי הסברים."""

    elif field == "discussion":
        base = f"""צור {count} שאלות לדיון בקטגוריה "{category}" בעברית עבור {COMMUNITY_CONTEXT}

כל שאלה צריכה להיות שורה אחת, מעוררת שיחה ומעניינת.
הטון: סקרני, פתוח, מזמין. שאלות שיגרמו לאנשים לשתף ולענות.
פלט: רק את השאלות, שורה אחת לכל שאלה, בלי מספור ובלי הסברים."""

    elif field == "trivia":
        base = f"""צור {count} שאלות טריוויה בעברית עבור {COMMUNITY_CONTEXT}

כל שאלה צריכה להיות בפורמט הבא (4 שורות לכל שאלה, מופרדות בשורה ריקה):
שאלה: [טקסט השאלה]
תשובות: [תשובה1] | [תשובה2] | [תשובה3] | [תשובה4]
נכונה: [מספר התשובה הנכונה 0-3]
קטגוריה: [קטגוריה]

נושאים מגוונים: תרבות, מדע, היסטוריה, בידור, גאוגרפיה, אוכל.
פלט: רק את השאלות בפורמט שצוין, בלי הסברים נוספים."""

    else:
        base = f"צור תוכן בעברית עבור {COMMUNITY_CONTEXT}"

    if mode == "append" and existing:
        base += f"\n\nהנה התוכן הקיים (אל תחזור עליו, צור תוכן חדש ושונה):\n{existing}"

    return base


async def _generate_via_cli(prompt: str) -> str:
    """Try generating content via Claude Code CLI."""
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, "--model", "sonnet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"CLI error: {stderr.decode()[:200]}")
    return stdout.decode().strip()


async def _generate_via_api(prompt: str) -> str:
    """Fallback: generate content via Anthropic API."""
    import httpx

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set — cannot fall back to API")

    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-4-20250514",
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"].strip()


@app.post("/api/generate")
async def generate_content(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    field = data["field"]       # "morning", "evening", "discussion", "trivia"
    mode = data["mode"]         # "append", "replace", "single", or "rewrite"
    existing = data.get("existing", "")
    category = data.get("category", "")
    instructions = (data.get("instructions") or "").strip()

    prompt = build_generation_prompt(field, mode, existing, category, instructions)

    # Try Claude Code CLI first, fall back to Anthropic API
    try:
        content = await _generate_via_cli(prompt)
    except Exception:
        try:
            content = await _generate_via_api(prompt)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Generation failed: {e}")

    return {"content": content}


# ── Analytics API ────────────────────────────────────────

@app.get("/api/analytics")
async def get_analytics(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    import datetime

    # Activity per day (last 14 days)
    fourteen_days_ago = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
    async with db._db.execute(
        """SELECT DATE(timestamp) as day, COUNT(*) as cnt
           FROM activity_log WHERE timestamp >= ?
           GROUP BY DATE(timestamp) ORDER BY day""",
        (fourteen_days_ago,)
    ) as cursor:
        daily_activity = [dict(row) for row in await cursor.fetchall()]

    # Activity by type (all time)
    async with db._db.execute(
        "SELECT action_type, COUNT(*) as cnt FROM activity_log GROUP BY action_type ORDER BY cnt DESC"
    ) as cursor:
        by_type = [dict(row) for row in await cursor.fetchall()]

    # Member join dates (last 30 days)
    thirty_days_ago = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()
    async with db._db.execute(
        """SELECT DATE(joined_at) as day, COUNT(*) as cnt
           FROM members WHERE joined_at >= ?
           GROUP BY DATE(joined_at) ORDER BY day""",
        (thirty_days_ago,)
    ) as cursor:
        member_growth = [dict(row) for row in await cursor.fetchall()]

    # Spam per day (last 14 days)
    async with db._db.execute(
        """SELECT DATE(timestamp) as day, COUNT(*) as cnt
           FROM spam_log WHERE timestamp >= ?
           GROUP BY DATE(timestamp) ORDER BY day""",
        (fourteen_days_ago,)
    ) as cursor:
        spam_daily = [dict(row) for row in await cursor.fetchall()]

    return {
        "daily_activity": daily_activity,
        "by_type": by_type,
        "member_growth": member_growth,
        "spam_daily": spam_daily,
    }


# ── Activity Log ─────────────────────────────────────────

@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    log = await db.get_activity_log(200)
    settings = get_settings()
    return templates.TemplateResponse(request, name="activity.html", context={"log": log, "settings": settings})


@app.post("/api/settings/gamification")
async def update_gamification(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    settings["gamification"] = data

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


@app.post("/api/settings/features")
async def update_features(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    settings["features"] = data

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)

    # Feature flags gate materialization — reload purges future auto rows for
    # disabled features and refills for newly-enabled ones.
    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded}


# ── Weekly Plan Page ────────────────────────────────────

# Feature-enabled check uses the shared bot helper so dashboard and bot can't
# disagree about whether a feature is on.
from bot.utils.config import is_feature_enabled as _is_feature_enabled


def _is_feature_enabled_simple(features: dict, key: str) -> bool:
    """Compat shim that delegates to bot.utils.config.is_feature_enabled.

    `features` is ignored — the shared helper re-reads settings.yaml itself
    so nothing can drift from the file.
    """
    return _is_feature_enabled(key)


@app.get("/api/weekplan/discussion-sample")
async def get_discussion_sample(request: Request, category: str):
    """Return the first question from a discussion category pool."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    try:
        pool = load_yaml("discussions.yaml") or {}
    except Exception as e:
        logger.error("[sample] failed to load discussions.yaml: %s", e)
        pool = {}

    questions = pool.get(category, [])
    logger.info("[sample] category=%s → %d questions, first=%r", category, len(questions), (questions[0][:60] if questions else None))
    if not questions:
        return {"text": "", "idx": -1}
    return {"text": questions[0], "idx": 0}


@app.post("/api/weekplan/save-day")
async def save_weekplan_day(request: Request, db: Database = Depends(get_db)):
    """Save or update a committed scheduled_messages row for a weekplan day slot.

    Body: {date, time, type, text, channel_topic_id?, scheduled_id?}
    - If scheduled_id is provided, updates that row.
    - Otherwise, creates a new scheduled_messages row.
    Returns: {status, id}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    date_str = (data.get("date") or "").strip()
    time_str = (data.get("time") or "").strip()
    mtype = (data.get("type") or "").strip()
    text = (data.get("text") or "").strip()
    channel_topic_id = data.get("channel_topic_id")
    scheduled_id = data.get("scheduled_id")

    if not text:
        raise HTTPException(status_code=400, detail="Missing text")
    if not date_str or not time_str:
        raise HTTPException(status_code=400, detail="Missing date or time")
    if mtype not in ("morning", "evening", "discussion"):
        raise HTTPException(status_code=400, detail=f"Invalid type: {mtype}")

    # Normalize topic_id to int or None
    if channel_topic_id in (None, "", "0", 0):
        channel_topic_id = None
    else:
        try:
            channel_topic_id = int(channel_topic_id)
        except (ValueError, TypeError):
            channel_topic_id = None

    logger.info("[weekplan.save-day] date=%s time=%s type=%s topic=%s scheduled_id=%s text=%r",
                date_str, time_str, mtype, channel_topic_id, scheduled_id, text[:60])

    if scheduled_id:
        # Update existing row
        try:
            scheduled_id_int = int(scheduled_id)
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid scheduled_id")
        await db.update_scheduled_message(
            scheduled_id_int,
            text=text,
            channel_topic_id=channel_topic_id,
            scheduled_date=date_str,
            scheduled_time=time_str,
            message_type=mtype,
        )
        logger.info("[weekplan.save-day] updated scheduled_messages id=%d", scheduled_id_int)
        return {"status": "ok", "id": scheduled_id_int, "action": "updated"}
    else:
        new_id = await db.create_scheduled_message(
            text=text,
            message_type=mtype,
            channel_topic_id=channel_topic_id,
            target_group="main",
            scheduled_date=date_str,
            scheduled_time=time_str,
            created_by="weekplan",
        )
        logger.info("[weekplan.save-day] created scheduled_messages id=%d", new_id)
        return {"status": "ok", "id": new_id, "action": "created"}


@app.post("/api/weekplan/ai-fill")
async def ai_fill_weekplan(request: Request, db: Database = Depends(get_db)):
    """Bulk-generate content via Claude for all non-committed slots of a given type in a week.

    Body: {week_offset, type}
    type in {morning, evening, discussion}
    Returns: {created, skipped, errors}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    week_offset = int(data.get("week_offset", 0))
    mtype = (data.get("type") or "").strip()
    if mtype not in ("morning", "evening", "discussion"):
        raise HTTPException(status_code=400, detail=f"Invalid type: {mtype}")

    settings = get_settings()
    schedule = settings.get("schedule", {})
    topic_ids = settings.get("topics", {}).get("discussions", {})
    goals_topic = settings.get("topics", {}).get("goals")

    # Compute week's Sunday
    from datetime import date, timedelta
    today = date.today()
    python_weekday = today.weekday()
    days_since_sunday = (python_weekday + 1) % 7
    current_sunday = today - timedelta(days=days_since_sunday)
    sunday = current_sunday + timedelta(weeks=week_offset)

    # Build committed index for this week
    raw_committed = await db.get_scheduled_messages(
        sunday.isoformat(), (sunday + timedelta(days=6)).isoformat()
    )
    committed_keys = set()
    for row in raw_committed:
        if row.get("status") == "cancelled":
            continue
        committed_keys.add((
            row.get("scheduled_date", ""),
            (row.get("scheduled_time") or "")[:5],
            row.get("message_type", ""),
        ))

    # Determine schedule info for this type
    if mtype == "morning":
        sched_key = "morning_prompt"
        times = [schedule.get(sched_key, {}).get("time", "09:00")]
        days_list = schedule.get(sched_key, {}).get("days", [])
    elif mtype == "evening":
        sched_key = "evening_prompt"
        times = [schedule.get(sched_key, {}).get("time", "21:00")]
        days_list = schedule.get(sched_key, {}).get("days", [])
    else:  # discussion
        sched_key = "discussion_prompt"
        times = schedule.get(sched_key, {}).get("times", ["18:00"])
        days_list = schedule.get(sched_key, {}).get("days", [])

    # Load pools for discussion category rotation
    try:
        discussions_pool = load_yaml("discussions.yaml") or {}
    except Exception:
        discussions_pool = {}
    active_categories = [c for c in discussions_pool if c in topic_ids and topic_ids[c]]

    created = 0
    skipped = 0
    errors: list[str] = []

    for i in range(7):
        if i not in days_list:
            continue
        day_date = sunday + timedelta(days=i)
        for t in times:
            key = (day_date.isoformat(), t, mtype)
            if key in committed_keys:
                skipped += 1
                continue

            # Generate content
            if mtype == "morning":
                prompt = build_generation_prompt("morning", "single", "", "")
                topic = goals_topic
            elif mtype == "evening":
                prompt = build_generation_prompt("evening", "single", "", "")
                topic = goals_topic
            else:  # discussion
                if not active_categories:
                    errors.append(f"no active discussion categories for day {i}")
                    continue
                cat = active_categories[i % len(active_categories)]
                prompt = build_generation_prompt("discussion", "single", "", cat)
                topic = topic_ids.get(cat)

            try:
                content = await _generate_via_cli(prompt)
            except Exception:
                try:
                    content = await _generate_via_api(prompt)
                except Exception as e:
                    errors.append(f"day {i}: generation failed: {e}")
                    continue

            # Clean up: take first non-empty line, strip surrounding quotes
            content = content.strip().replace('"', '').replace("'", "")
            lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
            content = lines[0] if lines else content

            try:
                new_id = await db.create_scheduled_message(
                    text=content,
                    message_type=mtype,
                    channel_topic_id=int(topic) if topic else None,
                    target_group="main",
                    scheduled_date=day_date.isoformat(),
                    scheduled_time=t,
                    created_by="ai-fill",
                )
                created += 1
                logger.info("[weekplan.ai-fill] created %s id=%d for %s %s: %r",
                            mtype, new_id, day_date.isoformat(), t, content[:60])
            except Exception as e:
                errors.append(f"day {i}: db insert failed: {e}")

    return {"created": created, "skipped": skipped, "errors": errors}


@app.post("/api/generate-content")
async def generate_content(request: Request):
    """Generate a single message via Claude for the create-drawer textarea.

    Body: {type: morning|evening|discussion, category?: str, existing?: str}
    Returns: {text: str}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    mtype = (data.get("type") or "").strip()
    category = (data.get("category") or "").strip()
    existing = (data.get("existing") or "").strip()

    if mtype not in ("morning", "evening", "discussion"):
        raise HTTPException(status_code=400, detail=f"Invalid type: {mtype}")
    if mtype == "discussion" and not category:
        raise HTTPException(status_code=400, detail="Discussion requires category")

    mode = "rewrite" if existing else "single"
    prompt = build_generation_prompt(mtype, mode, existing, category)

    try:
        content = await _generate_via_cli(prompt)
    except Exception:
        content = await _generate_via_api(prompt)

    content = content.strip().replace('"', '').replace("'", "")
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    text = lines[0] if lines else content

    logger.info("[generate-content] type=%s cat=%s mode=%s -> %r", mtype, category, mode, text[:60])
    return {"text": text}


@app.post("/api/weekplan/update-prompt")
async def update_weekplan_prompt(request: Request):
    """Update a single prompt text in its YAML pool from the weekplan modal."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    pool = (data.get("pool") or "").strip()
    idx = data.get("idx", -1)
    new_text = (data.get("text") or "").strip()

    logger.info("[weekplan.update] received: pool=%r idx=%r text=%r", pool, idx, new_text[:80])

    if not new_text or not isinstance(idx, int) or idx < 0:
        logger.warning("[weekplan.update] rejected: empty text or bad idx (idx=%r text_len=%d)", idx, len(new_text))
        raise HTTPException(status_code=400, detail="Missing text or invalid index")

    if pool in ("morning", "evening"):
        path = CONFIG_DIR / "prompts.yaml"
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f) or {}
        pool_list = content.get(pool, [])
        logger.info("[weekplan.update] pool=%s has %d entries, replacing idx=%d", pool, len(pool_list), idx)
        if idx >= len(pool_list):
            logger.warning("[weekplan.update] idx %d out of range for pool %s (len=%d)", idx, pool, len(pool_list))
            raise HTTPException(status_code=400, detail="Index out of range")
        old_text = pool_list[idx]
        pool_list[idx] = new_text
        content[pool] = pool_list
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(content, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info("[weekplan.update] saved %s[%d]: %r -> %r", pool, idx, old_text[:60], new_text[:60])
        return {"status": "ok"}

    if pool.startswith("discussion:"):
        category = pool.split(":", 1)[1]
        path = CONFIG_DIR / "discussions.yaml"
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f) or {}
        cat_list = content.get(category, [])
        logger.info("[weekplan.update] category=%s has %d entries, replacing idx=%d", category, len(cat_list), idx)
        if idx >= len(cat_list):
            logger.warning("[weekplan.update] idx %d out of range for category %s (len=%d)", idx, category, len(cat_list))
            raise HTTPException(status_code=400, detail="Index out of range")
        old_text = cat_list[idx]
        cat_list[idx] = new_text
        content[category] = cat_list
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(content, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info("[weekplan.update] saved discussion:%s[%d]: %r -> %r", category, idx, old_text[:60], new_text[:60])
        return {"status": "ok"}

    logger.warning("[weekplan.update] unknown pool: %r", pool)
    raise HTTPException(status_code=400, detail="Unknown pool")


@app.get("/weekplan", response_class=HTMLResponse)
async def weekplan_page(request: Request, week_offset: int = 0, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    schedule = settings.get("schedule", {})
    features = settings.get("features", {})

    # Load prompt pools for content previews
    try:
        prompts_pool = load_yaml("prompts.yaml")
    except Exception:
        prompts_pool = {}

    try:
        discussions_pool = load_yaml("discussions.yaml")
    except Exception:
        discussions_pool = {}

    from bot.handlers.discussions import CATEGORY_NAMES

    morning_queue = list(prompts_pool.get("morning", []))
    evening_queue = list(prompts_pool.get("evening", []))

    # Discussion categories: only those present in both YAML and settings
    topic_ids = settings.get("topics", {}).get("discussions", {})
    active_categories = [c for c in discussions_pool if c in topic_ids and topic_ids[c]]
    logger.info("[weekplan.render] week_offset=%d active_categories=%s", week_offset, active_categories)
    logger.info("[weekplan.render] discussions_pool keys (in yaml order)=%s", list(discussions_pool.keys()))
    # Show the actual first question for each active category (for sanity-checking saves)
    for _cat in active_categories:
        _qs = discussions_pool.get(_cat, [])
        logger.info("[weekplan.render]   %s[0]=%r (pool has %d entries)", _cat, (_qs[0][:70] if _qs else None), len(_qs))

    # Track prompt indices for rotating previews across the week
    morning_idx = 0
    evening_idx = 0
    discussion_idx = 0
    day_to_category_map = {}

    def _truncate(text: str, limit: int = 60) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    # Build week days (Sunday-Saturday for current week)
    from datetime import date, timedelta
    today = date.today()
    # Python weekday: Mon=0..Sun=6 → Hebrew: Sun=0..Sat=6
    python_weekday = today.weekday()  # 0=Mon
    days_since_sunday = (python_weekday + 1) % 7
    current_sunday = today - timedelta(days=days_since_sunday)
    sunday = current_sunday + timedelta(weeks=week_offset)

    hebrew_day_names = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]

    # Build committed-row index: (date_iso, "HH:MM", type) -> row
    # Only status='scheduled' rows count as committed; cancelled/failed/sent fall back to preview
    committed_index: dict[tuple[str, str, str], dict] = {}
    try:
        _raw_committed = await db.get_scheduled_messages(
            sunday.isoformat(), (sunday + timedelta(days=6)).isoformat()
        )
        for row in _raw_committed:
            if row.get("status") == "cancelled":
                continue
            mtype = row.get("message_type", "")
            if mtype not in ("morning", "evening", "discussion"):
                continue
            dkey = row.get("scheduled_date", "")
            tkey = (row.get("scheduled_time") or "")[:5]
            committed_index[(dkey, tkey, mtype)] = row
        logger.info("[weekplan.render] committed_index has %d entries: %s",
                    len(committed_index), list(committed_index.keys()))
    except Exception as e:
        logger.warning("[weekplan.render] failed to load committed_index: %s", e)

    week_days = []
    for i in range(7):
        day_date = sunday + timedelta(days=i)
        activities = []

        # Check each schedule item
        if i in schedule.get("morning_prompt", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "morning_prompt")
            m_time = schedule["morning_prompt"].get("time", "09:00")
            goals_topic = settings.get("topics", {}).get("goals")
            committed_row = committed_index.get((day_date.isoformat(), m_time, "morning"))
            if committed_row:
                full_text = committed_row.get("text", "")
                preview = _truncate(full_text)
                committed_topic = committed_row.get("channel_topic_id") or goals_topic or ""
                activities.append({
                    "time": m_time,
                    "type": "morning", "label": "בוקר",
                    "desc": preview or "הודעת בוקר — יום יום",
                    "full_text": full_text,
                    "pool": "morning",
                    "pool_idx": -1,
                    "topic_id": committed_topic,
                    "channel": "", "enabled": enabled,
                    "committed": True,
                    "scheduled_id": committed_row.get("id"),
                })
            else:
                preview = ""
                full_text = ""
                used_idx = -1
                if morning_queue and morning_idx < len(morning_queue):
                    full_text = morning_queue[morning_idx]
                    preview = _truncate(full_text)
                    used_idx = morning_idx
                    morning_idx += 1
                activities.append({
                    "time": m_time,
                    "type": "morning", "label": "בוקר",
                    "desc": preview or "הודעת בוקר — יום יום",
                    "full_text": full_text,
                    "pool": "morning",
                    "pool_idx": used_idx,
                    "topic_id": goals_topic if goals_topic else "",
                    "channel": "", "enabled": enabled,
                    "committed": False,
                    "scheduled_id": None,
                })

        if i in schedule.get("discussion_prompt", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "discussions")
            times = schedule["discussion_prompt"].get("times", ["18:00"])
            for t in times:
                committed_row = committed_index.get((day_date.isoformat(), t, "discussion"))
                if committed_row:
                    full_text = committed_row.get("text", "")
                    preview = _truncate(full_text)
                    committed_topic = committed_row.get("channel_topic_id") or ""
                    # Reverse-lookup category name from topic_id
                    cat_key = ""
                    for _ck, _tid in topic_ids.items():
                        if _tid == committed_topic:
                            cat_key = _ck
                            break
                    channel_hint = CATEGORY_NAMES.get(cat_key, cat_key) if cat_key else ""
                    activities.append({
                        "time": t, "type": "discussion", "label": "דיון",
                        "desc": preview or "שאלה לדיון",
                        "full_text": full_text,
                        "pool": f"discussion:{cat_key}" if cat_key else "",
                        "pool_idx": -1,
                        "topic_id": committed_topic,
                        "category": cat_key,
                        "channel": channel_hint, "enabled": enabled,
                        "committed": True,
                        "scheduled_id": committed_row.get("id"),
                    })
                else:
                    preview = ""
                    full_text = ""
                    channel_hint = ""
                    disc_pool = ""
                    disc_idx = -1
                    disc_topic_id = ""
                    disc_category = ""
                    if active_categories and discussions_pool:
                        cat = active_categories[discussion_idx % len(active_categories)]
                        cat_questions = discussions_pool.get(cat, [])
                        if cat_questions:
                            q_idx = (discussion_idx // len(active_categories)) % len(cat_questions)
                            full_text = cat_questions[q_idx]
                            preview = _truncate(full_text)
                            disc_pool = f"discussion:{cat}"
                            disc_idx = q_idx
                            logger.info("[weekplan.render]   day %d (%s) → %s[%d] = %r", i, hebrew_day_names[i], cat, q_idx, full_text[:60])
                            day_to_category_map[i] = f"{cat}[{q_idx}]"
                        channel_hint = CATEGORY_NAMES.get(cat, cat)
                        disc_topic_id = topic_ids.get(cat) or ""
                        disc_category = cat
                        discussion_idx += 1
                    activities.append({
                        "time": t, "type": "discussion", "label": "דיון",
                        "desc": preview or "שאלה לדיון",
                        "full_text": full_text,
                        "pool": disc_pool,
                        "pool_idx": disc_idx,
                        "topic_id": disc_topic_id,
                        "category": disc_category,
                        "channel": channel_hint, "enabled": enabled,
                        "committed": False,
                        "scheduled_id": None,
                    })

        if i in schedule.get("evening_prompt", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "evening_prompt")
            e_time = schedule["evening_prompt"].get("time", "21:00")
            goals_topic = settings.get("topics", {}).get("goals")
            committed_row = committed_index.get((day_date.isoformat(), e_time, "evening"))
            if committed_row:
                full_text = committed_row.get("text", "")
                preview = _truncate(full_text)
                committed_topic = committed_row.get("channel_topic_id") or goals_topic or ""
                activities.append({
                    "time": e_time,
                    "type": "evening", "label": "ערב",
                    "desc": preview or "הודעת ערב — יום יום",
                    "full_text": full_text,
                    "pool": "evening",
                    "pool_idx": -1,
                    "topic_id": committed_topic,
                    "channel": "", "enabled": enabled,
                    "committed": True,
                    "scheduled_id": committed_row.get("id"),
                })
            else:
                preview = ""
                full_text = ""
                used_idx = -1
                if evening_queue and evening_idx < len(evening_queue):
                    full_text = evening_queue[evening_idx]
                    preview = _truncate(full_text)
                    used_idx = evening_idx
                    evening_idx += 1
                activities.append({
                    "time": e_time,
                    "type": "evening", "label": "ערב",
                    "desc": preview or "הודעת ערב — יום יום",
                    "full_text": full_text,
                    "pool": "evening",
                    "pool_idx": used_idx,
                    "topic_id": goals_topic if goals_topic else "",
                    "channel": "", "enabled": enabled,
                    "committed": False,
                    "scheduled_id": None,
                })

        if i in schedule.get("weekly_leaderboard", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "levels")
            activities.append({
                "time": schedule["weekly_leaderboard"].get("time", "18:00"),
                "type": "leaderboard", "label": "לידרבורד",
                "desc": "טבלת מובילים שבועית",
                "full_text": "", "pool": "", "pool_idx": -1,
                "channel": "", "enabled": enabled
            })

        if i in schedule.get("weekly_roundup", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "roundup")
            activities.append({
                "time": schedule["weekly_roundup"].get("time", "18:00"),
                "type": "roundup", "label": "סיכום",
                "desc": "סיכום שבועי",
                "full_text": "", "pool": "", "pool_idx": -1,
                "channel": "", "enabled": enabled
            })

        # Sort by time
        activities.sort(key=lambda a: a["time"])

        week_days.append({
            "date": day_date,
            "day_name": hebrew_day_names[i],
            "day_num": i,
            "is_today": day_date == today,
            "is_weekend": i >= 5,
            "activities": activities
        })

    # Get calendar events (scheduled messages) for this week
    try:
        calendar_events = await db.get_scheduled_messages(
            sunday.isoformat(), (sunday + timedelta(days=6)).isoformat()
        )
    except Exception:
        calendar_events = []

    # Add NON-schedule calendar rows (custom one-offs) to the right days.
    # Skip cancelled rows entirely, and skip morning/evening/discussion rows
    # because those are already rendered as committed activities in the main
    # schedule loop — rendering them again here would produce ghost copies.
    scheduled_slot_types = {"morning", "evening", "discussion"}
    for evt in calendar_events:
        try:
            if evt.get("status") == "cancelled":
                continue
            if evt.get("message_type") in scheduled_slot_types:
                continue
            evt_date_str = evt.get("scheduled_date", "")
            evt_time_str = evt.get("scheduled_time", "00:00")
            from datetime import date as date_cls
            parts = evt_date_str.split("-")
            evt_date = date_cls(int(parts[0]), int(parts[1]), int(parts[2]))
            day_idx = (evt_date - sunday).days
            if 0 <= day_idx < 7:
                week_days[day_idx]["activities"].append({
                    "time": evt_time_str or "00:00",
                    "type": "calendar",
                    "label": evt.get("message_type", "הודעה"),
                    "desc": (evt.get("text", "") or "")[:60],
                    "full_text": "", "pool": "", "pool_idx": -1,
                    "channel": "", "enabled": True,
                    "status": evt.get("status", "")
                })
                week_days[day_idx]["activities"].sort(key=lambda a: a["time"])
        except (ValueError, TypeError, IndexError):
            pass

    # Build discussion channel list for the modal dropdown
    discussion_channels = []
    for cat, tid in topic_ids.items():
        if tid:
            discussion_channels.append({
                "key": cat,
                "name": CATEGORY_NAMES.get(cat, cat),
                "topic_id": tid,
            })

    logger.info("[weekplan.render] day→discussion map for week starting %s: %s", sunday, day_to_category_map)

    return templates.TemplateResponse(request, name="weekplan.html", context={
        "settings": settings,
        "week_days": week_days,
        "features": features,
        "week_offset": week_offset,
        "discussion_channels": discussion_channels,
    })


# ── Health Page ─────────────────────────────────────────

@app.get("/health", response_class=HTMLResponse)
async def health_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()

    # Get recent activity counts
    import datetime
    today = datetime.date.today().isoformat()

    # Activity stats
    async with db._db.execute(
        "SELECT action_type, COUNT(*) as cnt FROM activity_log WHERE timestamp >= ? GROUP BY action_type",
        (today,)
    ) as cursor:
        today_activity = {row["action_type"]: row["cnt"] for row in await cursor.fetchall()}

    async with db._db.execute(
        "SELECT COUNT(*) as cnt FROM activity_log"
    ) as cursor:
        total_activity = (await cursor.fetchone())["cnt"]

    async with db._db.execute(
        "SELECT COUNT(*) as cnt FROM members"
    ) as cursor:
        member_count = (await cursor.fetchone())["cnt"]

    async with db._db.execute(
        "SELECT COUNT(*) as cnt FROM spam_log WHERE timestamp >= ?", (today,)
    ) as cursor:
        spam_today = (await cursor.fetchone())["cnt"]

    async with db._db.execute(
        "SELECT action_type, description, timestamp FROM activity_log ORDER BY timestamp DESC LIMIT 10"
    ) as cursor:
        recent_log = [dict(row) for row in await cursor.fetchall()]

    # Get forum topics count
    forum_topics = await db.get_forum_topics()

    # Version check — compare running bot version vs current code
    import subprocess
    version_file = Path(__file__).parent.parent / "data" / "bot.version"
    bot_version = None
    bot_start_time = None
    code_version = None
    needs_restart = False

    if version_file.exists():
        lines = version_file.read_text().strip().split("\n")
        bot_version = lines[0] if lines else None
        bot_start_time = lines[1] if len(lines) > 1 else None

    try:
        code_version = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(Path(__file__).parent.parent),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        code_version = None

    if bot_version and code_version and bot_version != code_version:
        needs_restart = True

    return templates.TemplateResponse(request, name="health.html", context={
        "settings": settings,
        "today_activity": today_activity,
        "total_activity": total_activity,
        "member_count": member_count,
        "spam_today": spam_today,
        "recent_log": recent_log,
        "forum_topics": forum_topics,
        "today_weekday": datetime.date.today().weekday(),
        "bot_version": bot_version,
        "code_version": code_version,
        "bot_start_time": bot_start_time,
        "needs_restart": needs_restart,
    })


# ── Review Page ──────────────────────────────────────────

PENDING_REVIEWS_PATH = Path(__file__).parent.parent / "data" / "pending_reviews.json"
EMOJI_PUZZLE_REVIEW_FLAG = Path(__file__).parent.parent / "data" / ".emoji_puzzle_reviews_seeded"


def _emoji_puzzle_review_items():
    try:
        data = load_yaml("emoji_puzzles.yaml") or {}
    except FileNotFoundError:
        return []

    puzzles = data.get("puzzles", []) or []
    total = len(puzzles)
    items = []
    for idx, puzzle in enumerate(puzzles, start=1):
        aliases = puzzle.get("aliases", []) or []
        aliases_text = " | ".join(aliases[:4]) if aliases else "—"
        items.append({
            "id": f"emoji-puzzle-seed-{idx:02d}",
            "title": f"🎬 Emoji Night — seed {idx:02d}/{total}",
            "channel": "סרטים סדרות וכו (54)",
            "when": "",
            "preview": (
                f"אימוג'ים: {puzzle.get('emoji_prompt', '')}\n"
                f"עברית: {puzzle.get('answer_he', '')}\n"
                f"English: {puzzle.get('answer_en', '')}\n"
                f"כינויים לבדיקה: {aliases_text}"
            ),
            "note": (
                f"Emoji Night seed review · {puzzle.get('media_type', 'movie')} · "
                f"difficulty {puzzle.get('difficulty', 2)}"
            ),
        })
    return items


def _ensure_emoji_puzzle_reviews(items):
    if EMOJI_PUZZLE_REVIEW_FLAG.exists():
        return items

    seeded = items + _emoji_puzzle_review_items()
    EMOJI_PUZZLE_REVIEW_FLAG.parent.mkdir(parents=True, exist_ok=True)
    EMOJI_PUZZLE_REVIEW_FLAG.write_text("seeded\n", encoding="utf-8")
    _save_pending_reviews(seeded)
    return seeded


def _default_pending_reviews():
    return [
        {
            "id": "community-post",
            "title": "📝 פוסט קהילתי — למה בוטסון קיים",
            "channel": "כללי (General)",
            "when": "",
            "preview": "להיות אל-הורי לבד זה לא הרבה. אבל להכיר עוד אנשים אל-הוריים — זה מרחיב את המרחב שבו אפשר להרגיש חופשי עם זה.\n\nעם חברים שהם הורים, יש פער — בזמינות, בנושאים, בפרספקטיבה. אז הרצון להכיר אנשים שחולקים את הבחירה הזו הגיוני.\n\nמה שלא עבד לי לפני: קבוצות פייסבוק — שיחות נשארות על פני השטח, אף פעם לא ממש יודע עם מי מדברים. לארגן מפגשים — מתיש, הכל נופל על אחד, ולהיות מופנם זה לא עוזר. קבוצות וואטסאפ אחרות — סגנון הניהול שם הוריד אותי מהקיר.\n\nהקבוצה הזו היא ניסיון לקחת את הטוב מכל אלה. להכיר אנשים דרך תחומי עניין משותפים, לא רק דרך תווית. ליצור מקום שממנו אפשר למצוא חיבורים אמיתיים — ואולי גם מפגשים שצומחים מהיכרות שכבר קיימת.\n\nזה יותר מזכיר צ'אטים של שנות ה-90 מאשר רשתות חברתיות של היום.\n\nאם אתם מכירים אנשים אל-הוריים שהמקום הזה יכול להתאים להם — שלחו אותם. 🙂",
            "note": "פוסט אישי לקבוצה — לעיון לפני פרסום",
        },
        {
            "id": "weekend-rec",
            "title": "🎬 המלצת סופ״ש — סלוט 1/5",
            "channel": "סרטים סדרות וכו (54)",
            "when": "שישי 15:00 — רוטציה שבועית",
            "preview": "🎬 המלצת סופ״ש\n\nסרט אחד שהייתם רוצים שמישהו אחר בקבוצה יראה — ויגיד לכם מה חשב.\n\nלא חייב להיות חדש. לא חייב להיות מושלם. משהו שעבד לכם ואתם סקרנים אם יעבוד גם למישהו אחר.",
            "note": "רוטציה: סרטים → סדרות → משחקים → ספרים → פודקאסטים. סלוט ראשון בסופ״ש.",
        },
        {
            "id": "weekend-plans",
            "title": "🌙 סוף שבוע שקט — סלוט 2/5",
            "channel": "כללי (General)",
            "when": "שישי 20:00",
            "preview": "🌙 סוף שבוע שקט\n\nיש לכם את כל הסופ״ש. מה אתם עושים איתו?\n\n• יוצאים\n• סדרה על הספה\n• פרויקט אישי / לימודים\n• שטויות ובטלה\n• אחר — ספרו\n\nאין כאן לוח זמנים של ילדים אחרים — אפשר סתם לבהות בתקרה וזה בסדר.",
            "note": "Poll עם 5 אופציות + טקסט פתוח. מודד את ההזדהות עם הזווית האל-הורית.",
        },
        {
            "id": "saturday-noon",
            "title": "☀️ שבת בבוקר — סלוט 3/5",
            "channel": "כל מה שחמוד (335)",
            "when": "שבת 12:00",
            "preview": "☀️ שבת בצהריים\n\nמה אתם אוכלים / צופים / קוראים / בוהים בו עכשיו?\n\nתמונה אחת, שורה אחת. רק עכשיו — לא מה עשיתם אתמול ולא מה תעשו מחר.",
            "note": "Thread קצר ומיידי. אם לא נתפס אחרי 2 שבועות — מחליפים לטריוויה כפולה.",
        },
        {
            "id": "trivia-launch",
            "title": "🧠 טריוויה! — סלוט 4/5 (השקה)",
            "channel": "כללי (General)",
            "when": "שבת 17:00 — שבועי",
            "preview": "🧠 טריוויה ב-17:00!\n\n5 שאלות. 30 שניות לכל אחת. תשובה נכונה = 12 נק׳. המקום הראשון = +20 בונוס.\n\nקטגוריות: גיאוגרפיה, מדע, סרטים, היסטוריה, ספורט, גיימינג.\n\nהשאלה הראשונה יורדת עוד 10 דקות — תישארו בצ׳אט.",
            "note": "30 שאלות בבריכה, 5/שבוע = 6 שבועות תוכן. אחר־כך ליצור /triviapool חדשה עם GPT.",
        },
        {
            "id": "weekly-roundup",
            "title": "📊 סיכום שבועי + לידרבורד — סלוט 5/5",
            "channel": "כללי (General)",
            "when": "שבת 22:00",
            "preview": "📊 השבוע בקבוצה\n\nהודעות: {total}\nחברים פעילים: {active}/{members}\nפוסט בולט: {top_post_link}\n\n🏆 טופ 5 השבוע:\n1. {u1} — {p1} נק׳\n2. {u2} — {p2} נק׳\n3. {u3} — {p3} נק׳\n4. {u4} — {p4} נק׳\n5. {u5} — {p5} נק׳\n\nשבוע טוב לכולם 🌿",
            "note": "התבנית שהבוט כבר מחולל (weekly_roundup + weekly_leaderboard). להזיז מ-18:00 ל-22:00 כדי לסגור את הסופ״ש.",
        },
    ]


def _load_pending_reviews():
    if PENDING_REVIEWS_PATH.exists():
        try:
            items = json.loads(PENDING_REVIEWS_PATH.read_text(encoding="utf-8"))
            return _ensure_emoji_puzzle_reviews(items)
        except Exception:
            logger.exception("[review] failed to read %s — reseeding", PENDING_REVIEWS_PATH)
    items = _default_pending_reviews()
    _save_pending_reviews(items)
    return _ensure_emoji_puzzle_reviews(items)


def _save_pending_reviews(items):
    PENDING_REVIEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEWS_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)
    return templates.TemplateResponse(request, name="review.html", context={
        "pending": _load_pending_reviews(),
    })


_HEBREW_DAY_TO_IDX = {
    "ראשון": 0, "שני": 1, "שלישי": 2, "רביעי": 3,
    "חמישי": 4, "שישי": 5, "שבת": 6,
}


def _parse_when_field(when: str):
    """Parse drafts' `when` like 'שישי 15:00 — רוטציה שבועית'.

    Returns (hebrew_day_idx, 'HH:MM') or None if unparseable.
    """
    import re as _re
    if not when:
        return None
    m_time = _re.search(r"(\d{1,2}):(\d{2})", when)
    if not m_time:
        return None
    hh, mm = int(m_time.group(1)), int(m_time.group(2))
    if not (0 <= hh < 24 and 0 <= mm < 60):
        return None
    day_idx = None
    for name, idx in _HEBREW_DAY_TO_IDX.items():
        if name in when:
            day_idx = idx
            break
    if day_idx is None:
        return None
    return (day_idx, f"{hh:02d}:{mm:02d}")


def _parse_channel_topic_id(channel: str):
    """Extract topic_id from 'label (NN)'. Returns int or None."""
    import re as _re
    if not channel:
        return None
    m = _re.search(r"\((\d+)\)", channel)
    return int(m.group(1)) if m else None


def _next_date_for_hebrew_day(target_hebrew_day: int, time_str: str):
    """Next date (>= today) matching the Hebrew weekday.

    If today matches and the time is still in the future, use today;
    otherwise find the next occurrence in the coming week.
    """
    from datetime import date as _date, datetime as _dt, timedelta as _td
    today = _date.today()
    today_hebrew = (today.weekday() + 1) % 7  # Python Mon=0..Sun=6 → Heb Sun=0..Sat=6
    if today_hebrew == target_hebrew_day:
        hh, mm = map(int, time_str.split(":"))
        now = _dt.now()
        if (now.hour, now.minute) < (hh, mm):
            return today
        return today + _td(days=7)
    for i in range(1, 8):
        cand = today + _td(days=i)
        if (cand.weekday() + 1) % 7 == target_hebrew_day:
            return cand
    return today + _td(days=7)  # unreachable


@app.post("/api/review/{item_id}/dismiss")
async def review_dismiss(request: Request, item_id: str):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    action = (body.get("action") or "dismiss") if isinstance(body, dict) else "dismiss"

    items = _load_pending_reviews()
    remaining = [m for m in items if m.get("id") != item_id]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail="pending item not found")
    _save_pending_reviews(remaining)
    logger.info("[review] %s → %s (remaining=%d)", item_id, action, len(remaining))
    return {"ok": True, "remaining": len(remaining)}


@app.post("/api/review/{item_id}/approve")
async def review_approve(request: Request, item_id: str, db: Database = Depends(get_db)):
    """Create a DRAFT scheduled_messages row so the admin can review/edit it in /planner."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    items = _load_pending_reviews()
    item = next((m for m in items if m.get("id") == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="pending item not found")

    from datetime import date as _date
    parsed = _parse_when_field(item.get("when", ""))
    if parsed is not None:
        hebrew_day, time_str = parsed
        scheduled_date = _next_date_for_hebrew_day(hebrew_day, time_str).isoformat()
    else:
        # No parseable when — use today + 21:00 as a placeholder; admin edits in planner
        scheduled_date = _date.today().isoformat()
        time_str = "21:00"

    topic_id = _parse_channel_topic_id(item.get("channel", ""))

    msg_id = await db.create_scheduled_message(
        text=item.get("preview", ""),
        message_type="custom",
        channel_topic_id=topic_id,
        target_group="main",
        scheduled_date=scheduled_date,
        scheduled_time=time_str,
        recurrence=None,
        recurrence_days=None,
        auto_pin=False,
    )
    # Downgrade to 'draft' so it appears in the planner's drafts panel, not live schedule
    await db.update_scheduled_message(msg_id, status="draft")

    remaining = [m for m in items if m.get("id") != item_id]
    _save_pending_reviews(remaining)
    logger.info(
        "[review] approved id=%s → draft msg_id=%s date=%s time=%s topic=%s",
        item_id, msg_id, scheduled_date, time_str, topic_id,
    )
    return {
        "ok": True,
        "draft_id": msg_id,
        "scheduled_date": scheduled_date,
        "scheduled_time": time_str,
        "topic_id": topic_id,
        "redirect": f"/planner#draft-{msg_id}",
    }


# ── Content Calendar API ─────────────────────────────────

# Preview computation lives in bot/scheduler/materializer.py so the dashboard
# and the bot materializer share a single source of truth.
from bot.scheduler.materializer import compute_week_previews


@app.get("/api/calendar")
async def get_calendar(request: Request, db: Database = Depends(get_db)):
    """Get scheduled messages in FullCalendar event format."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Jerusalem")
    now = datetime.now(tz)

    date_from = request.query_params.get("start", now.strftime("%Y-%m-%d"))
    date_to = request.query_params.get("end", (now + timedelta(days=42)).strftime("%Y-%m-%d"))

    messages = await db.get_scheduled_messages(date_from, date_to)

    # Channel color map
    channel_colors = {
        1517: "#6366f1",  # gaming - indigo
        442: "#6366f1",   # geek/anime - indigo
        54: "#ef4444",    # movies - red
        347: "#a855f7",   # art - purple
        1431: "#f59e0b",  # politics - amber
        335: "#ec4899",   # cute - pink
        59: "#ec4899",    # singles - pink
        2184: "#f59e0b",  # goals/yom yom - amber
        341: "#3b82f6",   # welcome - blue
    }

    # Convert to FullCalendar format
    events = []
    for m in messages:
        if m.get("status") == "cancelled":
            continue

        color = channel_colors.get(m.get("channel_topic_id"), "#71717a")
        status = m.get("status", "scheduled")

        poll_options_raw = m.get("poll_options")
        poll_options: list | None = None
        if poll_options_raw:
            try:
                decoded = json.loads(poll_options_raw) if isinstance(poll_options_raw, str) else poll_options_raw
                if isinstance(decoded, list):
                    poll_options = [str(o) for o in decoded]
            except (TypeError, ValueError):
                poll_options = None

        event = {
            "id": str(m["id"]),
            "title": m.get("text", "")[:60],
            "start": f"{m['scheduled_date']}T{m.get('scheduled_time', '09:00')}:00",
            "allDay": False,
            "backgroundColor": color if status != "sent" else "#1a1a2e",
            "borderColor": "#22c55e" if status == "sent" else color,
            "textColor": "#fafafa" if status != "sent" else "#71717a",
            "extendedProps": {
                "fullText": m.get("text", ""),
                "status": status,
                "messageType": m.get("message_type", "custom"),
                "channelTopicId": m.get("channel_topic_id"),
                "recurrence": m.get("recurrence"),
                "sentAt": m.get("sent_at"),
                "createdBy": m.get("created_by"),
                "coverPath": m.get("cover_path"),
                "pollOptions": poll_options,
                "pollDuration": m.get("poll_duration"),
            },
        }
        events.append(event)

    # Build committed_index from real events to skip duplicates in previews.
    # Anything not cancelled is "committed" — including sent/failed — otherwise
    # already-sent slots would get a ghost preview on top.
    committed_index = {}
    for m in messages:
        if m.get("status") == "cancelled":
            continue
        mtype = m.get("message_type", "")
        if mtype not in ("morning", "evening", "discussion"):
            continue
        dkey = m.get("scheduled_date", "")
        tkey = (m.get("scheduled_time") or "")[:5]
        committed_index[(dkey, tkey, mtype)] = m

    # Compute preview events for each week in range
    from datetime import date, timedelta
    start_date = date.fromisoformat(date_from[:10])
    end_date = date.fromisoformat(date_to[:10])

    # Find first Sunday on or before start_date
    days_since_sunday = (start_date.weekday() + 1) % 7
    first_sunday = start_date - timedelta(days=days_since_sunday)

    current_sunday = first_sunday
    while current_sunday <= end_date:
        week_previews = compute_week_previews(current_sunday.isoformat(), committed_index)
        for p in week_previews:
            p_date = date.fromisoformat(p["date"])
            if p_date < start_date or p_date > end_date:
                continue
            color = channel_colors.get(p["topic_id"], "#71717a")
            events.append({
                "id": f"preview-{p['date']}-{p['time']}-{p['type']}",
                "title": (p["text"] or "")[:60],
                "start": f"{p['date']}T{p['time']}:00",
                "allDay": False,
                "backgroundColor": color + "26",
                "borderColor": color,
                "textColor": "#fafafa",
                "editable": False,
                "classNames": ["preview-event"],
                "extendedProps": {
                    "fullText": p["text"],
                    "status": "preview",
                    "messageType": p["type"],
                    "channelTopicId": p["topic_id"],
                    "category": p.get("category"),
                    "isPreview": True,
                },
            })
        current_sunday += timedelta(days=7)

    return events


@app.post("/api/calendar")
async def create_calendar_item(request: Request, db: Database = Depends(get_db)):
    """Create a new scheduled message."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    poll_options = data.get("poll_options")
    msg_id = await db.create_scheduled_message(
        text=data["text"],
        message_type=data.get("message_type", "custom"),
        channel_topic_id=data.get("channel_topic_id"),
        target_group=data.get("target_group", "main"),
        scheduled_date=data["scheduled_date"],
        scheduled_time=data["scheduled_time"],
        recurrence=data.get("recurrence"),
        recurrence_days=json.dumps(data["recurrence_days"]) if data.get("recurrence_days") else None,
        auto_pin=data.get("auto_pin", False),
        cover_path=data.get("cover_path"),
        poll_options=json.dumps(poll_options) if isinstance(poll_options, list) else poll_options,
        poll_duration=data.get("poll_duration"),
    )
    return {"status": "ok", "id": msg_id}


@app.put("/api/calendar/{msg_id}")
async def update_calendar_item(msg_id: int, request: Request, db: Database = Depends(get_db)):
    """Update a scheduled message."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    allowed = {"text", "channel_topic_id", "target_group", "scheduled_date", "scheduled_time",
               "recurrence", "recurrence_days", "status", "auto_pin", "message_type", "cover_path",
               "poll_options", "poll_duration"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if "recurrence_days" in fields and isinstance(fields["recurrence_days"], list):
        fields["recurrence_days"] = json.dumps(fields["recurrence_days"])
    if "poll_options" in fields and isinstance(fields["poll_options"], list):
        fields["poll_options"] = json.dumps(fields["poll_options"])

    await db.update_scheduled_message(msg_id, **fields)
    return {"status": "ok"}


@app.delete("/api/calendar/{msg_id}")
async def delete_calendar_item(msg_id: int, request: Request, db: Database = Depends(get_db)):
    """Cancel a scheduled message."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.delete_scheduled_message(msg_id)
    return {"status": "ok"}


@app.post("/api/calendar/{msg_id}/send-now")
async def send_calendar_item_now(msg_id: int, request: Request, db: Database = Depends(get_db)):
    """Send a scheduled message immediately."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    target = data.get("target", "main")  # "main" or "test"

    # Get the message
    messages = await db.get_scheduled_messages("2000-01-01", "2099-12-31")
    msg = next((m for m in messages if m["id"] == msg_id), None)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    from telegram import Bot
    bot = Bot(os.getenv("BOT_TOKEN", ""))

    if target == "test":
        group_id = int(os.getenv("TEST_GROUP_ID", "0"))
    else:
        group_id = int(os.getenv("GROUP_ID", "0"))

    from bot.handlers.calendar import (
        send_message_with_optional_cover,
        send_poll_message,
        _parse_poll_options,
    )

    try:
        opts = _parse_poll_options(msg.get("poll_options"))
        if msg.get("message_type") == "poll" and len(opts) >= 2:
            sent = await send_poll_message(
                bot,
                chat_id=group_id,
                question=msg["text"],
                options=opts,
                message_thread_id=msg.get("channel_topic_id"),
                duration_hours=msg.get("poll_duration"),
                cover_path=msg.get("cover_path"),
            )
        else:
            sent = await send_message_with_optional_cover(
                bot,
                chat_id=group_id,
                text=msg["text"],
                message_thread_id=msg.get("channel_topic_id"),
                cover_path=msg.get("cover_path"),
            )
        if target != "test":
            await db.mark_message_sent(msg_id, sent.message_id)
        return {"status": "ok", "message_id": sent.message_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Cover image endpoints ────────────────────────────────

_COVER_MIMES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

_MAX_COVER_BYTES = 8 * 1024 * 1024  # 8 MB cap


def _cover_filename(source_tag: str, ext: str) -> str:
    import time
    return f"{int(time.time())}_{source_tag}_{secrets.token_hex(4)}.{ext}"


def _cover_response(path: Path) -> dict:
    rel = path.relative_to(MEDIA_DIR).as_posix()
    return {"status": "ok", "path": rel, "url": f"/media/{rel}"}


@app.post("/api/covers/upload")
async def upload_cover(request: Request, file: UploadFile = File(...)):
    """Accept an uploaded image and save to MEDIA_DIR/covers/."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    ext = _COVER_MIMES.get((file.content_type or "").lower())
    if not ext:
        raise HTTPException(status_code=400, detail=f"Unsupported content-type {file.content_type}")
    data = await file.read(_MAX_COVER_BYTES + 1)
    if len(data) > _MAX_COVER_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 8MB)")
    dest = COVERS_DIR / _cover_filename("up", ext)
    dest.write_bytes(data)
    logger.info("Cover uploaded: %s (%d bytes)", dest.name, len(data))
    return _cover_response(dest)


@app.post("/api/covers/scrape")
async def scrape_cover(request: Request):
    """Fetch an image cover from a web page (og:image or first large img)."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = await request.json()
    url = (body.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Valid http(s) URL required")

    import httpx, re
    from urllib.parse import urljoin

    def _find_image_url(html: str) -> str | None:
        """Try many strategies to find a usable image URL on the page."""
        patterns = [
            r'<meta[^>]+property=["\']og:image(?::(?:secure_)?url)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::(?:secure_)?url)?["\']',
            r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
            r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']',
            r'<link[^>]+rel=["\']image_src["\'][^>]+href=["\']([^"\']+)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.I)
            if m and m.group(1).strip():
                return m.group(1).strip()

        m = re.search(r'"image"\s*:\s*"([^"]+)"', html)
        if m:
            return m.group(1).strip()
        m = re.search(r'"image"\s*:\s*\[\s*"([^"]+)"', html)
        if m:
            return m.group(1).strip()

        for m in re.finditer(r'<img\b[^>]+>', html, re.I):
            tag = m.group(0)
            for attr in ("src", "data-src", "data-original", "data-lazy-src"):
                a = re.search(rf'\b{attr}=["\']([^"\']+)["\']', tag, re.I)
                if a and a.group(1).strip():
                    return a.group(1).strip()
            a = re.search(r'\bsrcset=["\']([^"\']+)["\']', tag, re.I)
            if a:
                first = a.group(1).split(",")[0].strip().split(" ")[0]
                if first:
                    return first
        return None

    ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15.0, headers={"User-Agent": ua}) as client:
            r = await client.get(url)
            r.raise_for_status()
            ctype = (r.headers.get("content-type", "").split(";")[0]).lower()

            # Direct image URL — use response as-is
            if ctype.startswith("image/"):
                data = r.content
                if len(data) > _MAX_COVER_BYTES:
                    raise HTTPException(status_code=413, detail="Image too large (max 8MB)")
                ext = _COVER_MIMES.get(ctype, "jpg")
            else:
                # HTML page — find an image
                html = r.text
                found = _find_image_url(html)
                if not found:
                    raise HTTPException(status_code=404, detail="No image found on page")
                base = str(r.url)
                img_url = urljoin(base, found)
                ir = await client.get(img_url, headers={"Referer": base})
                ir.raise_for_status()
                data = ir.content
                if len(data) > _MAX_COVER_BYTES:
                    raise HTTPException(status_code=413, detail="Image too large (max 8MB)")
                mime = (ir.headers.get("content-type", "").split(";")[0]).lower()
                ext = _COVER_MIMES.get(mime, "jpg")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Fetch failed: {e}")

    dest = COVERS_DIR / _cover_filename("scrape", ext)
    dest.write_bytes(data)
    logger.info("Cover scraped from %s: %s (%d bytes)", url, dest.name, len(data))
    return _cover_response(dest)


@app.post("/api/covers/generate")
async def generate_cover(request: Request):
    """Generate a cover image via kie.ai Ideogram V3."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt required")

    api_key = os.getenv("KIE_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="KIE_API_KEY not set in environment")

    from bot.utils.kie_client import generate_image_sync
    try:
        data, ext = await generate_image_sync(api_key=api_key, prompt=prompt)
    except Exception as e:
        logger.error("kie.ai generation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")

    dest = COVERS_DIR / _cover_filename("ai", ext)
    dest.write_bytes(data)
    logger.info("Cover generated via kie.ai: %s (%d bytes)", dest.name, len(data))
    return _cover_response(dest)


# ── Planner Page ─────────────────────────────────────────

@app.get("/planner", response_class=HTMLResponse)
async def planner_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Jerusalem"))

    forum_topics = await db.get_forum_topics() if hasattr(db, 'get_forum_topics') else []
    drafts = await db.get_draft_messages() if hasattr(db, 'get_draft_messages') else []

    import json as _json
    for d in drafts:
        opts = d.get("draft_options")
        if opts and isinstance(opts, str):
            try:
                d["draft_options_list"] = _json.loads(opts)
            except Exception:
                d["draft_options_list"] = []
        else:
            d["draft_options_list"] = []

    topic_names = {t["topic_id"]: t["name"] for t in forum_topics}

    schedule_pattern = _json.dumps(get_settings().get("schedule", {}))

    # Build discussion channel list for the shared prompt modal dropdown
    from bot.handlers.discussions import CATEGORY_NAMES
    settings_obj = get_settings()
    topics_cfg = settings_obj.get("topics", {})
    topic_ids_dict = topics_cfg.get("discussions", {})
    discussion_channels = []
    for cat, tid in topic_ids_dict.items():
        if tid:
            discussion_channels.append({
                "key": cat,
                "name": CATEGORY_NAMES.get(cat, cat),
                "topic_id": tid,
            })

    # Group channels by purpose for the create drawer picker
    by_id = {t["topic_id"]: t for t in forum_topics}
    goals_id = topics_cfg.get("goals")
    welcome_id = topics_cfg.get("welcome")
    mapped_ids = set(topic_ids_dict.values()) | {goals_id, welcome_id}
    mapped_ids.discard(None)
    grouped_channels = {
        "discussions": [
            {"topic_id": tid, "name": by_id[tid]["name"], "category": cat}
            for cat, tid in topic_ids_dict.items()
            if tid and tid in by_id
        ],
        "daily": [by_id[goals_id]] if goals_id and goals_id in by_id else [],
        "other": [t for t in forum_topics if t["topic_id"] not in mapped_ids],
    }

    return templates.TemplateResponse(request, name="planner.html", context={
        "now_date": now.strftime("%Y-%m-%d"),
        "forum_topics": forum_topics,
        "topic_names": topic_names,
        "drafts": drafts,
        "schedule_pattern": schedule_pattern,
        "discussion_channels": discussion_channels,
        "grouped_channels": grouped_channels,
    })


# ── Members API ──────────────────────────────────────────

@app.get("/members", response_class=HTMLResponse)
async def members_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    async with db._db.execute(
        """SELECT m.*, s.current_streak, s.longest_streak, s.last_post_date
           FROM members m
           LEFT JOIN streaks s ON m.user_id = s.user_id
           ORDER BY m.joined_at DESC"""
    ) as cursor:
        rows = await cursor.fetchall()
        members = [dict(r) for r in rows]

    for m in members:
        lvl = get_level(m.get("karma_points", 0))
        m["level"] = lvl["level"]
        m["level_tag"] = lvl["tag"]
        m["level_emoji"] = lvl["emoji"]
    settings = get_settings()

    return templates.TemplateResponse(request, name="members.html", context={
        "members": members,
        "settings": settings,
    })


# ── Free Games (RSS) ─────────────────────────────────────

@app.get("/free-games", response_class=HTMLResponse)
async def free_games_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    recent = await db.recent_free_games(50)
    return templates.TemplateResponse(request, name="free-games.html", context={
        "settings": settings,
        "recent": recent,
    })


@app.post("/api/free-games/toggle")
async def toggle_free_games(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}

    if "features" not in settings:
        settings["features"] = {}
    existing = settings["features"].get("free_games") or {}
    if not isinstance(existing, dict):
        existing = {}
    if "enabled" in data:
        existing["enabled"] = bool(data["enabled"])
    if "groups" in data and isinstance(data["groups"], list):
        existing["groups"] = [str(g) for g in data["groups"]]
    else:
        existing.setdefault("groups", ["test"])
    existing.setdefault("enabled", False)
    settings["features"]["free_games"] = existing

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded, "feature": existing}


@app.post("/api/free-games/schedule")
async def update_free_games_schedule(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings_path = CONFIG_DIR / "settings.yaml"
    with open(settings_path, "r", encoding="utf-8") as f:
        settings = yaml.safe_load(f) or {}

    if "schedule" not in settings:
        settings["schedule"] = {}
    sched = settings["schedule"].get("free_games") or {}
    if not isinstance(sched, dict):
        sched = {}
    if "time" in data:
        sched["time"] = str(data["time"]).strip() or "10:00"
    if "days" in data and isinstance(data["days"], list):
        sched["days"] = [int(d) for d in data["days"]]
    if "feed_url" in data:
        sched["feed_url"] = str(data["feed_url"]).strip() or "https://gg.deals/eu/news/feed/"
    sched.setdefault("time", "10:00")
    sched.setdefault("days", [0, 1, 2, 3, 4, 5, 6])
    sched.setdefault("feed_url", "https://gg.deals/eu/news/feed/")
    settings["schedule"]["free_games"] = sched

    with open(settings_path, "w", encoding="utf-8") as f:
        yaml.dump(settings, f, allow_unicode=True, default_flow_style=False)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded, "schedule": sched}


@app.post("/api/free-games/post-now")
async def post_free_games_now(request: Request, db: Database = Depends(get_db)):
    """Manually fetch the feed and post new freebies right now.

    The dashboard creates its own telegram.Bot from BOT_TOKEN (same pattern as
    /api/bot/send-message etc.) — bot-process JobQueue is NOT invoked here.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    target = (data.get("target") or "test").lower()  # default to test for safety

    settings = get_settings()
    fg_schedule = settings.get("schedule", {}).get("free_games", {}) or {}
    feed_url = data.get("feed_url") or fg_schedule.get("feed_url") or "https://gg.deals/eu/news/feed/"

    if target == "main":
        group_id = int(os.getenv("GROUP_ID", "0"))
        topic_id = settings.get("topics", {}).get("gaming")
    else:
        group_id = int(os.getenv("TEST_GROUP_ID", "0"))
        topic_id = None  # test group may not have a forum structure

    if not group_id:
        raise HTTPException(status_code=400, detail=f"No {target} group ID configured")

    from telegram import Bot
    from bot.handlers.free_games import fetch_and_post_once
    bot = Bot(os.getenv("BOT_TOKEN", ""))
    summary = await fetch_and_post_once(bot, db, group_id, topic_id, feed_url)
    return {"status": "ok", "target": target, **summary}


@app.post("/api/free-games/unpost/{guid:path}")
async def unpost_free_game(guid: str, request: Request, db: Database = Depends(get_db)):
    """Remove a posted-game record so it can be re-posted by the feed."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    deleted = await db.unpost_free_game(guid)
    return {"status": "ok", "deleted": deleted}
