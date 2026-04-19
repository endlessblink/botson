"""Emoji Night runtime: schedule sessions, judge replies, and reveal unsolved rounds."""

import json
import logging
import random
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GROUP_ID, TEST_GROUP_ID, get_settings, is_feature_enabled
from ..utils.helpers import get_display_name, is_bot_user
from ..utils.levels import check_level_up
from ..utils.scoring import get_points

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _emoji_settings() -> tuple[dict, dict]:
    settings = get_settings()
    feature = settings.get("features", {}).get("emoji_puzzle", {}) or {}
    schedule = settings.get("schedule", {}).get("emoji_puzzle", {}) or {}
    return feature, schedule


def get_enabled_emoji_targets(settings: dict | None = None) -> list[tuple[int, int | None]]:
    """Resolve enabled feature groups to concrete chat/thread targets."""
    settings = settings or get_settings()
    feature = settings.get("features", {}).get("emoji_puzzle", {}) or {}
    if not isinstance(feature, dict) or not feature.get("enabled"):
        return []

    groups = feature.get("groups", []) or []
    topics = settings.get("topics", {}) or {}
    discussion_topics = topics.get("discussions", {}) or {}
    movies_topic = discussion_topics.get("movies")
    targets: list[tuple[int, int | None]] = []

    for group in groups:
        if group == "main" and GROUP_ID:
            targets.append((GROUP_ID, movies_topic))
        elif group == "test" and TEST_GROUP_ID:
            targets.append((TEST_GROUP_ID, None))
    return targets


def resolve_emoji_target(target: str, settings: dict | None = None) -> tuple[int | None, int | None]:
    """Resolve one dashboard target name to chat/thread ids."""
    settings = settings or get_settings()
    topics = settings.get("topics", {}) or {}
    discussion_topics = topics.get("discussions", {}) or {}
    movies_topic = discussion_topics.get("movies")
    if target == "main":
        return GROUP_ID or None, movies_topic
    if target == "test":
        return TEST_GROUP_ID or None, None
    return None, None


def _format_intro_text(puzzle_count: int, award_points: int) -> str:
    return (
        "🎬 Emoji Night מתחיל!\n\n"
        f"מחכות לכם {puzzle_count} חידות אימוג'י על סרטים וסדרות.\n"
        f"הראשונ/ה שפותר/ת כל חידה זוכה ב-{award_points} נקודות.\n\n"
        "עונים ב-reply להודעת החידה. עברית או אנגלית מתקבלות, ויש 24 שעות עד חשיפה."
    )


def _format_puzzle_text(puzzle: dict, index: int, total: int) -> str:
    return (
        f"🎬 חידת אימוג'י {index}/{total}\n\n"
        f"{puzzle['emoji_prompt']}\n\n"
        "איזה סרט או סדרה זה?\n"
        "השיבו ב-reply להודעה הזו."
    )


def _format_wrap_text(leaderboard: list[dict], total: int) -> str:
    if not leaderboard:
        return (
            "🎬 Emoji Night הסתיים!\n\n"
            f"{total} חידות יצאו, אבל אף אחת עוד לא נפתרה. אולי בפעם הבאה."
        )

    lines = ["🎬 Emoji Night הסתיים!", "", "טבלת הזוכים:"]
    medals = ["🥇", "🥈", "🥉"]
    for idx, row in enumerate(leaderboard, start=1):
        badge = medals[idx - 1] if idx <= len(medals) else f"{idx}."
        wins = int(row.get("wins", 0))
        lines.append(f"{badge} {row.get('display_name', 'חבר/ה')} — {wins} זכיות")
    return "\n".join(lines)


def _payload(message_type: str, **kwargs) -> str:
    data = {"kind": message_type, **kwargs}
    return json.dumps(data, ensure_ascii=False)


