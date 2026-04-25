"""Botson Dashboard — FastAPI backend for managing the bot."""

import asyncio
import copy
import json
import logging
import os
import random
import secrets
import signal
import time
from pathlib import Path
from datetime import datetime, date

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_h)
    logger.propagate = False

import yaml
from fastapi import FastAPI, Request, Depends, HTTPException, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot.database.db import Database
from bot.utils.config import DB_PATH, get_holiday_blackout, get_settings, get_prompts, get_spam_patterns, get_topic_rules, is_auto_blocked_on, load_yaml
from bot.utils.levels import get_level, get_progress
from dashboard.trivia_admin import TriviaVerificationError, build_round_trigger_payload, save_and_verify_trivia_questions
from dashboard.verified_topics import (
    VerifiedTopicError,
    merge_observed_and_verified_topics,
    normalize_verified_topic_entry,
)

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


@app.middleware("http")
async def no_cache_html(request: Request, call_next):
    """Force browsers to always re-fetch HTML pages so JS fixes deploy reliably.

    Without this, FastAPI's TemplateResponse has no cache headers; browsers
    apply heuristic caching (often ~10% of last-modified age), which means
    users see stale inline JS for hours after a deploy. Hard refresh fixes
    it for that one page load only — they hit it again on the next nav.

    Static assets (/media, /static) keep their default headers — those are
    immutable by name (timestamped filenames for covers, etc.).
    """
    response = await call_next(request)
    ct = response.headers.get("content-type", "")
    if ct.startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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
