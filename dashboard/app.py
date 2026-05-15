"""Botson Dashboard — FastAPI backend for managing the bot."""

import asyncio
import copy
import json
import logging
import os
import random
import re
import secrets
import signal
import time
import html
from pathlib import Path
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import urlencode

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
from bot.utils.freshness import freshness_rejection
from bot.utils.levels import get_level, get_progress
from dashboard.trivia_admin import TriviaVerificationError, build_round_trigger_payload, review_trivia_questions, save_and_verify_trivia_questions
from dashboard.verified_topics import (
    VerifiedTopicError,
    merge_observed_and_verified_topics,
    normalize_verified_topic_entry,
)

RELOAD_FLAG = Path(__file__).parent.parent / "data" / "reload"
_TRIVIA_TOPUP_LOCKS: dict[int, asyncio.Lock] = {}


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
FACTS_IMAGES_DIR = MEDIA_DIR / "facts"
FACTS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/media", StaticFiles(directory=str(MEDIA_DIR)), name="media")

# Dashboard password from env
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "botson-admin")

# DB instance (initialized on startup)
_db: Database | None = None
_AI_SUGGEST_JOBS: dict[str, dict] = {}
_AI_SUGGEST_JOB_TTL_SECONDS = 15 * 60


def _cleanup_ai_suggest_jobs() -> None:
    cutoff = time.monotonic() - _AI_SUGGEST_JOB_TTL_SECONDS
    stale = [job_id for job_id, job in _AI_SUGGEST_JOBS.items() if float(job.get("created_at") or 0) < cutoff]
    for job_id in stale:
        _AI_SUGGEST_JOBS.pop(job_id, None)


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

    # Prompt pools are few-shot/admin reference material in strict freshness mode.
    # Reload so the bot sees config changes without auto-sending static entries.
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
    "trivia_round": {"emoji": "🧠", "label": "סיבוב טריוויה", "css": "bg-fuchsia-500/20 text-fuchsia-200 border-fuchsia-500/40"},
    "emoji_puzzle": {"emoji": "🧩", "label": "Emoji Night", "css": "bg-pink-500/20 text-pink-200 border-pink-500/40"},
    "free_games": {"emoji": "🎮", "label": "משחקים חינם", "css": "bg-sky-500/20 text-sky-200 border-sky-500/40"},
    "facts_tidbit": {"emoji": "🔎", "label": "עובדה מעניינת", "css": "bg-cyan-500/20 text-cyan-200 border-cyan-500/40"},
    "facts_spooky": {"emoji": "🕯️", "label": "סיפור מסתורי", "css": "bg-purple-500/20 text-purple-200 border-purple-500/40"},
    "weekly_roundup": {"emoji": "📊", "label": "סיכום שבועי", "css": "bg-violet-500/20 text-violet-200 border-violet-500/40"},
    "weekly_leaderboard": {"emoji": "🏆", "label": "טבלת רמות", "css": "bg-yellow-500/20 text-yellow-200 border-yellow-500/40"},
    "weekly":     {"emoji": "📊", "label": "סיכום", "css": "bg-violet-500/20 text-violet-200 border-violet-500/40"},
    "event":      {"emoji": "🎉", "label": "אירוע", "css": "bg-rose-500/20 text-rose-200 border-rose-500/40"},
    "trivia_warmup_rsvp": {"emoji": "🙋", "label": "הכרזת RSVP", "css": "bg-teal-500/20 text-teal-200 border-teal-500/40"},
    "warmup_reminder":    {"emoji": "⏰", "label": "תזכורת RSVP", "css": "bg-teal-500/20 text-teal-200 border-teal-500/40"},
}


EXECUTABLE_SCHEDULER_TYPES = (
    "trivia_round",
    "emoji_puzzle",
    "free_games",
    "facts_tidbit",
    "facts_spooky",
    "weekly_roundup",
    "weekly_leaderboard",
)

AI_REGULAR_SLOT_TYPES = (
    "morning",
    "evening",
    "discussion",
    "custom",
    *EXECUTABLE_SCHEDULER_TYPES,
)


_TRIVIA_CATEGORY_NEEDLES = (
    ("מוזיק", "מוזיקה"),
    ("סרט", "סרטים"),
    ("סדרה", "סרטים"),
    ("גיימ", "גיימינג"),
    ("ישראל", "ישראל"),
    ("מדע", "מדע"),
    ("היסטור", "היסטוריה"),
    ("גאוגר", "גאוגרפיה"),
)


def _infer_trivia_categories(text: str) -> list[str]:
    """Return the round's explicit theme as a single-element category list.

    Mirrors bot/handlers/calendar.py:_infer_trivia_categories — the same
    text must produce the same categories on both sides.
    """
    lowered = (text or "").lower()
    if not lowered:
        return []
    anchor = "סיבוב טריוויה"
    idx = lowered.find(anchor)
    window = lowered[idx + len(anchor):] if idx != -1 else lowered
    for needle, category in _TRIVIA_CATEGORY_NEEDLES:
        if needle in window:
            return [category]
    return []


def _infer_question_count(text: str, default: int = 10) -> int:
    match = re.search(r"(\d{1,2})\s*(?:שאל|חיד)", text or "")
    if not match:
        return default
    return max(1, min(20, int(match.group(1))))


def _looks_like_trivia_launch(text: str) -> bool:
    compact = (text or "").lower()
    if not ("סיבוב טריוויה" in compact or compact.startswith("🧠 טריוויה") or "trivia round" in compact):
        return False
    if "בעוד" in compact or "תזכורת" in compact or "מתחממים" in compact:
        return False
    return True


def _looks_like_emoji_launch(text: str) -> bool:
    compact = (text or "").lower()
    if not ("emoji night" in compact or "חידת אימוג" in compact or "חידות אימוג" in compact):
        return False
    if "בעוד" in compact or "תזכורת" in compact or "מתחממים" in compact or "נפתח" in compact or "הערב ב" in compact:
        return False
    return True


def _coerce_game_message_fields(message_type: str, text: str, poll_options=None, teaser_topic_id: int | None = None) -> tuple[str, str | None]:
    """Turn natural-language game calendar items into executable launch rows."""
    mtype = (message_type or "custom").strip() or "custom"
    if mtype in {"trivia_round", "emoji_puzzle"}:
        return mtype, json.dumps(poll_options, ensure_ascii=False) if isinstance(poll_options, dict) else poll_options
    if mtype not in {"discussion", "custom", "trivia"}:
        return mtype, json.dumps(poll_options, ensure_ascii=False) if isinstance(poll_options, dict) else poll_options

    body = (text or "").strip()
    compact = body.lower()
    if _looks_like_trivia_launch(body):
        categories = _infer_trivia_categories(body)
        payload = {
            "pre_roll_s": 30,
            "theme_label": categories[0] if categories else "כללי",
            "categories": categories,
            "question_count": _infer_question_count(body),
        }
        if teaser_topic_id:
            payload["teaser_topic_id"] = int(teaser_topic_id)
        return "trivia_round", json.dumps(payload, ensure_ascii=False)

    if _looks_like_emoji_launch(body):
        return "emoji_puzzle", None

    return mtype, json.dumps(poll_options, ensure_ascii=False) if isinstance(poll_options, dict) else poll_options


def _parse_game_payload(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_lead_time(minutes: int) -> str:
    if minutes % 60 == 0:
        hours = minutes // 60
        return f"{hours} שעות" if hours != 1 else "שעה"
    return f"{minutes} דקות"


def _clean_activity_copy(raw: str) -> str | None:
    text = (raw or "").strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, dict):
                text = str(parsed.get("text") or "").strip()
    except Exception:
        pass
    text = text.replace('"', '').replace("'", "").strip()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    text = "\n".join(lines[:3])
    if len(text) > 280:
        text = text[:277].rstrip() + "..."
    return text if any("\u0590" <= ch <= "\u05ff" for ch in text) else None


async def _generate_activity_copy(kind: str, *, fallback: str | None = None,
                                  avoid_texts: set[str] | None = None, **ctx) -> str | None:
    if kind in {"emoji_warmup", "emoji_warmup_reminder"}:
        from bot.utils.copy import load_copy

        text = load_copy("activity_copy", kind, **ctx).strip()
        if text.startswith("[copy missing:"):
            logger.warning("[activity-copy] missing configured copy for %s", kind)
            return None
        rejection = freshness_rejection(text, avoid_texts=avoid_texts)
        if rejection:
            logger.info("[activity-copy] %s rejected configured copy: %s", kind, rejection)
            return None
        return text

    is_reminder = bool(ctx.get("is_reminder")) or kind.endswith("_reminder")
    if is_reminder:
        rules = (
            "- 1-2 שורות קצרות בעברית טבעית, RTL, ללא שיווק.\n"
            '- זה תזכורת — ההודעה המקורית עם הכפתור "🙋 אני בפנים" כבר נשלחה למעלה.\n'
            '- הסבר שעדיין אפשר להצטרף בלחיצה על הכפתור בהודעה המקורית (לא בהודעה הזו).\n'
            "- ציין את min_ready_players ואת זמן הפעילות בקצרה.\n"
            "- ללא חזרה על הטקסט המקורי; לא לסלוגנים גנריים."
        )
    else:
        rules = (
            "- לא להשתמש בנוסחים גנריים קבועים, סלוגנים חוזרים, או שם ערוץ קשיח.\n"
            "- 1-3 שורות קצרות, קריא RTL, טבעי ולא שיווקי מדי.\n"
            "- לא להבטיח פעולה שאין כפתור עבורה כרגע.\n"
            '- אם יש min_ready_players, הבהר שיש ללחוץ על כפתור "🙋 אני בפנים" בהודעה הזו כדי לאשר השתתפות — הכפתור מופיע מתחת לטקסט.\n'
            "- בלי אנגלית אלא אם שם הפעילות עצמו באנגלית."
        )
    canonical_rules = _load_quality_rules_short()
    rules_block = rules + (f"\n\n{canonical_rules}" if canonical_rules else "")
    prompt = f"""כתוב טקסט חדש בעברית להודעת פעילות בטלגרם לקהילת מבוגרים ישראלית.

סוג פעילות: {kind}
נתונים: {json.dumps(ctx, ensure_ascii=False)}

חוקים:
{rules_block}

פלט JSON בלבד: {{"text":"..."}}"""
    try:
        raw = await _generate_via_cli(prompt)
    except Exception:
        try:
            raw = await _generate_via_api(prompt)
        except Exception as e:
            logger.info("[activity-copy] %s generation failed; skipping warm-up: %s", kind, e)
            return None
    text = _clean_activity_copy(raw)
    if text is None:
        logger.info("[activity-copy] %s returned unusable copy; skipping warm-up", kind)
        return None
    game_time = str(ctx.get("game_time") or "").strip()[:5]
    if game_time and game_time not in text:
        text = f"{text}\nמתחיל ב-{game_time}."
    lead_minutes = ctx.get("lead_minutes", ctx.get("warmup_offset_min"))
    try:
        lead_minutes_int = int(lead_minutes)
    except Exception:
        lead_minutes_int = 0
    if lead_minutes_int > 0 and str(lead_minutes_int) not in text and "דקות" not in text and "בעוד" not in text:
        text = f"{text}\nבעוד {_format_lead_time(lead_minutes_int)} מתחילים."
    rejection = freshness_rejection(text, avoid_texts=avoid_texts)
    if rejection:
        logger.info("[activity-copy] %s rejected generated copy: %s", kind, rejection)
        return None
    return text


def _matching_trivia_questions(pool: list, categories: list[str]) -> list[dict]:
    wanted = {str(c).strip().lower() for c in categories if str(c).strip()}
    if not wanted:
        return [q for q in pool if isinstance(q, dict)]
    return [
        q for q in pool
        if isinstance(q, dict) and str(q.get("category") or "").strip().lower() in wanted
    ]


async def _ensure_trivia_pool_ready_for_round(row) -> dict:
    row_id = int(row["id"])
    lock = _TRIVIA_TOPUP_LOCKS.setdefault(row_id, asyncio.Lock())
    async with lock:
        return await _ensure_trivia_pool_ready_for_round_unlocked(row)


async def _ensure_trivia_pool_ready_for_round_unlocked(row) -> dict:
    """Ensure a trivia_round row has enough verified questions before it goes live.

    This is intentionally run during "Turn live" rather than only at fire time:
    approval should mean the round is actually runnable. If generation fails,
    the endpoint raises a visible error and leaves the row as draft.
    """
    payload = _parse_game_payload(row["poll_options"])
    categories = [str(c).strip() for c in (payload.get("categories") or []) if str(c).strip()]
    question_count = max(1, min(20, int(payload.get("question_count") or 10)))
    if not categories:
        return {"generated": 0, "available": question_count, "required": question_count}

    trivia_path = CONFIG_DIR / "trivia.yaml"
    data = load_yaml("trivia.yaml") or {}
    existing_pool = data.get("questions") or []
    available = len(_matching_trivia_questions(existing_pool, categories))
    if available >= question_count:
        return {"generated": 0, "available": available, "required": question_count}

    missing = question_count - available
    theme_label = str(payload.get("theme_label") or categories[0]).strip() or categories[0]
    prompt = build_generation_prompt(
        "trivia",
        "append",
        yaml.safe_dump({"questions": existing_pool}, allow_unicode=True, sort_keys=False),
        ",".join(categories),
        theme_label,
    )

    cli_err = None
    try:
        content = await _generate_via_cli(prompt)
    except Exception as e:
        cli_err = e
        logger.warning("trivia top-up: CLI failed, falling back to API: %s", e)
        try:
            content = await _generate_via_api(prompt)
        except Exception as api_err:
            raise HTTPException(
                status_code=503,
                detail=(
                    "לא הצלחתי לייצר שאלות טריוויה חסרות, ולכן הסיבוב לא הועבר ללייב. "
                    f"חסרות {missing} שאלות בקטגוריות {categories}. "
                    f"CLI={cli_err}; API={api_err}"
                ),
            )

    questions, invalid = _parse_trivia_blocks(content)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail="נוצרו שאלות טריוויה לא תקינות, ולכן הסיבוב לא הועבר ללייב: " + "; ".join(invalid[:5]),
        )
    try:
        review_trivia_questions(
            questions,
            allowed_categories=categories,
            existing_questions=existing_pool,
        )
    except TriviaVerificationError as e:
        raise HTTPException(status_code=422, detail=f"בודק הטריוויה דחה את השאלות שנוצרו: {e}")

    generated_matches = _matching_trivia_questions(questions, categories)
    if len(generated_matches) < missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"נוצרו רק {len(generated_matches)} שאלות מתאימות מתוך {missing} חסרות, "
                f"לכן הסיבוב לא הועבר ללייב."
            ),
        )

    merged = existing_pool + questions
    save_and_verify_trivia_questions(trivia_path, merged)
    logger.info(
        "trivia top-up: generated=%d categories=%s available_before=%d required=%d row=%s",
        len(questions), categories, available, question_count, row["id"],
    )
    return {"generated": len(questions), "available": available + len(generated_matches), "required": question_count}


