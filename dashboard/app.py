"""Botson Dashboard — FastAPI backend for managing the bot."""

import asyncio
import json
import logging
import os
import secrets
import signal
from pathlib import Path

logger = logging.getLogger(__name__)

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
    topic_id = data.get("topic_id")
    target = data.get("target", "main")  # "main" or "test"

    if not text:
        raise HTTPException(status_code=400, detail="Message text required")

    from telegram import Bot
    bot = Bot(os.getenv("BOT_TOKEN", ""))

    if target == "test":
        group_id = int(os.getenv("TEST_GROUP_ID", "0"))
    else:
        group_id = int(os.getenv("GROUP_ID", "0"))

    if not group_id:
        raise HTTPException(status_code=400, detail=f"No {target} group ID configured")

    kwargs = {"chat_id": group_id, "text": text}
    if topic_id:
        kwargs["message_thread_id"] = int(topic_id)

    try:
        msg = await bot.send_message(**kwargs)
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


# ── Weekly Plan Page ────────────────────────────────────

def _is_feature_enabled_simple(features: dict, key: str) -> bool:
    """Check if a feature is enabled (simple check for template use)."""
    feat = features.get(key, {})
    if isinstance(feat, bool):
        return feat
    if isinstance(feat, dict):
        return feat.get("enabled", False)
    return False