async def settings_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    observed_topics = await db.get_forum_topics()
    verified_topics = await db.get_verified_forum_topics() if hasattr(db, 'get_verified_forum_topics') else []
    merged_topics = merge_observed_and_verified_topics(observed_topics, verified_topics)
    handler_routings = await db.list_handler_routings() if hasattr(db, 'list_handler_routings') else []
    return templates.TemplateResponse(request, name="settings.html", context={
        "settings": settings,
        "verified_topics": verified_topics,
        "merged_topics": merged_topics,
        "handler_routings": handler_routings,
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
    if "general" in data and data.get("general"):
        raise HTTPException(status_code=400, detail="topics.general is no longer a trusted setting; use verified topics workflow")
    data = {k: v for k, v in data.items() if k != "general"}
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


@app.post("/api/settings/holiday-blackouts")
async def update_holiday_blackouts(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    rows = data.get("items", []) if isinstance(data, dict) else []
    cleaned: list[dict] = []
    seen_dates: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        date_iso = str(row.get("date") or "").strip()
        if not date_iso or date_iso in seen_dates:
            continue
        try:
            date.fromisoformat(date_iso)
        except ValueError:
            continue
        cleaned.append({
            "date": date_iso,
            "name": str(row.get("name") or "").strip(),
            "note": str(row.get("note") or "").strip(),
            "block_auto": bool(row.get("block_auto", True)),
        })
        seen_dates.add(date_iso)

    settings = _load_settings_file()
    settings["holiday_blackouts"] = cleaned
    _save_settings_file(settings)

    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded, "count": len(cleaned)}


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


@app.get("/api/topics/verified")
async def get_verified_topics(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    topics = await db.get_verified_forum_topics()
    return {"topics": topics}


@app.post("/api/topics/verified")
async def upsert_verified_topic(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    data = await request.json()
    try:
        entry = normalize_verified_topic_entry(
            topic_id=data.get("topic_id"),
            verified_name=data.get("verified_name"),
            category_key=data.get("category_key"),
            verification_source=data.get("verification_source"),
        )
    except VerifiedTopicError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.upsert_verified_forum_topic(**entry)
    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded, "entry": entry}


@app.delete("/api/topics/verified/{category_key}")
async def delete_verified_topic(category_key: str, request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    await db.remove_verified_forum_topic(category_key)
    reloaded = _signal_bot_reload()
    return {"status": "ok", "bot_reloaded": reloaded, "category_key": category_key}


@app.post("/api/topics/forum")
async def add_forum_topic(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    raise HTTPException(status_code=400, detail="Observed forum topic names are no longer manually writable; use /api/topics/verified for trusted mappings")


@app.get("/api/handler-routing")
async def list_handler_routing(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    rows = await db.list_handler_routings()
    return {"routings": rows}


@app.post("/api/handler-routing/save")
async def save_handler_routing(request: Request, db: Database = Depends(get_db)):
    """Upsert a single handler's routing row.

    Expected JSON: {handler, play_topic_id, teaser_topic_ids}
    play_topic_id must either be null or exist in verified_forum_topics.
    Each teaser_topic_ids entry must also be verified. Reject the
    edit loudly instead of silently storing a broken route.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    data = await request.json()
    handler = str(data.get("handler") or "").strip()
    if not handler:
        raise HTTPException(status_code=400, detail="handler is required")
    play_raw = data.get("play_topic_id")
    play_topic_id = int(play_raw) if play_raw not in (None, "", 0, "0") else None
    teasers_raw = data.get("teaser_topic_ids") or []
    if not isinstance(teasers_raw, list):
        raise HTTPException(status_code=400, detail="teaser_topic_ids must be a list")
    teaser_topic_ids: list[int] = []
    for x in teasers_raw:
        try:
            teaser_topic_ids.append(int(x))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"teaser id {x!r} is not an int")

    if play_topic_id is not None and not await db.is_verified_topic_id(play_topic_id):
        raise HTTPException(
            status_code=400,
            detail=f"play_topic_id {play_topic_id} is not verified; run the dot-test first",
        )
    for tid in teaser_topic_ids:
        if not await db.is_verified_topic_id(tid):
            raise HTTPException(
                status_code=400,
                detail=f"teaser_topic_id {tid} is not verified; run the dot-test first",
            )

    await db.set_handler_routing(handler, play_topic_id, teaser_topic_ids)
    reloaded = _signal_bot_reload()
    return {
        "status": "ok",
        "bot_reloaded": reloaded,
        "handler": handler,
        "play_topic_id": play_topic_id,
        "teaser_topic_ids": teaser_topic_ids,
    }


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
    is_topic_discovery = bool(data.get("is_topic_discovery"))

    if not text:
        raise HTTPException(status_code=400, detail="Message text required")

    from telegram import Bot
    from bot.handlers.calendar import (
        send_message_with_optional_cover,
        send_poll_message,
        _parse_poll_options,
    )
    from bot.utils.topic_guard import UnverifiedTopicError
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
                db=db,
                chat_id=group_id,
                question=text,
                options=opts,
                message_thread_id=int(topic_id) if topic_id else None,
                duration_hours=poll_duration,
                cover_path=cover_path,
                bypass_verification=is_topic_discovery,
            )
        else:
            msg = await send_message_with_optional_cover(
                bot,
                db=db,
                chat_id=group_id,
                text=text,
                message_thread_id=int(topic_id) if topic_id else None,
                cover_path=cover_path,
                bypass_verification=is_topic_discovery,
            )
        await db.log_activity("manual_send", f"שלח הודעה ידנית ({'טסט' if target == 'test' else 'ראשית'})", target_channel=str(topic_id or "general"))
        return {"status": "ok", "message_id": msg.message_id}
    except UnverifiedTopicError as e:
        raise HTTPException(status_code=400, detail=str(e))
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
    "trivia":     {"emoji": "🧠", "label": "טריוויה", "css": "bg-fuchsia-500/20 text-fuchsia-200 border-fuchsia-500/40"},
    "weekly":     {"emoji": "📊", "label": "סיכום", "css": "bg-violet-500/20 text-violet-200 border-violet-500/40"},
    "event":      {"emoji": "🎉", "label": "אירוע", "css": "bg-rose-500/20 text-rose-200 border-rose-500/40"},
}

# One-off Independence Day (יום העצמאות 5786) live trivia round — matches
# _TRIVIA_DEMO_STATE["starts_at"] and the pending review id below. Surfaced
# as a calendar chip so users see the 15:00 slot, not just the 12:00
# announcement.
_TRIVIA_INDEPENDENCE_DATE = "2026-04-22"
_TRIVIA_INDEPENDENCE_TIME = "15:00"
_TRIVIA_INDEPENDENCE_TEXT = (
    "🇮🇱 טריוויה חיה ליום העצמאות — 10 שאלות מהירות על ישראל: "
    "היסטוריה, ספרות, קולנוע, מוזיקה ותרבות.\n"
    "תשובה נכונה = 12 נק׳ · מקום ראשון = +20 בונוס 🏆"
)

_TRIVIA_DEMO_LOCK = asyncio.Lock()
_TRIVIA_DEMO_STATE = {
    "id": "independence-live",
    "title": "יום העצמאות — טריוויה חיה",
    "host": "Botson",
    "starts_at": "15:00",
    "status": "lobby",
    "question_duration": 15,
    "reveal_duration": 5,
    "question_index": -1,
    "question_started_at": None,
    "players": {},
    "answers": {},
    "used_question_texts": [],
    "questions": [],
    "task": None,
    "bot_tasks": [],
}

_TRIVIA_DEMO_CORRECT_POINTS = 10
_TRIVIA_DEMO_WRONG_PENALTY = 5


def _trivia_live_pool() -> list[dict]:
    try:
        data = load_yaml("trivia.yaml") or {}
    except Exception:
        return []
    return [
        q for q in (data.get("questions") or [])
        if str(q.get("category") or "").strip() == "ישראל"
    ]


def _pick_trivia_live_questions(count: int = 10) -> list[dict]:
    pool = _trivia_live_pool()
    if len(pool) < count:
        return copy.deepcopy(pool)
    import random as _random
    return copy.deepcopy(_random.sample(pool, count))


def _sorted_live_players(state: dict) -> list[dict]:
    players = list(state["players"].values())
    players.sort(key=lambda item: (-item.get("score", 0), item.get("joined_at", 0)))
    return players


def _player_rank(state: dict, player_id: str | None) -> int | None:
    if not player_id or player_id not in state["players"]:
        return None
    ordered = _sorted_live_players(state)
    for idx, player in enumerate(ordered, start=1):
        if player["id"] == player_id:
            return idx
    return None


def _public_trivia_live_state(player_id: str | None = None) -> dict:
    state = _TRIVIA_DEMO_STATE
    ordered = _sorted_live_players(state)
    question = None
    if 0 <= state["question_index"] < len(state["questions"]):
        raw_q = state["questions"][state["question_index"]]
        question = {
            "index": state["question_index"] + 1,
            "total": len(state["questions"]),
            "text": raw_q["text"],
            "options": raw_q["options"],
            "category": raw_q.get("category", "כללי"),
        }
        if state["status"] in {"reveal", "final"}:
            question["correct"] = raw_q["correct"]
    remaining_s = None
    if state["status"] == "question" and state["question_started_at"]:
        elapsed = max(0, time.time() - state["question_started_at"])
        remaining_s = max(0, int(state["question_duration"] - elapsed))

    you = state["players"].get(player_id) if player_id else None
    answered = player_id in state["answers"] if player_id else False
    payload = {
        "id": state["id"],
        "title": state["title"],
        "host": state["host"],
        "starts_at": state["starts_at"],
        "status": state["status"],
        "question": question,
        "remaining_s": remaining_s,
        "leaderboard": [
            {
                "id": player["id"],
                "name": player["name"],
                "score": player["score"],
                "correct": player["correct"],
                "streak": player["streak"],
                "last_delta": player.get("last_delta", 0),
                "is_bot": bool(player.get("is_bot")),
            }
            for player in ordered
        ],
        "player_count": len(ordered),
        "questions_left": max(0, len(state["questions"]) - max(0, state["question_index"] + (1 if state["status"] in {"question", "reveal"} else 0))),
        "you": {
            "id": you["id"],
            "name": you["name"],
            "score": you["score"],
            "rank": _player_rank(state, player_id),
            "answered": answered,
            "last_answer_correct": you.get("last_answer_correct"),
            "last_delta": you.get("last_delta", 0),
            "is_bot": bool(you.get("is_bot")),
        } if you else None,
    }
    return payload


def _ensure_trivia_demo_bots(min_total_players: int = 3) -> None:
    state = _TRIVIA_DEMO_STATE
    players = state["players"]
    human_count = sum(1 for player in players.values() if not player.get("is_bot"))
    if human_count != 1:
        return
    bot_names = ["Bot Herzl", "Bot Golda", "Bot Falcon", "Bot Carmel"]
    needed = max(0, min_total_players - len(players))
    for idx in range(needed):
        player_id = f"bot-{secrets.token_hex(4)}"
        players[player_id] = {
            "id": player_id,
            "name": bot_names[idx % len(bot_names)],
            "score": 0,
            "correct": 0,
            "streak": 0,
            "joined_at": time.time() + idx + 1,
            "last_delta": 0,
            "last_answer_correct": None,
            "is_bot": True,
        }


async def _trivia_demo_bot_answer(player_id: str, question_index: int) -> None:
    await asyncio.sleep(random.uniform(2.5, 8.5))
    async with _TRIVIA_DEMO_LOCK:
        state = _TRIVIA_DEMO_STATE
        if state["status"] != "question" or state["question_index"] != question_index:
            return
        if player_id not in state["players"] or player_id in state["answers"]:
            return
        player = state["players"][player_id]
        if not player.get("is_bot"):
            return
        question = state["questions"][question_index]
        correct = random.random() < 0.62
        if correct:
            answer_index = int(question["correct"])
        else:
            wrong = [idx for idx in range(len(question["options"])) if idx != question["correct"]]
            answer_index = random.choice(wrong)
        elapsed = max(0, time.time() - (state["question_started_at"] or time.time()))
        remaining = max(0, state["question_duration"] - elapsed)
        delta = 0
        if correct:
            delta = _TRIVIA_DEMO_CORRECT_POINTS + int(remaining)
            player["score"] += delta
            player["correct"] += 1
            player["streak"] += 1
        else:
            delta = -min(_TRIVIA_DEMO_WRONG_PENALTY, player["score"])
            player["score"] += delta
            player["streak"] = 0
        player["last_delta"] = delta
        player["last_answer_correct"] = correct
        state["answers"][player_id] = {
            "answer_index": answer_index,
            "correct": correct,
            "at": time.time(),
        }


def _schedule_trivia_demo_bots(question_index: int) -> None:
    state = _TRIVIA_DEMO_STATE
    for task in state.get("bot_tasks", []):
        task.cancel()
    state["bot_tasks"] = []
    for player_id, player in state["players"].items():
        if player.get("is_bot"):
            state["bot_tasks"].append(asyncio.create_task(_trivia_demo_bot_answer(player_id, question_index)))


async def _run_trivia_live_session() -> None:
    try:
        while True:
            await asyncio.sleep(_TRIVIA_DEMO_STATE["question_duration"])
            async with _TRIVIA_DEMO_LOCK:
                state = _TRIVIA_DEMO_STATE
                if state["status"] != "question":
                    break
                state["status"] = "reveal"
            await asyncio.sleep(_TRIVIA_DEMO_STATE["reveal_duration"])
            async with _TRIVIA_DEMO_LOCK:
                state = _TRIVIA_DEMO_STATE
                next_index = state["question_index"] + 1
                if next_index >= len(state["questions"]):
                    state["status"] = "final"
                    state["question_started_at"] = None
                    state["answers"] = {}
                    break
                state["status"] = "question"
                state["question_index"] = next_index
                state["question_started_at"] = time.time()
                state["answers"] = {}
                for player in state["players"].values():
                    player["last_delta"] = 0
                    player["last_answer_correct"] = None
                _schedule_trivia_demo_bots(next_index)
    finally:
        async with _TRIVIA_DEMO_LOCK:
            for task in _TRIVIA_DEMO_STATE.get("bot_tasks", []):
                task.cancel()
            _TRIVIA_DEMO_STATE["bot_tasks"] = []
            _TRIVIA_DEMO_STATE["task"] = None


async def _start_trivia_live_session() -> None:
    async with _TRIVIA_DEMO_LOCK:
        if _TRIVIA_DEMO_STATE["task"]:
            return
        if not _TRIVIA_DEMO_STATE["players"]:
            raise HTTPException(status_code=409, detail="No players joined yet")
        _ensure_trivia_demo_bots()
        _TRIVIA_DEMO_STATE["questions"] = _pick_trivia_live_questions(10)
        _TRIVIA_DEMO_STATE["used_question_texts"] = [q["text"] for q in _TRIVIA_DEMO_STATE["questions"]]
        _TRIVIA_DEMO_STATE["status"] = "question"
        _TRIVIA_DEMO_STATE["question_index"] = 0
        _TRIVIA_DEMO_STATE["question_started_at"] = time.time()
        _TRIVIA_DEMO_STATE["answers"] = {}
        for player in _TRIVIA_DEMO_STATE["players"].values():
            player["last_delta"] = 0
            player["last_answer_correct"] = None
        _schedule_trivia_demo_bots(0)
        _TRIVIA_DEMO_STATE["task"] = asyncio.create_task(_run_trivia_live_session())


async def _reset_trivia_live_session() -> None:
    async with _TRIVIA_DEMO_LOCK:
        task = _TRIVIA_DEMO_STATE.get("task")
        if task:
            task.cancel()
        _TRIVIA_DEMO_STATE.update({
            "status": "lobby",
            "question_index": -1,
            "question_started_at": None,
            "answers": {},
            "used_question_texts": [],
            "questions": [],
            "task": None,
            "bot_tasks": [],
        })
        for player in _TRIVIA_DEMO_STATE["players"].values():
            player.update({
                "score": 0,
                "correct": 0,
                "streak": 0,
                "last_delta": 0,
                "last_answer_correct": None,
            })


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
    from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
    from collections import defaultdict
    from zoneinfo import ZoneInfo as _ZoneInfo
    from bot.utils.config import is_feature_enabled as _is_feature_enabled

    settings = get_settings() or {}
    year, month_num = _parse_month(month)
    _tz = _ZoneInfo("Asia/Jerusalem")
    now_il = _datetime.now(_tz)
    today = now_il.date()
    current_hhmm = now_il.strftime("%H:%M")
    last_day = _cal.monthrange(year, month_num)[1]
    month_start = _date(year, month_num, 1).isoformat()
    month_end = _date(year, month_num, last_day).isoformat()

    async with db._db.execute(
        """SELECT scheduled_date, scheduled_time, message_type, text, channel_topic_id
           FROM scheduled_messages
           WHERE status='scheduled' AND scheduled_date BETWEEN ? AND ?
           ORDER BY scheduled_date, scheduled_time""",
        (month_start, month_end),
    ) as cur:
        sched_rows = await cur.fetchall()

    async with db._db.execute(
        """SELECT event_date, event_time, title, description, location,
                  rsvp_yes, rsvp_maybe, topic_id
           FROM events
           WHERE active=1 AND event_date BETWEEN ? AND ?
           ORDER BY event_date, event_time""",
        (month_start, month_end),
    ) as cur:
        event_rows = await cur.fetchall()

    # topic_id → Hebrew channel name. Prefer live forum_topics (bot-observed),
    # fall back to settings.topics (category → id map) so we can show *something*
    # for topics the bot hasn't seen yet.
    forum_topics = await db.get_forum_topics()
    topics_by_id = {row["topic_id"]: row["name"] for row in forum_topics if row.get("topic_id") and row.get("name")}
    topics_cfg = settings.get("topics", {}) or {}
    for key, tid in topics_cfg.items():
        if isinstance(tid, int) and tid not in topics_by_id:
            topics_by_id[tid] = key
    discussions_cfg = topics_cfg.get("discussions", {}) or {}
    for key, tid in discussions_cfg.items():
        if isinstance(tid, int) and tid not in topics_by_id:
            topics_by_id[tid] = key

    def _topic_name(tid):
        if tid is None:
            return None
        return topics_by_id.get(tid)

    def _short(s: str, n: int = 60) -> str:
        s = (s or "").strip().replace("\n", " ")
        return s if len(s) <= n else s[:n - 1] + "…"

    by_day = defaultdict(list)
    holiday_blackouts = {
        item["date"]: item
        for item in (settings.get("holiday_blackouts", []) or [])
        if isinstance(item, dict) and item.get("date")
    }
    for r in sched_rows:
        meta = _CAL_TYPE_STYLE.get(r["message_type"], _CAL_TYPE_STYLE["event"])
        full = (r["text"] or "").strip()
        tid = r["channel_topic_id"]
        by_day[r["scheduled_date"]].append({
            "emoji": meta["emoji"],
            "css": meta["css"],
            "label": meta["label"],
            "time": (r["scheduled_time"] or "")[:5],
            "type": r["message_type"],
            "text": full,
            "short": _short(full),
            "topic_id": tid,
            "topic_name": _topic_name(tid),
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
        tid = r["topic_id"]
        by_day[r["event_date"]].append({
            "emoji": meta["emoji"],
            "css": meta["css"],
            "label": meta["label"],
            "time": (r["event_time"] or "")[:5] or "—",
            "type": "event",
            "text": text,
            "short": _short(r["title"] or ""),
            "topic_id": tid,
            "topic_name": _topic_name(tid),
        })

    # ── Synthesize trivia chips ──────────────────────────────────────
    # Trivia is scheduled as a live APScheduler job (see bot/scheduler/jobs.py)
    # and never written to scheduled_messages or events, so it was invisible
    # on the calendar. Mirror that schedule here from config.
    if _is_feature_enabled("trivia"):
        trivia_meta = _CAL_TYPE_STYLE["trivia"]
        trivia_sched = settings.get("schedule", {}).get("trivia", {"time": "20:00", "days": [2, 5]}) or {}
        trivia_time = (trivia_sched.get("time", "20:00") or "20:00")[:5]
        # Hebrew days: 0=Sunday..6=Saturday. Python date.weekday(): 0=Monday..6=Sunday.
        heb_days = set(trivia_sched.get("days", [2, 5]) or [])
        d = _date.fromisoformat(month_start)
        end = _date.fromisoformat(month_end)
        while d <= end:
            heb_dow = (d.weekday() + 1) % 7
            if heb_dow in heb_days:
                iso = d.isoformat()
                # Skip past days and today's already-passed slots.
                if d < today or (d == today and trivia_time <= current_hhmm):
                    d += _timedelta(days=1)
                    continue
                by_day[iso].append({
                    "emoji": trivia_meta["emoji"],
                    "css": trivia_meta["css"],
                    "label": trivia_meta["label"],
                    "time": trivia_time,
                    "type": "trivia",
                    "text": "שאלת טריוויה אוטומטית — הבוט בוחר שאלה מ-trivia.yaml בזמן השליחה.",
                    "short": "שאלת טריוויה אוטומטית",
                    "topic_id": None,
                    "topic_name": None,
                })
            d += _timedelta(days=1)

    # One-off: Independence Day 5786 live trivia round at 15:00 (in-memory
    # demo state lives at _TRIVIA_DEMO_STATE). Show it on the day itself so
    # users see the actual slot, not just the 12:00 announcement row.
    if month_start <= _TRIVIA_INDEPENDENCE_DATE <= month_end:
        indep_date = _date.fromisoformat(_TRIVIA_INDEPENDENCE_DATE)
        if not (indep_date < today or (indep_date == today and _TRIVIA_INDEPENDENCE_TIME <= current_hhmm)):
            trivia_meta = _CAL_TYPE_STYLE["trivia"]
            by_day[_TRIVIA_INDEPENDENCE_DATE].append({
                "emoji": "🇮🇱",
                "css": trivia_meta["css"],
                "label": "טריוויה חיה",
                "time": _TRIVIA_INDEPENDENCE_TIME,
                "type": "trivia",
                "text": _TRIVIA_INDEPENDENCE_TEXT,
                "short": "טריוויה ליום העצמאות",
                "topic_id": None,
                "topic_name": None,
            })

    # Keep each day ordered by time — synthesized trivia rows were appended
    # after the DB rows, so a plain sort restores chronological order.
    for iso, items in by_day.items():
        items.sort(key=lambda it: it.get("time") or "")

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
                    "holiday_block": holiday_blackouts.get(iso),
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
        "holiday_blackouts_json": json.dumps(holiday_blackouts, ensure_ascii=False),
        "today_iso": today.isoformat(),
        "prev_url": f"/calendar?month={prev_year:04d}-{prev_month:02d}",
        "next_url": f"/calendar?month={next_year:04d}-{next_month:02d}",
        "today_url": "/calendar",
    })


@app.get("/trivia-live-demo", response_class=HTMLResponse)
async def trivia_live_demo(request: Request):
    """Public MVP for a Telegram Mini App style live trivia round."""
    return templates.TemplateResponse(request, name="trivia-live-demo.html", context={
        "initial_state_json": json.dumps(_public_trivia_live_state(), ensure_ascii=False),
    })


@app.get("/api/trivia-live/state")
async def trivia_live_state(player_id: str | None = None):
    return JSONResponse(_public_trivia_live_state(player_id))


@app.post("/api/trivia-live/join")
async def trivia_live_join(request: Request):
    data = await request.json()
    name = str(data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    async with _TRIVIA_DEMO_LOCK:
        player_id = secrets.token_urlsafe(8)
        _TRIVIA_DEMO_STATE["players"][player_id] = {
            "id": player_id,
            "name": name[:24],
            "score": 0,
            "correct": 0,
            "streak": 0,
            "joined_at": time.time(),
            "last_delta": 0,
            "last_answer_correct": None,
        }
    return JSONResponse({"player_id": player_id, "state": _public_trivia_live_state(player_id)})


@app.post("/api/trivia-live/answer")
async def trivia_live_answer(request: Request):
    data = await request.json()
    player_id = str(data.get("player_id") or "").strip()
    answer_index = int(data.get("answer_index"))
    async with _TRIVIA_DEMO_LOCK:
        state = _TRIVIA_DEMO_STATE
        if state["status"] != "question":
            raise HTTPException(status_code=409, detail="question not active")
        if player_id not in state["players"]:
            raise HTTPException(status_code=404, detail="player not found")
        if player_id in state["answers"]:
            raise HTTPException(status_code=409, detail="already answered")
        if not (0 <= state["question_index"] < len(state["questions"])):
            raise HTTPException(status_code=409, detail="invalid question state")
        question = state["questions"][state["question_index"]]
        if answer_index < 0 or answer_index >= len(question["options"]):
            raise HTTPException(status_code=400, detail="invalid answer index")
        player = state["players"][player_id]
        correct = answer_index == question["correct"]
        elapsed = max(0, time.time() - (state["question_started_at"] or time.time()))
        remaining = max(0, state["question_duration"] - elapsed)
        delta = 0
        if correct:
            delta = _TRIVIA_DEMO_CORRECT_POINTS + int(remaining)
            player["score"] += delta
            player["correct"] += 1
            player["streak"] += 1
        else:
            delta = -min(_TRIVIA_DEMO_WRONG_PENALTY, player["score"])
            player["score"] += delta
            player["streak"] = 0
        player["last_delta"] = delta
        player["last_answer_correct"] = correct
        state["answers"][player_id] = {
            "answer_index": answer_index,
            "correct": correct,
            "at": time.time(),
        }
    return JSONResponse({"ok": True, "correct": correct, "delta": delta})


@app.post("/api/trivia-live/start")
async def trivia_live_start():
    await _start_trivia_live_session()
    return JSONResponse({"ok": True, "state": _public_trivia_live_state()})


@app.post("/api/trivia-live/reset")
async def trivia_live_reset():
    await _reset_trivia_live_session()
    return JSONResponse({"ok": True, "state": _public_trivia_live_state()})


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


@app.get("/api/topics/live")
async def live_topics_api(request: Request, db: Database = Depends(get_db)):
    """Return the currently known forum topics directly from the live DB table."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    topics = await db.get_forum_topics()
    return {"topics": topics}


# ── Events API ───────────────────────────────────────────

@app.get("/events", response_class=HTMLResponse)
async def events_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    events = await db.get_all_events()
    for e in events:
        voters = await db.get_event_voters(e["id"])
        e["rsvp_yes"] = voters["yes"]
        e["rsvp_maybe"] = voters["maybe"]
        e["rsvp_yes_count"] = len(voters["yes"])
        e["rsvp_maybe_count"] = len(voters["maybe"])
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
    """Hebrew event card text. Plain text — no markdown, since the bot send
    doesn't pass parse_mode='Markdown' (and we don't want to risk injection
    from user-typed titles)."""
    lines = [f"📅 {title}"]
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


def _event_rsvp_markup(event_id: int):
    """Build RSVP buttons whose callback_data matches the bot's handler.

    bot/handlers/events.py:181 parses event_id from `rsvp_yes_{id}` and
    `rsvp_maybe_{id}`. Without the id suffix the click fires but never
    reaches the update logic — buttons appear inert.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ מגיע/ה", callback_data=f"rsvp_yes_{event_id}"),
        InlineKeyboardButton("🤔 אולי", callback_data=f"rsvp_maybe_{event_id}"),
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
    publish = data.get("publish", True)

    logger.info("[events.create] publish=%s title=%r date=%s time=%s topic=%s "
                "group=%s cover=%s auto_pin=%s source_poll=%s/%s",
                publish, title[:60], event_date, event_time, topic_id,
                target_group, cover_path, auto_pin,
                source_poll_message_id, source_poll_option_key)

    # Dedup check: an active event with the same title/date/time/description
    # is treated as a duplicate (likely a double-clicked submit). Return the
    # existing event instead of creating a second one and re-publishing.
    norm_desc = (description or "").strip()
    async with db._db.execute(
        """SELECT id FROM events
           WHERE active = 1
             AND title = ?
             AND event_date = ?
             AND IFNULL(event_time, '') = IFNULL(?, '')
             AND IFNULL(description, '') = ?
           ORDER BY id LIMIT 1""",
        (title, event_date, event_time, norm_desc),
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        logger.info("[events.create] duplicate detected — returning existing id=%d", existing["id"])
        return {"status": "duplicate", "event_id": existing["id"], "published": False}

    event_id = await db.create_event(
        title=title, description=description, event_date=event_date,
        event_time=event_time, location=location, created_by=0,
        cover_path=cover_path, auto_pin=auto_pin, topic_id=topic_id,
        source_poll_message_id=source_poll_message_id,
        source_poll_option_key=source_poll_option_key,
    )
    logger.info("[events.create] inserted event_id=%d", event_id)

    if not publish:
        logger.info("[events.create] publish=false — skipping Telegram send")
        return {"status": "ok", "event_id": event_id, "published": False}

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
            bot, db=db, chat_id=chat_id, text=text,
            message_thread_id=int(topic_id) if topic_id else None,
            cover_path=cover_path,
        )
        # Attach RSVP buttons (separate edit_reply_markup avoids needing to thread
        # markup through the photo/text helper).
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=sent.message_id,
                reply_markup=_event_rsvp_markup(event_id),
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
# The /trivia standalone page was removed on 2026-04-22. All trivia UI now
# lives in the planner drawer's "🧠 טריוויה" type. Leaderboard is on סקירה כללית.
# /api/trivia/* endpoints below are still called by the planner drawer.

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
        "interval_seconds": int(data.get("interval_seconds") or 20),
        "interval_minutes": int(data.get("interval_minutes") or 1),
        "intro_offset_seconds": int(data.get("intro_offset_seconds") or 10),
        "wrap_offset_seconds": int(data.get("wrap_offset_seconds") or 20),
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
    from telegram import Bot
    from bot.handlers.emoji_puzzle import resolve_emoji_target, start_emoji_night

    chat_id, thread_id = resolve_emoji_target(target)
    if not chat_id:
        raise HTTPException(status_code=400, detail=f"Unknown target '{target}'")

    ctx = type("EmojiCtx", (), {})()
    ctx.bot = Bot(os.getenv("BOT_TOKEN", ""))
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

    try:
        verification = save_and_verify_trivia_questions(path, data.get("questions") or [])
    except TriviaVerificationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"status": "ok", **verification}


@app.post("/api/trivia/reset")
async def reset_trivia(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.reset_trivia_scores()
    return {"status": "ok"}


TRIVIA_ROUND_TRIGGER = Path(__file__).parent.parent / "data" / "trivia_round_trigger.json"
TRIVIA_ROUND_STOP = Path(__file__).parent.parent / "data" / "trivia_round_stop"


@app.post("/api/trivia/round/start")
async def start_trivia_round(request: Request, db: Database = Depends(get_db)):
    """Write a persisted trigger file that the bot's trigger_watcher picks up within ~10s."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    settings = get_settings() or {}
    target = (data.get("target") or "test").lower()
    pre_roll_s = int(data.get("pre_roll_s", 30))
    topic_id = data.get("topic_id")
    topic_id = int(topic_id) if topic_id not in (None, "") else None
    teaser_topic_id = data.get("teaser_topic_id")
    teaser_topic_id = int(teaser_topic_id) if teaser_topic_id not in (None, "", 0, "0") else None
    teaser_text = data.get("teaser_text")
    teaser_text = str(teaser_text).strip() if teaser_text else None
    topic_verification_source = str(data.get("topic_verification_source") or "").strip()
    theme_label = str(data.get("theme_label") or "").strip() or "ישראל"
    raw_categories = data.get("categories") or []
    if isinstance(raw_categories, str):
        categories = [part.strip() for part in raw_categories.split(",") if part.strip()]
    else:
        categories = [str(part).strip() for part in raw_categories if str(part).strip()]
    question_count = int(data.get("question_count") or 5)

    main_group = int(os.getenv("GROUP_ID", "0"))
    test_group = int(os.getenv("TEST_GROUP_ID", "0"))
    verified_topic_ids = None
    if target == "main":
        verified_topics = await db.get_verified_forum_topics()
        verified_topic_ids = {int(row.get("topic_id")) for row in verified_topics if row.get("topic_id") is not None}

    try:
        payload = build_round_trigger_payload(
            target=target,
            main_group_id=main_group,
            test_group_id=test_group,
            pre_roll_s=pre_roll_s,
            topic_id=topic_id,
            topic_verification_source=topic_verification_source,
            theme_label=theme_label,
            categories=categories,
            question_count=question_count,
            live_topic_ids=verified_topic_ids,
            teaser_topic_id=teaser_topic_id,
            teaser_text=teaser_text,
        )
    except TriviaVerificationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Preflight: refuse to persist the trigger if trivia.yaml has too few
    # questions matching the requested categories. Without this, the bot
    # would silently log "not enough questions" to bot.log while the user
    # only sees "סיבוב מוכן" and never learns the round didn't run.
    if categories:
        try:
            tdata = load_yaml("trivia.yaml") or {}
            wanted = {c.strip().lower() for c in categories if c.strip()}
            matches = [
                q for q in (tdata.get("questions") or [])
                if isinstance(q, dict)
                and str(q.get("category") or "").strip().lower() in wanted
            ]
            if len(matches) < question_count:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"אין מספיק שאלות מתאימות בקטגוריות {sorted(wanted)} "
                        f"(נמצאו {len(matches)}, נדרש {question_count}). "
                        f"ייצר שאלות בנושא ולחץ שמור לפני השקת הסיבוב."
                    ),
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("trivia preflight category-match check skipped: %s", e)

    if target == "main":
        configured_general = (settings.get("topics") or {}).get("general")
        payload["target_provenance"]["configured_general_topic"] = configured_general

    TRIVIA_ROUND_TRIGGER.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, ensure_ascii=False)
    TRIVIA_ROUND_TRIGGER.write_text(serialized, encoding="utf-8")
    persisted = TRIVIA_ROUND_TRIGGER.read_text(encoding="utf-8")
    if persisted != serialized:
        raise HTTPException(status_code=500, detail="Trigger verification failed")

    logger.info(
        "trivia_round: trigger persisted target=%s chat=%s thread=%s theme=%s categories=%s count=%s provenance=%s",
        target,
        payload["chat_id"],
        payload["thread_id"],
        payload["theme_label"],
        payload["categories"],
        payload["question_count"],
        payload["target_provenance"],
    )
    return {"status": "ok", "persisted_trigger": payload}


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
        # `category` is the user's categories input (comma-separated, e.g.
        # "ישראל" or "טכנולוגיה,ישראל"); `instructions` is the theme_label.
        # CRITICAL: the category tag on each block must match one of the user's
        # values EXACTLY — the round launcher filters by string equality, so
        # free-form AI tags produce zero matches and _pick_questions ends up
        # empty (the 2026-04-23 "tech round showed film questions" bug).
        theme_hint = (instructions or "").strip()
        cat_hint = (category or "").strip()
        cat_list = [c.strip() for c in cat_hint.split(",") if c.strip()] if cat_hint else []
        if cat_list:
            allowed_tags = " | ".join(cat_list)
            topic_line = (
                f"נושא מרכזי: {theme_hint or cat_hint}. הקפד שכל השאלות יהיו בתחום זה ובקטגוריות הבאות בלבד: {cat_hint}.\n"
                f"חובה: בכל שאלה, השורה 'קטגוריה:' חייבת להיות בדיוק אחד מהערכים הבאים (ללא שינוי טקסט): {allowed_tags}."
            )
        elif theme_hint:
            topic_line = (
                f"נושא מרכזי: {theme_hint}. כל השאלות צריכות להיות קשורות לנושא הזה.\n"
                f"חובה: השורה 'קטגוריה:' של כל שאלה חייבת להיות בדיוק: {theme_hint}."
            )
        else:
            topic_line = "נושאים מגוונים: תרבות, מדע, היסטוריה, בידור, גאוגרפיה, אוכל."
        base = f"""צור {count} שאלות טריוויה בעברית עבור {COMMUNITY_CONTEXT}

כל שאלה צריכה להיות בפורמט הבא (4 שורות לכל שאלה, מופרדות בשורה ריקה):
שאלה: [טקסט השאלה]
תשובות: [תשובה1] | [תשובה2] | [תשובה3] | [תשובה4]
נכונה: [מספר התשובה הנכונה 0-3]
קטגוריה: [קטגוריה]

{topic_line}
פלט: רק את השאלות בפורמט שצוין, בלי הסברים נוספים."""

    else:
        base = f"צור תוכן בעברית עבור {COMMUNITY_CONTEXT}"

    if existing and (mode == "append" or (field == "trivia" and mode == "replace")):
        # For trivia replace, the frontend now sends the current pool so the AI
        # can avoid returning the same canonical questions on every regenerate
        # (narrow themes otherwise keep landing on Check Point / NSO / Waze).
        base += f"\n\nהנה התוכן הקיים (אל תחזור עליו, צור תוכן חדש ושונה):\n{existing}"

    return base


async def _generate_via_cli(prompt: str) -> str:
    """Try generating content via Claude Code CLI.

    systemd services run with HOME pointing at WorkingDirectory, not the
    user's real home — which means `claude` can't find ~/.claude/.
    Look up the real home from /etc/passwd and override HOME in env.
    """
    import pwd as _pwd
    try:
        real_home = _pwd.getpwuid(os.geteuid()).pw_dir
    except Exception:
        real_home = os.path.expanduser("~")
    env = {**os.environ, "HOME": real_home}
    proc = await asyncio.create_subprocess_exec(
        "claude", "-p", prompt, "--model", "sonnet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
    if proc.returncode != 0:
        raise RuntimeError(f"CLI error (rc={proc.returncode}): {stderr.decode()[:300]}")
    out = stdout.decode().strip()
    if not out:
        raise RuntimeError(f"CLI returned empty output (stderr={stderr.decode()[:200]})")
    return out


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
    cli_err = None
    try:
        content = await _generate_via_cli(prompt)
    except Exception as e:
        cli_err = e
        logger.warning("generate_content: CLI failed, falling back to API: %s", e)
        try:
            content = await _generate_via_api(prompt)
        except Exception as api_err:
            raise HTTPException(
                status_code=500,
                detail=f"Generation failed: CLI={cli_err}; API={api_err}",
            )

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
        if is_auto_blocked_on(day_date):
            skipped += len(times)
            continue
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


# ── AI fill: today-only, context-aware ─────────────────────

_HEBREW_DAY_NAMES = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]


def _render_today_context(today, holiday, events_today, week_committed, id_to_name):
    """Hebrew context blob shared across today's prompts."""
    from datetime import date as _date
    hebrew_day = (today.weekday() + 1) % 7
    lines = [f"היום: יום {_HEBREW_DAY_NAMES[hebrew_day]}, {today.isoformat()}."]
    if holiday:
        hname = (holiday.get("name") or "").strip()
        hnote = (holiday.get("note") or "").strip()
        suffix = f" — {hnote}" if hnote else ""
        if hname:
            lines.append(f"חג/מועד: {hname}{suffix}.")

    if events_today:
        lines.append("אירועים מתוכננים היום:")
        for e in events_today:
            parts = [f'"{(e.get("title") or "").strip()}"']
            et = (e.get("event_time") or "").strip()[:5]
            if et:
                parts.append(f"בשעה {et}")
            loc = (e.get("location") or "").strip()
            if loc:
                parts.append(f"מיקום: {loc}")
            tid = e.get("topic_id")
            if tid and id_to_name.get(int(tid)):
                parts.append(f"ערוץ: {id_to_name[int(tid)]}")
            lines.append("- " + ", ".join(parts))

    visible = [r for r in week_committed if r.get("status") != "cancelled"]
    if visible:
        lines.append("הודעות שכבר תוזמנו השבוע (לא לחזור על ניסוח/נושא):")
        for r in visible:
            d = r.get("scheduled_date", "") or ""
            try:
                dd = _date.fromisoformat(d)
                day_he = _HEBREW_DAY_NAMES[(dd.weekday() + 1) % 7]
            except Exception:
                day_he = d
            mt = r.get("message_type", "") or ""
            tid = r.get("channel_topic_id")
            tname = id_to_name.get(int(tid)) if tid else None
            topic_str = f", ערוץ {tname}" if tname else ""
            preview = (r.get("text") or "").strip().replace("\n", " ")[:60]
            lines.append(f"- יום {day_he} ({mt}{topic_str}): \"{preview}\"")

    return "\n".join(lines)


def _build_today_regular_prompt(mtype: str, category: str | None, context_block: str) -> str:
    type_instruction = {
        "morning": "צור הודעת בוקר אחת מעוררת השראה בעברית. שורה או שתיים, פותחת באמוג'י רלוונטי, הטון: חם, מעודד, קליל.",
        "evening": "צור הודעת ערב אחת רפלקטיבית בעברית. שורה או שתיים, פותחת באמוג'י רלוונטי, הטון: רגוע, מחבק, מעודד רפלקציה.",
        "discussion": f'צור שאלה אחת לדיון בקטגוריה "{category or ""}" בעברית. שורה אחת, מעוררת שיחה ומעניינת, הטון: סקרני, פתוח, מזמין.',
    }.get(mtype, "צור הודעה אחת בעברית.")
    return (
        f"{COMMUNITY_CONTEXT}\n\n"
        f"{context_block}\n\n"
        f"המשימה: {type_instruction}\n"
        f"אירועי היום מוזכרים לעיל לידיעה בלבד — יש להם תזכורת נפרדת, אין לשלב אותם בהודעה הזו.\n"
        f"אל תחזור על נושאים/ניסוחים שמופיעים כבר בהודעות השבוע.\n"
        f"פלט: רק ההודעה עצמה, בלי מספור, בלי מרכאות, בלי הסברים."
    )


def _build_event_reminder_prompt(event: dict, topic_name: str | None, context_block: str) -> str:
    title = (event.get("title") or "").strip()
    ev_time = (event.get("event_time") or "").strip()[:5]
    location = (event.get("location") or "").strip()
    bits = [f'האירוע: "{title}"']
    if ev_time:
        bits.append(f"בשעה {ev_time}")
    if location:
        bits.append(f"מיקום: {location}")
    if topic_name:
        bits.append(f"ערוץ: {topic_name}")
    return (
        f"{COMMUNITY_CONTEXT}\n\n"
        f"{context_block}\n\n"
        f"המשימה: כתוב תזכורת קצרה לאירוע של היום. שורה עד שתיים, פותחת באמוג'י, הטון: חם וידידותי, קוראת לחברי הקהילה לא לשכוח / להצטרף.\n"
        f"{', '.join(bits)}.\n"
        f"פלט: רק ההודעה, בלי מספור, בלי מרכאות, בלי הסברים."
    )


def _compute_reminder_time(event_time_hhmm: str, now_hhmm: str) -> str | None:
    """event_time - 2h; clamp to now+5min; return None if event <= now+5min."""
    try:
        eh, em = (int(x) for x in event_time_hhmm.split(":")[:2])
        nh, nm = (int(x) for x in now_hhmm.split(":")[:2])
    except (ValueError, TypeError):
        return None
    event_minutes = eh * 60 + em
    now_minutes = nh * 60 + nm
    if event_minutes <= now_minutes + 5:
        return None
    target = event_minutes - 120
    if target <= now_minutes:
        target = now_minutes + 5
        if target % 5:
            target += 5 - (target % 5)
        if target >= event_minutes:
            target = event_minutes - 5
    target = max(0, min(target, 24 * 60 - 1))
    return f"{target // 60:02d}:{target % 60:02d}"


def _parse_trivia_blocks(raw: str) -> tuple[list[dict], list[str]]:
    """Python port of _parseTriviaQuestions in planner.html:886-913.

    Parses AI output of form: שאלה/תשובות/נכונה/קטגוריה blocks separated by
    blank lines. Returns (questions, invalid_reasons).
    """
    questions: list[dict] = []
    invalid: list[str] = []
    blocks = [b.strip() for b in (raw or "").strip().split("\n\n") if b.strip()]
    for i, block in enumerate(blocks):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) != 4:
            invalid.append(f"block {i+1}: expected 4 lines, got {len(lines)}")
            continue
        q_text = lines[0].split(":", 1)[-1].strip() if lines[0].startswith("שאלה:") else ""
        opts_line = lines[1].split(":", 1)[-1].strip() if lines[1].startswith("תשובות:") else ""
        options = [o.strip() for o in opts_line.split("|") if o.strip()]
        correct_line = lines[2].split(":", 1)[-1].strip() if lines[2].startswith("נכונה:") else ""
        category = lines[3].split(":", 1)[-1].strip() if lines[3].startswith("קטגוריה:") else "כללי"
        if not q_text:
            invalid.append(f"block {i+1}: missing question text"); continue
        if len(options) != 4:
            invalid.append(f"block {i+1}: need exactly 4 options, got {len(options)}"); continue
        try:
            correct = int(correct_line)
        except (ValueError, TypeError):
            invalid.append(f"block {i+1}: invalid correct index"); continue
        if correct < 0 or correct > 3:
            invalid.append(f"block {i+1}: correct index out of range"); continue
        questions.append({
            "text": q_text, "options": options, "correct": correct,
            "category": category or "כללי",
        })
    return questions, invalid


def _parse_emoji_blocks(raw: str) -> tuple[list[dict], list[str]]:
    """Parse AI emoji-puzzle output: 4-line blocks with אמוג'י / תשובה_עברית /
    תשובה_אנגלית / חלופות, separated by blank lines. Returns (puzzles, invalid).
    """
    puzzles: list[dict] = []
    invalid: list[str] = []
    blocks = [b.strip() for b in (raw or "").strip().split("\n\n") if b.strip()]
    for i, block in enumerate(blocks):
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if len(lines) != 4:
            invalid.append(f"block {i+1}: expected 4 lines, got {len(lines)}")
            continue
        def _field(line: str, prefix: str) -> str:
            return line.split(":", 1)[-1].strip() if line.startswith(prefix + ":") else ""
        emoji_prompt = _field(lines[0], "אמוג'י")
        answer_he = _field(lines[1], "תשובה_עברית")
        answer_en = _field(lines[2], "תשובה_אנגלית")
        aliases_raw = _field(lines[3], "חלופות")
        if not emoji_prompt:
            invalid.append(f"block {i+1}: missing emoji prompt"); continue
        if not answer_he:
            invalid.append(f"block {i+1}: missing Hebrew answer"); continue
        if not answer_en:
            invalid.append(f"block {i+1}: missing English answer"); continue
        aliases_list: list[str] = []
        if aliases_raw and aliases_raw.strip() not in ("-", "—"):
            aliases_list = [a.strip() for a in aliases_raw.split(",") if a.strip()]
        puzzles.append({
            "emoji_prompt": emoji_prompt,
            "answer_he": answer_he,
            "answer_en": answer_en,
            "aliases": aliases_list,
        })
    return puzzles, invalid


def _build_emoji_puzzle_prompt(count: int, theme: str | None, context_block: str) -> str:
    theme_line = f", בהקשר של: {theme}" if theme else ""
    return (
        f"{COMMUNITY_CONTEXT}\n\n"
        f"{context_block}\n\n"
        f"המשימה: צור {count} חידות אמוג'י לקהילה{theme_line}.\n"
        f"כל חידה מייצגת סרט / סדרה / משחק / ספר ידועים באמצעות רצף אמוג'י קצר (3-6 אמוג'ים).\n"
        f"פורמט לכל חידה — 4 שורות, בלוקים מופרדים בשורה ריקה:\n"
        f"אמוג'י: <רצף אמוג'ים>\n"
        f"תשובה_עברית: <שם בעברית>\n"
        f"תשובה_אנגלית: <Name in English>\n"
        f"חלופות: <פסיקים> (כתיבים נוספים; 0-3 פריטים, או \"-\" אם אין)\n\n"
        f"הקפד שהשם המלא חד-משמעי. אל תחזור על חידות זהות.\n"
        f"פלט: רק הבלוקים, בלי הסברים, בלי מספור."
    )


# ── AI fill today: digest call (smart dedup, real event times, actionable drafts) ──

DIGEST_SYSTEM_PROMPT = """אתה העוזר האוטומטי של מנהלי קהילת "אלהוריים וזה" — קהילת צ'ילדפרי (ללא ילדים מבחירה) בטלגרם.
קיבלת את כל ההקשר על היום — אירועים, הודעות מתוזמנות, לוח הזמנים, תכנים שכבר קיימים. תפקידך: להחליט באופן חכם מה לייצר לקהילה היום, ולקרוא לכלי today_plan עם ההחלטות.

חוקי החלטה מחייבים:

1. מזג אירועים כפולים. רשומות events שמתארות את אותה פעילות (topic זהה, כותרות חופפות, או רשומה מאוחרת שמעדכנת את הקודמת) — מוזגות לאירוע קנוני אחד. כל ה-ids המכוסים חוזרים ב-covered_event_ids.

2. זמן אירוע אמיתי מהטקסט. עמודת event_time ב-DB לא אמינה — היא לפעמים זמן הפוסט של ההודעה, לא זמן האירוע. קרא כל description ו-text. "זז ל-HH:MM" / "עבר ל-HH:MM" / "moved to HH:MM" / הודעה שאומרת "היום ב-HH:MM" — הזמן הזה מנצח. הזכרה האחרונה והחד-משמעית ביותר היא המחייבת.

3. זמן התזכורת. reminder_scheduled_time = actual_event_time − event_reminder_lead_minutes. תגביל: לא לפני now+5 דקות, לא אחרי event-5 דקות. אם האירוע כבר עבר או קרוב מדי — אל תפיק תזכורת.

3a. **השעה הנוכחית — חובה לכבד.** השעה הנוכחית היא בשדה `now_time_il`. אסור להציע **שום** סלוט שזמנו מוקדם יותר מ-`now_time_il + 5 דקות`. דוגמאות:
    - אם now_time_il = 15:00 — **דלג על morning (09:00) לחלוטין**, רשום ב-skipped.morning "כבר עבר היום (now=15:00)". אל תכלול morning ב-regular_slots.
    - אם now_time_il = 20:00 — discussion 18:00 כבר עבר, דלג. אבל discussion 21:00 עדיין רלוונטי.
    - אם now_time_il = 22:30 — דלג על evening 21:00, אבל אפשר להציע סלוט custom ב-23:00 אם רלוונטי.
    - reminder לאירוע כבר משמרים את הכלל הזה ב-#3.

    **אם כל הסלוטים הרגילים של היום כבר עברו**, חובה להציע custom-type סלוטים שזמנם בעתיד הקרוב — "ערב סדרה ב-22:30", "פולס לפני שינה". אל תניח שהיום סגור.

4. בטיחות טופיקים. topic_id בכל פלט חייב להיות מתוך verified_topic_ids. אירוע עם topic_id=null → השתמש ב-events_publish_fallback_topic. אם אתה לא בטוח — אל תנחש, השאר את השורה החוצה ורשום ב-notes.

5. schedule.days — הנחיה רכה, לא חוקה. **חובה לקחת יוזמה:**
   - **אם הסקציה ריקה ([])** — דלג (סקציה כבויה במכוון). **רק זה הסיבה היחידה לדלג.**
   - **אם hebrew_day_num ברשימה** — הפק תוכן רגיל.
   - **אם hebrew_day_num לא ברשימה אבל הרשימה לא ריקה — חובה להציע תוכן בכל זאת**, אלא אם events_today או scheduled_messages_today כבר מכסים אותו slot של היום באותו טופיק. אל תשתמש ב"הימים לא כוללים את היום" כתירוץ לא להפיק. סמן needs_review=true ורשום ב-notes_for_admin תחת **"הצעות יוזמה"**.
   - **morning, evening, discussion** — הקהילה פעילה כל יום בשבוע, כולל שבת. אם הסקציה לא ריקה — חייב להציע (מינימום אחד מהשניים: morning או evening, ועדיף שניהם בימים שאינם בלו"ז כדי לכסות את היום).
   - **trivia ו-emoji** — אם הסקציה לא ריקה, הצע batch אחד ביום (גם בימים מחוץ ללו"ז). idempotence נשמר ע"י הקטגוריות הקיימות.
   - יוצא מן הכלל יחיד: **חג עם block_auto:true** — דלג כל הסקציות, כבר מכוסה ע"י short-circuit במשתנה holiday.

6. אל תכפיל מול existing_drafts_today. אם covered_event_ids שלך חופף ל-covered_event_ids של טיוטה קיימת — דלג, רשום ב-skipped.reminders "already_drafted".

7. אל תחזור על נושאים ש-this_week_previews כבר מכסים. regular_slots חייבים להיות בזווית חדשה.

7a. **איכות תוכן — לא generic. חובה.** הקהילה היא "אלהוריים וזה" — מבוגרים צ'יילדפרי בעברית, בני 30-50, עם ערוצים על gaming, סרטים, אומנות, פוליטיקה, גיקים, בישול וכד'. הימנע מתבניות חלולות כמו "מה היה היום?" או "ספרו דבר טוב". במקום זאת:

   - **שלב את היום בשבוע — ובאופן מדויק לפי השעה. הקפד: בישראל שישי+שבת = סוף שבוע. שבוע עבודה מתחיל ראשון בבוקר.**
     - ראשון בוקר = חזרה לשגרה, אחרי סוף שבוע, מה התכניות?
     - שני-חמישי = אמצע שבוע, יום עבודה רגיל
     - שישי בוקר = סוף שבוע מתחיל היום, מה תכניות לסוף שבוע?
     - שישי ערב = הסוף שבוע כבר בעיצומו, איך זה הולך?
     - **שבת בוקר/צהריים = עדיין סוף שבוע, האווירה רגועה ופנויה. אסור לדבר על "סוף השבוע שעבר"!**
     - **שבת ערב (עד 22:00) = סוף שבוע עדיין נמשך, בסיום קל. הימנע מ"איך עבר סוף השבוע?" כי הוא לא עבר. אפשר "מה עוד עושים הערב?", "מה רואים הלילה?".**
     - שבת מאוחר (22:00+) או ראשון מוקדם = סיום סוף שבוע, מותר להזכיר "סיכום סוף השבוע".

   - **התמקד בעתיד, לא בעבר. חובה לפחות סלוט אחד הצעת פעולה אקטיבית, לא רק שאלה רפלקטיבית:**
     - אסור לתלות הכל בסשן/אירוע של "אמש" (זה משעמם וצופה אחורה).
     - חייב להציע משהו לעשות עכשיו/הערב/השבוע: "מי בעניין של משחק רוקטליג ב-22:00?", "ערב סדרה ביחד? מה רואים?", "פיצ'ר את ה-3 משחקי קופסה האהובים עליכם — נבחר משחק להפעיל".
     - שאלות רפלקטיביות מותרות רק לסלוט morning, ובלבד שהן פותחות את היום ולא סוגרות אותו.

   - **השתמש בערוצים כהיק לתוכן ספציפי וחד**: שאלות discussion חייבות להתחבר לערוץ ולתחום באופן שמייצר תשובות מעניינות, לא generic. **אסור שאלות-תבנית** כמו "איזה X אהבתם?", "איזה Y חזרתם עליו?", "מה הX האהוב?" — זה ייצר תגובות חד-מילתיות בלי שיחה.

     במקום זה, השתמש בהיקים חזקים — אחד מהפורמטים הבאים:

     - **Hot take / opinion חצי-פרובוקטיבי**: "סרט שהיו בו 10 דקות מצוינות והשאר זמן מבוזבז — איזה?", "משחק קופסה שכולם משבחים אבל בעיניכם הוא overrated?"
     - **Specific scenario**: "אתם נכנסים לחדר עם 4 זרים. איזה משחק קופסה תרימו ראשון כדי לשבור את הקרח?"
     - **Compare / choose**: "פרסי הסרטים: צפייה ראשונה אונליין או חיכיתם לראות לבד? למה?"
     - **Behind-the-scenes / process**: "אומנים בקבוצה — מה הסטיוויט הכי לא נוח שאתם עובדים בו ועדיין אוהבים?"
     - **Mini-list**: "3 הסדרות שלא עניינו אתכם בעונה הראשונה אבל נכנסתם להן רק בעונה 2-3 — מי?"
     - **Niche/specific reference**: התחבר לאירוע אקטואלי, סדרה שיצאה השבוע, משחק שעלה לאחרונה. בקש מהקהילה לחלוק חוויה ספציפית, לא דעה כללית.

     **המבחן**: אם אתה יכול להחליף את שם הערוץ בערוץ אחר ולא להפסיד שום דבר — זו שאלה generic. תקן.

     **דוגמאות לתיקון** (מה לא לעשות → מה כן):
     - ❌ "סדרה שאתם מריצים ברקע שוב ושוב" → ✅ "Yellowstone, Suits, Friends — איזו סדרה הכי 'background friendly' אצלכם, ולמה דווקא היא?"
     - ❌ "איזה משחק תפס אתכם השבוע" → ✅ "מצב: סוף שבוע, פרק זמן 90 דקות, רוצים משחק חדש בלי לפתוח חוברת. איזה משחק עונה?"
     - ❌ "איזה אומן השפיע עליכם" → ✅ "אמן/אמנית שגיליתם השבוע באינסטגרם וכבר עוקבים אחריהם — מי?"

   - **למד מ-recent_sent_samples_by_type**: זה הסגנון של הקהילה. שכפל את הקצב, את האמוג'ים שעובדים, את אורך המשפט. אל תיצור משהו שלא יושב על הטון הזה.

   - **הצעות פעילות — חובה לפחות אחת ביום, לא רק שאלות.** שאלה רפלקטיבית = passive. הקהילה זקוקה גם לליווי אקטיבי:
     אסור לסיים את היום ללא לפחות סלוט אחד שהוא **הזמנה קונקרטית לפעולה** (not a question), משלושת הסוגים הבאים:

     a. **הזמנה למשחק/פעילות הערב או מחר** — שים ב-regular_slots עם type="discussion", category="gaming" אם רלוונטי. דוגמאות (אסור להעתיק, רק כדוגמה לסגנון):
        - "🎲 מי בא לשחק Among Us ב-22:30 בדיסקורד? צריכים מינימום 4. עיגול ✋ אם בא לכם."
        - "🃏 משחק קלפים אונליין הערב ב-23:00 — Skull, Coup, או Codenames? תגיבו עם הבחירה ואתם בפנים."
        - "🍿 watch party הערב ב-22:00 — צופים [סדרה/סרט]. מי מצטרף?"

     b. **הצעה לאירוע השבוע** — שתשפיע על תכנון:
        - "מתכננים מפגש משחקי קופסה בסוף שבוע הבא — שישי ערב או שבת ערב? תצביעו 🍕"
        - "פתחו אישתאלון: יום בשבוע + שעה הכי טובים למפגש קבוע. ניצור פולס בערב."

     c. **פולס מהיר עם 2-4 אופציות** — שים ב-regular_slots עם type="discussion" (יוצג כפולס בצד הבוט בערוץ הנכון):
        - "טוב או רע: ההחלטה של נטפליקס לעשות עוד עונה לסטריינג'ר ת'ינגס? 1) טוב 2) רע 3) לא אכפת לי"

     **חובה לסמן** הזמנה אקטיבית בשדה notes_for_admin תחת "**הזמנות אקטיביות:**" עם הערה שהזמן והפרטים הם הצעה ראשונית — על המנהל לתקן/לאשר לפני שליחה.

   - **לאירועים**: תזכורת לא חייבת להיות "האירוע מתחיל בעוד X דקות". יכולה להיות "🍿 הכינו פופקורן, X דקות מהמפגש" או "מי כבר בחדר ההמתנה?".

7b. **טריוויה צריכה להיות רלוונטית.** אם יש אירוע היום על משחקי לוח — שאלות trivia מאותו עולם מועדפות. אם זה שבת ושטחנו עם מוזיקה — שאלות על אלבומים. הקטגוריה חייבת להתחבר ליום, לא להיות סתם "כללי".

7c. **אמוג'י puzzles צריכות לתפוס סרטים/סדרות שהקהילה מכירה ובסבירות גבוהה זוכרת.** עדיף להישאר עם סרטים מ-90s/2000s מוכרים, סדרות נטפליקס פופולריות, מאשר נישות אינדי.

8. Trivia dedup. אל תייצר שאלה זהה או כמעט זהה ל-existing_trivia_samples. אל תייצר batch עבור category שכבר יש בה ≥3 שאלות ב-existing_trivia_categories.

9. Emoji dedup. אל תייצר חידה שהתשובה בעברית/אנגלית שלה כבר ב-existing_emoji_answers_sample.

10. דו-משמעות → needs_review=true. אם יש זמנים סותרים ללא "זז ל-X" ברור — אל תנחש. החזר את התזכורת עם needs_review=true והסבר ב-notes.

11. טקסט משתמש (text/canonical_title/reminder_text) — עברית בלבד. ללא markdown, ללא backticks, ללא IDs פנימיים, ללא אנגלית טכנית.

12. notes_for_admin — מנוסח כ-Markdown לקריאה נוחה. הקפד על המבנה הבא (השמט סקציה ריקה):

    **סיכום:** משפט אחד מסכם את החלטות היום.

    **אירועים:**
    - אירוע X (id N): ...

    **סלוטים רגילים:** ...
    - morning: ...
    - evening: ...

    **טריוויה:** ...

    **אמוג'י:** ...

    **דברים שדורשים תשומת לב:**
    - ...

    אסור: HTML גולמי, קישורים מלאים, יותר מ-300 מילים. מותר: עברית/אנגלית מעורב, **bold**, רשימות עם `- ` בתחילת השורה, פסקאות מופרדות בשורה ריקה.

חשוב: השתמש אך ורק בכלי today_plan. אל תחזיר טקסט חופשי."""


def _today_plan_tool_schema() -> dict:
    """JSON-schema for the tool the AI must call. Guarantees parseable typed
    output. `additionalProperties: false` + full `required` arrays are needed
    by OpenAI structured outputs (codex --output-schema); Anthropic tool_use
    also accepts this shape."""
    return {
        "name": "today_plan",
        "description": "Submit the structured plan for today — reminders for canonical events, scheduled regular slots, new trivia/emoji pool entries, and skipped-section reasons.",
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "notes_for_admin": {"type": "string", "description": "Free-form admin-facing summary of the decisions. May mix Hebrew/English."},
                "reminders": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "covered_event_ids": {"type": "array", "items": {"type": "integer"}, "description": "All events.id rows that merged into this canonical event."},
                            "canonical_title": {"type": "string"},
                            "actual_event_time": {"type": "string", "description": "HH:MM — the REAL event start time per the text."},
                            "reminder_scheduled_time": {"type": "string", "description": "HH:MM — when the reminder posts (event_time − lead minutes, clamped)."},
                            "topic_id": {"type": "integer", "description": "Must be in verified_topic_ids."},
                            "text": {"type": "string", "description": "Hebrew reminder text, 1-2 lines, opens with emoji."},
                            "needs_review": {"type": "boolean"},
                            "notes": {"type": "string"},
                        },
                        "required": ["covered_event_ids", "canonical_title", "actual_event_time", "reminder_scheduled_time", "topic_id", "text", "needs_review", "notes"],
                    },
                },
                "regular_slots": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "type": {"type": "string", "enum": ["morning", "evening", "discussion"]},
                            "scheduled_time": {"type": "string", "description": "HH:MM from schedule config."},
                            "topic_id": {"type": "integer"},
                            "text": {"type": "string", "description": "Hebrew. Morning/evening = 1-2 lines with emoji. Discussion = 1 question."},
                            "category": {"type": "string", "description": "Discussion category key (e.g., 'movies') — empty string for morning/evening."},
                        },
                        "required": ["type", "scheduled_time", "topic_id", "text", "category"],
                    },
                },
                "trivia_questions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string"},
                            "options": {"type": "array", "items": {"type": "string"}, "minItems": 4, "maxItems": 4},
                            "correct": {"type": "integer", "minimum": 0, "maximum": 3},
                            "category": {"type": "string"},
                        },
                        "required": ["text", "options", "correct", "category"],
                    },
                },
                "emoji_puzzles": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "emoji_prompt": {"type": "string", "description": "3-6 emoji representing the answer."},
                            "answer_he": {"type": "string"},
                            "answer_en": {"type": "string"},
                            "aliases": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["emoji_prompt", "answer_he", "answer_en", "aliases"],
                    },
                },
                "skipped": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "reminders": {"type": ["string", "null"]},
                        "morning": {"type": ["string", "null"]},
                        "evening": {"type": ["string", "null"]},
                        "discussion": {"type": ["string", "null"]},
                        "trivia": {"type": ["string", "null"]},
                        "emoji": {"type": ["string", "null"]},
                    },
                    "required": ["reminders", "morning", "evening", "discussion", "trivia", "emoji"],
                },
            },
            "required": ["notes_for_admin", "reminders", "regular_slots", "trivia_questions", "emoji_puzzles", "skipped"],
        },
    }