def _parse_payload(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _ceil_to_minute(value: datetime) -> tuple[str, str]:
    if value.second or value.microsecond:
        value = value.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return value.strftime("%Y-%m-%d"), value.strftime("%H:%M")


def _difficulty_targets(puzzle_count: int) -> dict[int, int]:
    if puzzle_count >= 7:
        return {1: 3, 2: 3, 3: 1}
    if puzzle_count <= 3:
        return {1: 1, 2: 1, 3: 1}
    return {1: 2, 2: 2, 3: 1}


def _normalize_compact(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower().strip()
    return "".join(ch for ch in text if ch.isalnum())


def _normalized_variants(text: str) -> set[str]:
    compact = _normalize_compact(text)
    if not compact:
        return set()
    variants = {compact}
    if compact.startswith("ה") and len(compact) > 1:
        variants.add(compact[1:])
    return variants


def _accepted_answers(round_row: dict) -> set[str]:
    accepted = set()
    for raw in [round_row.get("answer_he"), round_row.get("answer_en")]:
        if isinstance(raw, str):
            accepted.update(_normalized_variants(raw))
    aliases = round_row.get("aliases")
    try:
        alias_items = json.loads(aliases or "[]")
    except (TypeError, ValueError):
        alias_items = []
    for alias in alias_items:
        accepted.update(_normalized_variants(str(alias)))
    return accepted


async def _pick_session_puzzles(db: Database, puzzle_count: int) -> list[dict]:
    pool = await db.list_emoji_puzzles(enabled_only=True)
    if not pool:
        return []

    picked: list[dict] = []
    picked_ids: set[int] = set()
    targets = _difficulty_targets(puzzle_count)

    for difficulty, amount in targets.items():
        candidates = [p for p in pool if int(p.get("difficulty", 2)) == difficulty and p["id"] not in picked_ids]
        random.shuffle(candidates)
        for puzzle in candidates[:amount]:
            picked.append(puzzle)
            picked_ids.add(puzzle["id"])

    if len(picked) < puzzle_count:
        remainder = [p for p in pool if p["id"] not in picked_ids]
        random.shuffle(remainder)
        picked.extend(remainder[: puzzle_count - len(picked)])

    return picked[:puzzle_count]


async def start_emoji_night(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    thread_id: int | None,
    force: bool = False,
) -> int | None:
    """Create one Emoji Night session and schedule intro/puzzles/wrap rows."""
    if not force and not is_feature_enabled("emoji_puzzle", chat_id):
        logger.info("emoji_puzzle: feature disabled for chat %s", chat_id)
        return None

    db: Database = context.bot_data["db"]
    active = await db.get_active_session(chat_id, thread_id)
    if active:
        logger.info("emoji_puzzle: session already active in chat %s thread %s", chat_id, thread_id)
        return None

    _, schedule = _emoji_settings()
    puzzle_count = int(schedule.get("puzzle_count", 5) or 5)
    interval_minutes = int(schedule.get("interval_minutes", 6) or 6)
    intro_offset_seconds = int(schedule.get("intro_offset_seconds", 60) or 60)
    wrap_offset_seconds = int(schedule.get("wrap_offset_seconds", 420) or 420)
    award_points = get_points("emoji_puzzle_winner")

    puzzles = await _pick_session_puzzles(db, puzzle_count)
    if len(puzzles) < puzzle_count:
        logger.warning("emoji_puzzle: not enough enabled puzzles (%d/%d)", len(puzzles), puzzle_count)
        return None

    session_id = await db.create_emoji_session(chat_id, thread_id, puzzle_count)
    now = datetime.now(_IL_TZ)
    intro_at = now + timedelta(seconds=intro_offset_seconds)
    target_group = "test" if TEST_GROUP_ID and chat_id == TEST_GROUP_ID else "main"

    intro_date, intro_time = _ceil_to_minute(intro_at)
    await db.create_scheduled_message(
        text=_format_intro_text(puzzle_count, award_points),
        message_type="emoji_puzzle_intro",
        channel_topic_id=thread_id,
        target_group=target_group,
        scheduled_date=intro_date,
        scheduled_time=intro_time,
        created_by="emoji_puzzle",
        poll_options=_payload("emoji_puzzle_intro", session_id=session_id),
    )

    for idx, puzzle in enumerate(puzzles, start=1):
        send_at = intro_at + timedelta(minutes=1 + ((idx - 1) * interval_minutes))
        send_date, send_time = _ceil_to_minute(send_at)
        await db.create_scheduled_message(
            text=_format_puzzle_text(puzzle, idx, puzzle_count),
            message_type="emoji_puzzle_round",
            channel_topic_id=thread_id,
            target_group=target_group,
            scheduled_date=send_date,
            scheduled_time=send_time,
            created_by="emoji_puzzle",
            poll_options=_payload(
                "emoji_puzzle_round",
                session_id=session_id,
                puzzle_id=puzzle["id"],
                award_points=award_points,
                round_index=idx,
                total=puzzle_count,
            ),
        )

    wrap_at = intro_at + timedelta(minutes=1 + ((puzzle_count - 1) * interval_minutes), seconds=wrap_offset_seconds)
    wrap_date, wrap_time = _ceil_to_minute(wrap_at)
    await db.create_scheduled_message(
        text="Emoji Night wrap-up",
        message_type="emoji_puzzle_wrap",
        channel_topic_id=thread_id,
        target_group=target_group,
        scheduled_date=wrap_date,
        scheduled_time=wrap_time,
        created_by="emoji_puzzle",
        poll_options=_payload("emoji_puzzle_wrap", session_id=session_id, total=puzzle_count),
    )

    logger.info("emoji_puzzle: scheduled session %s with %d puzzles", session_id, puzzle_count)
    return session_id


async def send_scheduled_emoji_night(context: ContextTypes.DEFAULT_TYPE):
    """Cron job: start one Emoji Night session for each enabled target group."""
    settings = get_settings()
    for chat_id, thread_id in get_enabled_emoji_targets(settings):
        try:
            await start_emoji_night(context, chat_id, thread_id)
        except Exception as e:
            logger.warning("emoji_puzzle: scheduled start failed for chat %s: %s", chat_id, e)


async def send_scheduled_emoji_message(bot, db: Database, msg: dict):
    """Handle custom scheduled-message types for Emoji Night."""
    payload = _parse_payload(msg.get("poll_options"))
    message_type = msg.get("message_type")
    kwargs = {
        "chat_id": msg["_resolved_chat_id"],
        "text": msg["text"],
    }
    if msg.get("channel_topic_id") is not None:
        kwargs["message_thread_id"] = msg.get("channel_topic_id")

    if message_type == "emoji_puzzle_wrap":
        session_id = int(payload.get("session_id", 0) or 0)
        total = int(payload.get("total", 0) or 0)
        leaderboard = await db.get_session_leaderboard(session_id)
        kwargs["text"] = _format_wrap_text(leaderboard, total)
        sent = await bot.send_message(**kwargs)
        await db.complete_emoji_session(session_id, leaderboard)
        return sent

    sent = await bot.send_message(**kwargs)

    if message_type == "emoji_puzzle_round":
        await db.start_emoji_round(
            int(payload.get("session_id", 0) or 0),
            int(payload.get("puzzle_id", 0) or 0),
            sent.chat_id,
            sent.message_id,
            msg.get("channel_topic_id"),
            int(payload.get("award_points", 0) or 0),
        )

    return sent


async def handle_emoji_puzzle_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Judge reply guesses against active emoji rounds."""
    if not update.message or not update.effective_user or not update.effective_chat:
        return
    if is_bot_user(update.effective_user):
        return
    if not is_feature_enabled("emoji_puzzle", update.effective_chat.id):
        return

    reply_to = update.message.reply_to_message
    if not reply_to:
        return

    db: Database = context.bot_data["db"]
    round_row = await db.get_active_emoji_round_for_message(update.effective_chat.id, reply_to.message_id)
    if not round_row:
        return

    guess_variants = _normalized_variants(update.message.text or "")
    if not guess_variants:
        return
    if not (guess_variants & _accepted_answers(round_row)):
        return

    user = update.effective_user
    await db.upsert_member(user.id, user.username, get_display_name(user))

    if await db.user_has_emoji_win_since(user.id, 7):
        logger.info("emoji_puzzle: suppressing repeat winner user=%s round=%s", user.id, round_row["id"])
        return

    won = await db.mark_emoji_round_solved(round_row["id"], user.id, update.message.message_id)
    if not won:
        return

    award_points = int(round_row.get("award_points", 0) or get_points("emoji_puzzle_winner"))
    old_points = await db.add_points(user.id, award_points)
    await db.log_activity(
        "emoji_puzzle",
        f"+{award_points} נקודות ל-{get_display_name(user)} (Emoji Night)",
        user.id,
    )

    try:
        await update.message.reply_text(f"🎉 {get_display_name(user)} פתר/ה! +{award_points} נקודות")
    except Exception as e:
        logger.warning("emoji_puzzle: failed to send win reply: %s", e)

    new_level = check_level_up(old_points, old_points + award_points)
    if new_level:
        mention = f"[{get_display_name(user)}](tg://user?id={user.id})"
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"🎉 מזל טוב {mention}! עלה/תה לרמה {new_level['level']} — {new_level['emoji']} {new_level['tag']}!",
                parse_mode="Markdown",
                message_thread_id=getattr(update.message, "message_thread_id", None),
            )
        except Exception:
            pass


async def reveal_unsolved_rounds_job(context: ContextTypes.DEFAULT_TYPE):
    """Hourly reveal for emoji rounds left unsolved for 24h."""
    db: Database = context.bot_data["db"]
    rounds = await db.get_emoji_rounds_to_reveal(24)
    for round_row in rounds:
        answer = round_row.get("answer_he") or round_row.get("answer_en") or "לא ידוע"
        kwargs = {
            "chat_id": round_row["chat_id"],
            "text": f"🕒 זמן לחשוף: {round_row.get('emoji_prompt', '')}\nהתשובה היא: {answer}",
            "reply_to_message_id": round_row["message_id"],
        }
        if round_row.get("message_thread_id") is not None:
            kwargs["message_thread_id"] = round_row.get("message_thread_id")
        try:
            await context.bot.send_message(**kwargs)
            await db.mark_emoji_round_revealed(round_row["id"])
        except Exception as e:
            logger.warning("emoji_puzzle: failed to reveal round %s: %s", round_row["id"], e)


def register(app: Application):
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_emoji_puzzle_reply),
        group=4,
    )
