# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Emoji Night runtime: schedule sessions, judge replies, and reveal unsolved rounds."""

import asyncio
from difflib import SequenceMatcher
import json
import logging
import random
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.config import GROUP_ID, TEST_GROUP_ID, get_settings, is_auto_blocked_on, is_feature_enabled
from ..utils.copy import load_copy
from ..utils.emoji_puzzle_generation import generate_emoji_puzzles
from ..utils.game_categories import canonical_emoji_media_type
from ..utils.helpers import get_display_name, is_bot_user
from ..utils.levels import check_level_up
from ..utils.scoring import get_points
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")
_POINTS_BY_RANK = [5]
_session_tasks: dict[int, asyncio.Task] = {}


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
    thread_id = (settings.get("schedule", {}).get("emoji_puzzle", {}) or {}).get("topic_id")
    thread_id = int(thread_id) if thread_id else None
    targets: list[tuple[int, int | None]] = []

    for group in groups:
        if group == "main" and GROUP_ID:
            targets.append((GROUP_ID, thread_id))
        elif group == "test" and TEST_GROUP_ID:
            targets.append((TEST_GROUP_ID, None))
    return targets


def resolve_emoji_target(target: str, settings: dict | None = None) -> tuple[int | None, int | None]:
    """Resolve one dashboard target name to chat/thread ids."""
    settings = settings or get_settings()
    thread_id = (settings.get("schedule", {}).get("emoji_puzzle", {}) or {}).get("topic_id")
    thread_id = int(thread_id) if thread_id else None
    if target == "main":
        return GROUP_ID or None, thread_id
    if target == "test":
        return TEST_GROUP_ID or None, None
    return None, None


def _format_intro_text(puzzle_count: int, theme_label: str | None = None) -> str:
    theme = str(theme_label or "").strip()
    theme_line = f" בנושא {theme}" if theme else ""
    return (
        "🧩 Emoji Night מתחיל!\n\n"
        f"מחכות לכם {puzzle_count} חידות אימוג'י{theme_line}.\n"
        "הראשונ/ה שעונ/ה נכון על כל חידה מקבל/ת 5 נקודות מיד.\n\n"
        "עונים ב-reply להודעת החידה. אפשר לענות גם על חידות קודמות עד סוף המשחק, ובסוף נחשוף את מה שלא נפתר."
    )


def _format_puzzle_text(puzzle: dict, index: int, total: int) -> str:
    media = canonical_emoji_media_type(puzzle.get("media_type"))
    # Fall back to the "general" variant when the round uses a media_type
    # for which no specific copy is registered (e.g., a new category added
    # to puzzles.html without a matching copy.emoji_puzzle.* key).
    badge_general = load_copy("emoji_puzzle", "badge_general")
    question_general = load_copy("emoji_puzzle", "question_general")
    badge = load_copy("emoji_puzzle", f"badge_{media}", default=badge_general)
    question = load_copy("emoji_puzzle", f"question_{media}", default=question_general)
    header = load_copy("emoji_puzzle", "header", index=index, total=total)
    reply_hint = load_copy("emoji_puzzle", "reply_hint")
    return (
        f"{badge} {header}\n\n"
        f"{puzzle['emoji_prompt']}\n\n"
        f"{question}\n"
        f"{reply_hint}"
    )


def _format_wrap_text(leaderboard: list[dict], total: int, unsolved_rounds: list[dict] | None = None) -> str:
    unsolved_rounds = unsolved_rounds or []
    if not leaderboard:
        lines = [
            "🧩 Emoji Night הסתיים!\n\n"
            f"{total} חידות יצאו, אבל אף אחת עוד לא נפתרה. אולי בפעם הבאה."
        ]
    else:
        lines = ["🧩 Emoji Night הסתיים!", "", "טבלת הזוכים:"]
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(leaderboard, start=1):
            badge = medals[idx - 1] if idx <= len(medals) else f"{idx}."
            pts = int(row.get("total_points", 0))
            correct = int(row.get("correct_answers", 0))
            lines.append(f"{badge} {row.get('display_name', 'חבר/ה')} — {pts} נק׳ · {correct} תשובות נכונות")

    if unsolved_rounds:
        lines.extend(["", "תשובות שלא נפתרו:"])
        for round_row in unsolved_rounds:
            answer = round_row.get("answer_he") or round_row.get("answer_en") or "לא ידוע"
            lines.append(f"{round_row.get('emoji_prompt', '')}  →  {answer}")
    return "\n".join(lines)