async def _build_today_bundle(db: Database, today, sunday, saturday, settings: dict) -> dict:
    """Assemble the 11-source context for the digest. Only includes what's
    actually available in the DB (no chat history capture exists)."""
    today_iso = today.isoformat()
    hebrew_day = (today.weekday() + 1) % 7

    # Events today (full records — title AND description are needed for rescheduling clues)
    all_upcoming = await db.get_upcoming_events(50)
    events_today_raw = [e for e in all_upcoming if e.get("event_date") == today_iso]
    events_today = [
        {
            "id": e.get("id"),
            "title": (e.get("title") or "").strip(),
            "description": (e.get("description") or "").strip(),
            "event_time": e.get("event_time"),
            "topic_id": e.get("topic_id"),
            "active": bool(e.get("active", 1)),
        }
        for e in events_today_raw
    ]

    # All scheduled_messages today (non-cancelled) — full text because reschedule clues live there
    all_scheduled_week = await db.get_scheduled_messages(sunday.isoformat(), saturday.isoformat())
    scheduled_today = [
        {
            "id": m.get("id"),
            "text": (m.get("text") or "").strip(),
            "message_type": m.get("message_type"),
            "scheduled_time": (m.get("scheduled_time") or "")[:5],
            "topic_id": m.get("channel_topic_id"),
            "created_by": m.get("created_by"),
            "status": m.get("status"),
        }
        for m in all_scheduled_week
        if m.get("scheduled_date") == today_iso and m.get("status") != "cancelled"
    ]

    # This week's other-days scheduled messages (previews only — for thematic dedup)
    this_week_previews = []
    for m in all_scheduled_week:
        if m.get("status") == "cancelled" or m.get("scheduled_date") == today_iso:
            continue
        preview = (m.get("text") or "").strip().replace("\n", " ")[:80]
        this_week_previews.append({
            "date": m.get("scheduled_date"),
            "type": m.get("message_type"),
            "topic_id": m.get("channel_topic_id"),
            "preview": preview,
        })

    # Recent SENT messages — gives the AI a feel for the community's actual
    # voice and which content types/topics resonate. Last 21 days, capped at
    # 25 per type-bucket so trivia/digest spam doesn't crowd out morning/evening.
    recent_sent_by_type: dict[str, list[dict]] = {}
    try:
        from datetime import timedelta as _td
        twenty_one_days_ago = (today - _td(days=21)).isoformat()
        async with db._db.execute(
            """SELECT id, scheduled_date, scheduled_time, message_type,
                      channel_topic_id, text
               FROM scheduled_messages
               WHERE status = 'sent'
                 AND scheduled_date >= ? AND scheduled_date < ?
               ORDER BY scheduled_date DESC, scheduled_time DESC
               LIMIT 200""",
            (twenty_one_days_ago, today_iso),
        ) as cur:
            for row in await cur.fetchall():
                mt = (row["message_type"] or "custom") or "custom"
                bucket = recent_sent_by_type.setdefault(mt, [])
                if len(bucket) >= 8:  # at most 8 examples per message_type
                    continue
                txt = (row["text"] or "").strip().replace("\n", " ")
                if not txt:
                    continue
                bucket.append({
                    "date": row["scheduled_date"],
                    "topic_id": row["channel_topic_id"],
                    "text": txt[:140],
                })
    except Exception as e:
        logger.warning("[ai-fill-today] failed to load recent sent samples: %s", e)
        recent_sent_by_type = {}

    # Existing today drafts (idempotence signal)
    existing_drafts_today = [
        {
            "id": m.get("id"),
            "created_by": m.get("created_by"),
            "message_type": m.get("message_type"),
            "scheduled_time": (m.get("scheduled_time") or "")[:5],
            "text_preview": (m.get("text") or "").strip()[:60],
        }
        for m in scheduled_today
        if (m.get("created_by") or "").startswith("ai-fill-today")
    ]

    # Verified topics map (id → hebrew name)
    verified_rows = await db.get_verified_forum_topics()
    verified_topic_ids = []
    verified_topic_names = {}
    for v in verified_rows:
        try:
            tid = int(v.get("topic_id"))
        except (TypeError, ValueError):
            continue
        name = str(v.get("verified_name") or "").strip()
        if tid > 0 and name:
            verified_topic_ids.append(tid)
            verified_topic_names[tid] = name

    # Events-publish fallback topic
    events_fallback = None
    try:
        rt = await db.get_handler_routing("events_publish")
        if rt:
            events_fallback = rt.get("play_topic_id")
    except Exception:
        events_fallback = None

    # Existing trivia categories + samples
    try:
        trivia_pool = (load_yaml("trivia.yaml") or {}).get("questions") or []
    except Exception:
        trivia_pool = []
    categories_count: dict[str, int] = {}
    samples_by_category: dict[str, str] = {}
    for q in trivia_pool:
        cat = (q.get("category") or "").strip()
        if not cat:
            continue
        categories_count[cat] = categories_count.get(cat, 0) + 1
        if cat not in samples_by_category:
            samples_by_category[cat] = (q.get("text") or "")[:80]
    existing_trivia = [
        {"category": cat, "count": cnt, "sample": samples_by_category.get(cat, "")}
        for cat, cnt in sorted(categories_count.items(), key=lambda x: -x[1])
    ]

    # Existing emoji answers
    try:
        existing_puzzles = await db.list_emoji_puzzles()
        existing_emoji_answers = [
            {"he": p.get("answer_he"), "en": p.get("answer_en")}
            for p in existing_puzzles[-40:]  # last 40 most recent
        ]
    except Exception:
        existing_emoji_answers = []

    # Schedule config snapshot
    schedule = settings.get("schedule", {})
    schedule_snapshot = {
        key: schedule.get(key, {})
        for key in ("morning_prompt", "evening_prompt", "discussion_prompt", "trivia", "emoji_puzzle")
    }

    # Discussion categories available today
    topics_discussions = settings.get("topics", {}).get("discussions", {})
    try:
        discussions_pool = load_yaml("discussions.yaml") or {}
    except Exception:
        discussions_pool = {}
    active_discussion_categories = [
        {"key": c, "topic_id": topics_discussions.get(c)}
        for c in discussions_pool if c in topics_discussions and topics_discussions[c]
    ]

    # Holiday context (even non-blocking holidays are informative)
    holiday = get_holiday_blackout(today_iso)

    # Current Israel time — so AI knows what's already past today
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo as _Zi
    _now_il = _dt.now(_Zi("Asia/Jerusalem"))
    now_hhmm = _now_il.strftime("%H:%M")
    return {
        "today": today_iso,
        "hebrew_day_name": _HEBREW_DAY_NAMES[hebrew_day],
        "hebrew_day_num": hebrew_day,
        "now_time_il": now_hhmm,  # current local time, AI must skip slot times that already passed
        "event_reminder_lead_minutes": int(settings.get("event_reminder_lead_minutes", 10) or 10),
        "holiday": holiday,  # null when no holiday
        "events_today": events_today,
        "scheduled_messages_today": scheduled_today,
        "existing_drafts_today": existing_drafts_today,
        "this_week_previews": this_week_previews,
        "recent_sent_samples_by_type": recent_sent_by_type,
        "verified_topic_ids": verified_topic_ids,
        "verified_topic_names": {str(k): v for k, v in verified_topic_names.items()},
        "events_publish_fallback_topic": events_fallback,
        "existing_trivia_categories": existing_trivia[:30],
        "existing_emoji_answers_sample": existing_emoji_answers,
        "schedule": schedule_snapshot,
        "active_discussion_categories": active_discussion_categories,
        "goals_topic_id": settings.get("topics", {}).get("goals"),
    }


