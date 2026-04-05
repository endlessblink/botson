"""Botson Dashboard — FastAPI backend for managing the bot."""

import asyncio
import json
import os
import secrets
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

    return {"status": "ok"}


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


# ── Activity Log ─────────────────────────────────────────

@app.get("/activity", response_class=HTMLResponse)
async def activity_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    log = await db.get_activity_log(200)
    settings = get_settings()
    return templates.TemplateResponse(request, name="activity.html", context={"log": log, "settings": settings})


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


# ── Members API ──────────────────────────────────────────

@app.get("/members", response_class=HTMLResponse)
async def members_page(request: Request, db: Database = Depends(get_db)):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    async with db._db.execute(
        "SELECT * FROM members ORDER BY joined_at DESC"
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