@app.get("/api/weekplan/discussion-sample")
async def get_discussion_sample(request: Request, category: str):
    """Return the first question from a discussion category pool."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    try:
        pool = load_yaml("discussions.yaml") or {}
    except Exception:
        pool = {}

    questions = pool.get(category, [])
    if not questions:
        return {"text": "", "idx": -1}
    return {"text": questions[0], "idx": 0}


@app.post("/api/weekplan/update-prompt")
async def update_weekplan_prompt(request: Request):
    """Update a single prompt text in its YAML pool from the weekplan modal."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    pool = (data.get("pool") or "").strip()
    idx = data.get("idx", -1)
    new_text = (data.get("text") or "").strip()

    if not new_text or not isinstance(idx, int) or idx < 0:
        raise HTTPException(status_code=400, detail="Missing text or invalid index")

    if pool in ("morning", "evening"):
        path = CONFIG_DIR / "prompts.yaml"
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f) or {}
        pool_list = content.get(pool, [])
        if idx >= len(pool_list):
            raise HTTPException(status_code=400, detail="Index out of range")
        pool_list[idx] = new_text
        content[pool] = pool_list
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(content, f, allow_unicode=True, default_flow_style=False)
        return {"status": "ok"}

    if pool.startswith("discussion:"):
        category = pool.split(":", 1)[1]
        path = CONFIG_DIR / "discussions.yaml"
        with open(path, "r", encoding="utf-8") as f:
            content = yaml.safe_load(f) or {}
        cat_list = content.get(category, [])
        if idx >= len(cat_list):
            raise HTTPException(status_code=400, detail="Index out of range")
        cat_list[idx] = new_text
        content[category] = cat_list
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(content, f, allow_unicode=True, default_flow_style=False)
        return {"status": "ok"}

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

    # Track prompt indices for rotating previews across the week
    morning_idx = 0
    evening_idx = 0
    discussion_idx = 0

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

    week_days = []
    for i in range(7):
        day_date = sunday + timedelta(days=i)
        activities = []

        # Check each schedule item
        if i in schedule.get("morning_prompt", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "morning_prompt")
            preview = ""
            full_text = ""
            used_idx = -1
            if morning_queue and morning_idx < len(morning_queue):
                full_text = morning_queue[morning_idx]
                preview = _truncate(full_text)
                used_idx = morning_idx
                morning_idx += 1
            goals_topic = settings.get("topics", {}).get("goals")
            activities.append({
                "time": schedule["morning_prompt"].get("time", "09:00"),
                "type": "morning", "label": "בוקר",
                "desc": preview or "הודעת בוקר — יום יום",
                "full_text": full_text,
                "pool": "morning",
                "pool_idx": used_idx,
                "topic_id": goals_topic if goals_topic else "",
                "channel": "", "enabled": enabled
            })

        if i in schedule.get("discussion_prompt", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "discussions")
            times = schedule["discussion_prompt"].get("times", ["18:00"])
            for t in times:
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
                    "channel": channel_hint, "enabled": enabled
                })

        if i in schedule.get("evening_prompt", {}).get("days", []):
            enabled = _is_feature_enabled_simple(features, "evening_prompt")
            preview = ""
            full_text = ""
            used_idx = -1
            if evening_queue and evening_idx < len(evening_queue):
                full_text = evening_queue[evening_idx]
                preview = _truncate(full_text)
                used_idx = evening_idx
                evening_idx += 1
            goals_topic = settings.get("topics", {}).get("goals")
            activities.append({
                "time": schedule["evening_prompt"].get("time", "21:00"),
                "type": "evening", "label": "ערב",
                "desc": preview or "הודעת ערב — יום יום",
                "full_text": full_text,
                "pool": "evening",
                "pool_idx": used_idx,
                "topic_id": goals_topic if goals_topic else "",
                "channel": "", "enabled": enabled
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

    # Add calendar events to the right days
    for evt in calendar_events:
        try:
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

@app.get("/review", response_class=HTMLResponse)
async def review_page(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(url="/login", status_code=303)

    # Pending messages loaded from a simple list
    # Claude updates this when drafting messages for approval
    pending = [
        {
            "title": "📝 פוסט קהילתי — למה בוטסון קיים",
            "channel": "כללי (General)",
            "when": "",
            "preview": "להיות אל-הורי לבד זה לא הרבה. אבל להכיר עוד אנשים אל-הוריים — זה מרחיב את המרחב שבו אפשר להרגיש חופשי עם זה.\n\nעם חברים שהם הורים, יש פער — בזמינות, בנושאים, בפרספקטיבה. אז הרצון להכיר אנשים שחולקים את הבחירה הזו הגיוני.\n\nמה שלא עבד לי לפני: קבוצות פייסבוק — שיחות נשארות על פני השטח, אף פעם לא ממש יודע עם מי מדברים. לארגן מפגשים — מתיש, הכל נופל על אחד, ולהיות מופנם זה לא עוזר. קבוצות וואטסאפ אחרות — סגנון הניהול שם הוריד אותי מהקיר.\n\nהקבוצה הזו היא ניסיון לקחת את הטוב מכל אלה. להכיר אנשים דרך תחומי עניין משותפים, לא רק דרך תווית. ליצור מקום שממנו אפשר למצוא חיבורים אמיתיים — ואולי גם מפגשים שצומחים מהיכרות שכבר קיימת.\n\nזה יותר מזכיר צ'אטים של שנות ה-90 מאשר רשתות חברתיות של היום.\n\nאם אתם מכירים אנשים אל-הוריים שהמקום הזה יכול להתאים להם — שלחו אותם. 🙂",
            "note": "פוסט אישי לקבוצה — לעיון לפני פרסום",
        },
    ]

    return templates.TemplateResponse(request, name="review.html", context={
        "pending": pending,
    })


# ── Content Calendar API ─────────────────────────────────

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
            },
        }
        events.append(event)

    return events


@app.post("/api/calendar")
async def create_calendar_item(request: Request, db: Database = Depends(get_db)):
    """Create a new scheduled message."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
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
    )
    return {"status": "ok", "id": msg_id}


@app.put("/api/calendar/{msg_id}")
async def update_calendar_item(msg_id: int, request: Request, db: Database = Depends(get_db)):
    """Update a scheduled message."""
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401)

    data = await request.json()
    allowed = {"text", "channel_topic_id", "target_group", "scheduled_date", "scheduled_time",
               "recurrence", "recurrence_days", "status", "auto_pin", "message_type"}
    fields = {k: v for k, v in data.items() if k in allowed}
    if "recurrence_days" in fields and isinstance(fields["recurrence_days"], list):
        fields["recurrence_days"] = json.dumps(fields["recurrence_days"])

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

    kwargs = {"chat_id": group_id, "text": msg["text"]}
    if msg.get("channel_topic_id"):
        kwargs["message_thread_id"] = msg["channel_topic_id"]

    try:
        sent = await bot.send_message(**kwargs)
        if target != "test":
            await db.mark_message_sent(msg_id, sent.message_id)
        return {"status": "ok", "message_id": sent.message_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

    return templates.TemplateResponse(request, name="planner.html", context={
        "now_date": now.strftime("%Y-%m-%d"),
        "forum_topics": forum_topics,
        "topic_names": topic_names,
        "drafts": drafts,
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