def _strip_json_fences(raw: str) -> str:
    """Strip optional ```json ... ``` wrapping that Claude sometimes adds."""
    import re
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(?:json)?\s*\n?", "", txt, count=1)
        txt = re.sub(r"\n?```\s*$", "", txt, count=1)
    return txt.strip()


def _build_cli_digest_prompt(bundle: dict) -> str:
    """System + bundle + schema rolled into one prompt for CLI transports."""
    return (
        DIGEST_SYSTEM_PROMPT
        + "\n\n---\n\nקונטקסט היום (JSON):\n```json\n"
        + json.dumps(bundle, ensure_ascii=False, indent=2)
        + "\n```\n\n"
        "החזר אך ורק JSON חוקי שתואם את הסכמה של today_plan.\n"
        "הפלט חייב להיות בלוק ```json``` בלבד, ללא הקדמה, ללא הסבר, ללא טקסט אחר.\n\n"
        "סכמה:\n```json\n"
        + json.dumps(_today_plan_tool_schema()["input_schema"], ensure_ascii=False, indent=2)
        + "\n```"
    )


def _parse_digest_json(raw: str, transport: str) -> dict:
    """Parse a CLI JSON response with fence stripping + regex rescue."""
    cleaned = _strip_json_fences(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        import re
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if not match:
            raise RuntimeError(f"{transport}: non-JSON output (first 300 chars): {raw[:300]}")
        return json.loads(match.group(0))


async def _generate_today_plan_via_claude_cli(bundle: dict) -> tuple[dict, dict]:
    """Primary: Claude Code CLI. Uses the claude auth already logged in on
    the host — no API key needed.

    Direct invocation (not the shared `_generate_via_cli`) so we can capture
    BOTH stdout and stderr on failure. The claude CLI writes rate-limit and
    auth errors to stdout with rc=1 — we need stdout in the log to diagnose.
    """
    import shutil
    import pwd as _pwd

    claude_bin = shutil.which("claude") or "/usr/bin/claude"
    try:
        real_home = _pwd.getpwuid(os.geteuid()).pw_dir
    except Exception:
        real_home = os.path.expanduser("~")

    prompt = _build_cli_digest_prompt(bundle)
    logger.info(
        "[ai-fill-today] digest via claude CLI: bin=%s euid=%d HOME=%s prompt_chars=%d",
        claude_bin, os.geteuid(), real_home, len(prompt),
    )

    env = {**os.environ, "HOME": real_home}
    proc = await asyncio.create_subprocess_exec(
        claude_bin, "-p", prompt, "--model", "sonnet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=240)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("claude CLI timed out after 240s")

    stdout = stdout_b.decode(errors="replace").strip()
    stderr = stderr_b.decode(errors="replace").strip()
    logger.info(
        "[ai-fill-today] claude CLI rc=%d stdout_chars=%d stderr_chars=%d",
        proc.returncode, len(stdout), len(stderr),
    )
    if proc.returncode != 0:
        # Capture BOTH streams verbatim — rate-limit / auth errors land on stdout.
        raise RuntimeError(
            f"claude CLI rc={proc.returncode} "
            f"stdout={stdout[:400]!r} stderr={stderr[:400]!r}"
        )
    if not stdout:
        raise RuntimeError(f"claude CLI empty stdout, stderr={stderr[:400]!r}")
    return _parse_digest_json(stdout, "claude-cli"), {"transport": "claude-cli"}


def _codex_binary_path() -> str | None:
    """Resolve the codex binary, preferring the botson user's npm-global install
    on VPS (/opt/robotnik/.npm-global/bin/codex) before falling back to PATH."""
    import shutil
    import pwd as _pwd
    try:
        real_home = _pwd.getpwuid(os.geteuid()).pw_dir
    except Exception:
        real_home = os.path.expanduser("~")
    candidate = os.path.join(real_home, ".npm-global", "bin", "codex")
    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which("codex")


async def _generate_today_plan_via_codex_cli(bundle: dict) -> tuple[dict, dict]:
    """Fallback: Codex CLI (`codex exec --output-schema SCHEMA PROMPT`).
    Requires `codex` binary + a prior `codex login` (device-auth or browser).

    Uses `--output-schema` to constrain output to valid JSON at the model
    layer — no fence stripping needed for the happy path. Still falls back
    to regex rescue for edge cases (network hiccups, warnings leaking into
    stdout).
    """
    import pwd as _pwd
    import tempfile

    codex_bin = _codex_binary_path()
    if not codex_bin:
        raise RuntimeError("codex CLI not installed on this host")

    prompt = _build_cli_digest_prompt(bundle)
    try:
        real_home = _pwd.getpwuid(os.geteuid()).pw_dir
    except Exception:
        real_home = os.path.expanduser("~")

    logger.info(
        "[ai-fill-today] digest via codex CLI: bin=%s HOME=%s prompt_chars=%d",
        codex_bin, real_home, len(prompt),
    )

    # Write the schema to a temp file — codex --output-schema reads from disk
    schema_fd, schema_path = tempfile.mkstemp(suffix=".json", prefix="today-plan-schema-")
    try:
        with os.fdopen(schema_fd, "w", encoding="utf-8") as f:
            json.dump(_today_plan_tool_schema()["input_schema"], f, ensure_ascii=False)

        env = {**os.environ, "HOME": real_home}
        # Pass long Hebrew prompts via stdin (`-`) to avoid ARG_MAX issues;
        # codex reads stdin when the prompt arg is `-` or missing.
        proc = await asyncio.create_subprocess_exec(
            codex_bin, "exec",
            "--skip-git-repo-check",           # runs fine outside a git repo
            "--ignore-user-config",            # avoid AGENTS.md permission errors
            "--ignore-rules",                  # same
            "--output-schema", schema_path,    # constrain output to our JSON schema
            "-",                               # read prompt from stdin
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=300,
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError("codex CLI timed out after 300s")
    finally:
        try:
            os.unlink(schema_path)
        except OSError:
            pass

    stdout = stdout_b.decode(errors="replace").strip()
    stderr = stderr_b.decode(errors="replace").strip()
    logger.info(
        "[ai-fill-today] codex CLI rc=%d stdout_chars=%d stderr_chars=%d",
        proc.returncode, len(stdout), len(stderr),
    )

    if proc.returncode != 0:
        raise RuntimeError(
            f"codex CLI rc={proc.returncode} "
            f"stdout={stdout[:400]!r} stderr={stderr[:400]!r}"
        )
    if not stdout:
        raise RuntimeError(f"codex CLI empty stdout, stderr={stderr[:400]!r}")

    # Codex transcript wraps the JSON with session/user/codex blocks; grab the
    # last standalone JSON object (which is duplicated after "tokens used").
    import re
    matches = re.findall(r"(\{(?:[^{}]|(?:\{[^{}]*\}))*\})", stdout)
    candidates = []
    for m in matches:
        try:
            candidates.append(json.loads(m))
        except json.JSONDecodeError:
            pass
    # Pick the largest parseable dict — that's the real payload, not a
    # fragment from some session header.
    if not candidates:
        raise RuntimeError(f"codex CLI: no parseable JSON in output (first 400 chars): {stdout[:400]}")
    candidates.sort(key=lambda d: len(json.dumps(d)), reverse=True)
    return candidates[0], {"transport": "codex-cli"}


async def _generate_today_plan_via_ollama(bundle: dict) -> tuple[dict, dict]:
    """Tier-3 fallback: local Ollama endpoint (default localhost:11434).
    Works only when the dashboard runs on the same host as Ollama — i.e.
    local dev. On VPS this transport gets a connection refused and falls
    through cleanly.

    Uses Ollama's `format: "json"` to constrain output to valid JSON —
    no fence stripping needed.

    Env overrides: OLLAMA_URL (default http://localhost:11434),
    OLLAMA_MODEL (default gemma4:latest).
    """
    import httpx
    url = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    model = os.getenv("OLLAMA_MODEL", "gemma4:latest")
    prompt = _build_cli_digest_prompt(bundle)
    logger.info("[ai-fill-today] digest via ollama: model=%s url=%s prompt_chars=%d",
                model, url, len(prompt))

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.7, "num_predict": 4096},
    }
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(f"{url}/api/generate", json=payload)
    except httpx.ConnectError as e:
        raise RuntimeError(f"ollama not reachable at {url}: {e}") from e

    if resp.status_code != 200:
        raise RuntimeError(f"ollama rc={resp.status_code} body={resp.text[:400]}")
    data = resp.json()
    raw = (data.get("response") or "").strip()
    logger.info("[ai-fill-today] ollama returned chars=%d", len(raw))
    if not raw:
        raise RuntimeError(f"ollama empty response: {data}")
    return _parse_digest_json(raw, "ollama"), {"transport": "ollama", "model": model}


async def _generate_today_plan(bundle: dict) -> tuple[dict, dict]:
    """Transport chain:
      1. Claude Code CLI (preferred — same auth as every other endpoint)
      2. Codex CLI (fallback — works if `codex` is installed + logged in)
      3. Ollama / local Gemma (last resort — only if running on same host)

    Every attempt is logged at INFO so `journalctl -u botson-dashboard`
    shows the exact path taken.
    """
    errors = []

    for transport_name, fn in (
        ("claude-cli", _generate_today_plan_via_claude_cli),
        ("codex-cli", _generate_today_plan_via_codex_cli),
        ("ollama", _generate_today_plan_via_ollama),
    ):
        try:
            plan, usage = await fn(bundle)
            logger.info("[ai-fill-today] digest OK via %s", transport_name)
            return plan, usage
        except Exception as e:
            errors.append(f"{transport_name}: {e}")
            logger.warning("[ai-fill-today] %s failed: %s", transport_name, e)

    logger.error("[ai-fill-today] all transports failed: %s", " | ".join(errors))
    raise RuntimeError("digest failed on all transports: " + " | ".join(errors))


@app.post("/api/weekplan/ai-fill-today")
async def ai_fill_today(request: Request, db: Database = Depends(get_db)):
    """Fill only today's empty slots + one reminder per event today, with
    group-wide context (events, holiday, week's committed messages) in the
    prompt. Idempotent via created_by tagging.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    from datetime import date, timedelta
    today = date.today()
    today_iso = today.isoformat()
    hebrew_day = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=hebrew_day)
    saturday = sunday + timedelta(days=6)

    settings = get_settings()

    # Holiday short-circuit — block_auto=true means "manual content only today"
    if is_auto_blocked_on(today):
        return {
            "skipped_holiday": True,
            "holiday": get_holiday_blackout(today_iso),
            "reminders": [], "regular_slots": [], "trivia": {"generated": 0, "skipped": "holiday"},
            "emoji": {"generated": 0, "skipped": "holiday"},
            "notes_for_admin": "", "errors": [],
        }

    # Assemble the full context bundle and run the digest
    bundle = await _build_today_bundle(db, today, sunday, saturday, settings)
    logger.info(
        "[ai-fill-today] bundle: events=%d scheduled=%d drafts_existing=%d week_previews=%d verified_topics=%d trivia_cats=%d emoji_pool=%d",
        len(bundle["events_today"]), len(bundle["scheduled_messages_today"]),
        len(bundle["existing_drafts_today"]), len(bundle["this_week_previews"]),
        len(bundle["verified_topic_ids"]), len(bundle["existing_trivia_categories"]),
        len(bundle["existing_emoji_answers_sample"]),
    )
    errors: list[str] = []
    try:
        plan, usage = await _generate_today_plan(bundle)
    except Exception as e:
        # Already logged with traceback inside _generate_today_plan
        errors.append(f"digest: {e}")
        return {
            "skipped_holiday": False,
            "reminders": [], "regular_slots": [], "trivia": {"generated": 0, "skipped": "digest_failed"},
            "emoji": {"generated": 0, "skipped": "digest_failed"},
            "notes_for_admin": "", "errors": errors,
        }

    # ── Server-side validation + persistence ────────────────────────────────
    verified_topic_ids = set(bundle["verified_topic_ids"])
    existing_today_event_sets: list[set[int]] = []
    for d in bundle["existing_drafts_today"]:
        cb = d.get("created_by", "") or ""
        # Parse "ai-fill-today:events:9,10,11" → {9, 10, 11}
        if cb.startswith("ai-fill-today:events:"):
            try:
                existing_today_event_sets.append({int(x) for x in cb.split(":", 2)[2].split(",") if x.strip()})
            except (ValueError, IndexError):
                pass

    def _valid_hhmm(s: str) -> bool:
        try:
            h, m = s.split(":")
            return 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except (ValueError, AttributeError):
            return False

    reminders_out: list[dict] = []
    regular_out: list[dict] = []
    trivia_added = 0
    emoji_added = 0

    # ── Reminders ──────────────────────────────────────────────────────────
    for rem in plan.get("reminders") or []:
        covered = sorted({int(x) for x in (rem.get("covered_event_ids") or []) if isinstance(x, int)})
        rem_time = (rem.get("reminder_scheduled_time") or "").strip()
        topic = rem.get("topic_id")
        text = (rem.get("text") or "").strip()
        if not covered or not _valid_hhmm(rem_time) or not text:
            errors.append(f"reminder rejected (incomplete): {rem}")
            continue
        if topic not in verified_topic_ids:
            errors.append(f"reminder rejected (unverified topic_id={topic}): {rem.get('canonical_title')}")
            continue
        # Idempotence: skip if any existing draft already covers overlapping events
        covered_set = set(covered)
        if any(covered_set & existing for existing in existing_today_event_sets):
            continue
        marker = "ai-fill-today:events:" + ",".join(str(x) for x in covered)
        try:
            new_id = await db.create_scheduled_message(
                text=text, message_type="custom",
                channel_topic_id=int(topic),
                target_group="main",
                scheduled_date=today_iso, scheduled_time=rem_time,
                created_by=marker, status="draft",
            )
            existing_today_event_sets.append(covered_set)
            reminders_out.append({
                "id": new_id, "covered_event_ids": covered,
                "canonical_title": rem.get("canonical_title"),
                "actual_event_time": rem.get("actual_event_time"),
                "reminder_scheduled_time": rem_time,
                "topic_id": int(topic),
                "needs_review": bool(rem.get("needs_review")),
                "notes": rem.get("notes") or "",
            })
            logger.info("[ai-fill-today] draft reminder id=%d covers=%s at %s", new_id, covered, rem_time)
        except Exception as e:
            errors.append(f"reminder insert {covered}: {e}")

    # ── Regular slots (morning/evening/discussion) ─────────────────────────
    existing_slot_keys = {
        (d.get("scheduled_time"), d.get("message_type"))
        for d in bundle["existing_drafts_today"]
    }
    for slot in plan.get("regular_slots") or []:
        mtype = (slot.get("type") or "").strip()
        stime = (slot.get("scheduled_time") or "").strip()
        topic = slot.get("topic_id")
        text = (slot.get("text") or "").strip()
        if mtype not in ("morning", "evening", "discussion"):
            errors.append(f"slot rejected (bad type): {slot}")
            continue
        if not _valid_hhmm(stime) or not text:
            errors.append(f"slot rejected (incomplete): {slot}")
            continue
        if topic not in verified_topic_ids:
            errors.append(f"slot rejected (unverified topic_id={topic}): {mtype}")
            continue
        if (stime, mtype) in existing_slot_keys:
            continue
        try:
            new_id = await db.create_scheduled_message(
                text=text, message_type=mtype,
                channel_topic_id=int(topic),
                target_group="main",
                scheduled_date=today_iso, scheduled_time=stime,
                created_by="ai-fill-today", status="draft",
            )
            existing_slot_keys.add((stime, mtype))
            regular_out.append({"id": new_id, "type": mtype, "scheduled_time": stime, "topic_id": int(topic)})
            logger.info("[ai-fill-today] draft %s id=%d at %s", mtype, new_id, stime)
        except Exception as e:
            errors.append(f"{mtype} insert: {e}")

    # ── Trivia (append to trivia.yaml) ─────────────────────────────────────
    trivia_questions = plan.get("trivia_questions") or []
    if trivia_questions:
        try:
            existing_trivia = (load_yaml("trivia.yaml") or {}).get("questions") or []
            # Basic sanity check per question before merging
            valid_q = []
            for q in trivia_questions:
                opts = q.get("options") or []
                correct = q.get("correct")
                if (isinstance(opts, list) and len(opts) == 4
                        and all(isinstance(o, str) and o.strip() for o in opts)
                        and isinstance(correct, int) and 0 <= correct <= 3
                        and (q.get("text") or "").strip()):
                    valid_q.append({
                        "text": q["text"].strip(),
                        "options": [o.strip() for o in opts],
                        "correct": correct,
                        "category": (q.get("category") or "כללי").strip() or "כללי",
                        # Provenance — consumed by today-summary + future audits.
                        "added_at": today_iso,
                        "source": "ai-fill-today",
                    })
                else:
                    errors.append(f"trivia question rejected: {q}")
            if valid_q:
                merged = existing_trivia + valid_q
                save_and_verify_trivia_questions(CONFIG_DIR / "trivia.yaml", merged)
                trivia_added = len(valid_q)
                logger.info("[ai-fill-today] trivia appended %d question(s)", trivia_added)
        except TriviaVerificationError as e:
            errors.append(f"trivia save: {e}")
        except Exception as e:
            errors.append(f"trivia: {e}")

    # ── Emoji puzzles (insert into emoji_puzzles table) ────────────────────
    for p in plan.get("emoji_puzzles") or []:
        emoji_prompt_val = (p.get("emoji_prompt") or "").strip()
        answer_he = (p.get("answer_he") or "").strip()
        answer_en = (p.get("answer_en") or "").strip()
        aliases = p.get("aliases") or []
        if not emoji_prompt_val or not answer_he or not answer_en:
            errors.append(f"emoji rejected (incomplete): {p}")
            continue
        try:
            new_id = await db.create_emoji_puzzle(
                emoji_prompt=emoji_prompt_val,
                answer_he=answer_he,
                answer_en=answer_en,
                aliases=json.dumps([a for a in aliases if isinstance(a, str) and a.strip()], ensure_ascii=False),
                difficulty=2,
                media_type="movie",
            )
            emoji_added += 1
            logger.info("[ai-fill-today] emoji puzzle id=%d: %s -> %s", new_id, emoji_prompt_val, answer_he)
        except Exception as e:
            errors.append(f"emoji insert '{answer_he}': {e}")

    skipped = plan.get("skipped") or {}
    return {
        "skipped_holiday": False,
        "reminders": reminders_out,
        "regular_slots": regular_out,
        "trivia": {"generated": trivia_added, "skipped": skipped.get("trivia")},
        "emoji": {"generated": emoji_added, "skipped": skipped.get("emoji")},
        "skipped": skipped,
        "notes_for_admin": plan.get("notes_for_admin") or "",
        "errors": errors,
    }


# ── approve endpoints removed 2026-04-24 ──
# The draft→scheduled approve path was a design flaw: it handed content
# to the 60s autonomous calendar_checker, which would auto-send any
# past-due row. Replaced with explicit admin-initiated send-now:
#   • per-draft     → POST /api/calendar/{id}/send-now
#   • bulk-for-today → POST /api/weekplan/send-today-drafts-now
# Both post synchronously to Telegram; drafts never become 'scheduled'.


@app.get("/api/weekplan/today-summary")
async def today_summary(request: Request, db: Database = Depends(get_db)):
    """Planner banner counts. Now uses precise provenance fields rather than
    heuristics: trivia questions tagged with added_at/source by the digest
    path, and emoji puzzles counted by date(created_at) (only ai-fill-today
    writes to that table from the dashboard — no manual-add UI exists)."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    from datetime import date
    today_iso = date.today().isoformat()

    # Drafts waiting for admin approval
    async with db._db.execute(
        """SELECT COUNT(*) FROM scheduled_messages
           WHERE scheduled_date = ? AND status = 'draft'
             AND created_by LIKE 'ai-fill-today%'""",
        (today_iso,),
    ) as cur:
        row = await cur.fetchone()
        draft_count = int(row[0]) if row else 0

    # Emoji puzzles added today (+ preview answers for the banner)
    emoji_today_count = 0
    emoji_today_samples: list[dict] = []
    async with db._db.execute(
        """SELECT id, emoji_prompt, answer_he, answer_en
           FROM emoji_puzzles WHERE date(created_at) = ?
           ORDER BY id DESC LIMIT 50""",
        (today_iso,),
    ) as cur:
        rows = await cur.fetchall()
        emoji_today_count = len(rows)
        for r in rows[:5]:   # top 5 for the banner preview
            emoji_today_samples.append({
                "id": r["id"],
                "emoji_prompt": r["emoji_prompt"],
                "answer_he": r["answer_he"],
                "answer_en": r["answer_en"],
            })

    # Trivia questions added today by the digest (provenance-tagged)
    trivia_today_count = 0
    trivia_today_categories: dict[str, int] = {}
    try:
        pool = (load_yaml("trivia.yaml") or {}).get("questions") or []
        for q in pool:
            if (q.get("added_at") or "").strip() == today_iso and (q.get("source") or "") == "ai-fill-today":
                trivia_today_count += 1
                cat = (q.get("category") or "כללי").strip() or "כללי"
                trivia_today_categories[cat] = trivia_today_categories.get(cat, 0) + 1
    except Exception:
        pass

    # Sort categories by count descending for the banner
    trivia_categories_list = [
        {"category": cat, "count": cnt}
        for cat, cnt in sorted(trivia_today_categories.items(), key=lambda kv: -kv[1])
    ]

    return {
        "date": today_iso,
        "drafts": draft_count,
        "trivia_added_today": trivia_today_count,
        "trivia_categories": trivia_categories_list,
        "emoji_added_today": emoji_today_count,
        "emoji_samples": emoji_today_samples,
    }


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
        holiday_block = get_holiday_blackout(day_date)
        auto_blocked = bool(holiday_block and holiday_block.get("block_auto", True))
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
            elif not auto_blocked:
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
                elif not auto_blocked:
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
            elif not auto_blocked:
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

        if (not auto_blocked) and i in schedule.get("weekly_leaderboard", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "levels")
            activities.append({
                "time": schedule["weekly_leaderboard"].get("time", "18:00"),
                "type": "leaderboard", "label": "לידרבורד",
                "desc": "טבלת מובילים שבועית",
                "full_text": "", "pool": "", "pool_idx": -1,
                "channel": "", "enabled": enabled
            })

        if (not auto_blocked) and i in schedule.get("weekly_roundup", {}).get("days", []):
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
            "activities": activities,
            "holiday_block": holiday_block,
            "auto_blocked": auto_blocked,
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
PENDING_REVIEWS_CLEARED_FLAG = Path(__file__).parent.parent / "data" / ".pending_reviews_cleared"
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


def _ensure_special_pending_reviews(items):
    wanted_items = [
        {
            "id": "trivia-israel-announce-2026-04-22",
            "title": "🇮🇱 תזכורת — טריוויה ליום העצמאות",
            "channel": "כללי (General)",
            "when": "רביעי 14:45",
            "preview": "🇮🇱 היום ב-15:00 — טריוויה מיוחדת ליום העצמאות\n\n10 שאלות מהירות על ישראל: היסטוריה, ספרות, קולנוע, מוזיקה ותרבות.\n\nתשובה נכונה = 12 נק׳ · מקום ראשון = +20 בונוס 🏆\n\nהשאלה הראשונה יורדת ב-15:00 בדיוק — תהיו כאן.",
            "note": "טיוטת הכרזה לפני סיבוב הטריוויה המתוזמן של מחר. אישור כאן ייצור טיוטת planner שאפשר לשלוח ממנו.",
        },
        {
            "id": "trivia-israel-update-2026-04-22-1605",
            "title": "🇮🇱 עדכון — טריוויה היום ב-16:15",
            "channel": "ברוכים הבאים! מידע למצטרפים חדשים (341)",
            "when": "רביעי 16:05",
            "preview": "🇮🇱 היום ב-16:15 — טריוויה מיוחדת על ישראל\n\n10 שאלות מהירות על ישראל: היסטוריה, ספרות, קולנוע, מוזיקה ותרבות.\n\nתשובה נכונה = 12 נק׳ · מקום ראשון = +20 בונוס 🏆\n\nהשאלה הראשונה יורדת ב-16:15 בדיוק — תהיו כאן.",
            "note": "טיוטת עדכון ל-topic 341 לפני סיבוב הטריוויה. אישור כאן ייצור טיוטת planner שאפשר לשלוח ממנו אחרי deploy.",
        },
    ]
    existing_ids = {item.get("id") for item in items}
    missing = [item for item in wanted_items if item["id"] not in existing_ids]
    return items + missing


def _load_pending_reviews():
    if PENDING_REVIEWS_CLEARED_FLAG.exists():
        if PENDING_REVIEWS_PATH.exists():
            try:
                items = json.loads(PENDING_REVIEWS_PATH.read_text(encoding="utf-8"))
                return items if isinstance(items, list) else []
            except Exception:
                logger.exception("[review] failed to read cleared %s — returning empty queue", PENDING_REVIEWS_PATH)
                return []
        _save_pending_reviews([])
        return []

    if PENDING_REVIEWS_PATH.exists():
        try:
            items = json.loads(PENDING_REVIEWS_PATH.read_text(encoding="utf-8"))
            return _ensure_special_pending_reviews(_ensure_emoji_puzzle_reviews(items))
        except Exception:
            logger.exception("[review] failed to read %s — reseeding", PENDING_REVIEWS_PATH)
    items = _default_pending_reviews()
    _save_pending_reviews(items)
    return _ensure_special_pending_reviews(_ensure_emoji_puzzle_reviews(items))


def _save_pending_reviews(items):
    PENDING_REVIEWS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEWS_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _clear_all_pending_reviews():
    PENDING_REVIEWS_CLEARED_FLAG.parent.mkdir(parents=True, exist_ok=True)
    PENDING_REVIEWS_CLEARED_FLAG.write_text("cleared\n", encoding="utf-8")
    _save_pending_reviews([])


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


@app.post("/api/review/clear-all")
async def review_clear_all(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    items = _load_pending_reviews()
    cleared = len(items)
    _clear_all_pending_reviews()
    logger.info("[review] cleared all pending items (count=%d)", cleared)
    return {"ok": True, "cleared": cleared}


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

    # Match materializer behavior — exclude discussion questions that have
    # already been sent or are queued, so the calendar preview reflects what
    # the bot will actually schedule (no repeats).
    used_discussion_texts = await db.get_used_discussion_texts()

    current_sunday = first_sunday
    while current_sunday <= end_date:
        week_previews = compute_week_previews(
            current_sunday.isoformat(), committed_index, used_discussion_texts
        )
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


async def _send_scheduled_row(db: Database, msg: dict, target: str) -> int:
    """Send one scheduled_messages row to Telegram immediately.

    Handles message_type=poll vs default, respects topic_guard via safe_send
    (reached by send_message_with_optional_cover / send_poll_message), and
    marks the row status='sent' with sent_message_id when target != 'test'.

    Returns the sent Telegram message_id. Raises on error.
    """
    from telegram import Bot
    from bot.handlers.calendar import (
        send_message_with_optional_cover,
        send_poll_message,
        _parse_poll_options,
    )
    bot = Bot(os.getenv("BOT_TOKEN", ""))
    group_id = int(os.getenv("TEST_GROUP_ID", "0") if target == "test" else os.getenv("GROUP_ID", "0"))

    opts = _parse_poll_options(msg.get("poll_options"))
    if msg.get("message_type") == "poll" and len(opts) >= 2:
        sent = await send_poll_message(
            bot,
            db=db,
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
            db=db,
            chat_id=group_id,
            text=msg["text"],
            message_thread_id=msg.get("channel_topic_id"),
            cover_path=msg.get("cover_path"),
        )
    if target != "test":
        await db.mark_message_sent(msg["id"], sent.message_id)
    return sent.message_id


@app.post("/api/calendar/{msg_id}/send-now")
async def send_calendar_item_now(msg_id: int, request: Request, db: Database = Depends(get_db)):
    """Send a scheduled/draft row immediately, without touching the scheduler."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json() if request.headers.get("content-type") == "application/json" else {}
    target = data.get("target", "main")

    messages = await db.get_scheduled_messages("2000-01-01", "2099-12-31")
    msg = next((m for m in messages if m["id"] == msg_id), None)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    try:
        sent_id = await _send_scheduled_row(db, msg, target)
        logger.info("[send-now] msg_id=%d target=%s sent_message_id=%s", msg_id, target, sent_id)
        return {"status": "ok", "message_id": sent_id}
    except Exception as e:
        logger.exception("[send-now] failed for msg_id=%d", msg_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/weekplan/send-today-drafts-now")
async def send_today_drafts_now(request: Request, db: Database = Depends(get_db)):
    """Post every ai-fill-today draft for today immediately. No scheduler hop.

    Body: { target?: "main" | "test" } (default "main").
    Returns: { sent: [{id, message_id}], failed: [{id, error}] }.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    target = body.get("target", "main")

    from datetime import date
    today_iso = date.today().isoformat()
    drafts = await db.get_scheduled_messages(today_iso, today_iso)
    drafts = [
        m for m in drafts
        if m.get("status") == "draft"
        and (m.get("created_by") or "").startswith("ai-fill-today")
    ]
    drafts.sort(key=lambda m: (m.get("scheduled_time") or "", m.get("id") or 0))

    sent_list: list[dict] = []
    failed_list: list[dict] = []
    for i, msg in enumerate(drafts):
        if i > 0:
            await asyncio.sleep(2)  # Telegram rate-limit hygiene
        try:
            sent_id = await _send_scheduled_row(db, msg, target)
            sent_list.append({"id": msg["id"], "message_id": sent_id})
            logger.info("[send-today-now] msg_id=%d sent message_id=%s", msg["id"], sent_id)
        except Exception as e:
            failed_list.append({"id": msg["id"], "error": str(e)})
            logger.exception("[send-today-now] failed for msg_id=%d", msg["id"])

    return {"sent": sent_list, "failed": failed_list, "target": target}


# ── Cover image endpoints ────────────────────────────────

_COVER_MIMES = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
    "image/gif": "gif",
}

_MAX_COVER_BYTES = 8 * 1024 * 1024  # 8 MB cap


def _validated_cover_ext(data: bytes, content_type: str | None) -> str:
    mime = (content_type or "").split(";")[0].lower()
    ext = _COVER_MIMES.get(mime)
    if not ext:
        raise HTTPException(status_code=400, detail=f"Unsupported content-type {content_type}")
    if not data:
        raise HTTPException(status_code=400, detail="Image is empty")

    valid = False
    if ext == "jpg":
        valid = data.startswith(b"\xff\xd8") and data.rstrip().endswith(b"\xff\xd9")
    elif ext == "png":
        valid = data.startswith(b"\x89PNG\r\n\x1a\n") and data.rstrip().endswith(b"IEND\xaeB`\x82")
    elif ext == "gif":
        valid = data.startswith((b"GIF87a", b"GIF89a")) and data.rstrip().endswith(b";")
    elif ext == "webp":
        valid = len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"

    if not valid:
        raise HTTPException(status_code=422, detail="Image corrupt or unsupported")
    return ext


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
    data = await file.read(_MAX_COVER_BYTES + 1)
    if len(data) > _MAX_COVER_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 8MB)")
    ext = _validated_cover_ext(data, file.content_type)
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
                ext = _validated_cover_ext(data, ctype)
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
                ext = _validated_cover_ext(data, ir.headers.get("content-type"))
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

    content_type = next((mime for mime, known_ext in _COVER_MIMES.items() if known_ext == ext), None)
    ext = _validated_cover_ext(data, content_type)
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
    drafts_all = await db.get_draft_messages() if hasattr(db, 'get_draft_messages') else []
    # ai-fill-today drafts are reviewed in the dedicated modal — exclude them
    # from the legacy top-of-page inline pending list to avoid double-rendering
    # (the inline section's "אשר" button calls a removed endpoint).
    drafts = [
        d for d in drafts_all
        if not (d.get("created_by") or "").startswith("ai-fill-today")
    ]

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

    # Group channels by purpose for the create drawer picker. CLAUDE.md rule:
    # verified_forum_topics is the canonical source of truth for both *which*
    # topics are real AND for their display names — forum_topics.name can be
    # stale or polluted by user message text, so we never read from it here.
    verified_topics = await db.get_verified_forum_topics() if hasattr(db, 'get_verified_forum_topics') else []
    verified_by_id = {v["topic_id"]: v for v in verified_topics}
    goals_id = topics_cfg.get("goals")
    welcome_id = topics_cfg.get("welcome")
    mapped_ids = set(topic_ids_dict.values()) | {goals_id, welcome_id}
    mapped_ids.discard(None)
    grouped_channels = {
        "discussions": [
            {"topic_id": tid,
             "name": verified_by_id[tid]["verified_name"],
             "category": cat}
            for cat, tid in topic_ids_dict.items()
            if tid and tid in verified_by_id
        ],
        "daily": (
            [{"topic_id": goals_id, "name": verified_by_id[goals_id]["verified_name"]}]
            if goals_id and goals_id in verified_by_id else []
        ),
        "other": [
            {"topic_id": v["topic_id"], "name": v["verified_name"]}
            for v in verified_topics
            if v["topic_id"] not in mapped_ids
        ],
    }
    trivia_routing = await db.get_handler_routing("trivia_round") if hasattr(db, 'get_handler_routing') else None
    trivia_default_play = trivia_routing.get("play_topic_id") if trivia_routing else None

    # Load current trivia.yaml and render as the text-block format the AI
    # generator produces, so the questions textarea shows what the round
    # will actually use. Empty string if yaml is missing or malformed.
    trivia_current_questions_text = ""
    try:
        tdata = load_yaml("trivia.yaml") or {}
        blocks = []
        for q in (tdata.get("questions") or []):
            txt = str(q.get("text") or "").strip()
            opts = q.get("options") or []
            correct = q.get("correct")
            cat = str(q.get("category") or "כללי").strip()
            if not txt or len(opts) != 4 or not isinstance(correct, int):
                continue
            blocks.append(
                "שאלה: " + txt + "\n" +
                "תשובות: " + " | ".join(str(o).strip() for o in opts) + "\n" +
                "נכונה: " + str(int(correct)) + "\n" +
                "קטגוריה: " + cat
            )
        trivia_current_questions_text = "\n\n".join(blocks)
    except Exception:
        pass

    drafts_client_keys = (
        "id", "message_type", "text", "channel_topic_id",
        "scheduled_date", "scheduled_time", "recurrence",
        "cover_path", "poll_options", "poll_duration",
    )
    drafts_json = _json.dumps(
        [{k: d.get(k) for k in drafts_client_keys} for d in drafts],
        ensure_ascii=False,
        default=str,
    )

    return templates.TemplateResponse(request, name="planner.html", context={
        "now_date": now.strftime("%Y-%m-%d"),
        "forum_topics": forum_topics,
        "topic_names": topic_names,
        "drafts": drafts,
        "drafts_json": drafts_json,
        "schedule_pattern": schedule_pattern,
        "holiday_blackouts": _json.dumps(settings_obj.get("holiday_blackouts", []), ensure_ascii=False),
        "discussion_channels": discussion_channels,
        "grouped_channels": grouped_channels,
        "verified_topics": verified_topics,
        "trivia_default_play": trivia_default_play,
        "trivia_current_questions_text": trivia_current_questions_text,
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
