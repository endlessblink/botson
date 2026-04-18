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
from .handlers import welcome, goals, levels, antispam, discussions, events, trivia, trivia_round, topic_tracker, topic_router, polls, calendar_pop
from .handlers.calendar import check_and_send_due_messages
from .scheduler.jobs import setup_jobs
from .utils.config import BOT_TOKEN, get_emoji_puzzles, get_prompts

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

    # Seed emoji-puzzle pool from YAML
    emoji_puzzles = get_emoji_puzzles()
    await db.seed_emoji_puzzles(emoji_puzzles)

    # Setup reload watcher (checks for data/reload flag file every 5s)
    _setup_reload_watcher(app)

    # Materialize the next 14 days of morning/evening/discussion slots
    # into scheduled_messages so calendar_checker owns every send.
    from .scheduler.materializer import materialize_forward
    try:
        inserted = await materialize_forward(db)
        logger.info("Bootstrap materialize: %d new rows", inserted)
    except Exception as e:
        logger.error("Bootstrap materialize failed: %s", e)

    # Rehydrate poll vote cache from DB so a restart doesn't show empty buttons.
    try:
        await polls.hydrate_from_db(db)
    except Exception as e:
        logger.error("polls hydrate failed: %s", e)

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

    # Clean stale reload flag on startup
    if os.path.exists(reload_flag):
        os.unlink(reload_flag)
        logger.info("Cleaned stale reload flag")

    async def _check_reload(context):
        if os.path.exists(reload_flag):
            os.unlink(reload_flag)
            logger.info("Reload flag detected — reloading schedule...")
            await _reload_config(app)

    # Check reload every 5 seconds
    app.job_queue.run_repeating(_check_reload, interval=5, first=5, name="reload_watcher")

    # Content calendar checker — runs every minute
    app.job_queue.run_repeating(check_and_send_due_messages, interval=60, first=10, name="calendar_checker")


async def _reload_config(app):
    """Atomic reload: re-register cron jobs and re-materialize text-content rows.

    Two things must happen in sync:
      1. Cron jobs (leaderboard/roundup/trivia/event_reminder) re-registered
         from fresh settings.yaml — picks up new days/times.
      2. Future auto-materialized rows in scheduled_messages are purged and
         re-created from fresh settings + pools. User-committed rows are
         untouched.

    If setup_jobs fails, the old cron jobs are restored.
    Never clears the reload_watcher itself.
    """
    jq = app.job_queue
    if not jq:
        logger.error("No JobQueue — cannot reload")
        return

    # Save old jobs (excluding system jobs) so we can restore on failure
    system_jobs = {"reload_watcher", "calendar_checker"}
    old_jobs = [j for j in jq.jobs() if j.name not in system_jobs]

    # Remove only schedule jobs (not the reload_watcher)
    for job in old_jobs:
        job.schedule_removal()
    logger.info("Cleared %d schedule jobs for reload", len(old_jobs))

    try:
        setup_jobs(app)
        # Verify jobs were actually registered
        new_jobs = [j for j in jq.jobs() if j.name not in system_jobs]
        if len(new_jobs) == 0:
            raise RuntimeError("setup_jobs registered 0 jobs")
        logger.info("Config reloaded successfully — %d cron jobs active", len(new_jobs))
    except Exception as e:
        logger.error("Failed to reload config: %s — re-registering from old config", e)
        # Restore: call setup_jobs again (reads same settings, should work)
        try:
            setup_jobs(app)
            logger.info("Restored jobs after failed reload")
        except Exception as e2:
            logger.critical("CRITICAL: Could not restore jobs: %s — bot has no scheduled jobs!", e2)

    # Re-materialize text-content rows from fresh config.
    db = app.bot_data.get("db")
    if db:
        try:
            from .scheduler.materializer import purge_future_auto_rows, materialize_forward
            purged = await purge_future_auto_rows(db)
            inserted = await materialize_forward(db)
            logger.info("Reload materialize: purged %d, inserted %d rows", purged, inserted)
        except Exception as e:
            logger.error("Reload materialize failed: %s", e)


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
    topic_router.register(app)   # Group 5 — observe-only off-topic classifier
    welcome.register(app)    # Default group
    goals.register(app)      # Group 2
    levels.register(app)     # Group 3
    discussions.register(app)
    events.register(app)     # Event management
    trivia.register(app)     # Trivia questions (single-question)
    trivia_round.register(app)   # Trivia round — 5 questions, +20 first-place bonus
    polls.register(app)          # Inline button polls with vote tracking
    calendar_pop.register(app)   # Calendar popup demo (option-3 prototype)
    topic_tracker.register(app)  # Forum topic auto-detection (group 99)

    # Setup scheduled jobs (uses built-in JobQueue)
    setup_jobs(app)

    logger.info("Bot starting... (polling mode)")
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member"],
    )


if __name__ == "__main__":
    main()