def _format_wrap_countdown(minutes: int) -> str:
    return load_copy("emoji_puzzle", "wrap_countdown", minutes=minutes)


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


def _place_badge(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉", 4: "🏅"}.get(rank, "✅")


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


def _normalize_token(token: str) -> str:
    token = unicodedata.normalize("NFC", token or "").lower().strip()
    token = "".join(ch for ch in token if ch.isalnum())
    if len(token) > 3 and token[:1] in {"ה", "ו", "ל", "ב", "כ", "ש", "מ"}:
        token = token[1:]
    return token


def _meaningful_tokens(text: str) -> set[str]:
    stopwords = {"את", "the", "a", "an", "of", "and", "to"}
    tokens = {_normalize_token(part) for part in (text or "").split()}
    return {token for token in tokens if token and token not in stopwords}


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


def _accepted_raw_answers(round_row: dict) -> list[str]:
    raws: list[str] = []
    for raw in [round_row.get("answer_he"), round_row.get("answer_en")]:
        if isinstance(raw, str) and raw.strip():
            raws.append(raw.strip())
    aliases = round_row.get("aliases")
    try:
        alias_items = json.loads(aliases or "[]")
    except (TypeError, ValueError):
        alias_items = []
    for alias in alias_items:
        text = str(alias).strip()
        if text:
            raws.append(text)
    return raws


def _guess_matches(round_row: dict, guess_text: str) -> bool:
    guess_variants = _normalized_variants(guess_text)
    if not guess_variants:
        return False

    accepted = _accepted_answers(round_row)
    if guess_variants & accepted:
        return True

    guess_tokens = _meaningful_tokens(guess_text)
    guess_compact = _normalize_compact(guess_text)
    if not guess_tokens or len(guess_compact) < 4:
        return False

    for raw in _accepted_raw_answers(round_row):
        raw_tokens = _meaningful_tokens(raw)
        raw_compact = _normalize_compact(raw)
        if not raw_tokens or len(raw_compact) < 4:
            continue
        if not (guess_tokens & raw_tokens):
            continue
        if SequenceMatcher(None, guess_compact, raw_compact).ratio() >= 0.67:
            return True
    return False


def _get_answer_actor(update: Update):
    msg = update.message
    if not msg:
        return None
    if msg.sender_chat and (not update.effective_user or is_bot_user(update.effective_user)):
        return {
            "id": int(msg.sender_chat.id),
            "username": None,
            "display_name": msg.sender_chat.title or "אנונימי/ת",
            "is_anonymous": True,
        }
    if update.effective_user and not is_bot_user(update.effective_user):
        return {
            "id": int(update.effective_user.id),
            "username": update.effective_user.username,
            "display_name": get_display_name(update.effective_user),
            "is_anonymous": False,
        }
    return None


async def _pick_session_puzzles(
    db: Database, puzzle_count: int, media_types: list[str] | None = None,
) -> list[dict]:
    pool = await _ensure_fresh_emoji_pool(db, puzzle_count, media_types=media_types)
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


def _allowed_emoji_media_types(media_types: list[str] | None) -> set[str]:
    allowed: set[str] = set()
    # Canonicalize both sides of the filter so legacy scheduled rows with
    # poll_options.media_types=["music"] still match a normalized pool
    # where rows now carry media_type="song". Without this, post-normalize
    # the older scheduled rows would silently produce an empty pool and
    # skip the round (BUG-1 audit, 2026-05-17).
    for media in media_types or []:
        canonical = canonical_emoji_media_type(media)
        if canonical:
            allowed.add(canonical)
    return allowed


async def _fresh_emoji_pool(
    db: Database, media_types: list[str] | None = None,
) -> tuple[list[dict], list[dict], set[str]]:
    pool = await db.list_emoji_puzzles(enabled_only=True)
    allowed = _allowed_emoji_media_types(media_types)
    if allowed:
        pool = [
            p for p in pool
            if canonical_emoji_media_type(p.get("media_type")) in allowed
        ]

    try:
        recent_ids = await db.get_recent_emoji_puzzle_ids(days=30)
    except Exception as e:
        logger.warning("emoji_puzzle: recent-ids lookup failed: %s", e)
        recent_ids = set()
    fresh_pool = [p for p in pool if int(p["id"]) not in recent_ids]
    return pool, fresh_pool, allowed


async def _ensure_fresh_emoji_pool(
    db: Database, puzzle_count: int, media_types: list[str] | None = None,
) -> list[dict]:
    pool, fresh_pool, allowed = await _fresh_emoji_pool(db, media_types)
    if len(fresh_pool) < puzzle_count:
        deficit = puzzle_count - len(fresh_pool)
        refill_media = sorted(allowed)[0] if allowed else "general"
        logger.warning(
            "emoji_puzzle: only %d fresh puzzles after 30-day exclusion (need %d) — auto-refilling %d %s puzzles",
            len(fresh_pool), puzzle_count, deficit, refill_media,
        )
        await generate_emoji_puzzles(db, media_type=refill_media, count=deficit)
        _, fresh_pool, _ = await _fresh_emoji_pool(db, media_types)
    if len(fresh_pool) < puzzle_count:
        logger.error(
            "emoji_puzzle: auto-refill left only %d fresh puzzles (need %d); no recent replay will be sent",
            len(fresh_pool), puzzle_count,
        )
        return []
    return fresh_pool


async def emoji_skip_reason(
    db: Database, chat_id: int, thread_id: int | None,
    *, media_types: list[str] | None = None,
) -> str | None:
    """Pre-flight: auto-refill the matching pool and report only hard blockers."""
    if await db.get_active_session(chat_id, thread_id):
        return "active session in this thread"
    _, schedule = _emoji_settings()
    puzzle_count = int(schedule.get("puzzle_count", 5) or 5)
    fresh_pool = await _ensure_fresh_emoji_pool(db, puzzle_count, media_types=media_types)
    if len(fresh_pool) < puzzle_count:
        return (
            f"auto-refill could not create enough fresh puzzles ({len(fresh_pool)}/{puzzle_count}) for "
            f"media_types={media_types or 'any'} — "
            "recent replays are blocked"
        )
    return None


async def start_emoji_night(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    thread_id: int | None,
    force: bool = False,
    media_types: list[str] | None = None,
    theme_label: str | None = None,
    return_launch_info: bool = False,
) -> int | dict | None:
    """Create one Emoji Night session and launch its timed send flow."""
    if not force and not is_feature_enabled("emoji_puzzle", chat_id):
        logger.info("emoji_puzzle: feature disabled for chat %s", chat_id)
        return None

    db: Database = context.bot_data["db"]
    bot = getattr(context, "bot", None)
    if bot is None:
        raise RuntimeError("emoji_puzzle requires a bot instance")
    active = await db.get_active_session(chat_id, thread_id)
    if active:
        logger.info("emoji_puzzle: session already active in chat %s thread %s", chat_id, thread_id)
        return None

    _, schedule = _emoji_settings()
    puzzle_count = int(schedule.get("puzzle_count", 5) or 5)
    interval_seconds = int(schedule.get("interval_seconds") or (int(schedule.get("interval_minutes", 1) or 1) * 60))
    intro_offset_seconds = int(schedule.get("intro_offset_seconds", 10) or 10)
    wrap_offset_seconds = int(schedule.get("wrap_offset_seconds", 20) or 20)
    wrap_countdown_minutes = [
        int(m) for m in (schedule.get("wrap_countdown_minutes") or [])
        if str(m).strip().isdigit() and int(m) > 0
    ]

    puzzles = await _pick_session_puzzles(db, puzzle_count, media_types=media_types)
    if len(puzzles) < puzzle_count:
        logger.warning(
            "emoji_puzzle: not enough enabled puzzles (%d/%d) media_types=%s",
            len(puzzles), puzzle_count, media_types,
        )
        return None

    session_id = await db.create_emoji_session(chat_id, thread_id, puzzle_count)
    try:
        if intro_offset_seconds > 0:
            await asyncio.sleep(intro_offset_seconds)
        intro = await safe_send(
            bot,
            db,
            "send_message",
            chat_id=chat_id,
            text=_format_intro_text(len(puzzles), theme_label=theme_label),
            message_thread_id=thread_id,
        )
        intro_message_id = int(getattr(intro, "message_id", 0) or 0)
        if intro_message_id <= 0:
            raise RuntimeError("emoji_puzzle intro send did not return a message_id")
    except Exception:
        try:
            await db.complete_emoji_session(session_id, [])
        except Exception:
            pass
        raise

    task = asyncio.create_task(
        _run_emoji_session(
            bot=bot,
            db=db,
            session_id=session_id,
            chat_id=chat_id,
            thread_id=thread_id,
            puzzles=puzzles,
            theme_label=theme_label,
            intro_offset_seconds=0,
            interval_seconds=interval_seconds,
            wrap_offset_seconds=wrap_offset_seconds,
            wrap_countdown_minutes=wrap_countdown_minutes,
            intro_already_sent=True,
        )
    )
    _session_tasks[session_id] = task
    task.add_done_callback(lambda _: _session_tasks.pop(session_id, None))
    logger.info("emoji_puzzle: started live session %s with %d puzzles", session_id, puzzle_count)
    if return_launch_info:
        return {"session_id": session_id, "message_id": intro_message_id}
    return session_id


async def _run_emoji_session(
    bot,
    db: Database,
    session_id: int,
    chat_id: int,
    thread_id: int | None,
    puzzles: list[dict],
    theme_label: str | None,
    intro_offset_seconds: int,
    interval_seconds: int,
    wrap_offset_seconds: int,
    wrap_countdown_minutes: list[int],
    intro_already_sent: bool = False,
):
    if not intro_already_sent:
        if intro_offset_seconds > 0:
            await asyncio.sleep(intro_offset_seconds)
        await safe_send(
            bot,
            db,
            "send_message",
            chat_id=chat_id,
            text=_format_intro_text(len(puzzles), theme_label=theme_label),
            message_thread_id=thread_id,
        )

    for idx, puzzle in enumerate(puzzles, start=1):
        msg = await safe_send(
            bot,
            db,
            "send_message",
            chat_id=chat_id,
            text=_format_puzzle_text(puzzle, idx, len(puzzles)),
            message_thread_id=thread_id,
        )
        await db.start_emoji_round(session_id, puzzle["id"], chat_id, msg.message_id, thread_id, _POINTS_BY_RANK[0])
        if idx < len(puzzles) and interval_seconds > 0:
            await asyncio.sleep(interval_seconds)

    remaining = max(0, int(wrap_offset_seconds))
    countdowns = sorted(
        {m for m in wrap_countdown_minutes if m * 60 <= remaining},
        reverse=True,
    )
    for minutes in countdowns:
        target_remaining = minutes * 60
        if remaining > target_remaining:
            await asyncio.sleep(remaining - target_remaining)
            remaining = target_remaining
        await safe_send(
            bot,
            db,
            "send_message",
            chat_id=chat_id,
            text=_format_wrap_countdown(minutes),
            message_thread_id=thread_id,
        )
    if remaining > 0:
        await asyncio.sleep(remaining)

    leaderboard = await db.get_session_leaderboard(session_id)
    unsolved_rounds = await db.get_session_unsolved_rounds(session_id)
    await safe_send(
        bot,
        db,
        "send_message",
        chat_id=chat_id,
        text=_format_wrap_text(leaderboard, len(puzzles), unsolved_rounds),
        message_thread_id=thread_id,
    )
    await db.close_session_rounds(session_id)
    await db.complete_emoji_session(session_id, leaderboard)


async def send_scheduled_emoji_night(context: ContextTypes.DEFAULT_TYPE):
    """Cron job: start one Emoji Night session. Target comes from
    bot_message_routing.emoji_puzzle, not the legacy settings.topics lookup."""
    if is_auto_blocked_on(datetime.now(_IL_TZ).date()):
        logger.info("emoji_puzzle: blackout date, skipping automatic session")
        return

    db: Database = context.bot_data["db"]
    routing = await db.get_handler_routing("emoji_puzzle")
    if not routing or routing["play_topic_id"] is None:
        logger.warning("emoji_puzzle: no routing configured for 'emoji_puzzle'; skipping")
        return
    play_id = routing["play_topic_id"]

    settings = get_settings()
    feature = settings.get("features", {}).get("emoji_puzzle", {}) or {}
    groups = feature.get("groups", []) if isinstance(feature, dict) else []
    targets: list[tuple[int, int | None]] = []
    if "main" in (groups or []) and GROUP_ID:
        targets.append((GROUP_ID, play_id))
    if "test" in (groups or []) and TEST_GROUP_ID:
        targets.append((TEST_GROUP_ID, None))

    for chat_id, thread_id in targets:
        try:
            await start_emoji_night(context, chat_id, thread_id)
        except Exception as e:
            logger.warning("emoji_puzzle: scheduled start failed for chat %s: %s", chat_id, e)


async def send_scheduled_emoji_message(bot, db: Database, msg: dict):
    """Handle custom scheduled-message types for Emoji Night."""
    payload = _parse_payload(msg.get("poll_options"))
    message_type = msg.get("message_type")
    chat_id = msg["_resolved_chat_id"]
    text = msg["text"]
    thread_id = msg.get("channel_topic_id")

    if message_type == "emoji_puzzle_wrap":
        session_id = int(payload.get("session_id", 0) or 0)
        total = int(payload.get("total", 0) or 0)
        leaderboard = await db.get_session_leaderboard(session_id)
        unsolved_rounds = await db.get_session_unsolved_rounds(session_id)
        text = _format_wrap_text(leaderboard, total, unsolved_rounds)
        sent = await safe_send(bot, db, "send_message", chat_id=chat_id, text=text, message_thread_id=thread_id)
        if unsolved_rounds:
            await db.mark_session_rounds_revealed(session_id)
        await db.complete_emoji_session(session_id, leaderboard)
        return sent

    sent = await safe_send(bot, db, "send_message", chat_id=chat_id, text=text, message_thread_id=thread_id)

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
    if not update.message or not update.effective_chat:
        return
    actor = _get_answer_actor(update)
    if not actor:
        return

    reply_to = update.message.reply_to_message
    if not reply_to:
        return

    db: Database = context.bot_data["db"]
    round_row = await db.get_active_emoji_round_for_message(update.effective_chat.id, reply_to.message_id)
    if not round_row:
        return

    logger.info(
        "emoji_puzzle: received reply chat=%s round=%s user=%s text=%r",
        update.effective_chat.id,
        round_row["id"],
        actor["id"],
        (update.message.text or "")[:120],
    )

    if not _guess_matches(round_row, update.message.text or ""):
        return

    await db.upsert_member(actor["id"], actor["username"], actor["display_name"])

    answer = await db.record_emoji_correct_answer(
        round_row["id"],
        actor["id"],
        update.message.message_id,
        _POINTS_BY_RANK,
    )
    if not answer:
        try:
            await update.message.reply_text("👀 נקלט, אבל כבר רשמתי לך תשובה נכונה לחידה הזאת.")
        except Exception:
            pass
        return

    award_points = int(answer["points_awarded"])
    old_points = await db.add_points(actor["id"], award_points) if award_points > 0 else await db.get_points(actor["id"])
    if award_points > 0:
        await db.log_activity(
            "emoji_puzzle",
            f"+{award_points} נקודות ל-{actor['display_name']} (Emoji Night)",
            actor["id"],
        )
    logger.info(
        "emoji_puzzle: round=%s recognized user=%s rank=%s points=%s",
        round_row["id"], actor["id"], answer["answer_rank"], award_points,
    )

    try:
        rank = int(answer["answer_rank"])
        badge = _place_badge(rank)
        if award_points > 0:
            text = f"{badge} נקלט! מקום {rank} ל-{actor['display_name']} (+{award_points} נקודות)"
        else:
            text = f"✅ נקלט! {actor['display_name']} פתר/ה נכון, אבל רק הראשון/ה מקבל/ת נקודות על החידה הזאת."
        await safe_send(
            context.bot,
            db,
            "send_message",
            chat_id=update.effective_chat.id,
            text=text,
            reply_to_message_id=round_row["message_id"],
            message_thread_id=getattr(update.message, "message_thread_id", None),
        )
    except UnverifiedTopicError as e:
        logger.warning("emoji_puzzle: win reply refused by guard: %s", e)
    except Exception as e:
        logger.warning("emoji_puzzle: failed to send win reply: %s", e)

    new_level = check_level_up(old_points, old_points + award_points) if award_points > 0 else None
    if new_level and not actor["is_anonymous"]:
        mention = f"[{actor['display_name']}](tg://user?id={actor['id']})"
        try:
            await safe_send(
                context.bot,
                db,
                "send_message",
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
        try:
            await safe_send(
                context.bot,
                db,
                "send_message",
                chat_id=round_row["chat_id"],
                text=f"🕒 זמן לחשוף: {round_row.get('emoji_prompt', '')}\nהתשובה היא: {answer}",
                reply_to_message_id=round_row["message_id"],
                message_thread_id=round_row.get("message_thread_id"),
            )
            await db.mark_emoji_round_revealed(round_row["id"])
        except UnverifiedTopicError as e:
            logger.warning("emoji_puzzle: reveal refused by guard for round %s: %s", round_row["id"], e)
        except Exception as e:
            logger.warning("emoji_puzzle: failed to reveal round %s: %s", round_row["id"], e)


def register(app: Application):
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_emoji_puzzle_reply),
        group=4,
    )
