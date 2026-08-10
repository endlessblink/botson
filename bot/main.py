"""Bot entry point — setup, handler registration, and lifecycle management."""

import atexit
import logging
import os
import signal
import sys
from logging.handlers import RotatingFileHandler

from telegram.error import Forbidden
from telegram.ext import AIORateLimiter, Application, CommandHandler

PID_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "bot.pid")

from .database.db import Database
from .handlers import welcome, goals, levels, antispam, discussions, events, trivia, trivia_round, emoji_puzzle, topic_tracker, topic_router, polls, calendar_pop, daily_activity_digest, trivia_interest, reactions, dm_menu, tagall, member_activity
from .handlers.calendar import check_and_send_due_messages, cleanup_public_warmup_announcements
from .scheduler.jobs import setup_jobs
from .utils.config import BOT_TOKEN, deep_link, get_emoji_puzzles, get_prompts
from .utils.copy import load_copy

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
    """Handle /start command (DM only).

    `/start menu` (the t.me/<bot>?start=menu deep link) opens the personal menu;
    a bare /start shows the greeting.
    """
    if context.args:
        start_arg = context.args[0]
        if start_arg == "menu":
            await dm_menu.show_menu(update, context)
            return
        if start_arg.startswith("game_"):
            try:
                scheduled_msg_id = int(start_arg.split("_", 1)[1])
            except (IndexError, ValueError):
                scheduled_msg_id = 0
            if scheduled_msg_id > 0:
                await dm_menu.show_game_subscription(update, context, scheduled_msg_id)
                return
    # Pin the persistent menu keyboard so the buttons are visible from the very
    # first interaction — no need to remember /menu.
    await update.message.reply_text(
        load_copy("dm_menu", "greeting"), reply_markup=dm_menu.persistent_kb()
    )


async def help_command(update, context):
    """Handle /help command."""
    text = load_copy("dm_menu", "help_text")
    if update.effective_chat and update.effective_chat.type == "private":
        await update.message.reply_text(text)
        return

    user = update.effective_user
    if user:
        try:
            await context.bot.send_message(chat_id=user.id, text=text)
            return
        except Forbidden:
            pass
        except Exception as e:  # noqa: BLE001
            logger.warning("help: failed to DM user %s: %s", user.id, e)

    url = deep_link("menu")
    notice_key = "help_dm_failed_with_link" if url else "help_dm_failed"
    await update.message.reply_text(load_copy("dm_menu", notice_key, url=url))


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

    # Register the bot's command list so Telegram shows a tappable "Menu"
    # button in DMs (a second discovery path alongside the persistent keyboard).
    try:
        from telegram import (
            BotCommand,
            BotCommandScopeAllChatAdministrators,
            BotCommandScopeAllGroupChats,
        )
        commands = [
            BotCommand("menu", load_copy("dm_menu", "cmd_menu_desc")),
            BotCommand("help", load_copy("dm_menu", "cmd_help_desc")),
        ]
        await app.bot.set_my_commands(commands)
        await app.bot.set_my_commands(
            commands,
            scope=BotCommandScopeAllGroupChats(),
        )
        await app.bot.set_my_commands(
            [
                *commands,
                BotCommand("tagall", load_copy("dm_menu", "cmd_tagall_desc")),
                BotCommand("tagall_test", load_copy("dm_menu", "cmd_tagall_test_desc")),
                BotCommand("activity_report", load_copy("dm_menu", "cmd_activity_report_desc")),
                BotCommand("member_cleanup", load_copy("dm_menu", "cmd_member_cleanup_desc")),
            ],
            scope=BotCommandScopeAllChatAdministrators(),
        )
    except Exception as e:
        logger.warning("set_my_commands failed: %s", e)

    # Seed prompts from YAML
    prompts = get_prompts()
    await db.seed_prompts(prompts)

    # Seed emoji-puzzle pool from YAML
    emoji_puzzles = get_emoji_puzzles()
    await db.seed_emoji_puzzles(emoji_puzzles)

    # Setup reload watcher (checks for data/reload flag file every 5s)
    _setup_reload_watcher(app)

    # Audit every configured topic_id against verified_forum_topics.
    # Non-fatal: startup continues even if unverified IDs are found.
    from .utils.topic_audit import run as run_topic_audit
    try:
        await run_topic_audit(db)
    except Exception as e:
        logger.warning("topic_audit startup run failed: %s", e)

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

    # Personal pre-game reminders — DM signed-up users before kickoff
    from .handlers.dm_menu import send_due_game_reminders
    app.job_queue.run_repeating(send_due_game_reminders, interval=60, first=20, name="game_reminder_checker")

    # Anti-clutter: remove public game sign-up prompts after their short window.
    app.job_queue.run_repeating(cleanup_public_warmup_announcements, interval=60, first=90, name="warmup_cleanup_checker")

    # MTProto reconciliation catches topic changes that happened while the bot
    # was offline. Missing Telegram API credentials are logged by the job setup.
    from .scheduler.topic_sync import register_topic_sync_job
    register_topic_sync_job(app)


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
    system_jobs = {"reload_watcher", "calendar_checker", "game_reminder_checker", "warmup_cleanup_checker"}
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
    emoji_puzzle.register(app)   # Emoji Night reply watcher + reveal job
    polls.register(app)          # Inline button polls with vote tracking
    daily_activity_digest.register(app)  # Daily schedule digest buttons
    calendar_pop.register(app)   # Calendar popup demo (option-3 prototype)
    trivia_interest.register(app)  # Trivia warm-up RSVP interest check
    dm_menu.register(app)          # Private DM menu — sign-up + notification prefs
    tagall.register(app)            # Admin announcement with known-member mentions
    member_activity.register(app)   # Activity measurement and reversible cleanup opt-in
    reactions.register(app)        # Phase B: track reactions on bot's scheduled messages
    topic_tracker.register(app)  # Forum topic auto-detection (group 99)

    # Setup scheduled jobs (uses built-in JobQueue)
    setup_jobs(app)

    logger.info("Bot starting... (polling mode)")
    app.run_polling(
        drop_pending_updates=False,
        allowed_updates=["message", "callback_query", "chat_member", "my_chat_member", "message_reaction"],
    )


if __name__ == "__main__":
    main()
