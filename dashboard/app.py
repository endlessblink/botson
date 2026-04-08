"""Botson Dashboard — FastAPI backend for managing the bot."""

import asyncio
import json
import os
import secrets
import signal
from pathlib import Path

import yaml
from fastapi import FastAPI, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from bot.database.db import Database
from bot.utils.config import DB_PATH, get_settings, get_prompts, get_spam_patterns, load_yaml
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

app = FastAPI(title="Botson Dashboard", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("DASHBOARD_SECRET", secrets.token_hex(32)))

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
CONFIG_DIR = Path(__file__).parent.parent / "config"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

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

    return {"status": "ok"}


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

    return {"status": "ok"}


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

    return {"status": "ok"}


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
    topic_id = data.get("topic_id")  # None = general chat

    if not text:
        raise HTTPException(status_code=400, detail="Message text required")

    from telegram import Bot
    bot = Bot(os.getenv("BOT_TOKEN", ""))
    group_id = int(os.getenv("GROUP_ID", "0"))

    kwargs = {"chat_id": group_id, "text": text}
    if topic_id:
        kwargs["message_thread_id"] = int(topic_id)

    try:
        msg = await bot.send_message(**kwargs)
        await db.log_activity("goals", f"שלח הודעה ידנית", target_channel=str(topic_id or "general"))
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

    return {"status": "ok"}


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

    return templates.TemplateResponse(request, name="events.html", context={
        "events": events,
        "settings": settings,
    })


