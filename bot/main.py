"""Bot entry point — setup, handler registration, and lifecycle management."""

import atexit
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

from telegram.ext import AIORateLimiter, Application, CommandHandler

PID_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bot.pid")

from .database.db import Database
from .handlers import welcome, goals, levels, antispam, discussions, events, trivia, topic_tracker
from .scheduler.jobs import setup_jobs
from .utils.config import BOT_TOKEN, get_prompts

# Configure logging — file + stdout
LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bot.log")

_log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
_formatter = logging.Formatter(_log_format)

# Root logger
_root = logging.getLogger()
_root.setLevel(logging.INFO)

# Console handler
_console = logging.StreamHandler()
_console.setFormatter(_formatter)
_root.addHandler(_console)

# File handler with rotation (10MB, keep 3 backups)
_file = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=3, encoding="utf-8")
_file.setFormatter(_formatter)
_root.addHandler(_file)

logger = logging.getLogger(__name__)


async def start_command(update, context):
    """Handle /start command (DM only)."""
    await update.message.reply_text(
        "שלום! אני Botson, הבוט של אלהוריים וזה 🌟\n"
        "הוסיפו אותי לקבוצה כדי שאוכל לעזור.\n"
        "שלחו /help לרשימת פקודות."
    )


async def help_command(update, context):
    """Handle /help command."""
    text = (
        "📋 פקודות זמינות:\n\n"
        "/level — הצג את הרמה שלך\n"
        "/leaderboard — טופ 10 רמות\n"
        "/streak — הצג את הרצף שלך בהישגים\n"
        "\n"
        "🔧 פקודות מנהלים:\n"
        "/stats — סטטיסטיקות קבוצה\n"
        "/whitelist <pattern> — הוסף תבנית לרשימה לבנה\n"
        "/resetlevels — אפס רמות לעונה חדשה"
    )
    await update.message.reply_text(text)


async def stats_command(update, context):
    """Handle /stats command (admin only)."""
    from .utils.helpers import is_admin

    if not update.effective_user or not is_admin(update.effective_user.id):
        await update.message.reply_text("רק מנהלים יכולים לצפות בסטטיסטיקות")
        return

    db: Database = context.bot_data["db"]
    leaders = await db.get_leaderboard(3)
    streaks = await db.get_top_streaks(3)

    text = "📊 סטטיסטיקות:\n\n"
    if leaders:
        text += "⭐ טופ כוכבים:\n"
        for m in leaders:
            text += f"  {m['display_name']}: {m['karma_points']}\n"
    if streaks:
        text += "\n🔥 טופ רצפים:\n"
        for s in streaks:
            text += f"  {s['display_name']}: {s['current_streak']} ימים\n"

    await update.message.reply_text(text)


async def post_init(app: Application):
    """Run after bot initialization — setup DB and seed data."""
    db = Database()
    await db.init()
    app.bot_data["db"] = db

    # Seed prompts from YAML
    prompts = get_prompts()
    await db.seed_prompts(prompts)

    # Setup reload watcher (checks for data/reload flag file every 5s)
    _setup_reload_watcher(app)

    logger.info("Bot initialized successfully")


async def post_shutdown(app: Application):
    """Cleanup on shutdown."""
    db: Database = app.bot_data.get("db")
    if db:
        await db.close()
    logger.info("Bot shut down cleanly")


def _setup_reload_watcher(app):
    """Watch for a reload flag file and reload schedule when found."""
    reload_flag = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "reload")

    async def _check_reload(context):
        if os.path.exists(reload_flag):
            os.unlink(reload_flag)
            logger.info("Reload flag detected — reloading schedule...")
            await _reload_config(app)

    # Check every 5 seconds
    app.job_queue.run_repeating(_check_reload, interval=5, first=5, name="reload_watcher")


async def _reload_config(app):
    """Reload schedule config and re-register jobs."""
    try:
        # Cancel all existing scheduled jobs
        if app.job_queue:
            jobs = app.job_queue.jobs()
            for job in jobs:
                job.schedule_removal()
            logger.info("Cleared %d existing jobs", len(jobs))

        # Re-register from fresh config
        setup_jobs(app)
        logger.info("Config reloaded successfully")
    except Exception as e:
        logger.error("Failed to reload config: %s", e)


def _acquire_pid_lock():
    """Ensure only one bot instance runs. Exit if another is already running."""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = f.read().strip()
        if old_pid:
            try:
                os.kill(int(old_pid), 0)  # check if process alive
                logger.error("Bot already running (PID %s). Exiting.", old_pid)
                sys.exit(1)
            except (ProcessLookupError, ValueError):
                pass  # stale PID file
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(lambda: os.unlink(PID_FILE) if os.path.exists(PID_FILE) else None)
    logger.info("PID lock acquired: %d", os.getpid())

    # Write version info (git commit hash + timestamp)
    import subprocess, datetime
    version_file = os.path.join(os.path.dirname(PID_FILE), "bot.version")
    try:
        git_hash = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.dirname(__file__)),
            stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        git_hash = "unknown"
    with open(version_file, "w") as f:
        f.write(f"{git_hash}\n{datetime.datetime.now().isoformat()}\n")
    logger.info("Bot version: %s", git_hash)


def main():
    """Main entry point."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        sys.exit(1)

    _acquire_pid_lock()

    # Build application
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter(max_retries=3))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Register core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("stats", stats_command))

    # Register feature handlers
    antispam.register(app)   # Group 0 — highest priority
    welcome.register(app)    # Default group
    goals.register(app)      # Group 2
    levels.register(app)     # Group 3
    discussions.register(app)
    events.register(app)     # Event management
    trivia.register(app)     # Trivia questions
    topic_tracker.register(app)  # Forum topic auto-detection (group 99)

    # Setup scheduled jobs (uses built-in JobQueue)
    setup_jobs(app)

    logger.info("Bot starting... (polling mode)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
