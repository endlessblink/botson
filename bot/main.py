"""Bot entry point — setup, handler registration, and lifecycle management."""

import logging
import sys

from telegram.ext import Application, CommandHandler

from .database.db import Database
from .handlers import welcome, goals, levels, antispam, discussions, events, trivia
from .scheduler.jobs import setup_jobs
from .utils.config import BOT_TOKEN, get_prompts

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
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
    logger.info("Bot initialized successfully")


async def post_shutdown(app: Application):
    """Cleanup on shutdown."""
    db: Database = app.bot_data.get("db")
    if db:
        await db.close()
    logger.info("Bot shut down cleanly")


def main():
    """Main entry point."""
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN not set!")
        sys.exit(1)

    # Build application
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).post_shutdown(post_shutdown).build()

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

    # Setup scheduled jobs (uses built-in JobQueue)
    setup_jobs(app)

    logger.info("Bot starting... (polling mode)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