@app.post("/api/events/create")
async def create_event(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    event_id = await db.create_event(
        title=data["title"],
        description=data.get("description", ""),
        event_date=data["event_date"],
        event_time=data.get("event_time"),
        location=data.get("location"),
        created_by=0,  # Dashboard-created
    )
    return {"status": "ok", "event_id": event_id}


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
    return {"status": "ok"}


@app.post("/api/blocked/{user_id}/remove")
async def remove_blocked_user(user_id: int, request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    await db.unblock_user(user_id)
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


# ── Generate API ─────────────────────────────────────────

COMMUNITY_CONTEXT = """קהילת "אלהוריים וזה" — קהילת צ'ילדפרי (ללא ילדים מבחירה) בטלגרם.
הקהילה היא חמה, תומכת ומהנה. השפה עברית. התוכן רלוונטי לאורח חיים של מבוגרים, צמיחה אישית, וחיזוק הקשר הקהילתי."""


def build_generation_prompt(field: str, mode: str, existing: str, category: str) -> str:
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
    mode = data["mode"]         # "append" or "replace"
    existing = data.get("existing", "")
    category = data.get("category", "")

    prompt = build_generation_prompt(field, mode, existing, category)

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

    return {"status": "ok"}


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

    return {"status": "ok"}


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


# ── Planner Page ─────────────────────────────────────────

@app.get("/planner", response_class=HTMLResponse)
async def planner_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Jerusalem")
    now = datetime.now(tz)

    hebrew_days = ['שני', 'שלישי', 'רביעי', 'חמישי', 'שישי', 'שבת', 'ראשון']
    today_name = f"יום {hebrew_days[now.weekday()]}"
    today_str = now.strftime("%d.%m.%Y")
    now_time = now.strftime("%H:%M")

    settings = get_settings()
    schedule = settings.get("schedule", {})

    # Get ALL activity log entries (last 7 days for the week view)
    all_log = await db.get_activity_log(500)

    # Group log entries by date
    log_by_date = {}
    for entry in all_log:
        ts = entry.get("timestamp", "")
        if len(ts) >= 10:
            date_key = ts[:10]
            if date_key not in log_by_date:
                log_by_date[date_key] = []
            log_by_date[date_key].append(entry)

    today_date = now.strftime("%Y-%m-%d")

    # Build today's view — merge scheduled + actual activity
    today_items = []

    # Add items from activity log (what actually happened today)
    for entry in log_by_date.get(today_date, []):
        action = entry.get("action_type", "")
        desc = entry.get("description", "")
        time_str = entry.get("timestamp", "")[11:16] if len(entry.get("timestamp", "")) > 16 else ""

        type_map = {
            "goals": ("morning" if "בוקר" in desc else "evening", "בוקר" if "בוקר" in desc else "ערב"),
            "discussion": ("discussion", "דיון"),
            "points": ("points", "נקודות"),
            "level_up": ("level", "עליית רמה"),
            "spam": ("spam", "ספאם"),
            "trivia": ("trivia", "טריוויה"),
            "event": ("event", "אירוע"),
            "roundup": ("roundup", "סיכום"),
        }

        if action in type_map:
            t, label = type_map[action]
            today_items.append({
                "time": time_str,
                "type": t,
                "label": label,
                "desc": desc,
                "status": "done",
            })

    # Add upcoming scheduled items that haven't fired yet
    morning = schedule.get("morning_prompt", {})
    if isinstance(morning, dict):
        hebrew_day = (now.weekday() + 1) % 7
        if hebrew_day in morning.get("days", []):
            m_time = morning.get("time", "09:00")
            already_fired = any(i["type"] == "morning" for i in today_items)
            if not already_fired and now.strftime("%H:%M") < m_time:
                today_items.append({
                    "time": m_time, "type": "morning", "label": "בוקר",
                    "desc": "הודעת בוקר — יום יום",
                    "status": "pending"
                })

    evening = schedule.get("evening_prompt", {})
    if isinstance(evening, dict):
        hebrew_day = (now.weekday() + 1) % 7
        if hebrew_day in evening.get("days", []):
            e_time = evening.get("time", "21:00")
            already_fired = any(i["type"] == "evening" for i in today_items)
            if not already_fired and now.strftime("%H:%M") < e_time:
                today_items.append({
                    "time": e_time, "type": "evening", "label": "ערב",
                    "desc": "הודעת ערב — יום יום",
                    "status": "pending"
                })

    disc = schedule.get("discussion_prompt", {})
    if isinstance(disc, dict):
        hebrew_day = (now.weekday() + 1) % 7
        if hebrew_day in disc.get("days", []):
            for t in disc.get("times", []):
                already_fired = any(i["type"] == "discussion" and i["time"] == t for i in today_items)
                if not already_fired and now.strftime("%H:%M") < t:
                    today_items.append({
                        "time": t, "type": "discussion", "label": "דיון",
                        "desc": "שאלה לדיון",
                        "status": "pending"
                    })

    today_items.sort(key=lambda x: x["time"])

    # Build week plan
    week_plan = []
    for i in range(7):
        d = now + timedelta(days=i)
        day_name = f"יום {hebrew_days[d.weekday()]}"
        day_date = d.strftime("%d.%m")
        d_date_key = d.strftime("%Y-%m-%d")
        hebrew_day = (d.weekday() + 1) % 7

        entries = []

        if i == 0:
            # Today — use the merged today_items
            entries = today_items
        else:
            # Future days — show scheduled items
            if isinstance(morning, dict) and hebrew_day in morning.get("days", []):
                entries.append({"time": morning.get("time", "09:00"), "type": "morning", "label": "בוקר", "desc": "הודעת בוקר", "status": ""})
            if isinstance(disc, dict) and hebrew_day in disc.get("days", []):
                for t in disc.get("times", []):
                    entries.append({"time": t, "type": "discussion", "label": "דיון", "desc": "שאלה לדיון", "status": ""})
            if isinstance(evening, dict) and hebrew_day in evening.get("days", []):
                entries.append({"time": evening.get("time", "21:00"), "type": "evening", "label": "ערב", "desc": "הודעת ערב", "status": ""})

        entries.sort(key=lambda x: x.get("time", ""))

        sensitive = None
        holiday = None
        date_str = d.strftime("%m-%d")
        if date_str == "04-14":
            sensitive = "יום השואה"

        week_plan.append({
            "name": day_name,
            "date": day_date,
            "is_today": i == 0,
            "holiday": holiday,
            "sensitive": sensitive,
            "entries": entries,
            "note": "הכל כבוי חוץ מספאם" if sensitive else None,
        })

    actions = [
        {"when": "חמישי 9.4", "what": "להפעיל events לראשית + ליצור אירוע Codenames"},
        {"when": "שבת 11.4", "what": "להפעיל roundup + welcome לראשית"},
        {"when": "שני 13.4 לילה", "what": "לכבות הכל חוץ מספאם (יום השואה)"},
    ]

    return templates.TemplateResponse(request, name="planner.html", context={
        "today_name": today_name,
        "today_str": today_str,
        "now_time": now_time,
        "today_items": today_items,
        "week_plan": week_plan,
        "actions": actions,
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