async def _ensure_trivia_announcement_scheduled(db: Database, *, game_id: int) -> int | None:
    """Create one 341 warm-up announcement for a scheduled trivia game.

    The real trivia launcher remains the `trivia_round` row in botson_corner.
    This companion row must be scheduled too; drafts are visible on the calendar
    but are ignored by the autonomous sender.
    """
    async with db._db.execute(
        """SELECT id, text, scheduled_date, scheduled_time, message_type, status, poll_options, channel_topic_id
           FROM scheduled_messages WHERE id = ?""",
        (game_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row or row["message_type"] != "trivia_round" or row["status"] == "cancelled":
        return None

    game_time = (row["scheduled_time"] or "22:00")[:5]
    try:
        game_dt = datetime.strptime(f"{row['scheduled_date']} {game_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    payload = _parse_game_payload(row["poll_options"])
    trivia_defaults = (get_settings().get("trivia") or {}).get("populate_defaults") or {}
    try:
        lead_minutes = int(payload.get("warmup_offset_min") or trivia_defaults.get("warmup_offset_min") or 35)
    except (TypeError, ValueError):
        lead_minutes = 35
    lead_minutes = max(1, min(24 * 60, lead_minutes))
    announcement_dt = game_dt - timedelta(minutes=lead_minutes)
    from bot.utils.copy import default_theme_label
    theme = str(payload.get("theme_label") or "").strip() or default_theme_label()
    min_ready = int(payload.get("min_ready_players") or 0)
    text = await _generate_activity_copy(
        "trivia_warmup",
        avoid_texts={str(row["text"] or "")},
        game_time=game_time,
        lead_minutes=lead_minutes,
        theme_label=theme,
        question_count=int(payload.get("question_count") or 5),
        min_ready_players=min_ready,
    )
    if text is None:
        return None
    try:
        routing = await db.get_handler_routing("trivia_warmup")
    except Exception:
        routing = None
    announcement_topic_id = payload.get("teaser_topic_id")
    if announcement_topic_id is None and routing and routing.get("play_topic_id") is not None:
        announcement_topic_id = routing["play_topic_id"]
    if announcement_topic_id is None:
        announcement_topic_id = row.get("channel_topic_id") if hasattr(row, "get") else row["channel_topic_id"]

    warmup_marker = f"warmup-rsvp:{game_id}"
    question_count = int(payload.get("question_count") or 5)
    activity_label_with_count = f"הטריוויה על {theme} ({question_count} שאלות)"
    warmup_poll_options = json.dumps({
        "min_ready_players": min_ready,
        "game_time": game_time,
        "theme_label": theme,
        "activity_label": activity_label_with_count,
        "warmup_marker": warmup_marker,
    })

    marker = f"trivia-announcement-draft:{game_id}"
    async with db._db.execute(
        "SELECT id FROM scheduled_messages WHERE created_by = ? AND status != 'cancelled' LIMIT 1",
        (marker,),
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        existing_id = int(existing["id"])
        await db.update_scheduled_message(
            existing_id,
            text=text,
            message_type="trivia_warmup_rsvp",
            channel_topic_id=announcement_topic_id,
            target_group="main",
            scheduled_date=announcement_dt.date().isoformat(),
            scheduled_time=announcement_dt.strftime("%H:%M"),
            poll_options=warmup_poll_options,
            status="scheduled",
        )
        announcement_id = existing_id
    else:
        announcement_id = await db.create_scheduled_message(
            text=text,
            message_type="trivia_warmup_rsvp",
            channel_topic_id=announcement_topic_id,
            target_group="main",
            scheduled_date=announcement_dt.date().isoformat(),
            scheduled_time=announcement_dt.strftime("%H:%M"),
            poll_options=warmup_poll_options,
            created_by=marker,
            status="scheduled",
        )

    await _ensure_warmup_reminder_scheduled(
        db,
        parent_id=game_id,
        warmup_marker=warmup_marker,
        game_dt=game_dt,
        game_time=game_time,
        announcement_dt=announcement_dt,
        announcement_topic_id=announcement_topic_id,
        theme_label=theme,
        activity_label=activity_label_with_count,
        min_ready=min_ready,
        kind="trivia_warmup_reminder",
    )

    # T-127: stamp warmup_marker on the trivia_round game row so the
    # dispatch-time RSVP gate can find this announcement and count responses.
    if min_ready > 0:
        try:
            game_payload = dict(payload)
            game_payload["warmup_marker"] = warmup_marker
            await db.update_scheduled_message(
                game_id, poll_options=json.dumps(game_payload, ensure_ascii=False),
            )
        except Exception as e:
            logger.warning(
                "[trivia-rsvp] failed to stamp warmup_marker on game row %s: %s",
                game_id, e,
            )

    return announcement_id


async def _ensure_warmup_reminder_scheduled(
    db: Database,
    *,
    parent_id: int | str,
    warmup_marker: str,
    game_dt: datetime,
    game_time: str,
    announcement_dt: datetime,
    announcement_topic_id: int | None,
    theme_label: str,
    activity_label: str,
    min_ready: int,
    kind: str,
) -> int | None:
    """Create or update the second warm-up reminder row for an RSVP announcement.

    Reminder fires `warmup_reminder_offset_min` before game time (must be strictly
    after the announcement). Dispatch checks `trivia_interest_responses` against
    `min_ready_players` and short-circuits to `skipped` if the threshold is met.
    """
    if min_ready <= 0:
        return None
    trivia_defaults = (get_settings().get("trivia") or {}).get("populate_defaults") or {}
    try:
        reminder_offset = int(trivia_defaults.get("warmup_reminder_offset_min") or 0)
    except (TypeError, ValueError):
        reminder_offset = 0
    if reminder_offset <= 0:
        return None
    reminder_dt = game_dt - timedelta(minutes=reminder_offset)
    if reminder_dt <= announcement_dt:
        logger.info(
            "[warmup-reminder] reminder offset (%d) >= lead, skipping reminder for %s",
            reminder_offset, warmup_marker,
        )
        return None

    text = await _generate_activity_copy(
        kind,
        avoid_texts=None,
        game_time=game_time,
        reminder_offset_min=reminder_offset,
        theme_label=theme_label,
        activity_label=activity_label,
        min_ready_players=min_ready,
        is_reminder=True,
    )
    if text is None:
        return None

    reminder_poll_options = json.dumps({
        "min_ready_players": min_ready,
        "game_time": game_time,
        "theme_label": theme_label,
        "activity_label": activity_label,
        "warmup_marker": warmup_marker,
        "reminder_offset_min": reminder_offset,
    }, ensure_ascii=False)
    reminder_marker = f"warmup-reminder-draft:{parent_id}"
    async with db._db.execute(
        "SELECT id FROM scheduled_messages WHERE created_by = ? AND status != 'cancelled' LIMIT 1",
        (reminder_marker,),
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        existing_id = int(existing["id"])
        await db.update_scheduled_message(
            existing_id,
            text=text,
            message_type="warmup_reminder",
            channel_topic_id=announcement_topic_id,
            target_group="main",
            scheduled_date=reminder_dt.date().isoformat(),
            scheduled_time=reminder_dt.strftime("%H:%M"),
            poll_options=reminder_poll_options,
            status="scheduled",
        )
        return existing_id

    return await db.create_scheduled_message(
        text=text,
        message_type="warmup_reminder",
        channel_topic_id=announcement_topic_id,
        target_group="main",
        scheduled_date=reminder_dt.date().isoformat(),
        scheduled_time=reminder_dt.strftime("%H:%M"),
        poll_options=reminder_poll_options,
        created_by=reminder_marker,
        status="scheduled",
    )

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

    # Keep each day ordered by time.
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
        media_type=str(data.get("media_type") or "general").strip() or "general",
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
    existing_emoji_schedule = settings.get("schedule", {}).get("emoji_puzzle", {}) or {}
    media_types_raw = data.get("media_types", existing_emoji_schedule.get("media_types", []))
    if isinstance(media_types_raw, str):
        media_types = [x.strip() for x in media_types_raw.split(",") if x.strip()]
    else:
        media_types = [str(x).strip() for x in (media_types_raw or []) if str(x).strip()]
    settings["schedule"]["emoji_puzzle"] = {
        "days": [int(d) for d in data.get("days", [])],
        "time": str(data.get("time") or "22:00").strip() or "22:00",
        "announcement_lead_minutes": int(data.get("announcement_lead_minutes") or existing_emoji_schedule.get("announcement_lead_minutes") or 90),
        "theme_label": str(data.get("theme_label") or existing_emoji_schedule.get("theme_label") or "").strip(),
        "media_types": media_types,
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
    theme_label = str(data.get("theme_label") or "").strip()
    raw_categories = data.get("categories") or []
    if isinstance(raw_categories, str):
        categories = [part.strip() for part in raw_categories.split(",") if part.strip()]
    else:
        categories = [str(part).strip() for part in raw_categories if str(part).strip()]
    question_count = int(data.get("question_count") or 10)

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


from bot.utils.quality_rules import (
    load_quality_rules as _load_quality_rules,
    load_quality_rules_short as _load_quality_rules_short,
)


async def _fetch_recent_sent_for_dedup(
    db: "Database", message_type: str, *, category_topic_id: int | None = None, limit: int = 60
) -> list[str]:
    """Return up to `limit` distinct recent texts of a given message_type.
    Optionally scoped to a single channel_topic_id (preferred for discussion
    dedup so cross-channel content variety isn't penalized). Used as a
    "DO NOT REPEAT" block in build_generation_prompt.
    Includes both 'sent' and 'scheduled' rows so questions queued but not yet
    fired also count as "already proposed" — matches the existing
    get_used_discussion_texts() spirit.
    """
    sql = (
        "SELECT DISTINCT text FROM scheduled_messages "
        "WHERE message_type = ? AND text IS NOT NULL AND text != '' "
        "AND status IN ('sent', 'scheduled') "
    )
    params: list = [message_type]
    if category_topic_id is not None:
        sql += "AND channel_topic_id = ? "
        params.append(category_topic_id)
    sql += "ORDER BY scheduled_date DESC, scheduled_time DESC LIMIT ?"
    params.append(limit)
    out: list[str] = []
    try:
        async with db._db.execute(sql, params) as cursor:
            async for row in cursor:
                txt = (row[0] or "").strip()
                if txt and txt not in out:
                    out.append(txt)
    except Exception as e:
        logger.warning("[generate] recent-sent dedup query failed: %s", e)
    return out


_DRAFT_BANNED_REGEXES = (
    # 5 patterns swept against discussions.yaml + prompts.yaml — 0 false positives.
    # The "מה ה.+ האהוב" pattern was excluded after sweep flagged 2 legit entries
    # ("מה המדיום האהוב עליכם?" art, "מה הג'אנר האהוב עליכם במשחקים?" gaming).
    (re.compile(r"^ספרו על"), "rule_anti_pattern_vague"),
    (re.compile(r"^מה היה היום"), "rule11_generic_day"),
    (re.compile(r"מה עולה (על הסדר|הערב)"), "concrete_failure_agenda"),
    (re.compile(r"בולמוס של"), "concrete_failure_invented"),
    (re.compile(r"מה הפלן הערב"), "concrete_failure_plan"),
    (re.compile(r"מה נשאר (איתכם|אתכם)"), "rule11_generic_evening"),
    (re.compile(r"אחרי כל מה שהיה"), "rule11_generic_evening"),
    (re.compile(r"מה\s+ה\S*\s+הכי\s+מעריך"), "concrete_failure_bad_hebrew"),
    (re.compile(r"היום הזה עוד לא הוחלט"), "concrete_failure_generic_morning"),
    (re.compile(r"מה הדבר הכי שווה שאתם מכניסים אליו"), "concrete_failure_generic_morning"),
    (re.compile(r"איזה יצור .*מהדמיון"), "concrete_failure_weird_creature_prompt"),
    (re.compile(r"הגענו לאמצע השבוע"), "concrete_failure_calendar_filler"),
    (re.compile(r"מה שיניתם בו ממה שתכננתם ביום ראשון"), "concrete_failure_calendar_filler"),
    (re.compile(r"באיזה רגע .*ל?מבוגר האחראי.*בסיטואציה"), "concrete_failure_vague_situation_cliche"),
    (re.compile(r"כמעט סוף שבוע"), "concrete_failure_time_filler"),
    (re.compile(r"מה דבר אחד שאתם רוצים לסגור לפני שישי"), "concrete_failure_time_filler"),
    (re.compile(r"עוד שעה אחת לפני שנגמר השבוע"), "concrete_failure_time_filler"),
    (re.compile(r"מה אתם עושים איתה"), "concrete_failure_time_filler"),
    (re.compile(r"עוד קצת ואז כבר סוף שבוע"), "concrete_failure_time_filler"),
    (re.compile(r"מ[נ]?צלים את האנרגיה הזו"), "concrete_failure_generic_energy"),
    (re.compile(r"הריטואל שסוגר לכם את השבוע"), "concrete_failure_generic_ritual"),
    (re.compile(r"שכולם חושבים שזה דאגבר"), "concrete_failure_bad_hebrew"),
    (re.compile(r"ז'?אנר מסוים.*מגלים שזה משהו אחר לגמרי"), "concrete_failure_abstract_movie_bait"),
    (re.compile(r"מי חטף פנים"), "concrete_failure_bad_hebrew"),
    (re.compile(r"מי מוסיף פ[נפ]ים כזה"), "concrete_failure_bad_hebrew"),
    (re.compile(r"אחרי שבוע.*הנושא הפוליטי.*(עלה|בראש השולחן|הסכמה)"), "concrete_failure_generic_politics_report"),
    (re.compile(r"רשות מלאה לעשות בדיוק מה שבא לכם"), "concrete_failure_generic_permission_fantasy"),
    (re.compile(r"בלי תוכניות גדולות.*ניצחון או ויתור"), "concrete_failure_generic_stay_home_judgment"),
    (re.compile(r"החלטתם שממנו זה הדבר"), "concrete_failure_broken_hebrew"),
    (re.compile(r"החיה הכי חמודה.*(השבוע האחרון|לא ברשת)"), "concrete_failure_cutesy_no_payoff"),
    (re.compile(r"מה עדיין זוהר אצלכם מהיום"), "concrete_failure_vague_poetic_evening"),
    (re.compile(r"מה יצאתם ליצור.*בלי תכנון.*יוצא מזה משהו"), "concrete_failure_generic_art_bad_hebrew"),
    (re.compile(r"משחק שהתחלתם רק לה?י?רגע.*וגמרתם אותו לפני השינה"), "concrete_failure_generic_gaming_unwind"),
    (re.compile(r"מתי בדיוק החלטתם שהיום נגמר"), "concrete_failure_unclear_day_shutdown"),
    (re.compile(r"קפה, מקלחת, סגירת המחשב"), "concrete_failure_unclear_day_shutdown"),
    (re.compile(r"מה הכי הפתיע אתכם בעצמכם"), "concrete_failure_rewritten_self_reflection"),
    (re.compile(r"שלב הסינגלות הנוכחי"), "concrete_failure_bad_singles_wording"),
    (re.compile(r"בשעה שהמטבח כבר קר"), "concrete_failure_unclear_vegan_late_food"),
    (re.compile(r"אוכלים שהוא בקרוב צמחי"), "concrete_failure_bad_vegan_hebrew"),
    (re.compile(r"לסמן כ-Done"), "english_jargon:Done"),
    (re.compile(r"איך היה היום"), "concrete_failure_generic_day_checkin"),
    (re.compile(r"הרגע הכי שווה מהיום"), "concrete_failure_generic_day_highlight"),
    (re.compile(r"מה עשיתם היום בשביל עצמכם"), "concrete_failure_generic_self_care"),
    (re.compile(r"אם הייתם יכולים לחיות בעולם של סדרה/משחק/ספר"), "concrete_failure_generic_fandom_fantasy"),
    (re.compile(r"סרט שראיתם יותר מ-?3 פעמים"), "concrete_failure_generic_movie_rewatch"),
    (re.compile(r"מה אתם עושים בערב.*(ישן|בשבילכם|בשבילו)"), "concrete_failure_generic_evening_plan"),
    (re.compile(r"קפה בידיים.*לפטופ עוד סגור"), "concrete_failure_generic_morning_laptop"),
    (re.compile(r"גיליתם שלבד עדיף"), "concrete_failure_singles_smug_framing"),
)

_DRAFT_ENGLISH_JARGON = (
    "mechanic", "WIP", "NPC", "build", "meta", "pipeline",
    "stack", "loop", "boss", "spawn", "Done",
)


def _validate_draft_text(text: str) -> list[str]:
    """Lint a single Hebrew draft against the rules in `config/question_quality.md`.

    Returns a list of human-readable failure reasons (empty list = passes).
    Used by the ai-fill-today retry loop and by `/api/generate-content` so a
    bad model output never reaches the DB. Patterns are deliberately specific
    (caught real failures, no false positives) — softer style violations are
    enforced by the prompt, not by this validator.
    """
    if not text or not text.strip():
        return ["empty draft"]
    s = text.strip()
    failures: list[str] = []
    if len(s) > 200:
        failures.append(f"length>200 ({len(s)})")
    # Rule 2 says "1 question per post"; in practice two-question
    # rhetorical pairs ("על מה אתם מרוצים? על מה פחות?") show up in the
    # curated pool, so we only reject 3+ question marks here. The model
    # sees the strict rule in the prompt regardless.
    if s.count("?") > 2:
        failures.append("multiple question marks")
    tokens = re.findall(r"\b[A-Za-z]{2,}\b", s)
    for tok in tokens:
        if tok in _DRAFT_ENGLISH_JARGON:
            failures.append(f"english_jargon:{tok}")
            break
    for pattern, label in _DRAFT_BANNED_REGEXES:
        if pattern.search(s):
            failures.append(label)
    return failures


def _reject_bad_planner_text(text: str) -> None:
    failures = _validate_draft_text(text)
    freshness_failure = freshness_rejection(text)
    if freshness_failure:
        failures.append(freshness_failure)
    if failures:
        raise HTTPException(
            status_code=422,
            detail={"error": "quality_rejected", "failures": failures},
        )


def _quality_failures_for_planner_text(text: str, *, scheduled_date: str | None = None) -> list[str]:
    failures = _validate_draft_text(text)
    freshness_failure = freshness_rejection(text, scheduled_date=scheduled_date)
    if freshness_failure:
        failures.append(freshness_failure)
    return failures


def _reject_bad_message_row(row: dict) -> None:
    if row.get("message_type") not in {"morning", "evening", "discussion"}:
        return
    failures = _quality_failures_for_planner_text(
        str(row.get("text") or ""),
        scheduled_date=row.get("scheduled_date"),
    )
    if failures:
        raise HTTPException(
            status_code=422,
            detail={"error": "quality_rejected", "failures": failures},
        )


def _format_dedup_block(recent_sent: list[str] | None) -> str:
    """Render the 'do not repeat' block in Hebrew. Returns '' if nothing to dedupe."""
    if not recent_sent:
        return ""
    # Cap each line to keep prompt size sane; trim list to 60 items max.
    items = [(t.replace("\n", " ").strip()[:140]) for t in recent_sent[:60] if t and t.strip()]
    if not items:
        return ""
    body = "\n".join(f"- {t}" for t in items)
    return (
        "\n\nאסור לחזור על השאלות הבאות, גם לא לפראפרז שלהן (ניסוח שונה לאותו רעיון נחשב חזרה):\n"
        f"{body}\n"
        "אם השאלה החדשה שלך מזכירה אחת מהן ברעיון המרכזי או בפועל המפתח — נסח שאלה אחרת."
    )


from bot.utils.time_context import format_time_context as _format_time_context  # noqa: E402


def _sample_pool_examples(field: str, category: str, n: int = 3) -> str:
    """Pull a few random examples from the matching YAML pool as a few-shot anchor.

    Anchors the model on the existing voice without giving it canned text to
    copy. Returns '' if the pool can't be loaded or is empty. Used by both
    single-mode and multi-mode generation.
    """
    try:
        pool: list[str] = []
        cat = (category or "").strip()
        if field == "discussion" and cat:
            data = load_yaml("discussions.yaml") or {}
            raw = data.get(cat) or []
            pool = [str(x).strip() for x in raw if isinstance(x, str) and x.strip()]
        elif field == "morning":
            data = load_yaml("prompts.yaml") or {}
            raw = data.get("morning") or []
            pool = [str(x).strip() for x in raw if isinstance(x, str) and x.strip()]
        elif field == "evening":
            data = load_yaml("prompts.yaml") or {}
            raw = data.get("evening") or []
            pool = [str(x).strip() for x in raw if isinstance(x, str) and x.strip()]
        else:
            return ""
        if not pool:
            return ""
        k = min(n, len(pool))
        sample = random.sample(pool, k)
        body = "\n".join(f"- {s}" for s in sample)
        return (
            "\n\nדוגמאות לאיכות וסגנון (אל תחזור עליהן מילה במילה — תפוס רק את הטון והאורך):\n"
            f"{body}\n"
            "צור משהו חדש לגמרי — לא וריאציה של הדוגמאות. אם הרעיון שלך נשמע כמו אחת מהדוגמאות, חשוב על משהו אחר."
        )
    except Exception as e:
        logger.warning("[generate] few-shot pool sample failed: %s", e)
        return ""


def _load_channel_rubric(category: str) -> str:
    """T-173: per-channel guidance block injected for discussion prompts.

    Returns an empty string when no rubric is configured for the category
    (a quiet no-op so missing entries don't break generation).
    """
    if not category:
        return ""
    try:
        data = load_yaml("channel_rubrics.yaml") or {}
    except Exception:
        return ""
    rubrics = (data.get("rubrics") or {}).get(category) or []
    lines = [str(x).strip() for x in rubrics if str(x).strip()]
    if not lines:
        return ""
    from bot.utils.copy import load_copy as _load_copy
    header = _load_copy(
        "planner",
        "channel_rubric_header",
        default="הנחיות ספציפיות לערוץ זה:",  # noqa: hardcoded-content (Hebrew header, fallback only)
    )
    return "\n\n" + header + "\n" + "\n".join(f"- {ln}" for ln in lines)


def _finalize_prompt(
    base: str,
    field: str,
    category: str,
    *,
    recent_sent: list[str] | None,
    scheduled_date: str | None,
    scheduled_time: str | None,
    add_dedup: bool = True,
    group_stats: str | None = None,
) -> str:
    """Wrap the per-field base prompt with the canonical layers:
    context (weekday+time), few-shot from pool, dedup block, channel
    rubric (T-173), optional group-stats block, and the
    question_quality.md rules at the very top.

    Used by both single-mode and multi-mode generation paths so quality
    enforcement isn't silently bypassed in the single-row drawer (the
    historical `mode == "single"` early-return missed both rules and dedup,
    which is the bug behind the 2026-05-01 'בולטת של תוכן' draft).
    """
    out = base
    out += _format_time_context(scheduled_date, scheduled_time)
    out += _sample_pool_examples(field, category, n=3)
    if add_dedup and field != "trivia":
        out += _format_dedup_block(recent_sent)
    if field == "discussion" and category:
        out += _load_channel_rubric(category)
    # T-174: active style profile (operator-approved guidance learned from
    # rejected feedback). Loaded synchronously from a process-level cache
    # so prompt building stays sync; cache invalidates on apply.
    style_block = _active_style_profile_block_sync()
    if style_block:
        out += style_block
    if group_stats:
        out += "\n\n" + group_stats
    rules = _load_quality_rules()
    if rules:
        out = (
            "להלן חוקים קשיחים ליצירת שאלות / הודעות עבור הבוט. אסור לעבור עליהם:\n\n"
            f"{rules}\n\n"
            "──────────\n"
            f"{out}"
        )
    return out


# T-174: cached active style profile. The dashboard mutates this cache
# directly when /api/style-profile/apply succeeds so subsequent prompts
# pick up the new guidance without restarting the process.
_STYLE_PROFILE_CACHE: dict[str, str | None] = {"planner_hebrew_default": None}


def _active_style_profile_block_sync() -> str:
    guidance = _STYLE_PROFILE_CACHE.get("planner_hebrew_default")
    if not guidance:
        return ""
    from bot.utils.copy import load_copy as _load_copy
    header = _load_copy(
        "planner",
        "style_profile_header",
        default="הנחיות נוספות מבוססות-משוב אופרטור (אושרו ידנית):",  # noqa: hardcoded-content (Hebrew header, fallback only)
    )
    return "\n\n" + header + "\n" + str(guidance).strip()


async def _render_group_stats_context(db: Database, since_days: int = 7) -> str:
    """Hebrew block summarising live group state for prompt context.

    Returns an empty string on any failure so a stats-DB hiccup never
    blocks generation. Cheap: 3 single-table queries, ~5ms total.

    The model treats this as informational context — it must NOT quote
    user names verbatim into a prompt, only reference the group's mood
    ("רואים שיש פעילות חזקה השבוע", "אם רוצים לעודד שקטים, פנו אליהם").
    """
    try:
        since = datetime.now() - timedelta(days=since_days)
        new_members = await db.get_member_count_since(since)
        leaders = await db.get_weekly_leaders(limit=3)
        streaks = await db.get_top_streaks(limit=3)
    except Exception:
        return ""

    lines = ["מצב הקבוצה (אינפורמטיבי בלבד — לא לצטט שמות בתשובה):"]
    lines.append(f"- בשבוע האחרון הצטרפו {int(new_members or 0)} חברים חדשים.")

    if leaders:
        leader_count = len(leaders)
        top_score = int((leaders[0] or {}).get("weekly_stars") or 0)
        lines.append(
            f"- {leader_count} חברים מובילים בקארמה השבוע (השיא: {top_score} נקודות)."
        )

    if streaks:
        top_streak = int((streaks[0] or {}).get("current_streak") or 0)
        if top_streak >= 3:
            lines.append(f"- שיא רצף יומי פעיל בקבוצה: {top_streak} ימים רצוף.")

    lines.append(
        "השתמש במידע הזה רק כדי לכוון את האווירה (לדוגמה — אם הקבוצה פעילה השבוע, אפשר לרמוז לזה; אם שקטה, אפשר לפתוח שאלה מזמינה ללא לחץ). אל תכתוב מספרים מתוך הבלוק הזה ישירות לתוך השאלה."
    )
    return "\n".join(lines)


def build_generation_prompt(
    field: str,
    mode: str,
    existing: str,
    category: str,
    instructions: str = "",
    recent_sent: list[str] | None = None,
    scheduled_date: str | None = None,
    scheduled_time: str | None = None,
    group_stats: str | None = None,
) -> str:
    # Single-item rewrite mode — used by the weekplan modal
    if mode == "rewrite":
        type_he = {"morning": "הודעת בוקר", "evening": "הודעת ערב", "discussion": "שאלה לדיון"}.get(field, "הודעה")
        base = f"""שכתב את ה{type_he} הבאה בעברית. שמור על הרעיון המרכזי אבל הפוך אותה לטובה יותר — יותר מעניינת, טבעית ומזמינה. פלט: רק ההודעה החדשה, בלי הסברים, בלי מרכאות, בלי מספור.

ההודעה המקורית:
{existing}"""
        if instructions:
            base += f"\n\nהוראות נוספות: {instructions}"
        return _finalize_prompt(
            base, field, category,
            recent_sent=recent_sent,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            group_stats=group_stats,
        )

    # Single-item fresh generate — weekplan modal, one prompt only
    if mode == "single":
        if field == "morning":
            base = f"""צור הודעת בוקר אחת מעוררת השראה בעברית עבור {COMMUNITY_CONTEXT}

ההודעה צריכה להיות שורה או שתיים, לפתוח באמוג'י רלוונטי, לעודד את חברי הקהילה לבוקר טוב.
הטון: חם, מעודד, קליל. עברית תקנית בלבד — אל תמציא ביטויים, אל תשלב מילים באנגלית באמצע משפט עברי.
פלט: רק ההודעה, בלי מספור, בלי מרכאות, בלי הסברים."""
        elif field == "evening":
            base = f"""צור הודעת ערב אחת רפלקטיבית בעברית עבור {COMMUNITY_CONTEXT}

ההודעה צריכה להיות שורה או שתיים, לפתוח באמוג'י רלוונטי, ולעגן את הערב בסיטואציה ספציפית של מבוגרים בלי ילדים: זמן פנוי, בחירה קטנה, הרגל אישי, או דרך לסגור יום.
אסור לכתוב פילר גנרי כמו "אחרי כל מה שהיה", "מה נשאר איתכם", "מה עשה לכם את היום", או "מה הדבר הטוב היום".
הטון: רגוע, חכם, טבעי לטלגרם. עברית תקנית בלבד — אל תמציא ביטויים, אל תשלב מילים באנגלית באמצע משפט עברי.
פלט: רק ההודעה, בלי מספור, בלי מרכאות, בלי הסברים."""
        elif field == "discussion":
            base = f"""צור שאלה אחת לדיון בקטגוריה "{category}" בעברית עבור {COMMUNITY_CONTEXT}

השאלה צריכה להיות שורה אחת ספציפית, מעוררת שיחה ומעניינת — לא שאלה גנרית שאפשר לשאול בכל קבוצה.
הטון: סקרני, פתוח, מזמין. עברית תקנית בלבד — אל תמציא ביטויים, אל תשלב מילים באנגלית באמצע משפט עברי, אל תעבור 140 תווים.
פלט: רק השאלה, בלי מספור, בלי מרכאות, בלי הסברים."""
        elif field == "poll":
            cat_hint = f' בנושא "{category}"' if category else ""
            base = f"""צור סקר אחד בעברית עבור {COMMUNITY_CONTEXT}{cat_hint}.

הסקר חייב לכלול שאלה קצרה (עד 140 תווים) ו-3 או 4 אפשרויות בחירה. כל אפשרות עד 40 תווים.
האפשרויות צריכות להיות מובחנות זו מזו ולכסות את עיקר הקשת — לא 4 וריאציות של אותה תשובה.
עברית תקנית בלבד. אל תמציא ביטויים. אל תשלב מילים באנגלית באמצע משפט עברי.

פלט: JSON תקין בלבד, ללא טקסט נוסף לפני או אחרי, במבנה:
{{"question": "<טקסט השאלה>", "options": ["<אפשרות 1>", "<אפשרות 2>", "<אפשרות 3>"]}}"""
        else:
            base = f"צור תוכן בעברית עבור {COMMUNITY_CONTEXT}"
        if instructions:
            base += f"\n\nהוראות נוספות: {instructions}"
        return _finalize_prompt(
            base, field, category,
            recent_sent=recent_sent,
            scheduled_date=scheduled_date,
            scheduled_time=scheduled_time,
            group_stats=group_stats,
        )

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
            # T-115: no hardcoded topic-list fallback. When the operator
            # provides neither a theme nor categories, don't bias the LLM
            # toward a specific subject set — let it pick freely. Naming
            # "תרבות, מדע, היסטוריה, בידור, גאוגרפיה, אוכל" was a content
            # bias (CLAUDE.md hard rule: defaults must be blank, random,
            # or operator-configured).
            topic_line = "ללא נושא מרכזי — מגוון חופשי שמתאים לקהילה ישראלית של מבוגרים בלי ילדים."
        base = f"""צור 10 שאלות טריוויה בעברית עבור {COMMUNITY_CONTEXT}

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

    # Trivia uses a separate dedup mechanism (category exact-match + question
    # text dedup inside _pick_questions) — _finalize_prompt skips dedup for
    # trivia automatically.
    return _finalize_prompt(
        base, field, category,
        recent_sent=recent_sent,
        scheduled_date=scheduled_date,
        scheduled_time=scheduled_time,
    )


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

    from bot.utils.config import get_anthropic_config
    api_url, model = get_anthropic_config()
    async with httpx.AsyncClient(timeout=90) as client:
        resp = await client.post(
            api_url,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": model,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["content"][0]["text"].strip()


@app.post("/api/generate")
async def generate_content(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    field = data["field"]       # "morning", "evening", "discussion", "trivia"
    mode = data["mode"]         # "append", "replace", "single", or "rewrite"
    existing = data.get("existing", "")
    category = data.get("category", "")
    instructions = (data.get("instructions") or "").strip()

    # Fetch dedup history (skipped for trivia — that has its own dedup).
    recent_sent: list[str] = []
    if field in ("morning", "evening", "discussion"):
        recent_sent = await _fetch_recent_sent_for_dedup(db, field, limit=60)

    prompt = build_generation_prompt(field, mode, existing, category, instructions, recent_sent=recent_sent)

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

    review = None
    if field == "trivia":
        questions, invalid = _parse_trivia_blocks(content)
        if invalid:
            raise HTTPException(status_code=422, detail="Trivia reviewer rejected malformed output: " + "; ".join(invalid[:5]))
        allowed_categories = [part.strip() for part in (category or "").split(",") if part.strip()]
        try:
            existing_pool = (load_yaml("trivia.yaml") or {}).get("questions") or []
            review = review_trivia_questions(
                questions,
                allowed_categories=allowed_categories or None,
                existing_questions=existing_pool,
            )
        except TriviaVerificationError as e:
            raise HTTPException(status_code=422, detail=str(e))

    return {"content": content, "review": review}


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


@app.get("/api/engagement/recent")
async def engagement_recent(request: Request, limit: int = 30, db: Database = Depends(get_db)):
    """Phase B diagnostic: recent sent scheduled_messages with reaction counts.

    Answers "is anyone seeing the bot's posts?" without leaving the dashboard.
    Read-only.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    capped = max(1, min(int(limit), 200))
    async with db._db.execute(
        """SELECT sm.id, sm.scheduled_date, sm.scheduled_time, sm.message_type,
                  sm.channel_topic_id, sm.text, sm.sent_message_id,
                  COALESCE(me.reactions, 0) AS reactions,
                  COALESCE(me.distinct_reactors, 0) AS distinct_reactors,
                  me.last_updated
           FROM scheduled_messages sm
           LEFT JOIN message_engagement me ON me.scheduled_msg_id = sm.id
           WHERE sm.status = 'sent'
           ORDER BY sm.scheduled_date DESC, sm.scheduled_time DESC
           LIMIT ?""",
        (capped,),
    ) as cur:
        rows = await cur.fetchall()
    return {"items": [dict(r) for r in rows]}


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

    # Reload so the bot sees feature flag changes. Strict freshness mode does
    # not refill static prompt rows automatically.
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


def _activity_feature_state(settings: dict, key: str) -> dict:
    features = settings.get("features", {}) or {}
    raw = features.get(key, False)
    if isinstance(raw, dict):
        return {
            "enabled": bool(raw.get("enabled", False)),
            "groups": raw.get("groups", []) or [],
        }
    return {"enabled": bool(raw), "groups": []}


def _activity_schedule_label(settings: dict, key: str) -> str:
    schedule = (settings.get("schedule", {}) or {}).get(key) or {}
    if not isinstance(schedule, dict) or not schedule:
        return "ללא תזמון"
    days_he = ["א", "ב", "ג", "ד", "ה", "ו", "ש"]
    days = schedule.get("days", []) or []
    day_label = "ימים: " + ", ".join(days_he[int(d)] for d in days if isinstance(d, int) and 0 <= d < 7) if days else "ימים: כבוי"
    if "times" in schedule:
        time_label = ", ".join(str(t) for t in schedule.get("times") or []) or "ללא שעה"
    else:
        time_label = str(schedule.get("time") or "ללא שעה")
    return f"{day_label} · {time_label}"


def _count_pool_items(data) -> int:
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        total = 0
        for value in data.values():
            total += _count_pool_items(value)
        return total
    return 0


def _activity_status_label(enabled: bool) -> str:
    return "פעיל" if enabled else "כבוי"


async def _build_activities_context(db: Database) -> dict:
    settings = get_settings()
    prompts = load_yaml("prompts.yaml") or {}
    discussions = load_yaml("discussions.yaml") or {}
    trivia = load_yaml("trivia.yaml") or {}
    facts = load_yaml("facts.yaml") or {}
    emoji_puzzles = await db.list_emoji_puzzles()
    events = await db.get_all_events()
    recent_free_games = await db.recent_free_games(20)

    routing_names = {}
    for handler in (
        "trivia_round", "emoji_puzzle", "free_games", "facts_tidbit", "facts_spooky", "weekly_roundup",
        "weekly_leaderboard", "events_publish", "events_reminder",
    ):
        try:
            row = await db.get_handler_routing(handler)
            routing_names[handler] = row.get("play_topic_id") if row else None
        except Exception:
            routing_names[handler] = None

    def feature(key: str) -> dict:
        return _activity_feature_state(settings, key)

    def groups_label(key: str) -> str:
        groups = feature(key)["groups"]
        if not groups:
            return "כללי"
        names = {"main": "ראשית", "test": "טסט"}
        return ", ".join(names.get(g, g) for g in groups)

    def item(title, emoji, key, category, summary, href, count=None, schedule_key=None, routing_key=None, detail=None, scheduler_types=None):
        state = feature(key) if key else {"enabled": False, "groups": []}
        return {
            "title": title,
            "emoji": emoji,
            "key": key,
            "category": category,
            "summary": summary,
            "href": href,
            "count": count,
            "detail": detail or "",
            "enabled": state["enabled"],
            "status": _activity_status_label(state["enabled"]),
            "groups": groups_label(key) if key else "—",
            "schedule": _activity_schedule_label(settings, schedule_key or key) if (schedule_key or key) else "ללא תזמון",
            "routing": routing_names.get(routing_key) if routing_key else None,
            "scheduler_types": scheduler_types or [],
        }

    activities = [
        item("שאלות בוקר", "🌅", "morning_prompt", "שיחות ופרומפטים", "פתיחת יום קבועה לקהילה", "/prompts", count=len(prompts.get("morning", []) or []), scheduler_types=["morning"]),
        item("שאלות ערב", "🌙", "evening_prompt", "שיחות ופרומפטים", "סגירת יום ושיתוף התקדמות", "/prompts", count=len(prompts.get("evening", []) or []), scheduler_types=["evening"]),
        item("שאלות לדיון", "💬", "discussions", "שיחות ופרומפטים", "מאגר שאלות לפי ערוצים ונושאים", "/prompts", count=_count_pool_items(discussions), schedule_key="discussion_prompt", scheduler_types=["discussion"]),
        item("טריוויה", "🧠", "trivia", "משחקים", "סיבובי טריוויה, שאלות וניקוד", "/planner", count=len(trivia.get("questions", []) or []), routing_key="trivia_round", detail="ניהול ההרצה נמצא במגירת התכנון", scheduler_types=["trivia_round"]),
        item("חידות אימוג'י", "🧩", "emoji_puzzle", "משחקים", "Emoji Night וסבבי חידות", "/puzzles", count=len(emoji_puzzles), routing_key="emoji_puzzle", scheduler_types=["emoji_puzzle"]),
        item("משחקים חינם", "🎮", "free_games", "משחקים", "RSS של מבצעי משחקים חינמיים", "/free-games", count=len(recent_free_games), routing_key="free_games", scheduler_types=["free_games"]),
        item("עובדות מעניינות", "🔎", None, "תוכן מעניין", "מאגר tidbit ו-spooky מתוך facts.yaml", "/planner", count=_count_pool_items(facts), routing_key="facts_tidbit", detail="זמין לתזמון ידני ולמילוי AI כעובדה או סיפור מסתורי", scheduler_types=["facts_tidbit", "facts_spooky"]),
        item("סיכום שבועי", "📊", "roundup", "תוכן מעניין", "סיכום פעילות ותוכן סוף שבוע", "/planner", schedule_key="weekly_roundup", routing_key="weekly_roundup", scheduler_types=["weekly_roundup"]),
        item("טבלת רמות שבועית", "🏆", "levels", "תוכן מעניין", "פרסום leaderboard קבוע", "/levels", schedule_key="weekly_leaderboard", routing_key="weekly_leaderboard", scheduler_types=["weekly_leaderboard"]),
        item("אירועים", "🎉", "events", "אירועים", "יצירה, פרסום ו-RSVP", "/events", count=len(events), routing_key="events_publish", scheduler_types=["event"]),
        item("ברוכים הבאים", "👋", "welcome", "אירועים", "קליטת מצטרפים והודעות פתיחה", "/settings", detail="כולל batch window ו-topic קבלה"),
    ]

    categories = []
    for name in ("שיחות ופרומפטים", "משחקים", "תוכן מעניין", "אירועים"):
        rows = [a for a in activities if a["category"] == name]
        categories.append({"name": name, "rows": rows})
    return {"settings": settings, "activities": activities, "categories": categories}


@app.get("/activities", response_class=HTMLResponse)
async def activities_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)
    context = await _build_activities_context(db)
    return templates.TemplateResponse(request, name="activities.html", context=context)


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
    _reject_bad_planner_text(text)

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


async def _ai_fill_weekplan_inner(
    db: Database, mtype: str, week_offset: int,
    target_date: str | None = None,
) -> dict:
    """Inner logic of /api/weekplan/ai-fill — extracted so /ai-fill-regenerate
    can re-run a fill after wiping stale AI rows without going through HTTP.

    Validates the type, computes the week, deduplicates against existing
    rows, generates one content blob per (day, time, type) tuple, inserts
    the resulting rows tagged `created_by='ai-fill'`. Returns aggregate
    counts and errors.

    If `target_date` is provided ('YYYY-MM-DD'), the day loop is filtered
    to ONLY that date. Used by the day-level "Fill Today / Specific Day"
    buttons so they share the same always-generate semantics as the
    week-level Populate, just scoped tighter.
    """
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

    # Day filter: when target_date is supplied, restrict the loop to that
    # one day. Computed as the python weekday relative to that week's
    # Sunday so the existing day_index loop body works unchanged.
    target_day_index: int | None = None
    if target_date:
        try:
            td = date.fromisoformat(target_date)
            delta = (td - sunday).days
            if 0 <= delta <= 6:
                target_day_index = delta
            else:
                # target_date isn't in this week; nothing to do.
                return {"created": 0, "skipped": 0, "errors": [f"target_date {target_date} outside week"]}
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid target_date: {target_date}")

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

    # Fetch dedup history once per type so the model never paraphrases a
    # recent send. For discussion we additionally scope per-channel below.
    recent_sent_for_type = await _fetch_recent_sent_for_dedup(db, mtype, limit=60)

    # Compute group-state context once per fill so every job in this run
    # sees the same snapshot. Empty string on any DB error — content
    # generation should still work without it.
    group_stats_block = await _render_group_stats_context(db)

    created = 0
    skipped = 0
    errors: list[str] = []

    # Build the (day, time, category) work list. For morning/evening, one
    # job per scheduled slot. For discussion, one job per RANDOMLY SAMPLED
    # category per slot — capped by BOTSON_AI_FILL_MAX_DISCUSSION_CATS
    # (default 2). With 8 active categories × 2 slots × 1 day, the old
    # "all categories" loop produced 16 question drafts on Monday alone.
    # That was too much; the user wants variety, not a flood. Each click
    # gets a fresh random sample, so categories rotate naturally over
    # repeated clicks across weeks.
    disc_cap = max(1, int(os.environ.get("BOTSON_AI_FILL_MAX_DISCUSSION_CATS", "2")))
    jobs: list[tuple] = []  # (day_index, time_str, category_or_empty)
    for i in range(7):
        if target_day_index is not None and i != target_day_index:
            continue
        if i not in days_list:
            continue
        day_date = sunday + timedelta(days=i)
        if is_auto_blocked_on(day_date):
            skipped += len(times)
            continue
        for t in times:
            if mtype == "discussion":
                if not active_categories:
                    errors.append(f"no active discussion categories for day {i}")
                    continue
                chosen = random.sample(active_categories, min(disc_cap, len(active_categories)))
                for cat in chosen:
                    jobs.append((i, t, cat))
            else:
                jobs.append((i, t, ""))

    # Run jobs with bounded concurrency. Each job is independent (own
    # claude-CLI subprocess + DB insert); aiosqlite serialises writes
    # through the shared connection so concurrent inserts are safe. The
    # semaphore guards only the LLM call, which is the scarce resource
    # (CPU / CLI rate limits). Default 4 keeps the 4-core VPS comfortable
    # alongside Supabase/WAHA; bump via env when needed.
    concurrency = max(1, int(os.environ.get("BOTSON_AI_FILL_CONCURRENCY", "4")))
    sem = asyncio.Semaphore(concurrency)

    async def _run_job(day_index: int, t: str, cat: str) -> dict:
        day_date = sunday + timedelta(days=day_index)

        if mtype == "morning":
            prompt = build_generation_prompt(
                "morning", "single", "", "",
                recent_sent=recent_sent_for_type,
                scheduled_date=day_date.isoformat(),
                scheduled_time=t,
                group_stats=group_stats_block,
            )
            topic = goals_topic
        elif mtype == "evening":
            prompt = build_generation_prompt(
                "evening", "single", "", "",
                recent_sent=recent_sent_for_type,
                scheduled_date=day_date.isoformat(),
                scheduled_time=t,
                group_stats=group_stats_block,
            )
            topic = goals_topic
        else:  # discussion
            topic = topic_ids.get(cat)
            channel_topic_id = int(topic) if topic else None
            recent_for_channel = await _fetch_recent_sent_for_dedup(
                db, "discussion", category_topic_id=channel_topic_id, limit=60
            )
            prompt = build_generation_prompt(
                "discussion", "single", "", cat,
                recent_sent=recent_for_channel,
                scheduled_date=day_date.isoformat(),
                scheduled_time=t,
                group_stats=group_stats_block,
            )

        async def _gen_once(p: str) -> str:
            try:
                return await _generate_via_cli(p)
            except Exception:
                return await _generate_via_api(p)

        def _clean(raw: str) -> str:
            raw = raw.strip().replace('"', '').replace("'", "")
            ln = [x.strip() for x in raw.split("\n") if x.strip()]
            return ln[0] if ln else raw

        async with sem:
            try:
                content = _clean(await _gen_once(prompt))
            except Exception as e:
                return {"ok": False, "error": f"day {day_index}: generation failed: {e}"}

            # Quality gate: lint against config/question_quality.md rules.
            # On failure, retry once with a stricter suffix so the model
            # gets a chance to self-correct without re-running the whole
            # job. If it still fails: discussion gets a curated-pool
            # fallback (config/discussions.yaml has 8-10 examples per
            # category); morning/evening surface the failure.
            failures = _validate_draft_text(content)
            source = "ai-fill"
            if failures:
                logger.info("[weekplan.ai-fill] retry %s cat=%r reasons=%s",
                            mtype, cat, failures)
                try:
                    retry_prompt = prompt + "\n\n(הניסיון הקודם נדחה: " + ", ".join(failures) + ". נסח שאלה ספציפית אחרת בעברית טבעית, ללא ז'רגון אנגלי, שאלה אחת בלבד.)"
                    content = _clean(await _gen_once(retry_prompt))
                    failures = _validate_draft_text(content)
                except Exception as e:
                    failures = [f"retry generation failed: {e}"]

            if failures:
                if mtype == "discussion":
                    pool = discussions_pool.get(cat) or []
                    if pool:
                        content = _clean(random.choice(pool))
                        source = "ai-fill-pool"
                        logger.info("[weekplan.ai-fill] pool fallback cat=%r reasons=%s",
                                    cat, failures)
                    else:
                        return {"ok": False, "error": f"day {day_index} cat={cat}: validation failed ({failures[0]}) and no pool available"}
                else:
                    return {"ok": False, "error": f"day {day_index} {mtype}: validation failed ({failures[0]})"}

        # Topic-routing invariant: a discussion row's stored
        # channel_topic_id MUST match topic_ids[cat]. Without this guard
        # we have seen drafts land in unrelated channels (funny → ai_en).
        if mtype == "discussion":
            expected = topic_ids.get(cat)
            if not expected or int(expected) != int(topic or 0):
                return {"ok": False, "error": f"day {day_index} cat={cat}: topic mismatch expected={expected} got={topic}"}

        try:
            # Insert as 'draft' so they appear in the drafts panel for review
            # but DON'T auto-fire and don't conflict with other content already
            # scheduled at the same slot. The user picks which to keep, and
            # any existing 'auto'-materialized content at the same slot stays
            # untouched until the user removes it.
            new_id = await db.create_scheduled_message(
                text=content,
                message_type=mtype,
                channel_topic_id=int(topic) if topic else None,
                target_group="main",
                scheduled_date=day_date.isoformat(),
                scheduled_time=t,
                created_by=source,
                status="draft",
            )
            logger.info("[weekplan.ai-fill] created %s id=%d for %s %s cat=%r src=%s: %r",
                        mtype, new_id, day_date.isoformat(), t, cat, source, content[:60])
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": f"day {day_index}: db insert failed: {e}"}

    results = await asyncio.gather(*(_run_job(d, t, c) for d, t, c in jobs))
    for r in results:
        if r.get("ok"):
            created += 1
        else:
            errors.append(r.get("error", "unknown"))

    return {"created": created, "skipped": skipped, "errors": errors}


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
    return await _ai_fill_weekplan_inner(db, mtype, week_offset)


@app.post("/api/weekplan/ai-fill-regenerate")
async def ai_fill_regenerate(request: Request, db: Database = Depends(get_db)):
    """Wipe stale AI-generated drafts in a week window and regenerate them
    with the current prompt template. Targets `created_by LIKE 'ai-fill%'`
    only — user-edited rows (`created_by='dashboard'`/`'weekplan'`/etc.) are
    NOT deleted.

    Body: {week_offset?, types?[], target_date?: 'YYYY-MM-DD'}
    - week_offset (default 0) = current week
    - types defaults to all 3 supported types
    - target_date scopes both DELETE and the inner fill to one specific
      day. Used by the day-level Fill Today / Fill Specific Day buttons
      so they get the same always-generate semantics, just narrower.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    week_offset = int(data.get("week_offset", 0))
    types = data.get("types") or ["morning", "evening", "discussion"]
    target_date_raw = (data.get("target_date") or "").strip()
    target_date: str | None = target_date_raw or None
    if not isinstance(types, list):
        raise HTTPException(status_code=400, detail="types must be a list")
    if target_date:
        try:
            date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid target_date: {target_date}")

    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=days_since_sunday) + timedelta(weeks=week_offset)
    saturday = sunday + timedelta(days=6)

    # Wipe both 'scheduled' AND 'draft' ai-fill rows. ai-fill-today writes
    # rows as drafts (status='draft') for human review; if we only deleted
    # 'scheduled' rows, those drafts would survive and then occupy the same
    # (date, time, type) slot in the inner fill's `committed_keys` index,
    # silently blocking fresh generation. The user expects Populate to mean
    # "give me fresh content" — that requires wiping the stale drafts too.
    # Hand-edited rows still survive because their `created_by` is
    # 'dashboard'/'weekplan'/etc., not 'ai-fill%'.
    deleted = 0
    delete_window_start = target_date if target_date else sunday.isoformat()
    delete_window_end = target_date if target_date else saturday.isoformat()
    try:
        cur = await db._db.execute(
            "DELETE FROM scheduled_messages "
            "WHERE created_by LIKE 'ai-fill%' "
            "AND status IN ('scheduled', 'draft') "
            "AND scheduled_date BETWEEN ? AND ?",
            (delete_window_start, delete_window_end),
        )
        deleted = cur.rowcount or 0
        await db._db.commit()
        logger.info(
            "[ai-fill-regenerate] deleted %d AI rows (scheduled+draft) in [%s, %s]",
            deleted, delete_window_start, delete_window_end,
        )
    except Exception as e:
        logger.exception("[ai-fill-regenerate] delete failed")
        raise HTTPException(status_code=500, detail=f"Delete failed: {e}")

    total_created = 0
    total_skipped = 0
    total_errors: list[str] = []
    by_type: dict = {}
    for mtype in types:
        if mtype not in ("morning", "evening", "discussion"):
            total_errors.append(f"unsupported type skipped: {mtype}")
            continue
        try:
            r = await _ai_fill_weekplan_inner(db, mtype, week_offset, target_date=target_date)
            by_type[mtype] = r
            total_created += int(r.get("created", 0))
            total_skipped += int(r.get("skipped", 0))
            total_errors.extend(r.get("errors", []))
        except Exception as e:
            logger.exception("[ai-fill-regenerate] inner fill failed for %s", mtype)
            total_errors.append(f"{mtype}: {e}")

    return {
        "deleted": deleted,
        "created": total_created,
        "skipped": total_skipped,
        "errors": total_errors,
        "by_type": by_type,
    }


# ── AI fill: weekly trivia round ───────────────────────────

async def _handler_play_topic_or_error(db: Database, handler: str) -> int:
    routing = await db.get_handler_routing(handler)
    if not routing or routing.get("play_topic_id") is None:
        raise RuntimeError(f"missing bot_message_routing.play_topic_id for {handler}")
    return int(routing["play_topic_id"])


async def _ai_fill_trivia_for_week(
    db: Database, week_offset: int, target_date: str | None = None
) -> dict:
    """Schedule one mixed-pool trivia round for the requested week.

    Inserts two rows tagged `created_by='ai-fill-trivia'`:
      - Saturday 20:25 — `discussion` warm-up in botson_corner
      - Saturday 21:00 — `trivia_round` in botson_corner with mixed pool

    When `target_date` is provided ('YYYY-MM-DD'), the helper only
    inserts/wipes if that date IS the Saturday of the week. Used by
    Day-level Populate so a click on Saturday produces the trivia rows
    and a click on any other day is a no-op for trivia.

    Idempotent within a click: first wipes existing `ai-fill-trivia%` rows
    in the relevant window, then inserts fresh. The broader `LIKE 'ai-fill%'`
    wipe in `/api/weekplan/ai-fill-regenerate` also catches these.
    """
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=days_since_sunday) + timedelta(weeks=week_offset)
    saturday = sunday + timedelta(days=6)
    sat_iso = saturday.isoformat()

    # Day-level scoping: trivia only lands on Saturday. Other days = no-op.
    if target_date:
        if target_date != sat_iso:
            return {"inserted": 0, "errors": [], "scheduled_date": None,
                    "skipped_reason": f"target_date {target_date} is not Saturday"}
        wipe_start = wipe_end = sat_iso
    else:
        wipe_start = sunday.isoformat()
        wipe_end = sat_iso

    # Wipe any prior ai-fill-trivia rows in the relevant window
    try:
        await db._db.execute(
            "DELETE FROM scheduled_messages "
            "WHERE created_by LIKE 'ai-fill-trivia%' "
            "AND status IN ('scheduled', 'draft') "
            "AND scheduled_date BETWEEN ? AND ?",
            (wipe_start, wipe_end),
        )
        await db._db.commit()
    except Exception as e:
        logger.exception("[ai-fill-trivia] wipe failed")
        return {"inserted": 0, "errors": [f"wipe failed: {e}"]}

    inserted = 0
    errors: list[str] = []
    try:
        topic_id = await _handler_play_topic_or_error(db, "trivia_round")
    except Exception as e:
        return {"inserted": 0, "errors": [str(e)], "scheduled_date": sat_iso}

    settings = get_settings()
    trivia_defaults = (settings.get("trivia") or {}).get("populate_defaults") or {}
    trivia_cfg = (settings.get("schedule") or {}).get("trivia") or {}
    game_time = str(trivia_cfg.get("time") or "21:00")[:5]
    try:
        lead_minutes = int(trivia_defaults.get("warmup_offset_min") or 35)
    except (TypeError, ValueError):
        lead_minutes = 35
    try:
        game_h, game_m = [int(x) for x in game_time.split(":")]
        warm_total = game_h * 60 + game_m - lead_minutes
        if warm_total < 0:
            warm_total += 24 * 60
        warm_time = f"{warm_total // 60:02d}:{warm_total % 60:02d}"
    except Exception as e:
        return {"inserted": 0, "errors": [f"invalid trivia schedule: {e}"], "scheduled_date": sat_iso}

    try:
        theme_label = str(trivia_defaults.get("theme_label") or "כללי")
        min_ready = int(trivia_defaults.get("min_ready_players") or 0)
        warmup_text = await _generate_activity_copy(
            "trivia_warmup",
            avoid_texts=set(),
            game_time=game_time,
            lead_minutes=lead_minutes,
            theme_label=theme_label,
            min_ready_players=min_ready,
        )
        if warmup_text is None:
            raise RuntimeError("activity copy generation failed")
        await db.create_scheduled_message(
            text=warmup_text,
            message_type="discussion",
            channel_topic_id=topic_id,
            target_group="main",
            scheduled_date=sat_iso,
            scheduled_time=warm_time,
            created_by="ai-fill-trivia",
            status="draft",
        )
        inserted += 1
    except Exception as e:
        logger.exception("[ai-fill-trivia] warmup insert failed")
        errors.append(f"warmup: {e}")

    try:
        poll_payload = {
            "pre_roll_s": int(trivia_defaults.get("pre_roll_s") or 30),
            "theme_label": str(trivia_defaults.get("theme_label") or "כללי"),
            "categories": list(trivia_defaults.get("categories") or []),
            "question_count": int(trivia_defaults.get("question_count") or 5),
            "warmup_offset_min": lead_minutes,
            "min_ready_players": min_ready,
        }
        await db.create_scheduled_message(
            text="",
            message_type="trivia_round",
            channel_topic_id=topic_id,
            target_group="main",
            scheduled_date=sat_iso,
            scheduled_time=game_time,
            created_by="ai-fill-trivia",
            status="draft",
            poll_options=json.dumps(poll_payload, ensure_ascii=False),
        )
        inserted += 1
    except Exception as e:
        logger.exception("[ai-fill-trivia] round insert failed")
        errors.append(f"round: {e}")

    return {"inserted": inserted, "errors": errors, "scheduled_date": sat_iso}


@app.post("/api/weekplan/ai-fill-trivia")
async def ai_fill_trivia(request: Request, db: Database = Depends(get_db)):
    """Schedule one weekly trivia round + warm-up. See `_ai_fill_trivia_for_week`.

    Body: {week_offset?, target_date?: 'YYYY-MM-DD'}
    target_date scopes Day-level Populate: rows are only inserted if that
    date is the Saturday of the week.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    data = await request.json()
    week_offset = int(data.get("week_offset", 0))
    target_date_raw = (data.get("target_date") or "").strip()
    target_date: str | None = target_date_raw or None
    if target_date:
        try:
            date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid target_date: {target_date}")
    return await _ai_fill_trivia_for_week(db, week_offset, target_date=target_date)


# ── AI fill: pool-backed calendar rows (emoji + facts) ──────

async def _ai_fill_pool_rows_for_week(
    db: Database, week_offset: int, target_date: str | None = None
) -> dict:
    """Schedule pool-backed activities as calendar rows for the week:
    - Wed 22:00 emoji_puzzle row (botson_corner, draws from emoji_puzzles DB pool at fire time)
    - Tue 12:00 facts_tidbit row (botson_corner, picks from facts.yaml tidbit pool at fire time)
    - Thu 12:00 facts_tidbit row
    - Sat 22:00 facts_spooky row (after the 21:00 trivia round)

    When `target_date` is provided ('YYYY-MM-DD'), only the slots whose
    day matches that date are considered. Used by Day-level Populate.

    Skips a slot if its backing pool is empty so the bot doesn't fire onto
    an empty source. Tagged `created_by='ai-fill-pool-row'` so the
    `LIKE 'ai-fill%'` wipe pattern in `/api/weekplan/ai-fill-regenerate`
    handles re-runs without duplicates.
    """
    today = date.today()
    days_since_sunday = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=days_since_sunday) + timedelta(weeks=week_offset)
    saturday = sunday + timedelta(days=6)

    target_day_index: int | None = None
    if target_date:
        try:
            td = date.fromisoformat(target_date)
            delta = (td - sunday).days
            if 0 <= delta <= 6:
                target_day_index = delta
            else:
                return {"inserted": 0, "errors": [], "by_type": {},
                        "skipped_reason": f"target_date {target_date} outside week"}
        except ValueError:
            return {"inserted": 0, "errors": [f"invalid target_date: {target_date}"], "by_type": {}}

    wipe_start = target_date if target_date else sunday.isoformat()
    wipe_end = target_date if target_date else saturday.isoformat()

    # Wipe prior pool-row entries in this window
    try:
        await db._db.execute(
            "DELETE FROM scheduled_messages "
            "WHERE created_by LIKE 'ai-fill-pool-row%' "
            "AND status IN ('scheduled', 'draft') "
            "AND scheduled_date BETWEEN ? AND ?",
            (wipe_start, wipe_end),
        )
        await db._db.commit()
    except Exception as e:
        logger.exception("[ai-fill-pool-row] wipe failed")
        return {"inserted": 0, "errors": [f"wipe failed: {e}"], "by_type": {}}

    # Pool-availability checks: skip a slot when its source is empty so
    # the runtime handler isn't fed an unfireable row.
    try:
        async with db._db.execute("SELECT COUNT(*) FROM emoji_puzzles") as cur:
            emoji_pool_count = (await cur.fetchone())[0] or 0
    except Exception:
        emoji_pool_count = 0
    try:
        facts_yaml = load_yaml("facts.yaml") or {}
        tidbit_count = len(facts_yaml.get("tidbit") or [])
        spooky_count = len(facts_yaml.get("spooky") or [])
    except Exception:
        tidbit_count = 0
        spooky_count = 0

    slots: list[tuple[int, str, str, str]] = []  # (day_idx, time, mtype, text)
    if emoji_pool_count > 0:
        slots.append((3, "22:00", "emoji_puzzle", ""))
    if tidbit_count > 0:
        slots.append((2, "12:00", "facts_tidbit", ""))
        slots.append((4, "12:00", "facts_tidbit", ""))
    if spooky_count > 0:
        slots.append((6, "22:00", "facts_spooky", ""))

    # Day-level scoping: only slots whose day matches target_date.
    if target_day_index is not None:
        slots = [s for s in slots if s[0] == target_day_index]

    inserted = 0
    by_type: dict = {}
    errors: list[str] = []

    for day_idx, time_str, mtype, label in slots:
        day_date = sunday + timedelta(days=day_idx)
        try:
            topic_id = await _handler_play_topic_or_error(db, mtype)
            await db.create_scheduled_message(
                text=label,
                message_type=mtype,
                channel_topic_id=topic_id,
                target_group="main",
                scheduled_date=day_date.isoformat(),
                scheduled_time=time_str,
                created_by="ai-fill-pool-row",
                status="draft",
            )
            inserted += 1
            by_type[mtype] = by_type.get(mtype, 0) + 1
        except Exception as e:
            logger.exception("[ai-fill-pool-row] insert failed mtype=%s", mtype)
            errors.append(f"{mtype} {day_date.isoformat()}: {e}")

    skipped_empty: list[str] = []
    if emoji_pool_count == 0:
        skipped_empty.append("emoji_puzzles pool empty")
    if tidbit_count == 0:
        skipped_empty.append("facts.tidbit pool empty")
    if spooky_count == 0:
        skipped_empty.append("facts.spooky pool empty")

    return {
        "inserted": inserted,
        "by_type": by_type,
        "errors": errors,
        "skipped_empty": skipped_empty,
    }


@app.post("/api/weekplan/ai-fill-pool-rows")
async def ai_fill_pool_rows(request: Request, db: Database = Depends(get_db)):
    """Schedule emoji_puzzle + facts_tidbit + facts_spooky calendar rows for the week.

    Body: {week_offset?, target_date?: 'YYYY-MM-DD'}
    target_date scopes Day-level Populate to slots that fall on that date.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    data = await request.json()
    week_offset = int(data.get("week_offset", 0))
    target_date_raw = (data.get("target_date") or "").strip()
    target_date: str | None = target_date_raw or None
    if target_date:
        try:
            date.fromisoformat(target_date)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid target_date: {target_date}")
    return await _ai_fill_pool_rows_for_week(db, week_offset, target_date=target_date)


# ── AI Suggest: preview-then-confirm flow ───────────────────
#
# /api/weekplan/ai-suggest        — generates Hebrew text + slot list,
#                                   returns JSON, NO DB writes.
# /api/weekplan/ai-suggest-commit — takes the user-approved subset and
#                                   inserts each as a scheduled row.
#
# All slot times/topics are derived from `config/settings.yaml.schedule.*`
# and the `bot_message_routing` table — no day/time/topic literals here.


def _gather_schedule_slot_map(settings: dict) -> dict:
    """Return a dict mapping `message_type` -> {'times':[...]} pulled from
    settings.yaml.schedule. Each entry's `time` and `times` are merged into
    a single `times` list. `days` is intentionally NOT consumed — the
    suggestion engine picks days dynamically rather than restricting to
    the user's cron schedule.
    """
    sched = settings.get("schedule", {}) or {}
    out: dict = {}
    # canonical handler→schedule_key mapping mirrors how settings.yaml is
    # laid out today.
    pairs = [
        ("morning", "morning_prompt"),
        ("evening", "evening_prompt"),
        ("discussion", "discussion_prompt"),
        ("trivia_round", "trivia"),
        ("emoji_puzzle", "emoji_puzzle"),
        ("free_games", "free_games"),
        ("facts_tidbit", "facts_tidbit"),
        ("facts_spooky", "facts_spooky"),
        ("weekly_roundup", "weekly_roundup"),
        ("weekly_leaderboard", "weekly_leaderboard"),
    ]
    for mtype, key in pairs:
        cfg = sched.get(key, {}) or {}
        times: list = []
        if cfg.get("time"):
            times.append(cfg["time"])
        for t in cfg.get("times", []) or []:
            if t and t not in times:
                times.append(t)
        if times:
            out[mtype] = {"times": times}
    return out


def _ai_populate_caps(settings: dict, scope: str, slot_map: dict) -> dict:
    cfg = (settings.get("ai_populate") or {}).get("caps") or {}
    scoped = cfg.get("day" if scope == "day" else "week") or {}
    caps: dict[str, int] = {}
    for mtype in slot_map:
        try:
            caps[mtype] = max(0, int(scoped.get(mtype, 0)))
        except (TypeError, ValueError):
            caps[mtype] = 0
    return caps


def _hhmm_to_minutes(value: str) -> int | None:
    try:
        h, m = str(value or "").strip()[:5].split(":")
        hour = int(h)
        minute = int(m)
    except (TypeError, ValueError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _minutes_to_hhmm(value: int) -> str:
    value = max(0, min(23 * 60 + 59, int(value)))
    return f"{value // 60:02d}:{value % 60:02d}"


def _ai_populate_flex_config(settings: dict, scope: str) -> dict:
    flex = (settings.get("ai_populate") or {}).get("flex") or {}
    scoped = flex.get("day" if scope == "day" else "week") or {}
    if not flex.get("enabled") or not scoped.get("enabled"):
        return {}
    return scoped


def _expand_ai_flex_times(settings: dict, scope: str) -> list[str]:
    flex_cfg = _ai_populate_flex_config(settings, scope)
    times: list[str] = []
    for window in flex_cfg.get("windows") or []:
        if not isinstance(window, dict):
            continue
        start = _hhmm_to_minutes(window.get("start"))
        end = _hhmm_to_minutes(window.get("end"))
        try:
            step = int(window.get("step_minutes") or 0)
        except (TypeError, ValueError):
            step = 0
        if start is None or end is None or step <= 0 or end < start:
            continue
        current = start
        while current <= end:
            t = _minutes_to_hhmm(current)
            if t not in times:
                times.append(t)
            current += step
    return times


async def _resolve_routing_topic(db: Database, handler: str) -> int | None:
    """Look up `bot_message_routing.play_topic_id` for a handler. Returns
    None if no row — the caller decides what to do with that.
    """
    try:
        row = await db.get_handler_routing(handler)
        if row and row.get("play_topic_id") is not None:
            return int(row["play_topic_id"])
    except Exception:
        return None
    return None


async def _maybe_add_warmup_reminder_suggestion(
    *,
    _add_suggestion,
    slot_free,
    generated_activity_texts: set,
    d_iso: str,
    game_time: str,
    announce_t: str,
    announce_lead: int,
    topic: int,
    warmup_marker: str,
    theme_label: str,
    activity_label: str,
    min_ready: int,
    kind: str,
    source: str,
    settings: dict,
) -> None:
    """Append a `warmup_reminder` suggestion paired to an RSVP announcement.

    No-op when reminder offset is invalid, the slot is occupied, or the LLM
    copy generation fails. Reminder time = game_time - reminder_offset_min and
    must be strictly between announcement and game time.
    """
    if min_ready <= 0:
        return
    trivia_defaults = (settings.get("trivia") or {}).get("populate_defaults") or {}
    try:
        reminder_offset = int(trivia_defaults.get("warmup_reminder_offset_min") or 0)
    except (TypeError, ValueError):
        return
    if reminder_offset <= 0 or reminder_offset >= int(announce_lead):
        return
    try:
        gh, gm = [int(x) for x in str(game_time).split(":")]
        total = gh * 60 + gm - reminder_offset
        if total < 0:
            total += 24 * 60
        reminder_t = f"{total // 60:02d}:{total % 60:02d}"
    except Exception:
        return
    if reminder_t == announce_t:
        return
    if not slot_free(d_iso, reminder_t, "warmup_reminder"):
        return

    text = await _generate_activity_copy(
        kind,
        avoid_texts=generated_activity_texts,
        game_time=game_time,
        reminder_offset_min=reminder_offset,
        theme_label=theme_label,
        activity_label=activity_label,
        min_ready_players=min_ready,
        is_reminder=True,
    )
    if not text:
        return
    generated_activity_texts.add(text)
    poll = json.dumps({
        "min_ready_players": min_ready,
        "game_time": game_time,
        "theme_label": theme_label,
        "activity_label": activity_label,
        "warmup_marker": warmup_marker,
        "reminder_offset_min": reminder_offset,
    }, ensure_ascii=False)
    _add_suggestion(
        d_iso, reminder_t, "warmup_reminder",
        topic=topic,
        text=text,
        source=source,
        rationale=f"תזכורת RSVP — {reminder_offset} דקות לפני תחילת הפעילות",
        poll_options_json=poll,
        count_as=None,
    )


async def _ai_suggest_calendar(
    db: Database, target_date: str | None = None, week_offset: int = 0,
) -> dict:
    """Build a list of suggested calendar rows for a window without
    writing to the DB. The shape is intentionally close to a draft row so
    the commit step is a near-1:1 insert.

    Returns:
        {
          "window": {"start": "...", "end": "...", "scope": "day"|"week"},
          "suggestions": [
            {
              "key": "<uuid>",
              "date": "YYYY-MM-DD",
              "time": "HH:MM",
              "message_type": str,
              "topic_id": int,
              "topic_name": str,
              "category": str | None,
              "text": str,                      # for content types; placeholder for pool types
              "rationale": str,                 # short Hebrew "why this slot"
              "source": "ai-fill" | "ai-fill-pool" | "ai-fill-pool-row" | "ai-fill-trivia",
              "poll_options_json": str | None,  # for trivia_round
              "validation_failures": [str],     # empty if quality-clean
            }, ...
          ],
          "stats_block": str,
          "errors": [str],
        }
    """
    import uuid as _uuid

    now_dt = datetime.now()
    today_d = date.today()
    days_since_sunday = (today_d.weekday() + 1) % 7
    sunday = today_d - timedelta(days=days_since_sunday) + timedelta(weeks=week_offset)
    saturday = sunday + timedelta(days=6)

    if target_date:
        try:
            td = date.fromisoformat(target_date)
        except ValueError:
            return {"suggestions": [], "errors": [f"invalid target_date: {target_date}"],
                    "stats_block": "", "window": {}}
        window_dates = [td]
        scope = "day"
        win_start = win_end = td.isoformat()
    else:
        window_dates = [sunday + timedelta(days=i) for i in range(7)]
        scope = "week"
        win_start = sunday.isoformat()
        win_end = saturday.isoformat()

    settings = get_settings()
    slot_map = _gather_schedule_slot_map(settings)
    topic_ids = settings.get("topics", {}).get("discussions", {}) or {}
    goals_topic = settings.get("topics", {}).get("goals")
    welcome_topic = settings.get("topics", {}).get("welcome")

    # Existing rows in window — to avoid suggesting occupied or repeated slots.
    occupied: set = set()
    occupied_times: set = set()
    existing_activity_texts: set[str] = set()
    try:
        async with db._db.execute(
            "SELECT scheduled_date, scheduled_time, message_type, text "
            "FROM scheduled_messages "
            "WHERE status IN ('scheduled', 'draft') "
            "AND scheduled_date BETWEEN ? AND ?",
            (win_start, win_end),
        ) as cur:
            rows = await cur.fetchall()
        for r in rows:
            row_time = (r[1] or "")[:5]
            occupied.add((r[0], row_time, r[2]))
            occupied_times.add((r[0], row_time))
            if r[3]:
                existing_activity_texts.add(str(r[3]))
    except Exception:
        pass

    routed_topics: dict[str, int] = {}
    for handler in (
        "trivia_round", "trivia_warmup", "emoji_puzzle", "free_games", "facts_tidbit",
        "facts_spooky", "weekly_roundup", "weekly_leaderboard",
    ):
        topic = await _resolve_routing_topic(db, handler)
        if topic is not None:
            routed_topics[handler] = int(topic)

    # Topic name lookup for display in modal
    topic_names: dict = {}
    try:
        verified = await db.get_verified_forum_topics()
        for v in verified:
            try:
                topic_names[int(v["topic_id"])] = (
                    v.get("verified_name") or v.get("observed_name") or ""
                )
            except Exception:
                pass
    except Exception:
        pass

    # Group stats once (shared across LLM calls in this run)
    stats_block = await _render_group_stats_context(db)

    # Discussion category pool
    try:
        discussions_pool = load_yaml("discussions.yaml") or {}
    except Exception:
        discussions_pool = {}
    active_categories = [c for c in discussions_pool if c in topic_ids and topic_ids[c]]

    suggestions: list = []
    errors: list = []
    skip_reasons: list = []
    seen_skip_reasons: set[tuple[str, str, str, str]] = set()
    generated_activity_texts: set[str] = set()

    from bot.utils.copy import load_copy as _load_copy
    skip_reason_labels = {
        "past": _load_copy("ai_populate", "skip_past", default="past"),
        "past_or_too_soon": _load_copy("ai_populate", "skip_past_or_too_soon", default="past_or_too_soon"),
        "occupied": _load_copy("ai_populate", "skip_occupied", default="occupied"),
        "time_occupied": _load_copy("ai_populate", "skip_time_occupied", default="time_occupied"),
    }
    skip_default_label = _load_copy("ai_populate", "skip_default", default="skipped")
    empty_state_copy = {
        "title": _load_copy("ai_populate", "empty_title", default=""),
        "subtitle": _load_copy("ai_populate", "empty_subtitle", default=""),
        "fallback": _load_copy("ai_populate", "empty_fallback", default=""),
    }

    def _add_skip(d_iso: str, t: str, mtype: str, code: str, detail: str = "") -> None:
        key = (d_iso, str(t)[:5], mtype, code)
        if key in seen_skip_reasons:
            return
        seen_skip_reasons.add(key)
        skip_reasons.append({
            "date": d_iso,
            "time": str(t)[:5],
            "message_type": mtype,
            "code": code,
            "label": skip_reason_labels.get(code) or skip_default_label,
            "detail": detail,
        })

    def _slot_future(d_iso: str, t: str) -> bool:
        try:
            slot_dt = datetime.fromisoformat(f"{d_iso}T{str(t)[:5]}")
        except ValueError:
            return False
        return slot_dt >= now_dt

    def _slot_future_with_lead(d_iso: str, t: str, lead_minutes: int) -> bool:
        try:
            slot_dt = datetime.fromisoformat(f"{d_iso}T{str(t)[:5]}")
        except ValueError:
            return False
        return slot_dt >= now_dt + timedelta(minutes=max(0, int(lead_minutes)))

    def _slot_free(d_iso: str, t: str, mtype: str) -> bool:
        return _slot_future(d_iso, t) and (d_iso, t, mtype) not in occupied

    def _flex_slot_free(d_iso: str, t: str, mtype: str) -> bool:
        return _slot_free(d_iso, t, mtype) and (d_iso, t) not in occupied_times

    def _slot_available_or_skip(d_iso: str, t: str, mtype: str) -> bool:
        t = str(t)[:5]
        if not _slot_future(d_iso, t):
            _add_skip(d_iso, t, mtype, "past")
            return False
        if (d_iso, t, mtype) in occupied:
            _add_skip(d_iso, t, mtype, "occupied")
            return False
        return True

    def _flex_available_or_skip(d_iso: str, t: str, mtype: str) -> bool:
        t = str(t)[:5]
        if not _slot_future_with_lead(d_iso, t, flex_min_lead):
            _add_skip(d_iso, t, mtype, "past_or_too_soon")
            return False
        if (d_iso, t) in occupied_times:
            _add_skip(d_iso, t, mtype, "time_occupied")
            return False
        if (d_iso, t, mtype) in occupied:
            _add_skip(d_iso, t, mtype, "occupied")
            return False
        return True

    # ── Build candidate slot list ─────────────────────────────────
    # Strategy: for each day in window, lay out at most one of each
    # activity type at its natural time. Take a balanced subset across
    # the week. Day-scope = single day, all activity types if free.
    #
    # Time selection per type comes from slot_map (settings.yaml). When a
    # type has no time configured, it doesn't enter the candidate list.

    morning_t = (slot_map.get("morning") or {}).get("times") or []
    evening_t = (slot_map.get("evening") or {}).get("times") or []
    discussion_t = (slot_map.get("discussion") or {}).get("times") or []
    emoji_t = (slot_map.get("emoji_puzzle") or {}).get("times") or []
    free_games_t = (slot_map.get("free_games") or {}).get("times") or []
    tidbit_t = (slot_map.get("facts_tidbit") or {}).get("times") or []
    spooky_t = (slot_map.get("facts_spooky") or {}).get("times") or []
    trivia_t = (slot_map.get("trivia_round") or {}).get("times") or []
    roundup_t = (slot_map.get("weekly_roundup") or {}).get("times") or []
    leaderboard_t = (slot_map.get("weekly_leaderboard") or {}).get("times") or []
    flex_cfg = _ai_populate_flex_config(settings, scope)
    flex_t = _expand_ai_flex_times(settings, scope)
    flex_allowed = [
        str(item).strip()
        for item in (flex_cfg.get("allowed_types") or [])
        if str(item).strip() in {"discussion", "custom"}
    ]
    try:
        flex_max = max(0, int(flex_cfg.get("max_suggestions") or 0))
    except (TypeError, ValueError):
        flex_max = 0
    try:
        flex_min_lead = max(0, int(flex_cfg.get("min_lead_minutes") or 0))
    except (TypeError, ValueError):
        flex_min_lead = 0
    flex_rationale = str(flex_cfg.get("rationale") or "").strip()
    flex_count = 0

    cap_per_window = _ai_populate_caps(settings, scope, slot_map)
    counts = {k: 0 for k in cap_per_window}

    # T-170: retry budget configurable; default 3 (was effectively 1 retry,
    # then silent pool fallback). Each retry appends the prior draft + the
    # specific rejection reason + a "try a different angle" hint so the
    # model gets concrete guidance instead of repeating the same shape.
    _planner_retry_budget = int(
        ((settings.get("ai_populate") or {}).get("generation") or {}).get("retry_budget", 3)
    )
    from bot.utils.copy import load_copy as _load_copy_inner
    _planner_angle_hint = _load_copy_inner(
        "planner",
        "retry_angle_hint",
        default=(
            "נסה זווית שונה לגמרי: שאלה בינארית, דירוג, זיכרון קונקרטי, "  # noqa: hardcoded-content (Hebrew fallback only)
            "המלצה ספציפית, או דעה לא פופולרית."  # noqa: hardcoded-content (Hebrew fallback only)
        ),
    )

    async def _gen_text(field: str, cat: str, d_iso: str, t: str, recent: list) -> tuple[str, list]:
        """Run LLM with bounded retries + validate. Returns (text, validation_failures).

        On total failure returns ("", [last_reason, ...]) so the operator sees
        why generation failed instead of getting a silent pool fallback. Pool
        draws are a separate, explicit operator action (T-170).
        """
        base_prompt = build_generation_prompt(
            field, "single", "", cat,
            recent_sent=recent,
            scheduled_date=d_iso,
            scheduled_time=t,
            group_stats=stats_block,
        )
        # Source examples for the planner near-dup check: the curated
        # discussion pool for this category. Catches the LLM echoing
        # a pool item verbatim or paraphrasing it.
        sources: set[str] = set()
        if field == "discussion" and cat:
            sources = {
                str(x).strip()
                for x in (discussions_pool.get(cat) or [])
                if x
            }
        avoid_set = {str(x).strip() for x in (recent or []) if x}

        prompt = base_prompt
        last_text = ""
        last_fails: list[str] = []
        for attempt in range(max(1, _planner_retry_budget)):
            try:
                raw = await _generate_via_cli(prompt)
            except Exception:
                try:
                    raw = await _generate_via_api(prompt)
                except Exception as e:
                    return "", [f"generation failed: {e}"]
            text = (raw or "").strip().replace('"', '').replace("'", "")
            lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
            text = lines[0] if lines else text
            fails = _validate_draft_text(text)
            freshness_failure = freshness_rejection(
                text,
                avoid_texts=avoid_set,
                source_examples=sources,
                scheduled_date=d_iso,
            )
            if freshness_failure:
                fails.append(freshness_failure)
            if not fails:
                return text, []
            last_text = text
            last_fails = fails
            if attempt + 1 < _planner_retry_budget:
                prompt = (
                    base_prompt
                    + "\n\n(הניסיון הקודם נדחה: " + ", ".join(fails)
                    + f". {_planner_angle_hint})"
                )
        # All retries exhausted — return the last failure loudly. No silent
        # pool fallback (T-170): pool draws are an explicit operator button,
        # not a quality-failure hiding mechanism.
        return last_text, last_fails

    # Recent dedup blocks per type. T-170: limit shrunk 60→25 — the old
    # 60-item ceiling collided with ~25-item pool sizes and exhausted the
    # model's variance room. Near-dup detection (Jaccard via freshness) now
    # picks up paraphrases the substring check missed.
    _dedup_limit = int(
        ((settings.get("ai_populate") or {}).get("generation") or {}).get("dedup_window", 25)
    )
    recent_by_type: dict = {
        "morning": await _fetch_recent_sent_for_dedup(db, "morning", limit=_dedup_limit),
        "evening": await _fetch_recent_sent_for_dedup(db, "evening", limit=_dedup_limit),
    }

    def _emoji_media_aliases(media_type: str) -> list[str]:
        media = str(media_type or "").strip()
        if media in {"tv", "series"}:
            return ["tv", "series"]
        return [media] if media else []

    def _emoji_media_signature(media_types: list[str]) -> tuple[str, ...]:
        canonical = []
        for media in media_types:
            m = str(media or "").strip()
            if m == "tv":
                m = "series"
            if m and m not in canonical:
                canonical.append(m)
        return tuple(canonical)

    async def _recent_emoji_signatures(limit: int = 12) -> list[tuple[str, ...]]:
        out: list[tuple[str, ...]] = []
        try:
            async with db._db.execute(
                """SELECT poll_options FROM scheduled_messages
                   WHERE message_type = 'emoji_puzzle'
                     AND poll_options IS NOT NULL AND poll_options != ''
                     AND status IN ('sent', 'scheduled', 'draft')
                   ORDER BY scheduled_date DESC, scheduled_time DESC, id DESC
                   LIMIT ?""",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        except Exception:
            rows = []
        for row in rows:
            try:
                payload = json.loads(row[0] or "{}")
            except Exception:
                continue
            sig = _emoji_media_signature(payload.get("media_types") or [])
            if sig and sig not in out:
                out.append(sig)
        try:
            async with db._db.execute(
                """SELECT p.media_type
                   FROM emoji_puzzle_rounds r
                   JOIN emoji_puzzles p ON p.id = r.puzzle_id
                   WHERE p.media_type IS NOT NULL AND p.media_type != ''
                   ORDER BY r.sent_at DESC, r.id DESC
                   LIMIT ?""",
                (limit,),
            ) as cur:
                round_rows = await cur.fetchall()
        except Exception:
            round_rows = []
        for row in round_rows:
            sig = _emoji_media_signature([str(row[0] or "")])
            if sig and sig not in out:
                out.append(sig)
        return out

    async def _count_emoji_pool(media_types: list[str]) -> int:
        aliases: list[str] = []
        for media in media_types:
            for alias in _emoji_media_aliases(media):
                if alias and alias not in aliases:
                    aliases.append(alias)
        try:
            if aliases:
                placeholders = ",".join("?" for _ in aliases)
                async with db._db.execute(
                    f"SELECT COUNT(*) FROM emoji_puzzles WHERE enabled = 1 AND media_type IN ({placeholders})",
                    tuple(aliases),
                ) as cur:
                    return int((await cur.fetchone())[0] or 0)
            async with db._db.execute("SELECT COUNT(*) FROM emoji_puzzles WHERE enabled = 1") as cur:
                return int((await cur.fetchone())[0] or 0)
        except Exception:
            return 0

    async def _emoji_media_types_from_pool() -> list[str]:
        try:
            async with db._db.execute(
                "SELECT DISTINCT media_type FROM emoji_puzzles WHERE enabled = 1 AND media_type IS NOT NULL AND media_type != ''"
            ) as cur:
                rows = await cur.fetchall()
            return list(dict.fromkeys(
                "series" if str(row[0]).strip() == "tv" else str(row[0]).strip()
                for row in rows
                if str(row[0]).strip()
            ))
        except Exception:
            return []

    async def _choose_emoji_subject(emoji_cfg: dict, puzzle_count: int,
                                    recent_signatures: list[tuple[str, ...]],
                                    used_signatures: set[tuple[str, ...]]) -> tuple[str, list[str], int]:
        configured = [str(x).strip() for x in (emoji_cfg.get("media_types") or []) if str(x).strip()]
        if not configured:
            configured = await _emoji_media_types_from_pool()
        configured = ["series" if x == "tv" else x for x in configured]
        configured = list(dict.fromkeys(configured))
        labels = {"movie": "סרטים", "series": "סדרות", "tv": "סדרות", "game": "משחקים", "music": "מוזיקה", "book": "ספרים"}
        choices: list[tuple[str, list[str], int, tuple[str, ...]]] = []
        for media in configured:
            pool_n = await _count_emoji_pool([media])
            if pool_n >= puzzle_count:
                sig = _emoji_media_signature([media])
                choices.append((labels.get(media, str(emoji_cfg.get("theme_label") or media)), [media], pool_n, sig))
        if not choices:
            pool_n = await _count_emoji_pool(configured)
            if pool_n >= puzzle_count:
                sig = _emoji_media_signature(configured)
                label = str(emoji_cfg.get("theme_label") or " + ".join(configured)).strip()
                choices.append((label, configured, pool_n, sig))
        if not choices:
            return "", [], 0
        newest = recent_signatures[0] if recent_signatures else None
        ranked = sorted(
            choices,
            key=lambda c: (
                c[3] == newest,
                c[3] in used_signatures,
                (recent_signatures.index(c[3]) + 1) if c[3] in recent_signatures else 0,
                random.random(),
            ),
        )
        theme, media_types, pool_n, sig = ranked[0]
        used_signatures.add(sig)
        return theme, media_types, pool_n

    recent_emoji_signatures = await _recent_emoji_signatures()
    used_emoji_signatures: set[tuple[str, ...]] = set()

    def _trivia_category_counts() -> dict[str, int]:
        try:
            questions = (load_yaml("trivia.yaml") or {}).get("questions") or []
        except Exception:
            questions = []
        counts_by_cat: dict[str, int] = {}
        for q in questions:
            cat = str((q or {}).get("category") or "").strip()
            if cat:
                counts_by_cat[cat] = counts_by_cat.get(cat, 0) + 1
        return counts_by_cat

    def _trivia_signature(categories: list[str]) -> tuple[str, ...]:
        return tuple(str(c).strip() for c in categories if str(c).strip())

    async def _recent_trivia_signatures(limit: int = 12) -> list[tuple[str, ...]]:
        out: list[tuple[str, ...]] = []
        try:
            async with db._db.execute(
                """SELECT poll_options FROM scheduled_messages
                   WHERE message_type = 'trivia_round'
                     AND poll_options IS NOT NULL AND poll_options != ''
                     AND status IN ('sent', 'scheduled', 'draft')
                   ORDER BY scheduled_date DESC, scheduled_time DESC, id DESC
                   LIMIT ?""",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()
        except Exception:
            rows = []
        for row in rows:
            try:
                payload = json.loads(row[0] or "{}")
            except Exception:
                continue
            sig = _trivia_signature(payload.get("categories") or [])
            if sig and sig not in out:
                out.append(sig)
        return out

    def _choose_trivia_subject(trivia_cfg: dict, question_count: int,
                               counts_by_cat: dict[str, int],
                               recent_signatures: list[tuple[str, ...]],
                               used_signatures: set[tuple[str, ...]]) -> tuple[str, list[str], int]:
        configured = [str(x).strip() for x in (trivia_cfg.get("categories") or []) if str(x).strip()]
        eligible = [cat for cat, n in counts_by_cat.items() if n >= question_count]
        pool = [cat for cat in configured if cat in eligible] if configured else eligible
        if not pool:
            fallback_categories = configured
            from bot.utils.copy import default_theme_label
            fallback_theme = str(trivia_cfg.get("theme_label") or "").strip() or default_theme_label()
            fallback_count = min((counts_by_cat.get(c, 0) for c in fallback_categories), default=sum(counts_by_cat.values()))
            return fallback_theme, fallback_categories, fallback_count
        choices = []
        for cat in pool:
            sig = _trivia_signature([cat])
            choices.append((cat, [cat], counts_by_cat.get(cat, 0), sig))
        newest = recent_signatures[0] if recent_signatures else None
        ranked = sorted(
            choices,
            key=lambda c: (
                c[3] == newest,
                c[3] in used_signatures,
                recent_signatures.index(c[3]) if c[3] in recent_signatures else -1,
                random.random(),
            ),
        )
        theme, categories, count, sig = ranked[0]
        used_signatures.add(sig)
        return theme, categories, count

    def _preview_url(kind: str, **params) -> str:
        clean = {k: v for k, v in params.items() if v not in (None, "", [])}
        clean["kind"] = kind
        return "/planner/suggestion-preview?" + urlencode(clean, doseq=True)

    def _choose_fact_preview(pool: str) -> dict | None:
        try:
            items = [
                item for item in (load_yaml("facts.yaml") or {}).get(pool) or []
                if isinstance(item, dict) and item.get("id") and item.get("text_he")
            ]
            return random.choice(items) if items else None
        except Exception:
            return None

    trivia_category_counts = _trivia_category_counts()
    recent_trivia_signatures = await _recent_trivia_signatures()
    used_trivia_signatures: set[tuple[str, ...]] = set()

    # ── Walk window, propose slots ────────────────────────────────
    # Random-but-spread day pick: shuffle once so suggestions aren't all
    # on day 0 first. Cap per type enforces variety.
    day_indices = list(range(len(window_dates)))
    random.shuffle(day_indices)

    features = settings.get("features", {}) or {}

    def _feature_on(feature_key: str | None) -> bool:
        if not feature_key:
            return True
        return _is_feature_enabled_simple(features, feature_key)

    def _add_suggestion(d_iso: str, t: str, mtype: str, *, topic: int, text: str,
                        source: str, rationale: str, category: str | None = None,
                        poll_options_json: str | None = None,
                        validation_failures: list | None = None,
                        preview_url: str | None = None,
                        count_as: str | None = "__self__") -> None:
        if not _slot_future(d_iso, t):
            return
        quality_failures = (
            _quality_failures_for_planner_text(text, scheduled_date=d_iso)
            if mtype in {"morning", "evening", "discussion", "custom"}
            else []
        )
        suggestions.append({
            "key": _uuid.uuid4().hex,
            "date": d_iso,
            "time": t,
            "message_type": mtype,
            "topic_id": int(topic),
            "topic_name": topic_names.get(int(topic), ""),
            "category": category,
            "text": text,
            "rationale": rationale,
            "source": source,
            "poll_options_json": poll_options_json,
            "preview_url": preview_url,
            "validation_failures": validation_failures or [],
            "quality_status": "rejected" if quality_failures else "passed",
            "quality_failures": quality_failures,
        })
        if count_as == "__self__":
            count_as = mtype
        if count_as:
            counts[count_as] = counts.get(count_as, 0) + 1
        occupied.add((d_iso, t, mtype))
        occupied_times.add((d_iso, t))

    for di in day_indices:
        d = window_dates[di]
        d_iso = d.isoformat()
        weekday_idx = (d.weekday() + 1) % 7  # Sunday=0

        # Morning
        for t in morning_t:
            if not _feature_on("morning_prompt"):
                break
            if counts["morning"] >= cap_per_window["morning"]:
                break
            if not _slot_available_or_skip(d_iso, t, "morning"):
                continue
            text, fails = await _gen_text("morning", "", d_iso, t, recent_by_type["morning"])
            if not text:
                errors.extend(fails)
                continue
            if goals_topic:
                _add_suggestion(d_iso, t, "morning", topic=int(goals_topic),
                                text=text, source="ai-fill",
                                rationale="פתיחת יום — סלוט בוקר פנוי",
                                validation_failures=fails)
            break  # one morning per day

        # Evening
        for t in evening_t:
            if not _feature_on("evening_prompt"):
                break
            if counts["evening"] >= cap_per_window["evening"]:
                break
            if not _slot_available_or_skip(d_iso, t, "evening"):
                continue
            text, fails = await _gen_text("evening", "", d_iso, t, recent_by_type["evening"])
            if not text:
                errors.extend(fails)
                continue
            if goals_topic:
                _add_suggestion(d_iso, t, "evening", topic=int(goals_topic),
                                text=text, source="ai-fill",
                                rationale="סגירת יום — סלוט ערב פנוי",
                                validation_failures=fails)
            break

        # Discussion (1-2 cats per slot, capped at cap_per_window["discussion"])
        if active_categories and _feature_on("discussions"):
            cats_for_slot = random.sample(
                active_categories, min(2, len(active_categories))
            )
            for t in discussion_t:
                if counts["discussion"] >= cap_per_window["discussion"]:
                    break
                for cat in cats_for_slot:
                    if counts["discussion"] >= cap_per_window["discussion"]:
                        break
                    if not _slot_available_or_skip(d_iso, t, "discussion"):
                        continue
                    expected_topic = topic_ids.get(cat)
                    if not expected_topic:
                        continue
                    recent_chan = await _fetch_recent_sent_for_dedup(
                        db, "discussion", category_topic_id=int(expected_topic), limit=_dedup_limit,
                    )
                    text, fails = await _gen_text("discussion", cat, d_iso, t, recent_chan)
                    if not text:
                        errors.extend(fails)
                        continue
                    src = "ai-fill-pool" if (fails and not _validate_draft_text(text)) else "ai-fill"
                    _add_suggestion(d_iso, t, "discussion", topic=int(expected_topic),
                                    text=text, source=src, category=cat,
                                    rationale=f"שאלה ל{cat}",
                                    validation_failures=[])

        # Emoji puzzle row + announcement. Runtime filters the puzzle pool by
        # the selected subject payload, so the modal's subject is truthful.
        if (counts["emoji_puzzle"] < cap_per_window["emoji_puzzle"]
                and _feature_on("emoji_puzzle")
                and routed_topics.get("emoji_puzzle") is not None):
            emoji_cfg = (settings.get("schedule", {}) or {}).get("emoji_puzzle", {}) or {}
            emoji_count = int(emoji_cfg.get("puzzle_count") or 5)
            try:
                emoji_lead = int(emoji_cfg.get("announcement_lead_minutes") or 90)
            except (TypeError, ValueError):
                emoji_lead = 90
            emoji_theme, emoji_media_types, pool_n = await _choose_emoji_subject(
                emoji_cfg, emoji_count, recent_emoji_signatures, used_emoji_signatures,
            )
            if pool_n >= emoji_count:
                for t in emoji_t:
                    if not _slot_available_or_skip(d_iso, t, "emoji_puzzle"):
                        continue
                    try:
                        hh, mm = [int(x) for x in str(t).split(":")]
                        announce_total = hh * 60 + mm - emoji_lead
                        if announce_total < 0:
                            announce_total += 24 * 60
                        announce_t = f"{announce_total // 60:02d}:{announce_total % 60:02d}"
                    except Exception:
                        continue
                    emoji_min_ready = int(((settings.get("trivia") or {}).get("populate_defaults") or {}).get("min_ready_players") or 2)
                    # Invariant: when min_ready_players > 0, the game MUST be
                    # paired with a warmup announcement. If the announcement
                    # slot is past/occupied, skip the whole emoji slot — never
                    # emit a naked emoji_puzzle without RSVP plumbing.
                    if emoji_min_ready > 0 and not _slot_available_or_skip(d_iso, announce_t, "trivia_warmup_rsvp"):
                        continue
                    emoji_announcement_topic = int(welcome_topic or routed_topics["emoji_puzzle"])
                    emoji_text = await _generate_activity_copy(
                        "emoji_warmup",
                        avoid_texts=existing_activity_texts | generated_activity_texts,
                        game_time=t,
                        theme_label=emoji_theme,
                        puzzle_count=emoji_count,
                        min_ready_players=emoji_min_ready,
                    ) if emoji_min_ready > 0 else None
                    # Same invariant on LLM failure: if RSVP is required and
                    # we can't generate the announcement copy, skip the game
                    # too rather than scheduling it naked.
                    if emoji_min_ready > 0 and not emoji_text:
                        continue
                    emoji_announcement_emitted = False
                    emoji_warmup_marker = None
                    emoji_activity_label = f"Emoji Night על {emoji_theme} ({emoji_count} חידות)"
                    if emoji_text:
                        emoji_warmup_marker = f"warmup-rsvp:emoji:{d_iso}:{t}"
                        emoji_announce_poll = json.dumps({
                            "min_ready_players": emoji_min_ready,
                            "game_time": t,
                            "theme_label": emoji_theme,
                            "activity_label": emoji_activity_label,
                            "media_types": emoji_media_types,
                            "announcement_lead_minutes": emoji_lead,
                            "puzzle_count": emoji_count,
                            "warmup_marker": emoji_warmup_marker,
                        }, ensure_ascii=False)
                        generated_activity_texts.add(emoji_text)
                        _add_suggestion(
                            d_iso, announce_t, "trivia_warmup_rsvp", topic=emoji_announcement_topic,
                            text=emoji_text,
                            source="ai-fill-emoji",
                            rationale=f"הכרזה {emoji_lead} דקות לפני Emoji Night — מצטרפים ועדכונים",
                            poll_options_json=emoji_announce_poll,
                            count_as=None,
                        )
                        emoji_announcement_emitted = True
                        await _maybe_add_warmup_reminder_suggestion(
                            _add_suggestion=_add_suggestion,
                            slot_free=_slot_free,
                            generated_activity_texts=generated_activity_texts,
                            d_iso=d_iso,
                            game_time=t,
                            announce_t=announce_t,
                            announce_lead=emoji_lead,
                            topic=emoji_announcement_topic,
                            warmup_marker=emoji_warmup_marker,
                            theme_label=emoji_theme,
                            activity_label=f"Emoji Night על {emoji_theme}",
                            min_ready=emoji_min_ready,
                            kind="emoji_warmup_reminder",
                            source="ai-fill-emoji",
                            settings=settings,
                        )
                    # T-127: stamp marker on game row only when an announcement
                    # was actually emitted, so the dispatch-time gate has a
                    # paired RSVP pool to count (or stays a no-op if absent).
                    emoji_game_poll = {
                        "theme_label": emoji_theme,
                        "media_types": emoji_media_types,
                        "puzzle_count": emoji_count,
                        "activity_label": emoji_activity_label,
                    }
                    if emoji_announcement_emitted and emoji_warmup_marker:
                        emoji_game_poll["min_ready_players"] = emoji_min_ready
                        emoji_game_poll["warmup_marker"] = emoji_warmup_marker
                    _add_suggestion(d_iso, t, "emoji_puzzle", topic=routed_topics["emoji_puzzle"],
                                    text="",
                                    source="ai-fill-pool-row",
                                    rationale=f"נושא: {emoji_theme} · מאגר מתאים ({pool_n} פריטים)",
                                    preview_url=_preview_url(
                                        "emoji_puzzle",
                                        theme=emoji_theme,
                                        media=emoji_media_types,
                                        count=emoji_count,
                                    ),
                                    poll_options_json=json.dumps(emoji_game_poll, ensure_ascii=False))
                    break

        # Facts tidbit (max cap)
        if (counts["facts_tidbit"] < cap_per_window["facts_tidbit"]
                and routed_topics.get("facts_tidbit") is not None):
            try:
                fy = load_yaml("facts.yaml") or {}
                tn = len(fy.get("tidbit") or [])
            except Exception:
                tn = 0
            for t in tidbit_t:
                if not _slot_available_or_skip(d_iso, t, "facts_tidbit"):
                    continue
                preview_fact = _choose_fact_preview("tidbit")
                fact_payload = json.dumps({"fact_id": preview_fact.get("id")}, ensure_ascii=False) if preview_fact else None
                rationale = f"מאגר עובדות פעיל ({tn} פריטים)" if tn > 0 else "סלוט עובדה פנוי"
                _add_suggestion(d_iso, t, "facts_tidbit", topic=routed_topics["facts_tidbit"],
                                text=(str(preview_fact.get("text_he") or "").strip() if preview_fact else ""),
                                source="ai-fill-pool-row",
                                rationale=rationale,
                                poll_options_json=fact_payload,
                                preview_url=(_preview_url("facts_tidbit", id=preview_fact.get("id")) if preview_fact else None))
                break

        # Facts spooky (max 1)
        if (counts["facts_spooky"] < cap_per_window["facts_spooky"]
                and routed_topics.get("facts_spooky") is not None):
            try:
                fy = load_yaml("facts.yaml") or {}
                sn = len(fy.get("spooky") or [])
            except Exception:
                sn = 0
            for t in spooky_t:
                if not _slot_available_or_skip(d_iso, t, "facts_spooky"):
                    continue
                preview_fact = _choose_fact_preview("spooky")
                fact_payload = json.dumps({"fact_id": preview_fact.get("id")}, ensure_ascii=False) if preview_fact else None
                rationale = f"מאגר ספוקי פעיל ({sn} פריטים)" if sn > 0 else "סלוט סיפור מסתורי פנוי"
                _add_suggestion(d_iso, t, "facts_spooky", topic=routed_topics["facts_spooky"],
                                text=(str(preview_fact.get("text_he") or "").strip() if preview_fact else ""),
                                source="ai-fill-pool-row",
                                rationale=rationale,
                                poll_options_json=fact_payload,
                                preview_url=(_preview_url("facts_spooky", id=preview_fact.get("id")) if preview_fact else None))
                break

        # Free games / live weekly bot-generated rows.
        if (counts.get("free_games", 0) < cap_per_window.get("free_games", 0)
                and _feature_on("free_games")
                and routed_topics.get("free_games") is not None):
            for t in free_games_t:
                if not _slot_available_or_skip(d_iso, t, "free_games"):
                    continue
                _add_suggestion(d_iso, t, "free_games", topic=routed_topics["free_games"],
                                text="",
                                source="ai-fill-pool-row",
                                preview_url=_preview_url("free_games"),
                                rationale="סלוט משחקים חינם פנוי")
                break

        if (counts.get("weekly_roundup", 0) < cap_per_window.get("weekly_roundup", 0)
                and _feature_on("roundup")
                and routed_topics.get("weekly_roundup") is not None):
            for t in roundup_t:
                if not _slot_available_or_skip(d_iso, t, "weekly_roundup"):
                    continue
                _add_suggestion(d_iso, t, "weekly_roundup", topic=routed_topics["weekly_roundup"],
                                text="",
                                source="ai-fill-pool-row",
                                preview_url=_preview_url("weekly_roundup"),
                                rationale="סלוט סיכום שבועי פנוי")
                break

        if (counts.get("weekly_leaderboard", 0) < cap_per_window.get("weekly_leaderboard", 0)
                and _feature_on("levels")
                and routed_topics.get("weekly_leaderboard") is not None):
            for t in leaderboard_t:
                if not _slot_available_or_skip(d_iso, t, "weekly_leaderboard"):
                    continue
                _add_suggestion(d_iso, t, "weekly_leaderboard", topic=routed_topics["weekly_leaderboard"],
                                text="",
                                source="ai-fill-pool-row",
                                preview_url=_preview_url("weekly_leaderboard"),
                                rationale="סלוט טבלת רמות פנוי")
                break

        # Trivia round + warm-up. Defaults and warm-up offset come from settings.
        if (counts.get("trivia_round", 0) < cap_per_window.get("trivia_round", 0)
                and _feature_on("trivia")
                and routed_topics.get("trivia_round") is not None):
            for t in trivia_t:
                if not _slot_available_or_skip(d_iso, t, "trivia_round"):
                    continue
                trivia_cfg = (settings.get("trivia") or {}).get("populate_defaults") or {}
                try:
                    hh, mm = [int(x) for x in t.split(":")]
                    warmup_offset = int(trivia_cfg["warmup_offset_min"])
                    total = hh * 60 + mm - warmup_offset
                    if total < 0:
                        total += 24 * 60
                    warm_t = f"{total // 60:02d}:{total % 60:02d}"
                except Exception as e:
                    errors.append(f"trivia populate_defaults invalid: {e}")
                    break
                poll_payload = {
                    "pre_roll_s": int(trivia_cfg["pre_roll_s"]),
                    "question_count": int(trivia_cfg["question_count"]),
                    "min_ready_players": int(trivia_cfg.get("min_ready_players") or 0),
                }
                trivia_theme, trivia_categories, trivia_pool_n = _choose_trivia_subject(
                    trivia_cfg,
                    int(poll_payload["question_count"]),
                    trivia_category_counts,
                    recent_trivia_signatures,
                    used_trivia_signatures,
                )
                poll_payload["theme_label"] = trivia_theme
                poll_payload["categories"] = trivia_categories
                if trivia_categories and trivia_pool_n < int(poll_payload["question_count"]):
                    errors.append(
                        f"trivia pool too small for {trivia_categories}: {trivia_pool_n}/{poll_payload['question_count']}"
                    )
                    continue
                min_ready = int(poll_payload.get("min_ready_players") or 0)
                trivia_activity_label = f"הטריוויה על {poll_payload['theme_label']} ({poll_payload['question_count']} שאלות)"
                # Invariant: when min_ready_players > 0, trivia_round MUST be
                # paired with a warmup announcement + reminder. If the warm-up
                # slot is past/occupied OR the LLM rejects the copy, skip the
                # game entirely — never schedule a naked trivia_round without
                # RSVP plumbing.
                trivia_warmup_marker = None
                if min_ready > 0:
                    if not _slot_available_or_skip(d_iso, warm_t, "trivia_warmup_rsvp"):
                        continue
                    warmup_text = await _generate_activity_copy(
                        "trivia_warmup",
                        avoid_texts=existing_activity_texts | generated_activity_texts,
                        game_time=t,
                        warmup_offset_min=warmup_offset,
                        theme_label=poll_payload["theme_label"],
                        question_count=poll_payload["question_count"],
                        min_ready_players=min_ready,
                    )
                    if not warmup_text:
                        continue
                    warmup_topic = routed_topics.get("trivia_warmup") or routed_topics["trivia_round"]
                    trivia_warmup_marker = f"warmup-rsvp:trivia:{d_iso}:{t}"
                    warmup_poll_json = json.dumps({
                        "min_ready_players": min_ready,
                        "game_time": t,
                        "theme_label": poll_payload["theme_label"],
                        "activity_label": trivia_activity_label,
                        "warmup_marker": trivia_warmup_marker,
                    }, ensure_ascii=False)
                    generated_activity_texts.add(warmup_text)
                    _add_suggestion(d_iso, warm_t, "trivia_warmup_rsvp", topic=warmup_topic,
                                    text=warmup_text,
                                    source="ai-fill-trivia",
                                    rationale="חימום לסיבוב טריוויה — מצטרפים חדשים",
                                    poll_options_json=warmup_poll_json,
                                    count_as=None)
                    await _maybe_add_warmup_reminder_suggestion(
                        _add_suggestion=_add_suggestion,
                        slot_free=_slot_free,
                        generated_activity_texts=generated_activity_texts,
                        d_iso=d_iso,
                        game_time=t,
                        announce_t=warm_t,
                        announce_lead=warmup_offset,
                        topic=warmup_topic,
                        warmup_marker=trivia_warmup_marker,
                        theme_label=poll_payload["theme_label"],
                        activity_label=trivia_activity_label,
                        min_ready=min_ready,
                        kind="trivia_warmup_reminder",
                        source="ai-fill-trivia",
                        settings=settings,
                    )
                # T-127: stamp marker on game row when an announcement was
                # emitted (always when min_ready > 0 by the invariant above).
                # Always include activity_label so threshold-met confirmation
                # and dispatch-cancel notice mention the question count.
                poll_payload["activity_label"] = trivia_activity_label
                if trivia_warmup_marker:
                    poll_payload["warmup_marker"] = trivia_warmup_marker
                _add_suggestion(d_iso, t, "trivia_round", topic=routed_topics["trivia_round"],
                                 text="",
                                 source="ai-fill-trivia",
                                rationale=f"נושא: {poll_payload['theme_label']} · מאגר מתאים ({trivia_pool_n} שאלות)",
                                preview_url=_preview_url(
                                    "trivia_round",
                                    theme=poll_payload["theme_label"],
                                    categories=poll_payload["categories"],
                                    count=poll_payload["question_count"],
                                ),
                                 poll_options_json=json.dumps(poll_payload, ensure_ascii=False))
                break

        if flex_count < flex_max and flex_t and flex_allowed and active_categories and _feature_on("discussions"):
            cats_for_flex = random.sample(active_categories, len(active_categories))
            for t in flex_t:
                if flex_count >= flex_max:
                    break
                for mtype in flex_allowed:
                    if flex_count >= flex_max:
                        break
                    if not _flex_available_or_skip(d_iso, t, mtype):
                        continue
                    cat = cats_for_flex[flex_count % len(cats_for_flex)]
                    expected_topic = topic_ids.get(cat)
                    if not expected_topic:
                        continue
                    recent_chan = await _fetch_recent_sent_for_dedup(
                        db, "discussion", category_topic_id=int(expected_topic), limit=_dedup_limit,
                    )
                    text, fails = await _gen_text("discussion", cat, d_iso, t, recent_chan)
                    if not text:
                        errors.extend(fails)
                        continue
                    src = "ai-fill-flex-pool" if (fails and not _validate_draft_text(text)) else "ai-fill-flex"
                    _add_suggestion(
                        d_iso, t, mtype,
                        topic=int(expected_topic),
                        text=text,
                        source=src,
                        category=cat,
                        rationale=flex_rationale,
                        validation_failures=[],
                        count_as=None,
                    )
                    flex_count += 1
                    break

    return {
        "window": {"start": win_start, "end": win_end, "scope": scope},
        "suggestions": suggestions,
        "stats_block": stats_block,
        "errors": errors,
        "skip_reasons": skip_reasons,
        "empty_state": empty_state_copy,
    }


async def _read_json_object_body(request: Request, log_prefix: str) -> dict:
    raw = await request.body()
    if not raw or not raw.strip():
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        logger.warning("[%s] invalid JSON body: %s", log_prefix, e)
        raise HTTPException(status_code=400, detail=f"Invalid JSON body: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")
    return data


def _parse_ai_suggest_request(data: dict) -> tuple[str | None, int]:
    try:
        week_offset = int(data.get("week_offset", 0))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Invalid week_offset: {data.get('week_offset')!r}")
    target_date_raw = (data.get("target_date") or "").strip()
    if target_date_raw.lower() == "today":
        target_date_raw = datetime.now(ZoneInfo("Asia/Jerusalem")).date().isoformat()
    target_date: str | None = target_date_raw or None
    if target_date:
        try:
            date.fromisoformat(target_date)
        except ValueError:
            logger.warning("[weekplan.ai-suggest] invalid target_date=%r body=%s", target_date, data)
            raise HTTPException(status_code=400, detail=f"Invalid target_date: {target_date}")
    return target_date, week_offset


async def _run_ai_suggest_job(job_id: str, db: Database, target_date: str | None, week_offset: int) -> None:
    job = _AI_SUGGEST_JOBS.get(job_id)
    if not job:
        return
    job["status"] = "running"
    try:
        result = await _ai_suggest_calendar(db, target_date=target_date, week_offset=week_offset)
        job.update({"status": "completed", "result": result, "completed_at": time.monotonic()})
    except asyncio.CancelledError:
        job.update({"status": "cancelled", "error": "AI suggest cancelled", "completed_at": time.monotonic()})
        raise
    except Exception as e:
        logger.exception("[weekplan.ai-suggest] job failed id=%s target_date=%r week_offset=%s", job_id, target_date, week_offset)
        job.update({"status": "failed", "error": f"AI suggest failed: {type(e).__name__}: {e}", "completed_at": time.monotonic()})


@app.post("/api/weekplan/ai-suggest")
async def ai_suggest(request: Request, db: Database = Depends(get_db)):
    """Build calendar-fill suggestions for review. Does NOT touch the DB.

    Body: {target_date?: 'YYYY-MM-DD', week_offset?: int}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    data = await _read_json_object_body(request, "weekplan.ai-suggest")
    target_date, week_offset = _parse_ai_suggest_request(data)
    _cleanup_ai_suggest_jobs()
    job_id = secrets.token_urlsafe(18)
    _AI_SUGGEST_JOBS[job_id] = {
        "id": job_id,
        "status": "pending",
        "created_at": time.monotonic(),
        "target_date": target_date,
        "week_offset": week_offset,
    }
    task = asyncio.create_task(_run_ai_suggest_job(job_id, db, target_date, week_offset))
    _AI_SUGGEST_JOBS[job_id]["task"] = task
    return {"job_id": job_id, "status": "pending"}


@app.get("/api/weekplan/ai-suggest/{job_id}")
async def ai_suggest_status(job_id: str, request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    _cleanup_ai_suggest_jobs()
    job = _AI_SUGGEST_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="AI suggest job not found")
    status = str(job.get("status") or "pending")
    response = {"job_id": job_id, "status": status}
    if status == "completed":
        response["result"] = job.get("result") or {}
    elif status in {"failed", "cancelled"}:
        response["error"] = job.get("error") or "AI suggest failed"
    return response


@app.post("/api/weekplan/ai-suggest/{job_id}/cancel")
async def ai_suggest_cancel(job_id: str, request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    _cleanup_ai_suggest_jobs()
    job = _AI_SUGGEST_JOBS.get(job_id)
    if not job:
        return {"job_id": job_id, "status": "missing"}
    task = job.get("task")
    if task and not task.done():
        task.cancel()
    if str(job.get("status") or "") not in {"completed", "failed", "cancelled"}:
        job.update({"status": "cancelled", "error": "AI suggest cancelled", "completed_at": time.monotonic()})
    return {"job_id": job_id, "status": job.get("status")}


@app.post("/api/weekplan/ai-suggest-commit")
async def ai_suggest_commit(request: Request, db: Database = Depends(get_db)):
    """Insert the user-approved subset of suggestions as scheduled rows.

    Body: {approved: [{date, time, message_type, topic_id, text,
                       category?, source, poll_options_json?}, ...]}
    Each item is validated (topic must be a known integer; type must be
    in the supported set; date must parse) before insert. Inserts use the
    item's `source` as `created_by` so they remain inside the
    `LIKE 'ai-fill%'` wipe pattern.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    data = await _read_json_object_body(request, "weekplan.ai-suggest-commit")
    approved = data.get("approved") or []
    if not isinstance(approved, list):
        logger.warning("[weekplan.ai-suggest-commit] approved is not list: %r", type(approved).__name__)
        raise HTTPException(status_code=400, detail="approved must be a list")

    valid_types = {
        "morning", "evening", "discussion", "custom", "trivia_round", "trivia_warmup_rsvp",
        "warmup_reminder",
        "emoji_puzzle", "free_games", "facts_tidbit", "facts_spooky",
        "weekly_roundup", "weekly_leaderboard",
    }
    inserted_ids: list = []
    errors: list = []
    by_type: dict = {}

    for i, item in enumerate(approved):
        if not isinstance(item, dict):
            errors.append(f"#{i}: not a dict")
            continue
        try:
            d = str(item["date"])
            t = str(item["time"])
            mtype = str(item["message_type"])
            topic = int(item["topic_id"])
            text = str(item.get("text") or "")
        except (KeyError, ValueError, TypeError) as e:
            errors.append(f"#{i}: bad shape ({e})")
            continue
        if mtype not in valid_types:
            errors.append(f"#{i}: unknown type {mtype}")
            continue
        try:
            date.fromisoformat(d)
        except ValueError:
            errors.append(f"#{i}: bad date {d}")
            continue

        if mtype in {"morning", "evening", "discussion", "custom"}:
            failures = _validate_draft_text(text)
            freshness_failure = freshness_rejection(text, scheduled_date=d)
            if freshness_failure:
                failures.append(freshness_failure)
            if failures:
                errors.append(f"#{i}: quality rejected ({', '.join(failures)})")
                continue

        source = str(item.get("source") or "ai-fill")
        if not source.startswith("ai-fill"):
            source = "ai-fill"  # anchor inside the wipe pattern

        category = str(item.get("category") or "").strip()
        if mtype == "discussion" and category:
            expected_topics = (get_settings().get("topics", {}).get("discussions", {}) or {})
            expected_topic = expected_topics.get(category)
            if expected_topic is None:
                errors.append(f"#{i}: unknown discussion category {category}")
                continue
            if int(expected_topic) != topic:
                errors.append(f"#{i}: discussion topic mismatch for {category}")
                continue

        poll_options_json = item.get("poll_options_json") or None
        if poll_options_json is not None and not isinstance(poll_options_json, str):
            try:
                poll_options_json = json.dumps(poll_options_json, ensure_ascii=False)
            except Exception:
                poll_options_json = None

        try:
            new_id = await db.create_scheduled_message(
                text=text,
                message_type=mtype,
                channel_topic_id=topic,
                target_group="main",
                scheduled_date=d,
                scheduled_time=t,
                created_by=source,
                status="scheduled",
                poll_options=poll_options_json,
            )
            inserted_ids.append(new_id)
            by_type[mtype] = by_type.get(mtype, 0) + 1
        except Exception as e:
            errors.append(f"#{i}: insert failed: {e}")

    logger.info(
        "[weekplan.ai-suggest-commit] inserted=%d ids=%s by_type=%s errors=%s",
        len(inserted_ids), inserted_ids, by_type, errors,
    )
    return {"inserted": len(inserted_ids), "ids": inserted_ids,
            "by_type": by_type, "errors": errors}


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

═════════════════════════════════════════════════════════════
**🚨 CRITICAL — אסור-מוחלט לכל הסלוטים, יוצא לפני כל כלל אחר:**

1. **אם hebrew_day_name = "שבת" — זהו לב סוף השבוע, הוא לא עבר.** אסור מוחלט להשתמש בביטויים שמרמזים שסוף השבוע נגמר:
   - ❌ "סיכום סוף השבוע"
   - ❌ "איך היה הסוף שבוע"
   - ❌ "מה היה הרגע הכי טוב מהשבוע/סוף השבוע"
   - ❌ "השבוע שעבר"
   - ❌ כל ניסוח שמתייחס לסוף שבוע בעבר
   רק מותר: "מה עושים הערב?", "מה רואים הלילה?", "איך היום מתקדם?", "מה עוד מתכננים?". סוף שבוע = פעיל ונמשך.

2. **אם hebrew_day_name = "שישי" אחרי 18:00** — סוף השבוע התחיל. אסור "מה היה?" — רק "מה מתכננים?", "מה עכשיו?".

3. **אם hebrew_day_name = "ראשון" בבוקר** — *זה* הזמן הנכון לסיכום סוף שבוע. רק כאן מותר "איך היה?".

עבירה על הכלל הזה = פלט לא תקין. בדוק כל text שאתה כותב מול הכלל הזה לפני שאתה שולח.
═════════════════════════════════════════════════════════════

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
    - **trivia, emoji, free_games, facts_tidbit, facts_spooky, weekly_roundup, weekly_leaderboard** — אם הסקציה לא ריקה, מותר להציע סלוט ביצוע ישיר עם type מתאים מתוך הסכמה. idempotence נשמר ע"י existing_drafts_today.
   - יוצא מן הכלל יחיד: **חג עם block_auto:true** — דלג כל הסקציות, כבר מכוסה ע"י short-circuit במשתנה holiday.

6. **אל תכפיל מול existing_drafts_today.** הרשימה כוללת ai-fill-today rows מכל סטטוס (draft, scheduled, sent) — לא רק drafts ממתינים. הכלל:
   - אם רשומה קיימת עם `status='draft'` — האדמין עוד מתכוון לעבוד עליה. דלג והוסף ב-notes_for_admin: "כבר קיימת טיוטה ממתינה ל-X".
   - אם רשומה קיימת עם `status='scheduled'` — האדמין כבר אישר את הסלוט. **אסור לחלוטין** להציע סלוט נוסף לאותו (scheduled_time, message_type, topic_id) או לאותו covered_event_ids. אם הצעת — האדמין יקבל כפיל. רשום ב-skipped: "כבר תוזמן ע"י המנהל".
   - אם רשומה קיימת עם `status='sent'` — כבר נשלח לקהילה. אסור להציע משהו דומה לאותה שעה/טופיק.
   - אם תזכורת: covered_event_ids שלך חופף לסט קיים → דלג ב-skipped.reminders "already_drafted".
   - **לסלוטים custom (הזמנות אקטיביות)**: בדוק לפי (scheduled_time, topic_id) — לא רק לפי message_type, כי custom יכול להיות בכל שעה. אם יש כבר ai-fill-today custom באותה שעה+טופיק (כל סטטוס) — דלג.

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

   - **שאלות discussion חייבות להיות חדות וחיביתיות** — אסור שאלות-תבנית ("איזה X אהבתם?", "מה הY האהוב?"). אם אפשר להחליף את שם הערוץ ולא להפסיד דבר — זה generic, תקן.

     **מבחן ה"swap":** קח את השאלה. החלף את שם הערוץ במשהו אחר (gaming → cooking, movies → politics, art → vegan). אם השאלה עדיין הגיונית והאיכות לא נפגעה — היא generic. תכתוב מחדש.

     **דוגמאות מפורשות לאיסור:**
     ❌ "מה היצירה האחרונה שצפיתם בה ואמרתם 'מי הנפש שמאחורי זה'? ציור, תצלום, מוזיקה, כתיבה — כל אחד מביא דוגמה."
        — multi-medium chain (ציור/תצלום/מוזיקה/כתיבה = 4 sub-categories), framing מעורפל ("מי הנפש"), swap-test fail (עובד גם ב-vegan/geek/movies), לחץ קבוצתי ("כל אחד מביא").
     ❌ "מה הסרט/הסדרה/הספר/הפודקאסט האחרון..." — listing N media categories ב-slot אחד = multi-ask.
     ❌ "ספרו על משהו שהשפיע עליכם" — generic invite, אפשר לשאול אותו דבר בכל ערוץ.
     ✅ "צילום אחד שצילמתם בסבב האחרון בעיר — שלפו." (ערוץ אומנות, single medium, action verb, specific time anchor)
     ✅ "פיצ'ר את שלושת האלבומים שאתם מאזינים להם הכי הרבה לאחרונה — נראה דפוס?" (collective list, specific count, observable result)

     **חוק יחיד-מדיה:** שאלה שמזכירה יותר ממדיה אחת או יותר מתת-קטגוריה אחת באותו ערוץ — שגויה. תפצל אותה לשאלות נפרדות, או בחר אחת ותתחייב.

     **חוק שבוע ישראלי — חובה:**
     - **ראשון** = היום הראשון בשבוע. ראשון בבוקר = פתיחת שבוע. ראשון בלילה = סוף יום ראשון, אמצע שבוע מתחיל.
     - **שני** ≠ "שבוע חדש". שני זה היום השני, השבוע כבר התחיל.
     - **שישי+שבת** = סוף שבוע (לא ראשון).
     - אסור להגיד "שבוע חדש התחיל" אם היום שני, שלישי, רביעי, חמישי. רק ראשון או שישי-לפני-יום ראשון.
     - ❌ "🌙 שני בלילה — שבוע חדש התחיל." — שגוי. שני זה אמצע שבוע.
     - ✅ "🌙 ראשון בערב — היום הראשון של השבוע נסגר. מה המשימה הכי חשובה השבוע?"

     **חוק dedup סמנטי — חובה לפני submit:**
     1. בדוק את recent_sent_samples_by_type ואת this_week_previews. אם השאלה שלך היא **פרפראזה** (אותה משמעות במילים אחרות) של משהו שכבר נשלח/תוזמן — תזרוק אותה ותבחר זווית אחרת לחלוטין.
     2. דוגמאות פרפרזה שלא תעשה:
        - "המשחק שאכל לכם את החיים" ≈ "המשחק שגרם לכם לאבד שעות שינה" ≈ "המשחק שלא הצלחתם להפסיק"
        - "סדרה שראיתם 5 פעמים" ≈ "סדרה שאתם מריצים שוב" ≈ "Comfort show שלכם"
        - "שיר שלא יוצא לכם מהראש" ≈ "שיר שאתם שומעים בלולאה"
     3. אם הקטגוריה כולה (gaming/movies/etc) קיבלה ≥2 שאלות החודש בנושא דומה — חפש זווית מנוגדת. אם דובר על "משחקים שאכלו זמן", תשאל על "משחקים שהפסקתם באמצע", "משחקים ש-DNF". זווית מנוגדת = איכות.

     **15 פורמטים מומלצים** (בחר אחד שמתאים לערוץ ולשעה — אל תחזור על אותו פורמט פעמיים באותו יום):

     a. **Hot take / דעה לא פופולרית** — "סרט שכולם אוהבים — ולא מבינים מה הם רואים?", "סדרה הכי overrated של 2025?"
     b. **Forced choice / scenario עם אילוץ** — "אם אתם יכולים לשחק רק משחק אחד עד סוף החיים — מי?", "סדרה אחת לעולם בודד?"
     c. **Mini-list (top 3 / ranking)** — "פיצ'ר 3 משחקי קופסה האהובים", "דרגו: Yellowstone/Suits/Friends — האהוב, הכי 'ברקע', הכי בינג'."
     d. **Recommendation request** — "מחפש פודקאסט על קולנוע — ממליצים?", "סוף שבוע גשום, סדרה חדשה לבינג', מה כדאי?"
     e. **Comparison / binary A-vs-B** — "PC או קונסולה ולמה?", "DC או Marvel?"
     f. **Specific memory / nostalgia anchor** — "הסרט הראשון שראיתם בקולנוע — זוכרים?", "אנימה ראשונה שהשתקעתם בה — איזו?"
     g. **Show-and-tell (image cue)** — "התמונה האחרונה במצלמה.", "צלם את הספר שעל השולחן עכשיו."
     h. **Insider knowledge / hack** — "טיפ של 5 שנים בגיימינג שכל מתחיל היה צריך לדעת?"
     i. **Fill-in-the-blank** — "ערב סוף שבוע מושלם = ___ + ___ + ___."
     j. **Web rabbit-hole** — "ירדתם השבוע ל-rabbit hole? איזה?", "wikipedia article אחת ששלחתם לחבר השבוע?"
     k. **Niche self-expression** — "על איזה נושא תוכלו להעביר הרצאת TED של 20 דקות בלי הכנה?"
     l. **Would-you-rather (dilemma)** — "תעדיפו לקרוא את כל הספרים שלא קראתם או לראות את כל הסרטים — בלי לישון?"
     m. **Pool the group (collective list)** — "בואו נכתוב יחד את 10 הסדרות שכל גיק חייב לראות — תכתבו אחת + שורה."
     n. **Frame-your-own-question (meta)** — "אם הייתם המנחים הערב — איזו שאלה הייתם שואלים?"
     o. **Childfree-specific** — "הדבר שאתם עושים עכשיו שלא הייתם עושים אם היו לכם ילדים?" (השתמש במידה — חשוב לקהילה אבל לא בכל פוסט)

     דוגמה: ❌ "סדרה שאתם מריצים שוב ושוב" → ✅ "Yellowstone/Suits/Friends — איזו הכי 'background friendly' ולמה?"

   - **למד מ-recent_sent_samples_by_type**: זה הסגנון של הקהילה. שכפל את הקצב, את האמוג'ים שעובדים, את אורך המשפט. אל תיצור משהו שלא יושב על הטון הזה.

   - **הצעות פעילות — חובה לפחות אחת ביום, לא רק שאלות.** שאלה רפלקטיבית = passive. הקהילה זקוקה גם לליווי אקטיבי:
     **כללי תוכן אקטיבי:**

     a. **אסור לחלוטין להציע פעילויות שהמנהל מארגן ידנית** — board games, Among Us, watch parties, מפגשי משחקי קופסה, ערבי קלפים, וכל פעילות אחרת שדורשת תיאום בין משתתפים. זה תפקיד המנהל, לא של ה-AI. אסור לכתוב "מי בא לשחק...?" / "מי מצטרף ל...?".

     b. **טריוויה / אמוג'י-פאזל — מותר ומומלץ, אך כסלוט ביצוע אוטומטי ולא כשאלה**:
         - אסור: "מי בעניין?", "מי מצטרף?", "כמה מגיעים?".
         - מותר: "🧠 הערב ב-22:00 — סיבוב טריוויה על מוזיקת 80s. 10 שאלות מהירות בערוץ הפינה." (regular_slots, type="discussion" או type="trivia_round" אם יש התאמה ברורה, scheduled_time=שעת הסיבוב). שורת סיבוב כזו תהפוך להפעלה אוטומטית של המשחק בזמן המתוכנן אחרי אישור המנהל.
         - תזכורת/חימום היא אופציונלית ורק אם יש מספיק זמן לפני הסיבוב. אם מוסיפים תזכורת, היא regular_slots רגיל עם טקסט קצר כמו "🧠 בעוד 10 דקות — סיבוב טריוויה!".
         - חובה גם לאכלס trivia_questions ב-10 שאלות באותה קטגוריה אלא אם האדמין ביקש מספר אחר (אמוג'י: 3-5 חידות).
         - חובה ב-notes_for_admin תחת **"סיבובי משחק שתוזמנו:"** לרשום את הסיבובים כטיוטות שממתינות לאישור. אל תכתוב לפתוח ידנית עם /trivia או /puzzles; אחרי אישור, שורת המשחק מפעילה את המשחק אוטומטית בזמן שלה.
         - אם קיימת תזכורת נפרדת, רשום שהיא הודעת חימום רגילה שאפשר למחוק אם מבטלים את המשחק. אל תציג אותה כאזהרה קריטית ואל תטען שהמשחק לא יופעל אוטומטית.

     c. **פולס מהיר** עם 2-4 אופציות בטקסט (regular_slots, type="discussion") — מותר אם הנושא מעניין ולא דורש תיאום בין-אישי.

     **גם אם schedule.trivia.days=[] או emoji_puzzle.days=[]** — הכרזה על סיבוב חד-פעמי + תזכורת ב-regular_slots מותרת (days=[] רק מכבה auto-pool, לא הכרזות special-event).

   - **לאירועים**: תזכורת לא חייבת להיות "האירוע מתחיל בעוד X דקות". יכולה להיות "🍿 הכינו פופקורן, X דקות מהמפגש" או "מי כבר בחדר ההמתנה?".

7b. **טריוויה צריכה להיות רלוונטית.** אם יש אירוע היום על משחקי לוח — שאלות trivia מאותו עולם מועדפות. אם זה שבת ושטחנו עם מוזיקה — שאלות על אלבומים. הקטגוריה חייבת להתחבר ליום, לא להיות סתם "כללי".

7c. **אמוג'י puzzles צריכות לתפוס סרטים/סדרות שהקהילה מכירה ובסבירות גבוהה זוכרת.** עדיף להישאר עם סרטים מ-90s/2000s מוכרים, סדרות נטפליקס פופולריות, מאשר נישות אינדי.

8. Trivia dedup. אל תייצר שאלה זהה או כמעט זהה ל-existing_trivia_samples. אל תייצר batch עבור category שכבר יש בה ≥3 שאלות ב-existing_trivia_categories.

9. Emoji dedup. אל תייצר חידה שהתשובה בעברית/אנגלית שלה כבר ב-existing_emoji_answers_sample.

10. דו-משמעות → needs_review=true. אם יש זמנים סותרים ללא "זז ל-X" ברור — אל תנחש. החזר את התזכורת עם needs_review=true והסבר ב-notes.

11. טקסט משתמש (text/canonical_title/reminder_text) — עברית בלבד. ללא markdown, ללא backticks, ללא IDs פנימיים, ללא אנגלית טכנית.

12. **כיסוי פעילויות — חובה.** קרא את `activity_coverage_requirements`. עבור כל item עם `relevance="required"` חובה לעשות אחד משני דברים:
    - ליצור `regular_slots` מתאים, או
    - להחזיר `coverage_decisions` עם `action="skipped"` / `"already_covered"` וסיבה מפורשת.

    אסור להשמיט פעילות רלוונטית בשקט. אם לא יצרת weekly_roundup / weekly_leaderboard / discussion / evening וכו' — חייבים לראות למה ב-coverage_decisions וב-notes_for_admin.

13. notes_for_admin — מנוסח כ-Markdown לקריאה נוחה. הקפד על המבנה הבא (השמט סקציה ריקה):

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


async def _retry_failed_regular_slots(
    plan: dict, db: "Database", today_iso: str,
) -> tuple[dict, list[str]]:
    """Lint each `regular_slots[].text` in the freshly-generated plan; for
    every slot that fails `_validate_draft_text`, try one regeneration via
    `build_generation_prompt`. If the replacement also fails, drop that slot
    and surface it in the returned `notes` list so the operator sees why.

    Returns the (possibly-modified) plan plus a list of human-readable notes.
    Idempotent on a clean plan: zero failures → zero retries → unchanged plan.
    """
    notes: list[str] = []
    raw_slots = plan.get("regular_slots") or []
    if not isinstance(raw_slots, list) or not raw_slots:
        return plan, notes

    surviving_slots: list[dict] = []
    for slot in raw_slots:
        if not isinstance(slot, dict):
            surviving_slots.append(slot)
            continue
        text = (slot.get("text") or "").strip()
        failures = _validate_draft_text(text)
        if not failures:
            surviving_slots.append(slot)
            continue

        slot_type = (slot.get("type") or "").strip()
        category = (slot.get("category") or "").strip()
        scheduled_time = (slot.get("scheduled_time") or "").strip()
        # Only retry text-shaped slots through build_generation_prompt; other
        # slot types fall back to "drop and note" since the per-row builder
        # only knows morning/evening/discussion.
        if slot_type not in ("morning", "evening", "discussion"):
            notes.append(
                f"slot {slot_type} @ {scheduled_time} dropped: {', '.join(failures)} (not retriable)"
            )
            continue

        logger.info(
            "[ai-fill-today] validator failed slot type=%s @ %s reasons=%s — retrying via build_generation_prompt",
            slot_type, scheduled_time, failures,
        )
        try:
            recent = await _fetch_recent_sent_for_dedup(db, slot_type, limit=60)
            retry_prompt = build_generation_prompt(
                slot_type, "single", "", category,
                recent_sent=recent,
                scheduled_date=today_iso,
                scheduled_time=scheduled_time,
            )
            try:
                content = await _generate_via_cli(retry_prompt)
            except Exception:
                content = await _generate_via_api(retry_prompt)
            cleaned = content.strip().replace('"', '').replace("'", "")
            lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
            replacement = lines[0] if lines else cleaned
        except Exception as e:
            notes.append(
                f"slot {slot_type} @ {scheduled_time} dropped: retry call failed ({e})"
            )
            continue

        retry_failures = _validate_draft_text(replacement)
        if retry_failures:
            notes.append(
                f"slot {slot_type} @ {scheduled_time} dropped: original={failures}; "
                f"retry also failed {retry_failures}"
            )
            continue

        slot["text"] = replacement
        surviving_slots.append(slot)
        logger.info(
            "[ai-fill-today] slot type=%s @ %s replaced after validation: %r",
            slot_type, scheduled_time, replacement[:80],
        )

    plan["regular_slots"] = surviving_slots
    return plan, notes


def _build_digest_cli_prompt() -> str:
    """Build the ai-fill-today digest prompt: centralized quality rules from
    config/question_quality.md (Hard rules + Concrete failures only — Anti-patterns
    section dropped to fit the 28k CLI timeout budget) + operational digest rules.

    Per-row few-shot lives in `build_generation_prompt`'s `_finalize_prompt`;
    the digest path skips few-shot to stay within budget. The concrete-failures
    section in the centralized rules carries the negative anchors instead.
    """
    rules_block = _load_quality_rules_short()
    rules_section = f"\n\n{rules_block}\n\n" if rules_block else "\n\n"

    return (
        'אתה עוזר מנהלי קהילת "אלהוריים וזה" — צ\'ילדפרי בטלגרם. '
        'החזר JSON בלבד לפי הסכמה.'
        + rules_section
        + """חוקים מבצעיים:
- כבד now_time_il: אל תיצור סלוט שעבר או קרוב מ-5 דקות.
- כבד verified_topic_ids בלבד. אל תנחש topic_id.
- אל תכפיל מול existing_drafts_today או scheduled_messages_today.
- מזג אירועים כפולים; reminder_scheduled_time הוא זמן האירוע פחות event_reminder_lead_minutes.
- אם היום שבת או שישי בערב: סוף השבוע עדיין קורה; אל תכתוב "איך היה"/"סיכום"/עבר. ראשון בבוקר הוא זמן סיכום סוף שבוע.
- פעילויות מותרות רק אם הבוט מפעיל אותן או שהן שאלה/פול קל. אסור להציע מפגש/משחק שדורש תיאום אדמין.
- טריוויה/אמוג'י: אם יוצרים סיבוב, ספק גם שאלות/חידות מתאימות ולא כפולות; קטגוריה חייבת להתחבר ליום/ערוץ.
- חובה לטפל בכל activity_coverage_requirements עם relevance="required": regular_slots או coverage_decisions עם action+סיבה. אין השמטה שקטה.
- notes_for_admin: Markdown קצר, עד 300 מילים."""
    )


# Built lazily per-call so few-shot rotates each time. Keep a thin alias for
# backwards compatibility with any future callers expecting the old constant.
DIGEST_CLI_PROMPT = "DEPRECATED — use _build_digest_cli_prompt() so few-shot rotates per call"


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
                            "type": {"type": "string", "enum": list(AI_REGULAR_SLOT_TYPES)},
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
                "coverage_decisions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "activity_type": {"type": "string", "enum": list(AI_REGULAR_SLOT_TYPES)},
                            "scheduled_time": {"type": "string"},
                            "topic_id": {"type": ["integer", "null"]},
                            "action": {"type": "string", "enum": ["drafted", "skipped", "already_covered", "not_relevant"]},
                            "reason": {"type": "string"},
                        },
                        "required": ["activity_type", "scheduled_time", "topic_id", "action", "reason"],
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
            "required": ["notes_for_admin", "reminders", "regular_slots", "trivia_questions", "emoji_puzzles", "coverage_decisions", "skipped"],
        },
    }


def _hhmm_minutes(value: str) -> int | None:
    try:
        h, m = str(value or "").split(":")
        return int(h) * 60 + int(m)
    except (ValueError, TypeError):
        return None


def _build_activity_coverage_requirements(
    *,
    settings: dict,
    hebrew_day: int,
    now_hhmm: str,
    existing_drafts_today: list[dict],
    active_discussion_categories: list[dict],
) -> list[dict]:
    schedule = settings.get("schedule", {}) or {}
    topics = settings.get("topics", {}) or {}
    now_minutes = _hhmm_minutes(now_hhmm) or 0

    def is_future(time_str: str) -> bool:
        minutes = _hhmm_minutes(time_str)
        return minutes is not None and minutes >= now_minutes + 5

    def covered(activity_type: str, time_str: str, topic_id: int | None = None) -> bool:
        for row in existing_drafts_today:
            if row.get("message_type") != activity_type:
                continue
            if row.get("scheduled_time") != time_str:
                continue
            if topic_id is not None and row.get("topic_id") != topic_id:
                continue
            return True
        return False

    out: list[dict] = []

    def add(activity_type: str, time_str: str, topic_id: int | None, relevance: str, reason: str):
        out.append({
            "activity_type": activity_type,
            "scheduled_time": time_str,
            "topic_id": topic_id,
            "relevance": relevance,
            "reason": reason,
        })

    def add_scheduled(activity_type: str, schedule_key: str, topic_id: int | None, feature_key: str | None = None):
        cfg = schedule.get(schedule_key, {}) or {}
        days = cfg.get("days", []) or []
        time_str = str(cfg.get("time") or "18:00")[:5]
        enabled = _is_feature_enabled_simple(settings.get("features", {}), feature_key or schedule_key)
        if not enabled:
            add(activity_type, time_str, topic_id, "not_relevant", "feature disabled")
        elif not days:
            add(activity_type, time_str, topic_id, "not_relevant", "schedule days empty")
        elif hebrew_day not in days:
            add(activity_type, time_str, topic_id, "optional", "off configured day")
        elif not is_future(time_str):
            add(activity_type, time_str, topic_id, "not_relevant", "slot already passed")
        elif covered(activity_type, time_str, topic_id):
            add(activity_type, time_str, topic_id, "already_covered", "existing ai-fill row")
        else:
            add(activity_type, time_str, topic_id, "required", "enabled, scheduled today, future slot")

    add_scheduled("morning", "morning_prompt", topics.get("goals"), "morning_prompt")
    add_scheduled("evening", "evening_prompt", topics.get("goals"), "evening_prompt")

    discussion_cfg = schedule.get("discussion_prompt", {}) or {}
    discussion_days = discussion_cfg.get("days", []) or []
    discussion_enabled = _is_feature_enabled_simple(settings.get("features", {}), "discussions")
    for time_str in discussion_cfg.get("times", []) or []:
        time_str = str(time_str)[:5]
        if not discussion_enabled:
            add("discussion", time_str, None, "not_relevant", "feature disabled")
        elif not discussion_days:
            add("discussion", time_str, None, "not_relevant", "schedule days empty")
        elif hebrew_day not in discussion_days:
            add("discussion", time_str, None, "optional", "off configured day")
        elif not is_future(time_str):
            add("discussion", time_str, None, "not_relevant", "slot already passed")
        elif covered("discussion", time_str):
            add("discussion", time_str, None, "already_covered", "existing ai-fill row")
        elif active_discussion_categories:
            add("discussion", time_str, None, "required", "enabled, scheduled today, future slot")
        else:
            add("discussion", time_str, None, "not_relevant", "no active discussion categories")

    add_scheduled("trivia_round", "trivia", None, "trivia")
    add_scheduled("emoji_puzzle", "emoji_puzzle", None, "emoji_puzzle")
    add_scheduled("free_games", "free_games", None, "free_games")
    add_scheduled("facts_tidbit", "facts_tidbit", None, "facts_tidbit")
    add_scheduled("facts_spooky", "facts_spooky", None, "facts_spooky")
    add_scheduled("weekly_roundup", "weekly_roundup", None, "roundup")
    add_scheduled("weekly_leaderboard", "weekly_leaderboard", None, "levels")
    return out


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
    # voice. Capped tight (3 per type, 100 chars each) to keep total prompt
    # under ~15k chars. Earlier 8-per-type at 140 chars pushed the digest
    # past 28k and blew out claude CLI's 240s timeout.
    recent_sent_by_type: dict[str, list[dict]] = {}
    try:
        from datetime import timedelta as _td
        fourteen_days_ago = (today - _td(days=14)).isoformat()
        async with db._db.execute(
            """SELECT scheduled_date, message_type, channel_topic_id, text
               FROM scheduled_messages
               WHERE status = 'sent'
                 AND scheduled_date >= ? AND scheduled_date < ?
               ORDER BY scheduled_date DESC, scheduled_time DESC
               LIMIT 80""",
            (fourteen_days_ago, today_iso),
        ) as cur:
            for row in await cur.fetchall():
                mt = (row["message_type"] or "custom") or "custom"
                bucket = recent_sent_by_type.setdefault(mt, [])
                if len(bucket) >= 3:  # at most 3 examples per message_type
                    continue
                txt = (row["text"] or "").strip().replace("\n", " ")
                if not txt:
                    continue
                bucket.append({
                    "date": row["scheduled_date"],
                    "topic_id": row["channel_topic_id"],
                    "text": txt[:100],
                })
    except Exception as e:
        logger.warning("[ai-fill-today] failed to load recent sent samples: %s", e)
        recent_sent_by_type = {}

    # Existing ai-fill-today rows for today (idempotence signal). Includes ALL
    # non-cancelled statuses — draft, scheduled, sent — so the AI doesn't
    # re-propose a slot the admin has already promoted (status='scheduled')
    # or the bot has already sent (status='sent'). Each row carries its
    # status so the AI can describe state in notes_for_admin.
    existing_drafts_today = [
        {
            "id": m.get("id"),
            "created_by": m.get("created_by"),
            "status": m.get("status"),  # draft | scheduled | sent | failed
            "message_type": m.get("message_type"),
            "scheduled_time": (m.get("scheduled_time") or "")[:5],
            "topic_id": m.get("topic_id"),
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

    # Existing emoji answers — kept tight to bound prompt size
    try:
        existing_puzzles = await db.list_emoji_puzzles()
        existing_emoji_answers = [
            {"he": p.get("answer_he"), "en": p.get("answer_en")}
            for p in existing_puzzles[-20:]  # last 20 most recent
        ]
    except Exception:
        existing_emoji_answers = []

    # Schedule config snapshot
    schedule = settings.get("schedule", {})
    schedule_snapshot = {
        key: schedule.get(key, {})
        for key in ("morning_prompt", "evening_prompt", "discussion_prompt", "trivia", "emoji_puzzle", "free_games", "facts_tidbit", "facts_spooky", "weekly_roundup", "weekly_leaderboard")
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
    activity_coverage_requirements = _build_activity_coverage_requirements(
        settings=settings,
        hebrew_day=hebrew_day,
        now_hhmm=now_hhmm,
        existing_drafts_today=existing_drafts_today,
        active_discussion_categories=active_discussion_categories,
    )
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
        "existing_trivia_categories": existing_trivia[:15],
        "existing_emoji_answers_sample": existing_emoji_answers,
        "schedule": schedule_snapshot,
        "active_discussion_categories": active_discussion_categories,
        "activity_coverage_requirements": activity_coverage_requirements,
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
    compact_bundle = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    compact_schema = json.dumps(_today_plan_tool_schema()["input_schema"], ensure_ascii=False, separators=(",", ":"))
    return (
        _build_digest_cli_prompt()
        + "\n\n---\n\nקונטקסט היום (JSON):\n```json\n"
        + compact_bundle
        + "\n```\n\n"
        "החזר אך ורק JSON חוקי שתואם את הסכמה של today_plan.\n"
        "הפלט חייב להיות בלוק ```json``` בלבד, ללא הקדמה, ללא הסבר, ללא טקסט אחר.\n\n"
        "סכמה:\n```json\n"
        + compact_schema
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

    # Defensive: ensure $HOME/.codex is a directory. The codex CLI can sometimes
    # create `.codex` as an empty FILE if it was invoked with HOME pointing at
    # a path where `.codex` already existed as something else, or during certain
    # init failures. We've seen this happen multiple times in production. If it
    # IS a file, remove it; then mkdir if missing.
    codex_dir = os.path.join(real_home, ".codex")
    if os.path.exists(codex_dir) and not os.path.isdir(codex_dir):
        try:
            os.remove(codex_dir)
            logger.warning("[ai-fill-today] removed stray .codex file at %s", codex_dir)
        except OSError as _e:
            logger.warning("[ai-fill-today] could not remove stray .codex: %s", _e)
    if not os.path.isdir(codex_dir):
        try:
            os.makedirs(codex_dir, mode=0o700, exist_ok=True)
            logger.info("[ai-fill-today] created .codex dir at %s", codex_dir)
        except OSError as _e:
            logger.warning("[ai-fill-today] could not create .codex dir: %s", _e)
    # Note: this does NOT restore auth.json. If auth was lost the digest will
    # still fail, but with a clearer "Logged out" error from codex itself
    # rather than the cryptic "Not a directory (os error 20)".

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


# ── T-172: Operator content feedback capture ─────────────────────
# These endpoints record operator verdicts on AI suggestions so future
# generation can learn from them (T-174). Storage only at this phase;
# no live consumer beyond the operator history page.


@app.post("/api/content-feedback")
async def content_feedback_create(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid body")
    original_text = str(body.get("original_text") or "").strip()
    verdict = str(body.get("verdict") or "").strip()
    content_type = str(body.get("content_type") or "").strip()
    source = str(body.get("source") or "planner_ai_suggest").strip()
    if not original_text or not verdict or not content_type:
        raise HTTPException(
            status_code=400,
            detail="original_text, verdict, content_type are required",
        )
    metadata = body.get("suggestion_metadata")
    if metadata is not None and not isinstance(metadata, str):
        try:
            metadata = json.dumps(metadata, ensure_ascii=False)
        except Exception:
            metadata = None
    row_id = await db.record_content_feedback(
        source=source,
        content_type=content_type,
        topic_key=(str(body.get("topic_key")).strip() if body.get("topic_key") else None),
        original_text=original_text,
        verdict=verdict,
        reason=(str(body.get("reason")).strip() if body.get("reason") else None),
        corrected_text=(str(body.get("corrected_text")).strip() if body.get("corrected_text") else None),
        suggestion_metadata=metadata,
    )
    return {"id": row_id}


@app.get("/api/content-feedback")
async def content_feedback_list(
    request: Request,
    content_type: str | None = None,
    verdict: str | None = None,
    limit: int = 50,
    db: Database = Depends(get_db),
):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    rows = await db.list_content_feedback(
        content_type=content_type, verdict=verdict, limit=int(limit),
    )
    return {"feedback": rows, "count": len(rows)}


# ── T-174: Operator-approved style-profile learning ─────────────
# Two-step flow:
#   1. POST /api/style-profile/propose — bot summarizes recent feedback
#      into a markdown guidance patch. Returns the diff; writes NOTHING.
#   2. POST /api/style-profile/apply — operator accepts the patch; new
#      version is inserted and activated, cache invalidated.
# Hard invariant: AI proposes, operator applies. No auto-deploy.


def _summarize_feedback_to_guidance(feedback_rows: list[dict]) -> str:
    """Deterministic summarizer (no LLM call for the MVP) — pulls out the
    distinct rejection reasons and lists the bad/corrected text pairs as
    explicit "avoid X, prefer Y" guidance.

    A future enhancement can swap this for an LLM call; the contract
    (input feedback rows → markdown guidance string) stays stable so
    callers don't change.
    """
    if not feedback_rows:
        return ""
    lines: list[str] = []
    seen_reasons: set[str] = set()
    for row in feedback_rows:
        reason = (row.get("reason") or "").strip()
        original = (row.get("original_text") or "").strip()
        corrected = (row.get("corrected_text") or "").strip()
        verdict = (row.get("verdict") or "").strip()
        if reason and reason not in seen_reasons:
            seen_reasons.add(reason)
            lines.append(f"- {reason}")
        if verdict in ("rejected", "bad_wording") and original:
            short = original[:80] + ("…" if len(original) > 80 else "")
            lines.append(f"- אל תייצרו טקסט בסגנון: \"{short}\"")
        if corrected and original:
            short_orig = original[:60] + ("…" if len(original) > 60 else "")
            short_corr = corrected[:60] + ("…" if len(corrected) > 60 else "")
            lines.append(f"- במקום \"{short_orig}\" — עדיף \"{short_corr}\"")
    return "\n".join(lines[:20])  # cap so prompts don't bloat


@app.post("/api/style-profile/propose")
async def style_profile_propose(request: Request, db: Database = Depends(get_db)):
    """Read recent feedback and return a proposed guidance patch + diff.
    Writes nothing — operator must explicitly call /apply to persist."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = await request.json() if request.headers.get("content-length") else {}
    if not isinstance(body, dict):
        body = {}
    content_type = (body.get("content_type") or None)
    limit = int(body.get("limit") or 25)
    feedback = await db.list_content_feedback(content_type=content_type, limit=limit)
    proposed_guidance = _summarize_feedback_to_guidance(feedback)
    active = await db.get_active_style_profile()
    current_guidance = (active or {}).get("guidance") or ""
    source_ids = [int(r["id"]) for r in feedback]
    return {
        "current_guidance": current_guidance,
        "proposed_guidance": proposed_guidance,
        "source_feedback_count": len(feedback),
        "source_feedback_ids": source_ids,
        "diff_preview": {
            "added_lines": [
                ln for ln in proposed_guidance.splitlines()
                if ln and ln not in current_guidance
            ],
            "removed_lines": [
                ln for ln in current_guidance.splitlines()
                if ln and ln not in proposed_guidance
            ],
        },
    }


@app.post("/api/style-profile/apply")
async def style_profile_apply(request: Request, db: Database = Depends(get_db)):
    """Persist + activate a new style-profile version. Body must include
    the exact `guidance` text the operator approved (NOT a re-fetch — the
    operator-approved text is what gets stored)."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid body")
    guidance = str(body.get("guidance") or "").strip()
    if not guidance:
        raise HTTPException(status_code=400, detail="guidance is required")
    source_ids = body.get("source_feedback_ids") or []
    source_ids_json = json.dumps(source_ids, ensure_ascii=False) if source_ids else None
    profile_key = str(body.get("profile_key") or "planner_hebrew_default")
    new_id = await db.insert_style_profile(
        profile_key=profile_key,
        guidance=guidance,
        source_feedback_ids=source_ids_json,
        status="draft",
    )
    await db.activate_style_profile(new_id, profile_key=profile_key)
    # Invalidate the in-process cache so subsequent prompts pick up the
    # new guidance without restarting the dashboard.
    _STYLE_PROFILE_CACHE[profile_key] = guidance
    return {"id": new_id, "profile_key": profile_key, "status": "active"}


@app.get("/api/style-profile/active")
async def style_profile_active(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    profile_key = "planner_hebrew_default"
    row = await db.get_active_style_profile(profile_key)
    # Sync the cache opportunistically — covers the case where the
    # dashboard process restarted between apply and prompt build.
    _STYLE_PROFILE_CACHE[profile_key] = (row or {}).get("guidance")
    return {"profile": row}


@app.post("/api/weekplan/ai-fill-today")
async def ai_fill_today(request: Request, db: Database = Depends(get_db)):
    """Fill empty slots + one reminder per event for a target day, with
    group-wide context (events, holiday, week's committed messages) in the
    prompt. Idempotent via created_by tagging.

    Body (all optional): {target_date: "YYYY-MM-DD"} — defaults to today.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    from datetime import date, datetime, timedelta

    target_date_str = ""
    try:
        body = await request.json()
        target_date_str = (body.get("target_date") or "").strip() if isinstance(body, dict) else ""
    except Exception:
        target_date_str = ""

    if target_date_str:
        try:
            today = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid target_date: {target_date_str}")
    else:
        today = date.today()
    today_iso = today.isoformat()
    hebrew_day = (today.weekday() + 1) % 7
    sunday = today - timedelta(days=hebrew_day)
    saturday = sunday + timedelta(days=6)

    settings = get_settings()

    # Holiday short-circuit — block_auto=true means "manual content only today"
    if is_auto_blocked_on(today):
        return {
            "date": today_iso,
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
        raise HTTPException(status_code=503, detail={
            "date": today_iso,
            "message": "AI digest failed on all available transports",
            "errors": errors,
        })

    # ── Quality validation + per-slot retry ─────────────────────────────────
    # Lint every regular_slots[].text against _validate_draft_text. For each
    # failed slot, regenerate via the per-row build_generation_prompt path
    # (one extra API call per failure). If the replacement also fails, drop
    # the slot and surface it in notes_for_admin so the operator sees why.
    plan, retry_notes = await _retry_failed_regular_slots(plan, db, today_iso)
    if retry_notes:
        errors.extend(retry_notes)
        existing_notes = (plan.get("notes_for_admin") or "").rstrip()
        plan["notes_for_admin"] = (
            existing_notes
            + ("\n\n" if existing_notes else "")
            + "**Quality validator surfaced these:**\n"
            + "\n".join(f"- {n}" for n in retry_notes)
        )

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

    # ── Regular slots and executable activities ────────────────────────────
    # Dedup key includes topic_id so two discussions at the same time in
    # different channels are NOT collapsed (legitimate case). Considers ALL
    # ai-fill-today rows for today (draft / scheduled / sent) — so once
    # admin promotes or sends a slot, AI can't re-propose the same slot.
    existing_slot_keys = {
        (d.get("scheduled_time"), d.get("message_type"), d.get("topic_id"))
        for d in bundle["existing_drafts_today"]
    }
    for slot in plan.get("regular_slots") or []:
        mtype = (slot.get("type") or "").strip()
        stime = (slot.get("scheduled_time") or "").strip()
        topic = slot.get("topic_id")
        text = (slot.get("text") or "").strip()
        if mtype not in AI_REGULAR_SLOT_TYPES:
            errors.append(f"slot rejected (bad type): {slot}")
            continue
        if not _valid_hhmm(stime) or not text:
            errors.append(f"slot rejected (incomplete): {slot}")
            continue
        if topic not in verified_topic_ids:
            errors.append(f"slot rejected (unverified topic_id={topic}): {mtype}")
            continue
        if (stime, mtype, topic) in existing_slot_keys:
            logger.info("[ai-fill-today] slot deduped: time=%s type=%s topic=%s already exists", stime, mtype, topic)
            continue
        try:
            message_type, poll_options = _coerce_game_message_fields(mtype, text, teaser_topic_id=int(topic))
            channel_topic_id = int(topic)
            if message_type == "trivia_round" and mtype != "trivia_round":
                routing = await db.get_handler_routing("trivia_round")
                if routing and routing.get("play_topic_id") is not None:
                    channel_topic_id = int(routing["play_topic_id"])
            elif message_type == "emoji_puzzle" and mtype != "emoji_puzzle":
                routing = await db.get_handler_routing("emoji_puzzle")
                if routing and routing.get("play_topic_id") is not None:
                    channel_topic_id = int(routing["play_topic_id"])
            new_id = await db.create_scheduled_message(
                text=text, message_type=message_type,
                channel_topic_id=channel_topic_id,
                target_group="main",
                scheduled_date=today_iso, scheduled_time=stime,
                poll_options=poll_options,
                created_by="ai-fill-today", status="draft",
            )
            existing_slot_keys.add((stime, mtype, int(topic)))
            regular_out.append({"id": new_id, "type": message_type, "scheduled_time": stime, "topic_id": channel_topic_id})
            logger.info("[ai-fill-today] draft %s id=%d at %s", message_type, new_id, stime)
        except Exception as e:
            errors.append(f"{mtype} insert: {e}")

    # Coverage guard: the model must explicitly account for each relevant
    # activity. Missing items become visible errors instead of silent under-fill.
    coverage_decisions = plan.get("coverage_decisions") or []
    decision_keys = {
        (
            (d.get("activity_type") or "").strip(),
            (d.get("scheduled_time") or "").strip(),
            d.get("topic_id"),
        )
        for d in coverage_decisions
        if isinstance(d, dict)
    }
    created_keys = {
        (r.get("type"), r.get("scheduled_time"), r.get("topic_id"))
        for r in regular_out
    }
    created_loose_keys = {
        (r.get("type"), r.get("scheduled_time"), None)
        for r in regular_out
    }
    covered_existing_keys = {
        (d.get("message_type"), d.get("scheduled_time"), d.get("topic_id"))
        for d in bundle["existing_drafts_today"]
    }
    covered_existing_loose_keys = {
        (d.get("message_type"), d.get("scheduled_time"), None)
        for d in bundle["existing_drafts_today"]
    }
    for req in bundle.get("activity_coverage_requirements") or []:
        if req.get("relevance") != "required":
            continue
        key = (req.get("activity_type"), req.get("scheduled_time"), req.get("topic_id"))
        loose_key = (req.get("activity_type"), req.get("scheduled_time"), None)
        if key in created_keys or key in covered_existing_keys or key in decision_keys:
            continue
        if loose_key in decision_keys or loose_key in created_loose_keys or loose_key in covered_existing_loose_keys:
            continue
        errors.append(
            "coverage missing: "
            f"{req.get('activity_type')} at {req.get('scheduled_time')} "
            f"({req.get('reason')})"
        )

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
        "date": today_iso,
        "skipped_holiday": False,
        "reminders": reminders_out,
        "regular_slots": regular_out,
        "trivia": {"generated": trivia_added, "skipped": skipped.get("trivia")},
        "emoji": {"generated": emoji_added, "skipped": skipped.get("emoji")},
        "skipped": skipped,
        "coverage_decisions": coverage_decisions,
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
async def generate_content(request: Request, db: Database = Depends(get_db)):
    """Generate a single message via Claude for the create-drawer textarea.

    Body: {type, category?, existing?, scheduled_date?, scheduled_time?}
    type in {morning, evening, discussion, custom, poll}.
    Returns: {text} for text types, {question, options[]} for poll.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    mtype = (data.get("type") or "").strip()
    category = (data.get("category") or "").strip()
    existing = (data.get("existing") or "").strip()
    sched_date = (data.get("scheduled_date") or "").strip() or None
    sched_time = (data.get("scheduled_time") or "").strip() or None

    if mtype not in ("morning", "evening", "discussion", "custom", "poll"):
        raise HTTPException(status_code=400, detail=f"Invalid type: {mtype}")
    if mtype == "discussion" and not category:
        raise HTTPException(status_code=400, detail="Discussion requires category")

    mode = "rewrite" if existing else "single"
    recent_sent = await _fetch_recent_sent_for_dedup(db, mtype, limit=60)
    prompt = build_generation_prompt(
        mtype, mode, existing, category,
        recent_sent=recent_sent,
        scheduled_date=sched_date,
        scheduled_time=sched_time,
    )

    try:
        content = await _generate_via_cli(prompt)
    except Exception:
        content = await _generate_via_api(prompt)

    if mtype == "poll":
        # Poll prompts ask for strict JSON. Extract the first {...} blob and
        # parse — surface a 502 with raw text if the model didn't comply, so
        # the user sees the failure rather than a half-row written from a
        # malformed response.
        raw = content.strip()
        try:
            start = raw.find("{")
            end = raw.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("no JSON object found")
            payload = json.loads(raw[start:end + 1])
            question = (payload.get("question") or "").strip()
            options = [str(o).strip() for o in (payload.get("options") or []) if str(o).strip()]
            if not question or len(options) < 2:
                raise ValueError("missing question or fewer than 2 options")
            options = options[:4]
            logger.info(
                "[generate-content] poll cat=%s mode=%s -> q=%r opts=%d",
                category, mode, question[:60], len(options),
            )
            return {"question": question, "options": options}
        except Exception as e:
            logger.warning("[generate-content] poll JSON parse failed: %s — raw=%r", e, raw[:300])
            raise HTTPException(
                status_code=502,
                detail=f"AI did not return valid poll JSON: {e}. Raw: {raw[:200]}",
            )

    content = content.strip().replace('"', '').replace("'", "")
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    text = lines[0] if lines else content
    if mtype in {"morning", "evening", "discussion"}:
        _reject_bad_planner_text(text)

    logger.info("[generate-content] type=%s cat=%s mode=%s -> %r", mtype, category, mode, text[:60])
    return {"text": text}


_TRIVIA_VALID_CATEGORIES = (
    "אומנות", "גיאוגרפיה", "גיימינג", "היסטוריה", "טבע", "טכנולוגיה",
    "טלוויזיה", "ישראל", "כללי", "מדע", "מוזיקה", "ספורט", "ספרות", "סרטים",
)


@app.post("/api/trivia-round/suggest")
async def trivia_round_suggest(request: Request, db: Database = Depends(get_db)):
    """Suggest a themed trivia round configuration via Claude.

    The result is meant to PRE-FILL the live trivia form (theme/categories/
    count/pre-roll) and provide a warm-up announcement string the operator
    can paste into a separate planner row at T-35 minutes (warm-up rows are
    NOT auto-created here — see CLAUDE.md "Trivia Round Scheduling"; that
    multi-row scheduling is owned by the planner write-side).

    Body: {date?: 'YYYY-MM-DD', time?: 'HH:MM', hint?: str, category_hint?: str}
    Returns: {theme_label, categories: [...], question_count, pre_roll_s, warmup_text, raw}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    sched_date = (data.get("date") or "").strip() or None
    sched_time = (data.get("time") or "").strip() or None
    hint = (data.get("hint") or "").strip()
    category_hint = (data.get("category_hint") or "").strip()

    # Avoid recently used themes by sniffing recent trivia_round poll_options
    recent_themes: list[str] = []
    try:
        async with db._db.execute(
            "SELECT poll_options FROM scheduled_messages "
            "WHERE message_type = 'trivia_round' "
            "AND poll_options IS NOT NULL AND poll_options != '' "
            "ORDER BY created_at DESC LIMIT 30"
        ) as cur:
            async for row in cur:
                try:
                    payload = json.loads(row["poll_options"])
                    label = (payload.get("theme_label") or "").strip()
                    if label and label not in recent_themes:
                        recent_themes.append(label)
                except Exception:
                    pass
    except Exception:
        pass

    avoid = ""
    if recent_themes:
        avoid = (
            "\n\nאסור לחזור על אחד מהנושאים האלה (השתמשו לאחרונה):\n"
            + "\n".join(f"- {t}" for t in recent_themes[:15])
        )

    cat_list_he = " | ".join(_TRIVIA_VALID_CATEGORIES)
    time_ctx = _format_time_context(sched_date, sched_time)

    prompt = f"""בחר תצורה לסיבוב טריוויה לקהילת מבוגרים ישראלית (childfree, גילאי 30-50). הסיבוב מורכב מ-{{question_count}} שאלות בערוץ הטלגרם.

חוקים:
1. theme_label: כותרת קצרה ומזמינה בעברית (עד 25 תווים), ברורה ולא גנרית. אל תשתמש במילה "טריוויה" בכותרת.
2. categories: רשימה של 1-3 קטגוריות מהסט הסגור הבא (חובה לאיית בדיוק כך, כל ערך כפי שמופיע):
   {cat_list_he}
   אם בחרת נושא רחב — בחר 2-3 קטגוריות. אם נושא צר — אחת מספיקה. הקטגוריות חייבות להתאים ל-theme_label.
3. question_count: 5, 7, או 10. בחר על פי האווירה — סיבוב קצר ומהיר (5), בינוני (7), או מלא (10).
4. pre_roll_s: 30 או 60 (השהייה בשניות בין ההכרזה לשאלה הראשונה).
5. warmup_text: שורה אחת קצרה (עד 140 תווים) להודעת חימום לפני המשחק. כתוב טבעי, לא תבנית קבועה, ואל תבטיח כפתורים או הרשמה מוקדמת. הזכר את ה-theme_label.

עברית תקנית בלבד. אל תמציא ביטויים. אל תשלב מילים באנגלית באמצע משפט עברי.{avoid}{time_ctx}

{f'הקשר/רמז למפעיל: {hint}' if hint else ''}
{f'העדפת קטגוריה: {category_hint}' if category_hint else ''}

פלט: JSON תקין בלבד, ללא טקסט נוסף לפני או אחרי, במבנה:
{{"theme_label": "<כותרת>", "categories": ["<קטגוריה>"], "question_count": 7, "pre_roll_s": 30, "warmup_text": "<טקסט חימום>"}}"""

    try:
        content = await _generate_via_cli(prompt)
    except Exception:
        content = await _generate_via_api(prompt)

    raw = content.strip()
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("no JSON object found")
        payload = json.loads(raw[start:end + 1])
    except Exception as e:
        logger.warning("[trivia-suggest] JSON parse failed: %s — raw=%r", e, raw[:300])
        raise HTTPException(
            status_code=502,
            detail=f"AI did not return valid JSON: {e}. Raw: {raw[:200]}",
        )

    theme_label = (payload.get("theme_label") or "").strip()
    raw_cats = payload.get("categories") or []
    if not isinstance(raw_cats, list):
        raw_cats = []
    categories = [
        c.strip() for c in raw_cats
        if isinstance(c, str) and c.strip() in _TRIVIA_VALID_CATEGORIES
    ]
    dropped = [
        c for c in raw_cats
        if isinstance(c, str) and c.strip() and c.strip() not in _TRIVIA_VALID_CATEGORIES
    ]
    try:
        question_count = int(payload.get("question_count") or 7)
    except Exception:
        question_count = 7
    question_count = max(1, min(question_count, 20))
    try:
        pre_roll_s = int(payload.get("pre_roll_s") or 30)
    except Exception:
        pre_roll_s = 30
    pre_roll_s = max(5, min(pre_roll_s, 3600))
    warmup_text = (payload.get("warmup_text") or "").strip()

    if not theme_label or not categories:
        raise HTTPException(
            status_code=502,
            detail=(
                "AI suggestion missing theme_label or no valid categories. "
                f"Got theme={theme_label!r} categories={raw_cats!r}"
            ),
        )

    logger.info(
        "[trivia-suggest] theme=%r cats=%s qc=%d pre_roll=%d dropped=%s",
        theme_label, categories, question_count, pre_roll_s, dropped,
    )

    return {
        "theme_label": theme_label,
        "categories": categories,
        "question_count": question_count,
        "pre_roll_s": pre_roll_s,
        "warmup_text": warmup_text,
        "dropped_categories": dropped,
        "valid_categories": list(_TRIVIA_VALID_CATEGORIES),
    }


@app.post("/api/pool/emoji-puzzles/suggest")
async def pool_emoji_puzzles_suggest(request: Request, db: Database = Depends(get_db)):
    """Generate N new emoji puzzles via Claude and insert them into the
    `emoji_puzzles` pool table directly.

    Body: {count: int (1-20), media_type: 'movie'|'tv'|'book'|'song'}
    Returns: {inserted: int, items: [{id, emoji_prompt, answer_he, answer_en}], errors: [str]}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    try:
        count = max(1, min(int(data.get("count", 5)), 20))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid count")
    media_type = (data.get("media_type") or "").strip().lower()
    if media_type not in ("movie", "tv", "book", "song"):
        raise HTTPException(status_code=400, detail="media_type is required (movie, tv, book, or song)")

    media_he = {
        "movie": "סרט", "tv": "סדרת טלוויזיה", "book": "ספר", "song": "שיר",
    }[media_type]

    existing_he: list[str] = []
    try:
        async with db._db.execute("SELECT answer_he FROM emoji_puzzles") as cur:
            async for row in cur:
                v = (row["answer_he"] or "").strip()
                if v:
                    existing_he.append(v)
    except Exception:
        pass

    avoid_block = ""
    if existing_he:
        sample = existing_he[:60]
        avoid_block = (
            "\n\nאסור להציע אחת מהתשובות הבאות (כבר קיימות במאגר):\n"
            + "\n".join(f"- {s}" for s in sample)
        )

    prompt = f"""צור {count} חידות אמוג'י חדשות בעברית. כל חידה היא רצף של 3-6 אמוג'ים שמייצגים שם של {media_he}.

הקפד על:
- שמות מוכרים — {media_he}ים שרוב הקהילה (קהילת מבוגרים ישראלית, גילאי 30-50, ללא ילדים) תזהה.
- אמוג'ים ויזואליים בלבד. אל תשתמש באותיות לטיניות מאמוג'ים (🅰️🅱️ וכו').
- תשובה בעברית מדויקת ובאנגלית הבינלאומית.
- אליאסים: 2-4 שמות חלופיים, איות שונה, או וריאציות מקובלות שאנשים עשויים להקליד.{avoid_block}

פלט: JSON תקין בלבד, ללא טקסט נוסף לפני או אחרי, מערך של אובייקטים במבנה:
[{{"emoji_prompt": "🦁👑", "answer_he": "מלך האריות", "answer_en": "The Lion King", "aliases": ["lion king", "Lion King", "המלך האריה"]}}]"""

    try:
        content = await _generate_via_cli(prompt)
    except Exception:
        content = await _generate_via_api(prompt)

    raw = content.strip()
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("no JSON array found")
        items = json.loads(raw[start:end + 1])
        if not isinstance(items, list):
            raise ValueError("not an array")
    except Exception as e:
        logger.warning("[emoji-suggest] JSON parse failed: %s — raw=%r", e, raw[:300])
        raise HTTPException(
            status_code=502,
            detail=f"AI did not return valid JSON: {e}. Raw: {raw[:200]}",
        )

    inserted: list[dict] = []
    errors: list[str] = []
    for p in items:
        if not isinstance(p, dict):
            errors.append(f"non-dict item skipped: {p}")
            continue
        emoji_prompt_val = (p.get("emoji_prompt") or "").strip()
        answer_he = (p.get("answer_he") or "").strip()
        answer_en = (p.get("answer_en") or "").strip()
        aliases = p.get("aliases") or []
        if not emoji_prompt_val or not answer_he or not answer_en:
            errors.append(f"incomplete entry: {p}")
            continue
        try:
            new_id = await db.create_emoji_puzzle(
                emoji_prompt=emoji_prompt_val,
                answer_he=answer_he,
                answer_en=answer_en,
                aliases=json.dumps(
                    [a for a in aliases if isinstance(a, str) and a.strip()],
                    ensure_ascii=False,
                ),
                difficulty=2,
                media_type=media_type,
            )
            inserted.append({
                "id": new_id,
                "emoji_prompt": emoji_prompt_val,
                "answer_he": answer_he,
                "answer_en": answer_en,
            })
            logger.info(
                "[emoji-suggest] id=%d media=%s %s -> %s",
                new_id, media_type, emoji_prompt_val, answer_he,
            )
        except Exception as e:
            errors.append(f"insert failed for {answer_he}: {e}")

    return {"inserted": len(inserted), "items": inserted, "errors": errors}


_FACTS_VENUE_KEYWORDS = (
    "Press", "Journal", "Nature", "Science", "Library", "University",
    "Museum", "Post", "Magazine", "Society", "Review", "Communications",
    "Atlas", "Encyclopedia", "Wikipedia", "Ministry", "Archive",
    "Foundation", "Institute", "Israel", "France", "Britain",
)
_FACTS_URL_MARKERS = (".com", ".org", ".il", ".edu", ".gov", ".fr", ".uk", ".net", "http")


def _validate_fact_entry(item_id: str, text_he: str, source: str, existing_ids: set) -> list[str]:
    """Mirror of `tests/test_facts_pool.py` validators so the dashboard rejects
    bad suggestions before they hit the YAML. CI is the final check; this is
    the friendly first line of defense.
    """
    errors: list[str] = []
    if not item_id:
        errors.append("missing id")
    elif item_id in existing_ids:
        errors.append("id already exists in pool")
    elif not item_id.replace("_", "").replace("-", "").isalnum():
        errors.append("id must be ASCII alphanumeric (snake_case)")
    if not text_he:
        errors.append("missing text_he")
    elif len(text_he.strip()) < 50:
        errors.append("text_he too short (< 50 chars)")
    elif not any(0x0590 <= ord(c) <= 0x05FF for c in text_he):
        errors.append("text_he has no Hebrew characters")
    if not source:
        errors.append("missing source")
    else:
        import re as _re
        year_re = _re.compile(r"\b(?:19|20)\d{2}\b")
        has_year = bool(year_re.search(source))
        has_venue = any(k in source for k in _FACTS_VENUE_KEYWORDS)
        has_url = any(m in source for m in _FACTS_URL_MARKERS)
        if not (has_year or has_venue or has_url):
            errors.append("source doesn't look like a real citation (missing year/venue/URL)")
    return errors


def _load_facts_existing_ids() -> set:
    try:
        with open(CONFIG_DIR / "facts.yaml", "r", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
        ids: set = set()
        for pool_name in ("tidbit", "spooky"):
            for entry in existing.get(pool_name, []) or []:
                if entry.get("id"):
                    ids.add(entry["id"])
        return ids
    except Exception:
        return set()


@app.post("/api/pool/facts/suggest")
async def pool_facts_suggest(request: Request):
    """Generate candidate facts via Claude for manual review.

    Does NOT write to config/facts.yaml. Returns suggestions with per-item
    validation results so the admin can approve only the entries whose
    citations they actually verified.

    Body: {kind: 'tidbit'|'spooky', count: int (1-10)}
    Returns: {kind, suggestions: [{id, text_he, source, validation_pass, validation_errors}], note}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    kind = (data.get("kind") or "tidbit").strip().lower()
    if kind not in ("tidbit", "spooky"):
        raise HTTPException(status_code=400, detail="Invalid kind (must be tidbit or spooky)")
    try:
        count = max(1, min(int(data.get("count", 5)), 10))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid count")

    kind_he = "עובדה מעניינת מהמדע / היסטוריה / טבע" if kind == "tidbit" else "סיפור מסתורי / פולקלורי / לא-מוסבר"
    prompt = f"""צור {count} פריטים חדשים מסוג "{kind_he}" בעברית עבור קהילת מבוגרים סקרנים.

חוקים קשיחים:
1. כל פריט חייב להגיע ממקור אמיתי שניתן לאמת (מאמר אקדמי, מוסד מחקר, ארכיון רשמי, אנציקלופדיה מוכרת, אתר ממשלתי).
   אסור להמציא מקורות. אם אינך בטוח שהמקור קיים — דלג ובחר נושא אחר.
2. השדה source חייב לכלול לפחות אחד מהבאים: שם מחבר + שנה (4 ספרות 1900-2099), או שם כתב-עת מוכר (Nature/Science/וכו'), או URL.
   דוגמאות חוקיות: "Oren et al., Science (2024). Hebrew University." | "Seymour et al., Science Advances (2023)." | "https://www.nature.com/articles/..."
3. text_he: לפחות 50 תווים, עברית טבעית מספרת. לא תרגום מילולי, לא רשימה יבשה.
4. id: מזהה לטיני קצר (snake_case, אנגלית בלבד) שמתאר את התוכן. ייחודי לכל פריט.
5. בחר נושאים לא-טריוויאליים. עובדה ש-90% כבר מכירים — דלג.

פלט: JSON תקין בלבד, ללא טקסט נוסף לפני או אחרי, מערך אובייקטים במבנה:
[{{"id": "marmoset_calls", "text_he": "מחקר מהאוניברסיטה העברית מצא...", "source": "Oren et al., Science (2024). Hebrew University."}}]"""

    try:
        content = await _generate_via_cli(prompt)
    except Exception:
        content = await _generate_via_api(prompt)

    raw = content.strip()
    try:
        start = raw.find("[")
        end = raw.rfind("]")
        if start < 0 or end <= start:
            raise ValueError("no JSON array found")
        items = json.loads(raw[start:end + 1])
        if not isinstance(items, list):
            raise ValueError("not an array")
    except Exception as e:
        logger.warning("[facts-suggest] JSON parse failed: %s — raw=%r", e, raw[:300])
        raise HTTPException(
            status_code=502,
            detail=f"AI did not return valid JSON: {e}. Raw: {raw[:200]}",
        )

    existing_ids = _load_facts_existing_ids()
    suggestions: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        item_id = (it.get("id") or "").strip()
        text_he = (it.get("text_he") or "").strip()
        source = (it.get("source") or "").strip()
        verrors = _validate_fact_entry(item_id, text_he, source, existing_ids)
        suggestions.append({
            "id": item_id,
            "text_he": text_he,
            "source": source,
            "validation_pass": not verrors,
            "validation_errors": verrors,
        })

    return {
        "kind": kind,
        "suggestions": suggestions,
        "note": (
            "אמת ידנית שכל מקור אכן קיים לפני אישור. אישור מוסיף ל-config/facts.yaml — "
            "ה-CI יבדוק את הצורה אבל לא את אמיתות המקור."
        ),
    }


@app.post("/api/pool/facts/append")
async def pool_facts_append(request: Request):
    """Append a single validated fact to config/facts.yaml under the chosen pool.

    Re-runs the validator (don't trust client-side `validation_pass`) and
    refuses to write if the entry would fail `tests/test_facts_pool.py`.

    Body: {kind: 'tidbit'|'spooky', id, text_he, source}
    Returns: {ok, kind, id} or 422 with errors
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    kind = (data.get("kind") or "").strip().lower()
    if kind not in ("tidbit", "spooky"):
        raise HTTPException(status_code=400, detail="Invalid kind")
    item_id = (data.get("id") or "").strip()
    text_he = (data.get("text_he") or "").strip()
    source = (data.get("source") or "").strip()

    existing_ids = _load_facts_existing_ids()
    verrors = _validate_fact_entry(item_id, text_he, source, existing_ids)
    if verrors:
        raise HTTPException(status_code=422, detail={"validation_errors": verrors})

    path = CONFIG_DIR / "facts.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data_yaml = yaml.safe_load(f) or {}
        if kind not in data_yaml or not isinstance(data_yaml[kind], list):
            data_yaml[kind] = []
        data_yaml[kind].append({
            "id": item_id,
            "text_he": text_he,
            "source": source,
        })
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(data_yaml, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
        logger.info("[facts-append] kind=%s id=%s appended", kind, item_id)
    except Exception as e:
        logger.exception("[facts-append] write failed")
        raise HTTPException(status_code=500, detail=f"YAML write failed: {e}")

    return {"ok": True, "kind": kind, "id": item_id}


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
    skipped_slots: set[tuple[str, str, str]] = set()
    try:
        _raw_committed = await db.get_scheduled_messages(
            sunday.isoformat(), (sunday + timedelta(days=6)).isoformat(),
            include_cancelled=True,
        )
        for row in _raw_committed:
            mtype = row.get("message_type", "")
            if mtype not in ("morning", "evening", "discussion"):
                continue
            dkey = row.get("scheduled_date", "")
            tkey = (row.get("scheduled_time") or "")[:5]
            if row.get("status") == "cancelled":
                # User explicitly cleared this slot — don't fall back to the pool.
                skipped_slots.add((dkey, tkey, mtype))
                continue
            committed_index[(dkey, tkey, mtype)] = row
        logger.info("[weekplan.render] committed=%d skipped=%d",
                    len(committed_index), len(skipped_slots))
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
            slot_key = (day_date.isoformat(), m_time, "morning")
            committed_row = committed_index.get(slot_key)
            if slot_key in skipped_slots and not committed_row:
                pass  # user cleared this slot — render nothing, don't fall back to pool
            elif committed_row:
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
                slot_key = (day_date.isoformat(), t, "discussion")
                committed_row = committed_index.get(slot_key)
                if slot_key in skipped_slots and not committed_row:
                    # advance discussion_idx so the rotation doesn't reuse the
                    # same category on the next slot when this one was skipped
                    if active_categories:
                        discussion_idx += 1
                    continue
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
            slot_key = (day_date.isoformat(), e_time, "evening")
            committed_row = committed_index.get(slot_key)
            if slot_key in skipped_slots and not committed_row:
                pass  # user cleared this slot — render nothing
            elif committed_row:
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

    # Phase B: attach reaction-count badges to each committed row.
    # Read-only display; no behaviour change. Bulk-fetch first so the
    # per-activity loop is O(1) lookups.
    try:
        scheduled_ids = [
            int(act["scheduled_id"])
            for day in week_days
            for act in day.get("activities", [])
            if act.get("scheduled_id")
        ]
        engagement_map = await db.list_message_engagement(scheduled_ids) if scheduled_ids else {}
        for day in week_days:
            for act in day.get("activities", []):
                sid = act.get("scheduled_id")
                if sid and sid in engagement_map:
                    act["engagement"] = {
                        "reactions": engagement_map[sid]["reactions"],
                        "distinct_reactors": engagement_map[sid]["distinct_reactors"],
                    }
    except Exception as e:
        logger.warning("[weekplan.render] engagement enrichment failed: %s", e)

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


def _default_pending_reviews():
    return []


def _ensure_special_pending_reviews(items):
    return items


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
            return _ensure_special_pending_reviews(items)
        except Exception:
            logger.exception("[review] failed to read %s — reseeding", PENDING_REVIEWS_PATH)
    items = _default_pending_reviews()
    _save_pending_reviews(items)
    return _ensure_special_pending_reviews(items)


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


# ── QA Scoring ──────────────────────────────────────────
#
# A separate page from /review. Generates fresh AI-fill outputs through the
# upgraded prompt pipeline so the operator can score them 1-5 + comment, and
# we get a longitudinal record of whether the prompts are working.
#
# Lives in its own JSON file to avoid mixing with the reviewer queue.

QA_DRAFTS_PATH = Path(__file__).parent.parent / "data" / "qa_drafts.json"

_QA_SAMPLE_SLOTS = [
    # (draft_type, category, time, hebrew-day-name)
    ("morning",    "",         "09:00", "שני"),
    ("evening",    "",         "21:00", "ראשון"),
    ("discussion", "movies",   "21:00", "שישי"),
    ("discussion", "gaming",   "21:00", "שישי"),
    ("discussion", "general",  "20:00", "שבת"),
    ("discussion", "singles",  "18:00", "שני"),
    ("discussion", "vegan",    "18:00", "שלישי"),
    ("discussion", "art",      "18:00", "רביעי"),
    ("discussion", "funny",    "18:00", "חמישי"),
    ("discussion", "politics", "20:00", "ראשון"),
]


def _load_qa_drafts() -> list[dict]:
    if not QA_DRAFTS_PATH.exists():
        return []
    try:
        items = json.loads(QA_DRAFTS_PATH.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except Exception:
        logger.exception("[qa-scoring] failed to read %s — returning empty", QA_DRAFTS_PATH)
        return []


def _save_qa_drafts(items: list) -> None:
    QA_DRAFTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    QA_DRAFTS_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _next_date_for_hebrew_day_name(name: str) -> str:
    """'שישי' → '2026-05-08' (the next Friday from today)."""
    target = _HEBREW_DAY_TO_IDX.get(name)
    if target is None:
        return date.today().isoformat()
    today = date.today()
    today_hebrew = (today.weekday() + 1) % 7
    if today_hebrew == target:
        return today.isoformat()
    for i in range(1, 8):
        cand = today + timedelta(days=i)
        if (cand.weekday() + 1) % 7 == target:
            return cand.isoformat()
    return today.isoformat()


@app.get("/qa-scoring", response_class=HTMLResponse)
async def qa_scoring_page(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)
    drafts = _load_qa_drafts()
    # Newest first; unscored at the top so the operator sees what needs work.
    drafts.sort(key=lambda d: (d.get("score") is not None, -float(d.get("generated_at_ts") or 0)))
    return templates.TemplateResponse(request, name="qa_scoring.html", context={
        "drafts": drafts,
        "active_page": "qa_scoring",
    })


@app.post("/api/qa-scoring/generate")
async def qa_scoring_generate(request: Request):
    """Generate a batch of fresh AI-fill outputs through build_generation_prompt
    + _generate_via_cli/_generate_via_api and store them in qa_drafts.json.

    Body: {count?: int (1-10, default 6)}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json() if await request.body() else {}
    try:
        count = max(1, min(int(data.get("count", 6)), 10))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid count")

    import time as _time
    now_iso = datetime.now().isoformat(timespec="seconds")
    now_ts = _time.time()

    items = _load_qa_drafts()
    next_id = (max((int(d.get("id", 0)) for d in items), default=0) or 0) + 1
    generated: list[dict] = []
    errors: list[str] = []

    # Cycle through the sample slots so the operator sees a representative mix.
    slots = list(_QA_SAMPLE_SLOTS)
    random.shuffle(slots)
    slots = slots[:count]

    for draft_type, category, time_str, day_name in slots:
        target_date = _next_date_for_hebrew_day_name(day_name)
        try:
            prompt = build_generation_prompt(
                draft_type, "single", "", category,
                scheduled_date=target_date,
                scheduled_time=time_str,
            )
        except Exception as e:
            errors.append(f"prompt build failed for {draft_type}/{category}: {e}")
            continue

        content: str
        try:
            content = await _generate_via_cli(prompt)
        except Exception as cli_err:
            try:
                content = await _generate_via_api(prompt)
            except Exception as api_err:
                errors.append(
                    f"{draft_type}/{category}: cli={cli_err}; api={api_err}"
                )
                continue

        text = content.strip().replace('"', '').replace("'", "")
        first_line = next(
            (ln.strip() for ln in text.split("\n") if ln.strip()),
            text,
        )

        entry = {
            "id": next_id,
            "draft_type": draft_type,
            "category": category,
            "target_date": target_date,
            "target_time": time_str,
            "target_day_name": day_name,
            "text": first_line,
            "generated_at": now_iso,
            "generated_at_ts": now_ts,
            "score": None,
            "score_comment": "",
            "scored_at": None,
        }
        items.append(entry)
        generated.append(entry)
        next_id += 1
        logger.info(
            "[qa-scoring] generated id=%d %s/%s @ %s %s -> %r",
            entry["id"], draft_type, category, day_name, time_str, first_line[:60],
        )

    _save_qa_drafts(items)
    return {"generated": len(generated), "errors": errors, "items": generated}


@app.post("/api/qa-scoring/{draft_id}/score")
async def qa_scoring_set_score(draft_id: int, request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = await request.json() if await request.body() else {}
    try:
        score = int(body.get("score"))
    except Exception:
        raise HTTPException(status_code=400, detail="score required (int 1-5)")
    if not (1 <= score <= 5):
        raise HTTPException(status_code=400, detail="score must be 1-5")
    comment = (body.get("comment") or "").strip()

    items = _load_qa_drafts()
    found = False
    for d in items:
        if int(d.get("id", -1)) == int(draft_id):
            d["score"] = score
            d["score_comment"] = comment
            d["scored_at"] = datetime.now().isoformat(timespec="seconds")
            found = True
            break
    if not found:
        raise HTTPException(status_code=404, detail="draft not found")
    _save_qa_drafts(items)
    return {"ok": True, "id": draft_id, "score": score}


@app.post("/api/qa-scoring/{draft_id}/delete")
async def qa_scoring_delete(draft_id: int, request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    items = _load_qa_drafts()
    remaining = [d for d in items if int(d.get("id", -1)) != int(draft_id)]
    if len(remaining) == len(items):
        raise HTTPException(status_code=404, detail="draft not found")
    _save_qa_drafts(remaining)
    return {"ok": True, "remaining": len(remaining)}


@app.post("/api/qa-scoring/clear")
async def qa_scoring_clear(request: Request):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    body = await request.json() if await request.body() else {}
    only_scored = bool(body.get("only_scored", False))
    items = _load_qa_drafts()
    if only_scored:
        kept = [d for d in items if d.get("score") is None]
    else:
        kept = []
    _save_qa_drafts(kept)
    return {"ok": True, "deleted": len(items) - len(kept), "remaining": len(kept)}


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

# Static YAML pool previews are disabled in strict freshness mode; this import
# remains a compatibility hook and should return no generated preview rows.
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

    # Include cancelled rows so we can build a skipped_slots set for previews.
    # The visible-events loop below filters cancelled out before rendering.
    messages = await db.get_scheduled_messages(date_from, date_to, include_cancelled=True)

    # Channel color map
    channel_colors = {
        1517: "#6366f1",  # gaming - indigo
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
        diagnostic_label = {
            "draft": "טיוטה",
            "scheduled": "יישלח",
            "sent": "נשלח",
            "failed": "נכשל",
            "skipped": "דולג",
        }.get(status, status)
        diagnostic_detail = {
            "draft": "טיוטה: לא תישלח עד תזמון או שליחה ידנית",
            "scheduled": "מתוזמן: הסקזולר ינסה לשלוח בזמן הזה",
            "sent": "נשלח: הפעולה כבר הסתיימה",
        }.get(status, "")
        if status == "skipped":
            reason = (m.get("error_message") or "").strip()
            diagnostic_detail = f"דולג: {reason}" if reason else "דולג: הסקזולר החליט לא לשלוח"
        elif status == "failed":
            err = (m.get("error_message") or "").strip()
            if err.startswith("stale:"):
                # Stale-drop is operationally distinct from a real Telegram
                # failure — the row was just too late for the worker to fire.
                # Surface it explicitly so operators don't chase a phantom bug.
                diagnostic_label = "איחור"
                diagnostic_detail = f"איחור: {err}"
            else:
                diagnostic_detail = f"נכשל: {err}" if err else "נכשל: לא נשמרה סיבת כשל"

        quality_failures: list[str] = []
        if status == "draft" and m.get("message_type") in {"morning", "evening", "discussion"}:
            quality_failures = _quality_failures_for_planner_text(
                str(m.get("text") or ""),
                scheduled_date=m.get("scheduled_date"),
            )

        poll_options_raw = m.get("poll_options")
        poll_options: list | None = None
        game_payload: dict | None = None
        if poll_options_raw:
            try:
                decoded = json.loads(poll_options_raw) if isinstance(poll_options_raw, str) else poll_options_raw
                if isinstance(decoded, list):
                    poll_options = [str(o) for o in decoded]
                elif isinstance(decoded, dict):
                    game_payload = decoded
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
                "willSend": status == "scheduled",
                "diagnosticLabel": diagnostic_label,
                "diagnosticDetail": diagnostic_detail,
                "errorMessage": m.get("error_message"),
                "messageType": m.get("message_type", "custom"),
                "channelTopicId": m.get("channel_topic_id"),
                "recurrence": m.get("recurrence"),
                "sentAt": m.get("sent_at"),
                "createdBy": m.get("created_by"),
                "coverPath": m.get("cover_path"),
                "pollOptions": poll_options,
                "gamePayload": game_payload,
                "pollDuration": m.get("poll_duration"),
                "qualityStatus": "rejected" if quality_failures else "passed",
                "qualityFailures": quality_failures,
            },
        }
        events.append(event)

    # Build committed_index from real events to skip duplicates in previews.
    # Anything not cancelled is "committed" — including sent/failed — otherwise
    # already-sent slots would get a ghost preview on top.
    # skipped_slots: cancelled rows act as "skip markers" so the user can clear
    # a pool entry for a specific day and the preview won't regenerate it.
    committed_index: dict[tuple[str, str, str], dict] = {}
    skipped_slots: set[tuple[str, str, str]] = set()
    for m in messages:
        mtype = m.get("message_type", "")
        if mtype not in ("morning", "evening", "discussion"):
            continue
        dkey = m.get("scheduled_date", "")
        tkey = (m.get("scheduled_time") or "")[:5]
        if m.get("status") == "cancelled":
            skipped_slots.add((dkey, tkey, mtype))
            continue
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
            current_sunday.isoformat(), committed_index, used_discussion_texts,
            skipped_slots=skipped_slots,
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
                    "willSend": False,
                    "diagnosticLabel": "תצוגה בלבד",
                    "diagnosticDetail": "תצוגה בלבד: לא יישלח עד שמירה",
                    "messageType": p["type"],
                    "channelTopicId": p["topic_id"],
                    "category": p.get("category"),
                    "isPreview": True,
                },
            })
        current_sunday += timedelta(days=7)

    return events


_EXECUTABLE_HANDLERS_REQUIRING_ROUTING = {
    "trivia_round", "emoji_puzzle", "free_games",
    "facts_tidbit", "facts_spooky",
    "weekly_roundup", "weekly_leaderboard",
    "events_publish", "events_reminder",
}


def _planner_day_diagnostic_reason(
    *,
    status: str,
    error_message: str | None,
    target_group: str | None,
    channel_topic_id: int | None,
    topic_verified: bool | None,
    message_type: str | None,
    routing_by_handler: dict,
    minutes_from_now: int | None,
    will_calendar_checker_consider: bool,
) -> str:
    """Render a one-line Hebrew explanation of why a row is in its current
    state. Operators use this via /api/diagnostics/planner-day instead of
    parsing raw status + error_message combinations.

    The result must be short and self-contained: it is shown in tooltips and
    diagnostic tables, not in a Telegram message.
    """
    err = (error_message or "").strip()
    if status == "sent":
        return "נשלח בהצלחה"
    if status == "draft":
        return "טיוטה — לא תישלח עד תזמון או שליחה ידנית"
    if status == "cancelled":
        return "בוטל"
    if status == "skipped":
        return f"דולג: {err}" if err else "דולג ללא סיבה שמורה"
    if status == "failed":
        if err.startswith("stale:"):
            return f"איחור — הסקזולר לא הספיק לשלוח בזמן ({err})"
        if "No group ID" in err:
            return "כשל הגדרה: לא הוגדר ID לקבוצת היעד (GROUP_ID/TEST_GROUP_ID)"
        return f"נכשל: {err}" if err else "נכשל ללא סיבה שמורה"
    if status == "scheduled":
        notes: list[str] = []
        # Hazards an operator should know about BEFORE the scheduler ticks:
        if target_group == "main" and channel_topic_id is not None and topic_verified is False:
            notes.append(f"אזהרה: ערוץ {channel_topic_id} לא מאומת ב-verified_forum_topics")
        if message_type in _EXECUTABLE_HANDLERS_REQUIRING_ROUTING:
            if message_type not in routing_by_handler:
                notes.append(f"אזהרה: אין רשומת ניתוב ל-{message_type} ב-bot_message_routing")
        if minutes_from_now is not None and minutes_from_now < -30:
            notes.append(f"אזהרה: הזמן עבר ב-{abs(minutes_from_now)} דק׳ — צפוי stale-drop בטיק הבא")
        elif will_calendar_checker_consider:
            notes.append("יישקל בטיק הבא של הסקזולר")
        else:
            notes.append("ממתין לזמן השליחה")
        return " · ".join(notes)
    return status


@app.get("/api/diagnostics/planner-day")
async def planner_day_diagnostics(request: Request, db: Database = Depends(get_db)):
    """Read-only diagnostics for scheduled_messages on one planner date.

    This endpoint exists so agents/admins can verify production scheduler state
    without SSHing into SQLite. It returns row status/type/topic/payload data
    needed to explain whether the autonomous calendar_checker will pick a row.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    from datetime import datetime
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Jerusalem")
    now = datetime.now(tz)
    date_str = (request.query_params.get("date") or now.date().isoformat()).strip()
    try:
        target_date = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {date_str}")

    messages = await db.get_scheduled_messages(target_date.isoformat(), target_date.isoformat(), include_cancelled=True)
    due_rows = await db.get_due_messages(target_date.isoformat(), now.strftime("%H:%M")) if target_date == now.date() else []
    due_ids = {int(row["id"]) for row in due_rows}

    try:
        verified_rows = await db.get_verified_forum_topics()
    except Exception:
        verified_rows = []
    verified_topics = {}
    for row in verified_rows:
        try:
            verified_topics[int(row["topic_id"])] = {
                "category_key": row.get("category_key"),
                "name": row.get("verified_name") or row.get("observed_name"),
            }
        except Exception:
            pass

    try:
        routings = await db.list_handler_routings()
    except Exception:
        routings = []
    routing_by_handler = {r.get("handler"): r for r in routings if r.get("handler")}

    rows = []
    for msg in messages:
        topic_id = msg.get("channel_topic_id")
        try:
            topic_id_int = int(topic_id) if topic_id is not None else None
        except (TypeError, ValueError):
            topic_id_int = None
        payload = _parse_game_payload(msg.get("poll_options"))
        poll_options = None
        if msg.get("poll_options") and not payload:
            try:
                parsed = json.loads(msg.get("poll_options"))
                if isinstance(parsed, list):
                    poll_options = [str(x) for x in parsed]
            except Exception:
                poll_options = None

        sched_time = (msg.get("scheduled_time") or "00:00")[:5]
        try:
            sched_dt = datetime.fromisoformat(f"{target_date.isoformat()}T{sched_time}").replace(tzinfo=tz)
            minutes_from_now = int((sched_dt - now).total_seconds() / 60)
        except Exception:
            minutes_from_now = None
        status = msg.get("status") or "scheduled"
        will_calendar_checker_consider = status == "scheduled" and (
            target_date < now.date() or (target_date == now.date() and sched_time <= now.strftime("%H:%M"))
        )
        topic_verified = topic_id_int in verified_topics if topic_id_int is not None else None
        diagnostic_reason = _planner_day_diagnostic_reason(
            status=status,
            error_message=msg.get("error_message"),
            target_group=msg.get("target_group"),
            channel_topic_id=topic_id_int,
            topic_verified=topic_verified,
            message_type=msg.get("message_type"),
            routing_by_handler=routing_by_handler,
            minutes_from_now=minutes_from_now,
            will_calendar_checker_consider=will_calendar_checker_consider,
        )
        rows.append({
            "id": msg.get("id"),
            "scheduled_date": msg.get("scheduled_date"),
            "scheduled_time": sched_time,
            "minutes_from_now": minutes_from_now,
            "status": status,
            "message_type": msg.get("message_type"),
            "target_group": msg.get("target_group"),
            "created_by": msg.get("created_by"),
            "channel_topic_id": topic_id_int,
            "topic_verified": topic_verified,
            "topic_name": (verified_topics.get(topic_id_int) or {}).get("name") if topic_id_int is not None else None,
            "text_preview": (msg.get("text") or "").replace("\n", " ")[:120],
            "payload": payload,
            "poll_options": poll_options,
            "error_message": msg.get("error_message"),
            "sent_at": msg.get("sent_at"),
            "sent_message_id": msg.get("sent_message_id"),
            "due_now": int(msg.get("id")) in due_ids,
            "will_calendar_checker_consider": will_calendar_checker_consider,
            "diagnostic_reason": diagnostic_reason,
        })

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for row in rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
        mtype = row.get("message_type") or "custom"
        by_type[mtype] = by_type.get(mtype, 0) + 1

    return {
        "date": target_date.isoformat(),
        "now_il": now.isoformat(timespec="seconds"),
        "counts": {"total": len(rows), "by_status": by_status, "by_type": by_type},
        "routing": routing_by_handler,
        "rows": rows,
    }


@app.post("/api/calendar")
async def create_calendar_item(request: Request, db: Database = Depends(get_db)):
    """Create a new scheduled message."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    poll_options = data.get("poll_options")
    raw_type = data.get("message_type", "custom")
    raw_topic = data.get("channel_topic_id")
    message_type, poll_options = _coerce_game_message_fields(raw_type, data["text"], poll_options, raw_topic)
    channel_topic_id = raw_topic
    if message_type == "trivia_round" and (raw_type != "trivia_round" or not raw_topic):
        routing = await db.get_handler_routing("trivia_round")
        channel_topic_id = routing["play_topic_id"] if routing and routing.get("play_topic_id") is not None else raw_topic
    elif message_type == "emoji_puzzle" and (raw_type != "emoji_puzzle" or not raw_topic):
        routing = await db.get_handler_routing("emoji_puzzle")
        channel_topic_id = routing["play_topic_id"] if routing and routing.get("play_topic_id") is not None else raw_topic
    elif message_type in {"free_games", "facts_tidbit", "facts_spooky", "weekly_roundup", "weekly_leaderboard"} and not raw_topic:
        routing = await db.get_handler_routing(message_type)
        channel_topic_id = routing["play_topic_id"] if routing and routing.get("play_topic_id") is not None else raw_topic
    if message_type in {"morning", "evening", "discussion"}:
        _reject_bad_planner_text(data["text"])

    trivia_topup = None
    if message_type == "trivia_round":
        trivia_topup = await _ensure_trivia_pool_ready_for_round({
            "id": "new",
            "poll_options": poll_options,
        })

    msg_id = await db.create_scheduled_message(
        text=data["text"],
        message_type=message_type,
        channel_topic_id=channel_topic_id,
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
    announcement_draft_id = None
    if message_type == "trivia_round":
        announcement_draft_id = await _ensure_trivia_announcement_scheduled(db, game_id=msg_id)
    return {"status": "ok", "id": msg_id, "announcement_draft_id": announcement_draft_id, "trivia_topup": trivia_topup}


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
    if "text" in fields or "message_type" in fields:
        raw_type = fields.get("message_type", data.get("message_type", "custom"))
        raw_topic = fields.get("channel_topic_id", data.get("channel_topic_id"))
        coerced_type, coerced_poll_options = _coerce_game_message_fields(
            raw_type,
            fields.get("text", data.get("text", "")),
            fields.get("poll_options"),
            raw_topic,
        )
        fields["message_type"] = coerced_type
        if coerced_poll_options is not None:
            fields["poll_options"] = coerced_poll_options
        if coerced_type == "trivia_round" and (raw_type != "trivia_round" or not raw_topic):
            routing = await db.get_handler_routing("trivia_round")
            if routing and routing.get("play_topic_id") is not None:
                fields["channel_topic_id"] = routing["play_topic_id"]
        elif coerced_type == "emoji_puzzle" and (raw_type != "emoji_puzzle" or not raw_topic):
            routing = await db.get_handler_routing("emoji_puzzle")
            if routing and routing.get("play_topic_id") is not None:
                fields["channel_topic_id"] = routing["play_topic_id"]
        elif coerced_type in {"free_games", "facts_tidbit", "facts_spooky", "weekly_roundup", "weekly_leaderboard"} and not raw_topic:
            routing = await db.get_handler_routing(coerced_type)
            if routing and routing.get("play_topic_id") is not None:
                fields["channel_topic_id"] = routing["play_topic_id"]
    if "recurrence_days" in fields and isinstance(fields["recurrence_days"], list):
        fields["recurrence_days"] = json.dumps(fields["recurrence_days"])
    if "poll_options" in fields and isinstance(fields["poll_options"], list):
        fields["poll_options"] = json.dumps(fields["poll_options"])
    if "text" in fields:
        effective_text_type = fields.get("message_type")
        if effective_text_type is None:
            async with db._db.execute(
                "SELECT message_type FROM scheduled_messages WHERE id = ?",
                (msg_id,),
            ) as cur:
                existing_type_row = await cur.fetchone()
            effective_text_type = existing_type_row["message_type"] if existing_type_row else None
        if effective_text_type in {"morning", "evening", "discussion"}:
            _reject_bad_planner_text(str(fields["text"] or ""))

    trivia_topup = None
    if fields.get("status") == "scheduled":
        async with db._db.execute(
            "SELECT id, text, scheduled_date, message_type, poll_options FROM scheduled_messages WHERE id = ?",
            (msg_id,),
        ) as cur:
            existing_row = await cur.fetchone()
        if existing_row:
            _reject_bad_message_row({
                "text": fields.get("text", existing_row["text"]),
                "message_type": fields.get("message_type", existing_row["message_type"]),
                "scheduled_date": fields.get("scheduled_date", existing_row["scheduled_date"]),
            })
        effective_type = fields.get("message_type") or (existing_row["message_type"] if existing_row else None)
        if effective_type == "trivia_round":
            trivia_topup = await _ensure_trivia_pool_ready_for_round({
                "id": msg_id,
                "poll_options": fields.get("poll_options") if "poll_options" in fields else (existing_row["poll_options"] if existing_row else None),
            })

    await db.update_scheduled_message(msg_id, **fields)
    announcement_draft_id = None
    if fields.get("status") == "scheduled":
        async with db._db.execute(
            "SELECT message_type FROM scheduled_messages WHERE id = ?",
            (msg_id,),
        ) as cur:
            row = await cur.fetchone()
        if row and row["message_type"] == "trivia_round":
            announcement_draft_id = await _ensure_trivia_announcement_scheduled(db, game_id=msg_id)
    return {"status": "ok", "announcement_draft_id": announcement_draft_id, "trivia_topup": trivia_topup}


@app.delete("/api/calendar/{msg_id}")
async def delete_calendar_item(msg_id: int, request: Request, db: Database = Depends(get_db)):
    """Cancel a scheduled message."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.delete_scheduled_message(msg_id)
    return {"status": "ok"}


@app.post("/api/weekplan/skip-slot")
async def skip_weekplan_slot(request: Request, db: Database = Depends(get_db)):
    """Mark a planner slot as skipped so the pool fallback won't fill it.

    Body: {date, time, type}
    Creates a scheduled_messages row with empty text and status='cancelled' which
    the planner reads as a 'skip marker' for that slot.
    Returns: {status, id}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    date_str = (data.get("date") or "").strip()
    time_str = (data.get("time") or "").strip()
    mtype = (data.get("type") or "").strip()

    if not date_str or not time_str:
        raise HTTPException(status_code=400, detail="Missing date or time")
    if mtype not in ("morning", "evening", "discussion"):
        raise HTTPException(status_code=400, detail=f"Invalid type: {mtype}")

    new_id = await db.create_scheduled_message(
        text="",
        message_type=mtype,
        channel_topic_id=None,
        target_group="main",
        scheduled_date=date_str,
        scheduled_time=time_str,
        created_by="weekplan-skip",
    )
    await db.delete_scheduled_message(new_id)  # immediately mark cancelled
    logger.info("[weekplan.skip-slot] date=%s time=%s type=%s id=%d", date_str, time_str, mtype, new_id)
    return {"status": "ok", "id": new_id}


@app.post("/api/weekplan/cancel-auto-future")
async def cancel_auto_future_rows(request: Request, db: Database = Depends(get_db)):
    """Bulk-cancel all future auto-materialized scheduled rows.

    Used to purge AI/auto-generated content that pre-dates a quality-rules
    change so the next ai-fill regenerates everything under the new rules.
    Leaves user-committed rows (created_by != 'auto') untouched.

    Returns: {status, cancelled, from_date}
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    from datetime import date
    today_iso = date.today().isoformat()
    cancelled = await db.cancel_future_auto_scheduled_messages(today_iso)
    logger.info("[weekplan.cancel-auto-future] from=%s cancelled=%d", today_iso, cancelled)
    return {"status": "ok", "cancelled": cancelled, "from_date": today_iso}


async def _send_scheduled_row(db: Database, msg: dict, target: str) -> int:
    """Send one scheduled_messages row to Telegram immediately.

    This is the operator-triggered counterpart to
    ``bot.handlers.calendar.check_and_send_due_messages`` — both routes call
    the same per-type handlers (start_scheduled_trivia_round, start_emoji_night,
    send_scheduled_fact, send_weekly_*, send_poll_message,
    send_message_with_optional_cover) and reach status='sent' on the same row.

    Intentional differences from the scheduler (pinned by
    ``tests/test_send_now_parity.py::IntentionalDivergenceTests``):

    * No stale-drop guard — operator explicitly said "send now".
    * No recurrence next-occurrence — operator firing once is one-shot.
    * No warm-up RSVP gate — operator override of the attendance check.
    * No ``should_skip_scheduled_message`` blackout check — operator override.
    * Test target (``target == 'test'``) skips ``mark_message_sent`` so a
      probe doesn't burn the row.
    * Failures propagate as exceptions (the route turns them into HTTP 500);
      the row keeps its prior status rather than flipping to ``failed``.

    Known gaps vs the scheduler (filed as REG-T157-* regression tasks; current
    behavior pinned by
    ``tests/test_send_now_parity.py::CurrentBugBehaviorPinnedTests``):

    * Emoji puzzle: ignores ``media_types`` and ``theme_label`` from poll_options.
    * Facts: ignores pinned ``fact_id`` from poll_options.
    * Event: no events-table row, no RSVP button attachment.
    * SkippedActivity: not surfaced as ``status='skipped'``; lost to a 500.
    * Activity log: no ``db.log_activity`` audit entry.
    * Auto-pin: ``auto_pin`` field on the row is not honored.

    Returns the sent Telegram message_id. Raises on error.
    """
    from telegram import Bot
    from types import SimpleNamespace
    from bot.handlers.calendar import (
        send_message_with_optional_cover,
        send_poll_message,
        _parse_poll_options,
    )
    from bot.handlers.emoji_puzzle import start_emoji_night
    from bot.handlers.facts import send_scheduled_fact
    from bot.handlers.free_games import send_free_games
    from bot.handlers.levels import send_weekly_leaderboard
    from bot.handlers.roundup import send_weekly_roundup
    from bot.handlers.trivia_round import start_scheduled_trivia_round
    bot = Bot(os.getenv("BOT_TOKEN", ""))
    group_id = int(os.getenv("TEST_GROUP_ID", "0") if target == "test" else os.getenv("GROUP_ID", "0"))
    context = SimpleNamespace(bot=bot, bot_data={"db": db})

    if msg.get("message_type") == "trivia_round":
        msg = dict(msg)
        msg["_resolved_chat_id"] = group_id
        message_id = int(await start_scheduled_trivia_round(context, msg) or 0)
        if message_id <= 0:
            raise RuntimeError("trivia_round did not return a Telegram message_id")
        if target != "test":
            await db.mark_message_sent(msg["id"], message_id)
        return message_id
    if msg.get("message_type") == "emoji_puzzle":
        session_id = await start_emoji_night(context, group_id, msg.get("channel_topic_id"), force=True)
        if session_id is None:
            raise RuntimeError("Emoji Night did not start")
        if target != "test":
            await db.mark_message_sent(msg["id"], session_id)
        return session_id
    if msg.get("message_type") == "free_games":
        summary = await send_free_games(context, force=True)
        if not summary or int(summary.get("posted") or 0) <= 0:
            raise RuntimeError(f"free_games did not post: {summary}")
        if target != "test":
            await db.mark_message_sent(msg["id"], 1)
        return 1
    if msg.get("message_type") in {"facts_tidbit", "facts_spooky"}:
        pool = msg.get("message_type", "").removeprefix("facts_")
        sent_ok = await send_scheduled_fact(
            bot,
            db,
            pool=pool,
            chat_id=group_id,
            thread_id=msg.get("channel_topic_id"),
        )
        if not sent_ok:
            raise RuntimeError(f"facts {pool} did not send")
        if target != "test":
            await db.mark_message_sent(msg["id"], 1)
        return 1
    if msg.get("message_type") == "weekly_roundup":
        message_id = int(await send_weekly_roundup(context, force=True) or 0)
        if message_id <= 0:
            raise RuntimeError("weekly_roundup did not return a Telegram message_id")
        if target != "test":
            await db.mark_message_sent(msg["id"], message_id)
        return message_id
    if msg.get("message_type") == "weekly_leaderboard":
        message_id = int(await send_weekly_leaderboard(context) or 0)
        if message_id <= 0:
            raise RuntimeError("weekly_leaderboard did not return a Telegram message_id")
        if target != "test":
            await db.mark_message_sent(msg["id"], message_id)
        return message_id

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

    headers = getattr(request, "headers", {}) or {}
    data = await request.json() if headers.get("content-type") == "application/json" else {}
    target = data.get("target", "main")

    messages = await db.get_scheduled_messages("2000-01-01", "2099-12-31")
    msg = next((m for m in messages if m["id"] == msg_id), None)
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    _reject_bad_message_row(msg)

    try:
        sent_id = await _send_scheduled_row(db, msg, target)
        logger.info("[send-now] msg_id=%d target=%s sent_message_id=%s", msg_id, target, sent_id)
        return {"status": "ok", "message_id": sent_id}
    except Exception as e:
        logger.exception("[send-now] failed for msg_id=%d", msg_id)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/calendar/{msg_id}/schedule")
async def schedule_calendar_item(msg_id: int, request: Request, db: Database = Depends(get_db)):
    """Set a draft row to status='scheduled' so the bot's calendar_checker
    delivers it at the scheduled_time. Optionally update scheduled_time first
    (body: {scheduled_time: 'HH:MM'}).

    Refuses if the resulting scheduled datetime is in the past or within the
    next 2 minutes — the bot's 60s tick would fire it almost immediately,
    bypassing the 'review then schedule' intent. Pass force=true to override.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    try:
        body = await request.json()
    except Exception:
        body = {}
    new_date = (body.get("scheduled_date") or "").strip() or None
    new_time = (body.get("scheduled_time") or "").strip() or None
    force = bool(body.get("force", False))

    async with db._db.execute(
        "SELECT id, text, scheduled_date, scheduled_time, status, message_type, poll_options FROM scheduled_messages WHERE id = ?",
        (msg_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="message not found")

    _reject_bad_message_row({
        "text": row["text"],
        "message_type": row["message_type"],
        "scheduled_date": new_date or row["scheduled_date"],
    })

    target_date_str = new_date or row["scheduled_date"]
    target_time_str = new_time or (row["scheduled_time"] or "")[:5]
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Jerusalem")).replace(tzinfo=None)
    try:
        target_dt = datetime.strptime(
            f"{target_date_str} {target_time_str}", "%Y-%m-%d %H:%M"
        )
    except ValueError:
        raise HTTPException(status_code=400, detail=f"invalid scheduled date/time {target_date_str!r} {target_time_str!r}")

    if not force:
        delta = (target_dt - now).total_seconds()
        if delta < 120:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "past_due",
                    "message": (
                        "הסלוט מתוזמן בעבר או פחות מ-2 דקות בעתיד. "
                        "הוסף force=true או 'שלח עכשיו' אם זה מה שרצית."
                    ),
                    "scheduled_for": target_dt.isoformat(),
                    "seconds_from_now": int(delta),
                },
            )

    topup_result = None
    if row["message_type"] == "trivia_round":
        topup_result = await _ensure_trivia_pool_ready_for_round(row)

    update_fields: dict = {"status": "scheduled"}
    if new_date and new_date != row["scheduled_date"]:
        update_fields["scheduled_date"] = new_date
    if new_time and new_time != (row["scheduled_time"] or "")[:5]:
        update_fields["scheduled_time"] = new_time
    await db.update_scheduled_message(msg_id, **update_fields)
    announcement_draft_id = None
    if row["message_type"] == "trivia_round":
        announcement_draft_id = await _ensure_trivia_announcement_scheduled(db, game_id=msg_id)
    logger.info(
        "[schedule] msg_id=%d → status=scheduled at %s (force=%s)",
        msg_id, target_dt.isoformat(), force,
    )
    return {
        "status": "ok",
        "id": msg_id,
        "scheduled_for": target_dt.isoformat(),
        "announcement_draft_id": announcement_draft_id,
        "trivia_topup": topup_result,
    }


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
            _reject_bad_message_row(msg)
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


@app.post("/api/facts/{pool}/{fact_id}/generate-image")
async def generate_fact_preview_image(pool: str, fact_id: str, request: Request):
    """Generate a preview image for a curated fact and persist its URL.

    Calls kie.ai with the fact's image_prompt (or the pool-default
    template), saves the bytes to ``media/facts/`` so the dashboard can
    serve them as static, and writes the resulting URL into
    ``config/facts.yaml`` against the matching item. The next preview
    render reads ``image_url`` directly and shows the real picture
    instead of the "תמונה תיווצר בזמן השליחה" placeholder.

    Idempotent in the sense that re-clicking the button regenerates
    (the URL changes every call). Operators who want a stable image
    can keep the first one and just not click again.
    """
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)
    if pool not in {"tidbit", "spooky"}:
        raise HTTPException(status_code=400, detail=f"unknown pool: {pool}")

    api_key = os.getenv("KIE_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=500, detail="KIE_API_KEY not set in environment")

    # Locate the fact in facts.yaml. We rewrite the file in-place after
    # generation so the dashboard preview and the bot's runtime path read
    # the same source of truth.
    facts_path = CONFIG_DIR / "facts.yaml"
    try:
        facts_data = yaml.safe_load(facts_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"facts.yaml unreadable: {e}")
    pool_items = facts_data.get(pool) or []
    target_idx = next(
        (i for i, x in enumerate(pool_items) if isinstance(x, dict) and str(x.get("id") or "") == fact_id),
        -1,
    )
    if target_idx < 0:
        raise HTTPException(status_code=404, detail=f"fact {fact_id!r} not found in {pool}")
    item = pool_items[target_idx]

    # Reuse the bot's prompt builder so generation matches what runtime
    # would do (identical mood/suffix templates from settings.yaml).
    from bot.handlers.facts import _build_fact_image_prompt
    from bot.utils.kie_client import generate_image_sync

    prompt = _build_fact_image_prompt(pool, item)
    try:
        data, ext = await generate_image_sync(api_key=api_key, prompt=prompt)
    except Exception as e:
        logger.error("kie.ai facts generation failed for %s/%s: %s", pool, fact_id, e)
        raise HTTPException(status_code=502, detail=f"Generation failed: {e}")

    # Filename pattern keys on (pool, fact_id) so re-clicks overwrite the
    # same file rather than littering the directory.
    safe_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", fact_id)[:80] or "fact"
    dest = FACTS_IMAGES_DIR / f"{pool}_{safe_id}.{ext or 'png'}"
    dest.write_bytes(data)
    rel = dest.relative_to(MEDIA_DIR).as_posix()
    image_url = f"/media/{rel}"
    logger.info("[facts] preview image generated for %s/%s → %s (%d bytes)", pool, fact_id, dest.name, len(data))

    # Persist URL back into facts.yaml so subsequent previews + sends
    # both pick it up. Cache-bust query string forces the dashboard <img>
    # to refresh after a re-click.
    item["image_url"] = image_url
    pool_items[target_idx] = item
    facts_data[pool] = pool_items
    facts_path.write_text(
        yaml.dump(facts_data, allow_unicode=True, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )
    return {"status": "ok", "url": image_url, "path": rel, "cache_bust": int(dest.stat().st_mtime)}


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
    daily_chips = []
    if goals_id and goals_id in verified_by_id:
        daily_chips.append({"topic_id": goals_id, "name": verified_by_id[goals_id]["verified_name"]})
    if welcome_id and welcome_id in verified_by_id:
        daily_chips.append({"topic_id": welcome_id, "name": verified_by_id[welcome_id]["verified_name"]})
    grouped_channels = {
        "discussions": [
            {"topic_id": tid,
             "name": verified_by_id[tid]["verified_name"],
             "category": cat}
            for cat, tid in topic_ids_dict.items()
            if tid and tid in verified_by_id
        ],
        "daily": daily_chips,
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
        "trivia_populate_defaults": (settings_obj.get("trivia") or {}).get("populate_defaults") or {},
        "trivia_current_questions_text": trivia_current_questions_text,
    })


@app.get("/planner/suggestion-preview", response_class=HTMLResponse)
async def planner_suggestion_preview(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    qp = request.query_params
    kind = str(qp.get("kind") or "").strip()
    title = kind
    body = ""

    if kind in {"facts_tidbit", "facts_spooky"}:
        pool = "tidbit" if kind == "facts_tidbit" else "spooky"
        wanted_id = str(qp.get("id") or "").strip()
        item = None
        for candidate in (load_yaml("facts.yaml") or {}).get(pool) or []:
            if isinstance(candidate, dict) and str(candidate.get("id") or "") == wanted_id:
                item = candidate
                break
        if not item:
            raise HTTPException(status_code=404, detail="preview fact not found")
        title = "עובדה מעניינת" if pool == "tidbit" else "סיפור מסתורי"
        image_url = str(item.get("image_url") or "").strip()
        image_prompt = str(item.get("image_prompt") or "").strip()
        image_block = f'<img src="{html.escape(image_url)}" alt="" class="preview-img">' if image_url else ""
        if not image_block and image_prompt:
            image_block = (
                '<div class="image-prompt">תמונה תיווצר בזמן השליחה לפי הנחיה אוצרת. הטקסט והמקור למטה הם התוכן לאישור.</div>'
            )
        # "Generate preview image" button — only meaningful when there's
        # a curated image_prompt. The button calls /api/facts/{pool}/{id}
        # /generate-image, which writes image_url back into facts.yaml so
        # the next preview render swaps the placeholder for the real
        # picture. JS is inline so the preview page stays self-contained.
        gen_button_html = ""
        if image_prompt:
            label = "צור תמונת תצוגה מחדש" if image_url else "צור תמונת תצוגה"
            gen_button_html = (
                '<div class="image-actions" style="margin:8px 0;">'
                f'<button id="facts-gen-img-btn" data-pool="{html.escape(pool)}" data-id="{html.escape(wanted_id)}" '
                'style="padding:6px 12px;background:#7c3aed;color:white;border:none;border-radius:4px;cursor:pointer;">'
                f'{label}</button>'
                '<span id="facts-gen-img-status" style="margin-right:8px;color:#a1a1aa;font-size:12px;"></span>'
                '</div>'
                '<script>'
                '(function(){'
                'var btn=document.getElementById("facts-gen-img-btn");'
                'if(!btn)return;'
                'btn.addEventListener("click",async function(){'
                'var status=document.getElementById("facts-gen-img-status");'
                'btn.disabled=true;status.textContent="מייצר תמונה — עד 30 שניות...";'
                'try{'
                'var r=await fetch("/api/facts/"+btn.dataset.pool+"/"+encodeURIComponent(btn.dataset.id)+"/generate-image",{method:"POST"});'
                'if(!r.ok){status.textContent="נכשל: "+r.status;btn.disabled=false;return;}'
                'var j=await r.json();status.textContent="✓ נוצרה תמונה — מרענן";'
                'setTimeout(function(){location.reload();},400);'
                '}catch(e){status.textContent="שגיאה: "+e.message;btn.disabled=false;}'
                '});'
                '})();'
                '</script>'
            )
        # Mirror the runtime caption preface so the preview matches what the
        # bot actually sends. Loaded from settings.yaml:copy.facts.preface_*.
        from bot.utils.copy import load_copy as _load_copy_preface
        preface = (_load_copy_preface("facts", f"preface_{pool}", default="") or "").strip()
        preface_html = (
            f'<div class="post-preface" style="font-weight:600;margin-bottom:8px;">{html.escape(preface)}</div>'
            if preface else ""
        )
        text_html = html.escape(str(item.get("text_he") or "")).replace(chr(10), "<br>")
        source = html.escape(str(item.get("source") or ""))
        source_url = html.escape(str(item.get("source_url") or ""))
        body = image_block + gen_button_html + preface_html + f'<div class="post-text">{text_html}</div><div class="source">מקור: {source}<br><a href="{source_url}" target="_blank" rel="noopener">{source_url}</a></div>'
    elif kind == "emoji_puzzle":
        media = [str(x).strip() for x in qp.getlist("media") if str(x).strip()]
        aliases = []
        for m in media:
            aliases.extend(["tv", "series"] if m in {"tv", "series"} else [m])
        title = f"Emoji Night — {qp.get('theme') or 'נושא'}"
        rows = []
        if aliases:
            placeholders = ",".join("?" for _ in aliases)
            async with db._db.execute(
                f"SELECT emoji_prompt, answer_he, media_type FROM emoji_puzzles WHERE enabled = 1 AND media_type IN ({placeholders}) LIMIT 8",
                tuple(aliases),
            ) as cur:
                rows = await cur.fetchall()
        body = "".join(
            f'<div class="sample"><b>{html.escape(str(r[0]))}</b><span>{html.escape(str(r[1]))} · {html.escape(str(r[2]))}</span></div>'
            for r in rows
        ) or '<div class="muted">אין דוגמאות זמינות לנושא הזה.</div>'
    elif kind == "trivia_round":
        cats = [str(x).strip() for x in qp.getlist("categories") if str(x).strip()]
        count = int(qp.get("count") or 5)
        title = f"טריוויה — {qp.get('theme') or ', '.join(cats) or 'כללי'}"
        questions = (load_yaml("trivia.yaml") or {}).get("questions") or []
        matching = [q for q in questions if not cats or str((q or {}).get("category") or "").strip() in cats]
        random.shuffle(matching)
        body = "".join(
            f'<div class="sample"><b>{html.escape(str(q.get("text") or ""))}</b><span>{html.escape(str(q.get("category") or ""))}</span></div>'
            for q in matching[:count]
        ) or '<div class="muted">אין שאלות תואמות לתצוגה.</div>'
    elif kind in {"free_games", "weekly_roundup", "weekly_leaderboard"}:
        labels = {
            "free_games": "משחקים חינם",
            "weekly_roundup": "סיכום שבועי",
            "weekly_leaderboard": "טבלת רמות שבועית",
        }
        title = labels[kind]
        body = '<div class="muted">תוכן זה מחושב בזמן השליחה ממידע חי. אין טקסט דמו או נושא מוסתר; אישור הסלוט מאשר רק את זמן ההרצה והערוץ.</div>'
    else:
        raise HTTPException(status_code=400, detail="unknown preview kind")

    page = f"""<!doctype html>
<html lang="he" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{margin:0;background:#09090b;color:#f4f4f5;font-family:system-ui,-apple-system,Segoe UI,sans-serif;padding:32px;line-height:1.55}}
.wrap{{max-width:760px;margin:0 auto;background:#111318;border:1px solid #27272a;border-radius:16px;padding:24px}}
h1{{margin:0 0 18px;font-size:24px}}.post-text{{font-size:18px;margin:16px 0}}.source{{color:#a1a1aa;border-top:1px solid #27272a;padding-top:14px;margin-top:18px;font-size:14px}}
a{{color:#7dd3fc}}.preview-img{{width:100%;border-radius:12px;margin:8px 0 18px}}.image-prompt{{direction:ltr;text-align:left;background:#18181b;border:1px solid #3f3f46;border-radius:12px;padding:14px;color:#d4d4d8;margin:8px 0 18px}}
.sample{{border:1px solid #27272a;border-radius:12px;padding:14px;margin:10px 0;background:#0b0d10}}.sample b{{display:block;font-size:17px;margin-bottom:6px}}.sample span,.muted{{color:#a1a1aa}}
</style></head><body><main class="wrap"><h1>{html.escape(title)}</h1>{body}</main></body></html>"""
    return HTMLResponse(page)


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
