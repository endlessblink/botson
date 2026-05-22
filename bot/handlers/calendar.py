# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Content calendar — checks for due messages and sends them automatically."""

import json
import logging
import os
import re
from types import SimpleNamespace
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes

from ..database.db import Database
from .emoji_puzzle import emoji_skip_reason, send_scheduled_emoji_message, start_emoji_night
from .trivia_round import start_scheduled_trivia_round
from .free_games import send_free_games
from .facts import send_scheduled_fact
from .levels import send_weekly_leaderboard
from .roundup import send_weekly_roundup
from ..utils.config import should_skip_scheduled_message
from ..utils.scheduling_errors import SkippedActivity
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)

_IL_TZ = ZoneInfo("Asia/Jerusalem")


def _subject_markers_for_log(message_type: str, poll_options) -> str:
    """Gap 11: build a "<key>:<value>" marker string for activity_log.

    Populate rotation reads activity_log via get_recent_activity_subjects(key=…)
    to learn what actually-ran subjects to rotate away from. Different game
    types tag different markers:

      - trivia_round / trivia_warmup_rsvp: ``categories:movies+gaming``
      - emoji_puzzle:                       ``media_type:song``
      - facts_tidbit / facts_spooky:        ``fact_id:dybbuk_origin``

    Empty string when there's no payload, no subject, or unknown type.
    Multiple markers are space-separated. Marker values are sanitised to
    alphanumerics + hyphen/underscore to match the regex in
    Database.get_recent_activity_subjects.
    """
    if not poll_options:
        return ""
    try:
        payload = json.loads(poll_options) if isinstance(poll_options, str) else dict(poll_options)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""

    def _clean(value: str) -> str:
        s = str(value or "").strip()
        return re.sub(r"[^A-Za-z0-9_\-]", "", s)

    parts: list[str] = []
    mtype = (message_type or "").strip()
    if mtype in ("trivia_round", "trivia_warmup_rsvp", "warmup_reminder"):
        cats = payload.get("categories") or []
        if isinstance(cats, (list, tuple)):
            tokens = [t for t in (_clean(c) for c in cats) if t]
            if tokens:
                parts.append(f"categories:{'+'.join(tokens)}")
    if mtype in ("emoji_puzzle", "trivia_warmup_rsvp"):
        media_types = payload.get("media_types") or []
        if isinstance(media_types, (list, tuple)):
            tokens = [t for t in (_clean(m) for m in media_types) if t]
            if tokens:
                parts.append(f"media_type:{'+'.join(tokens)}")
    if mtype in ("facts_tidbit", "facts_spooky"):
        fid = _clean(payload.get("fact_id") or "")
        if fid:
            parts.append(f"fact_id:{fid}")
    return " ".join(parts)


def _require_message_id(value, activity: str) -> int:
    if isinstance(value, dict) and value.get("skipped"):
        raise SkippedActivity(f"{activity} skipped: {value.get('skipped')}")
    if isinstance(value, dict) and value.get("message_id") is not None:
        value = value.get("message_id")
    message_id = int(value or 0)
    if message_id <= 0:
        raise RuntimeError(f"{activity} did not return a Telegram message_id")
    return message_id


def _parse_payload(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _format_rsvp_names(responses: list[dict], *, limit: int = 5) -> str:
    names = [str(row.get("display_name") or "").strip() for row in responses]
    names = [name for name in names if name]
    if not names:
        return "אין נרשמים"  # noqa: hardcoded-content (temporary fallback; copy extraction follow-up)
    shown = names[:limit]
    suffix = f" +{len(names) - limit}" if len(names) > limit else ""
    return ", ".join(shown) + suffix


async def _enforce_warmup_rsvp_gate(db: Database, msg: dict, bot, group_id: int) -> None:
    """T-127: Cancel a trivia/emoji game launch if the paired warm-up RSVP
    count is below `min_ready_players`. No-op when the row has no
    `warmup_marker` or threshold is 0 — preserves legacy rows.

    Raises SkippedActivity to short-circuit the dispatch loop. The existing
    exception handler in check_and_send_due_messages marks the row 'skipped'.
    """
    payload = _parse_payload(msg.get("poll_options"))
    marker = str(payload.get("warmup_marker") or "").strip()
    threshold = int(payload.get("min_ready_players") or 0)
    if not marker or threshold <= 0:
        return

    async with db._db.execute(
        """SELECT id, sent_message_id, channel_topic_id, poll_options
           FROM scheduled_messages
           WHERE message_type = 'trivia_warmup_rsvp' AND status = 'sent'
           ORDER BY id DESC""",
    ) as cur:
        candidates = await cur.fetchall()
    ann = None
    for cand in candidates:
        try:
            cand_payload = json.loads(cand["poll_options"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if str(cand_payload.get("warmup_marker") or "") == marker:
            ann = cand
            break
    if not ann:
        # Announcement never sent — leave the game alone, the existing
        # in-game ready-gate is still the safety net.
        return

    responses = await db.get_trivia_interest_responses(int(ann["id"]))
    rsvp_count = len(responses)
    if rsvp_count >= threshold:
        return

    # B.3: 4-level fallback chain for the cancel-notice label.
    # 1) explicit activity_label from poll_options (most specific)
    # 2) theme_label from poll_options (e.g. "גיימינג")
    # 3) settings.yaml:copy.default_activity_label (operator-tunable)
    # 4) hard-coded last-resort string with noqa documenting why
    from ..utils.copy import default_activity_label
    activity_label = (
        str(payload.get("activity_label") or "").strip()
        or str(payload.get("theme_label") or "").strip()
        or default_activity_label()
        or "המשחק"  # noqa: hardcoded-content (last-resort when settings.copy.default_activity_label is empty; visible reminder to operator)
    )
    cancel_text = (
        f"❌ {activity_label} לא יוצא לדרך הפעם — רק {rsvp_count}/{threshold} סימנו שהם בפנים.\n"
        f"נרשמו: {_format_rsvp_names(responses)}\n"
        "ננסה שוב בתאריך הבא 🙂"
    )
    ann_topic = ann["channel_topic_id"]
    ann_message_id = ann["sent_message_id"]
    try:
        kwargs = {
            "chat_id": group_id,
            "text": cancel_text,
            "message_thread_id": int(ann_topic) if ann_topic else None,
        }
        if ann_message_id:
            kwargs["reply_to_message_id"] = int(ann_message_id)
        await safe_send(bot, db, "send_message", **kwargs)
    except UnverifiedTopicError as e:
        logger.warning("warmup_rsvp_gate: cancel notice refused by guard: %s", e)
    except Exception as e:
        logger.warning("warmup_rsvp_gate: cancel notice send failed: %s", e)

    raise SkippedActivity(
        f"warmup_rsvp_gate: {rsvp_count}/{threshold} ready, cancelling launch"
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

    History: an earlier version walked the entire text and took every
    matching keyword, which polluted music rounds when the announcement said
    things like "המוזיקה לאורך ההיסטוריה" — the round then mixed history
    questions in. Now we only look at the word right after "סיבוב טריוויה",
    which is what the user actually typed as the theme.
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


async def _coerce_due_game_row(db: Database, msg: dict, target: str) -> dict:
    """Treat natural-language scheduled game rows as executable game launches."""
    message_type = msg.get("message_type") or "custom"
    if message_type in {"trivia_round", "emoji_puzzle"}:
        return msg
    if message_type not in {"discussion", "custom", "trivia"}:
        return msg

    text = msg.get("text") or ""
    compact = text.lower()
    coerced = dict(msg)
    if _looks_like_trivia_launch(text):
        original_topic = coerced.get("channel_topic_id")
        categories = _infer_trivia_categories(text)
        payload = {
            "pre_roll_s": 30,
            "theme_label": categories[0] if categories else "כללי",
            "categories": categories,
            "question_count": _infer_question_count(text),
        }
        routing = await db.get_handler_routing("trivia_round")
        play_topic_id = routing["play_topic_id"] if routing and routing.get("play_topic_id") is not None else original_topic
        if target == "test":
            coerced["channel_topic_id"] = None
        else:
            coerced["channel_topic_id"] = play_topic_id
            if original_topic and original_topic != play_topic_id:
                payload["teaser_topic_id"] = int(original_topic)
        coerced["message_type"] = "trivia_round"
        coerced["poll_options"] = json.dumps(payload, ensure_ascii=False)
    elif _looks_like_emoji_launch(text):
        routing = await db.get_handler_routing("emoji_puzzle")
        if target == "test":
            coerced["channel_topic_id"] = None
        elif routing and routing.get("play_topic_id") is not None:
            coerced["channel_topic_id"] = routing["play_topic_id"]
        coerced["message_type"] = "emoji_puzzle"
    return coerced


def _media_dir() -> Path:
    return Path(os.getenv("MEDIA_DIR", "./media")).resolve()


async def send_message_with_optional_cover(bot, *, db: Database, chat_id: int, text: str,
                                            message_thread_id: int | None = None,
                                            cover_path: str | None = None,
                                            bypass_verification: bool = False):
    """Send a message, as a photo with caption if cover_path is set.

    cover_path is stored relative to MEDIA_DIR (e.g. "covers/foo.png").
    Returns the sent Message object. Raises UnverifiedTopicError if the target
    fails the topic_guard check (unless bypass_verification=True).
    """
    if cover_path:
        full = _media_dir() / cover_path
        if full.exists():
            with full.open("rb") as f:
                return await safe_send(
                    bot,
                    db,
                    "send_photo",
                    chat_id=chat_id,
                    photo=f,
                    caption=text,
                    message_thread_id=message_thread_id,
                    bypass_verification=bypass_verification,
                )
        logger.warning("cover_path %s not found at %s — falling back to text", cover_path, full)
    return await safe_send(
        bot,
        db,
        "send_message",
        chat_id=chat_id,
        text=text,
        message_thread_id=message_thread_id,
        bypass_verification=bypass_verification,
    )


async def _create_event_row_from_scheduled(db: Database, msg: dict) -> int:
    """Create an `events` row for a scheduled message of type 'event'.

    The planner wizard stores the full card body in `text` (no separate
    title/description fields on scheduled_messages), so derive a title from
    the first non-empty line and keep the whole text as description for the
    dashboard events list. Location is already embedded in the text by the
    wizard, so we don't split it out here.
    """
    raw = (msg.get("text") or "").strip()
    first_line = next((ln.strip() for ln in raw.splitlines() if ln.strip()), "")
    title = (first_line[:80] or "אירוע") if first_line else "אירוע"
    created_by = msg.get("created_by")
    try:
        created_by_int = int(created_by) if created_by is not None else 0
    except (TypeError, ValueError):
        created_by_int = 0
    return await db.create_event(
        title=title,
        description=raw,
        event_date=msg.get("scheduled_date") or "",
        event_time=msg.get("scheduled_time"),
        location=None,
        created_by=created_by_int,
        cover_path=msg.get("cover_path"),
        auto_pin=bool(msg.get("auto_pin")),
        topic_id=msg.get("channel_topic_id"),
    )


def _parse_poll_options(raw) -> list[str]:
    """Decode poll_options from DB (JSON string or list) into a clean list."""
    if not raw:
        return []
    if isinstance(raw, list):
        items = raw
    else:
        try:
            items = json.loads(raw)
        except (TypeError, ValueError):
            return []
    return [str(o).strip() for o in items if str(o).strip()]


def _build_poll_markup(options: list[str]):
    """Build an InlineKeyboardMarkup with one button per option.

    Buttons are labelled "🗓️ {option}" with callback_data "poll_{index}" so the
    existing `polls.handle_poll_vote` handler auto-discovers labels and tracks
    voter names in the button text.
    """
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = [
        [InlineKeyboardButton(f"🗓️ {opt}", callback_data=f"poll_{i}")]
        for i, opt in enumerate(options)
    ]
    return InlineKeyboardMarkup(rows)


async def send_poll_message(bot, *, db: Database, chat_id: int, question: str,
                             options: list[str],
                             message_thread_id: int | None = None,
                             duration_hours: int | None = None,
                             cover_path: str | None = None,
                             bypass_verification: bool = False):
    """Send an inline-button poll. Returns the sent Message. Raises
    UnverifiedTopicError for guarded targets."""
    markup = _build_poll_markup(options)
    if cover_path:
        full = _media_dir() / cover_path
        if full.exists():
            with full.open("rb") as f:
                return await safe_send(
                    bot,
                    db,
                    "send_photo",
                    chat_id=chat_id,
                    photo=f,
                    caption=question,
                    reply_markup=markup,
                    message_thread_id=message_thread_id,
                    bypass_verification=bypass_verification,
                )
        logger.warning("poll cover_path %s not found — sending without photo", cover_path)
    return await safe_send(
        bot,
        db,
        "send_message",
        chat_id=chat_id,
        text=question,
        reply_markup=markup,
        message_thread_id=message_thread_id,
        bypass_verification=bypass_verification,
    )


def _next_matching_day(current_date: date, days: list[int]) -> date:
    """Find the next date that matches one of the Hebrew days.
    Hebrew: 0=Sun, 1=Mon, 2=Tue, 3=Wed, 4=Thu, 5=Fri, 6=Sat
    """
    for i in range(1, 8):
        next_d = current_date + timedelta(days=i)
        hebrew_day = (next_d.weekday() + 1) % 7
        if hebrew_day in days:
            return next_d
    return current_date + timedelta(days=7)  # fallback


async def check_and_send_due_messages(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute. Checks for due messages and sends them."""
    now = datetime.now(_IL_TZ)
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    db: Database = context.bot_data["db"]
    due = await db.get_due_messages(current_date, current_time)

    if not due:
        return

    import os
    from telegram import Bot
    bot_token = os.getenv("BOT_TOKEN", "")
    main_group = int(os.getenv("GROUP_ID", "0"))
    test_group = int(os.getenv("TEST_GROUP_ID", "0"))

    # Stale-drop guard: defense-in-depth against rows whose scheduled_time is
    # far in the past (bot was offline for hours, DB anomaly, etc.). Threshold
    # via settings.yaml (stale_drop_minutes, default 30). Marks row 'failed'
    # so it neither sends nor retries. Future-dated rows are unaffected.
    try:
        from bot.utils.config import get_settings as _get_settings
        stale_drop_minutes = int((_get_settings() or {}).get("stale_drop_minutes", 30) or 30)
    except Exception:
        stale_drop_minutes = 30

    for msg in due:
        if should_skip_scheduled_message(msg.get("scheduled_date", ""), msg.get("created_by")):
            logger.info(
                "Skipping auto scheduled message %s on blackout date %s",
                msg.get("id"),
                msg.get("scheduled_date"),
            )
            await db.delete_scheduled_message(msg["id"])
            continue

        # Stale-drop check — fires only for rows significantly behind now.
        if stale_drop_minutes > 0:
            try:
                sched_dt = datetime.strptime(
                    f"{msg.get('scheduled_date','')} {(msg.get('scheduled_time') or '00:00')[:5]}",
                    "%Y-%m-%d %H:%M",
                ).replace(tzinfo=_IL_TZ)
                delay_minutes = (now - sched_dt).total_seconds() / 60.0
            except (ValueError, TypeError):
                delay_minutes = 0
            if delay_minutes > stale_drop_minutes:
                logger.warning(
                    "Stale-drop: msg id=%s scheduled=%s %s was %.1f min late (threshold %d) — marking failed without send",
                    msg.get("id"), msg.get("scheduled_date"), msg.get("scheduled_time"),
                    delay_minutes, stale_drop_minutes,
                )
                await db.mark_message_failed(msg["id"], f"stale:{int(delay_minutes)}min")
                continue

        target = msg.get("target_group", "main")
        if target == "test":
            group_id = test_group
        elif target == "both":
            group_id = main_group  # Send to main first, test handled separately
        else:
            group_id = main_group

        if not group_id:
            await db.mark_message_failed(msg["id"], f"No group ID for target '{target}'")
            continue

        msg = await _coerce_due_game_row(db, msg, target)

        try:
            bot = Bot(bot_token)
            msg["_resolved_chat_id"] = group_id
            event_id_for_rsvp: int | None = None
            if msg.get("message_type") == "trivia_round":
                await _enforce_warmup_rsvp_gate(db, msg, bot, group_id)
                sent = SimpleNamespace(
                    message_id=_require_message_id(
                        await start_scheduled_trivia_round(context, msg),
                        "trivia_round",
                    )
                )
            elif msg.get("message_type") == "emoji_puzzle":
                await _enforce_warmup_rsvp_gate(db, msg, bot, group_id)
                payload = _parse_payload(msg.get("poll_options"))
                # A.1.4 pre-flight: distinguish "pool exhausted by cooldown
                # or media-type filter" (legit skip) from "real launch
                # failure" (e.g. bot instance missing). Without this, both
                # collapse to RuntimeError → mark_message_failed, which
                # the user/operator can't tell apart from actual bugs.
                _emoji_skip = await emoji_skip_reason(
                    db, group_id, msg.get("channel_topic_id"),
                    media_types=payload.get("media_types") or None,
                )
                if _emoji_skip:
                    raise SkippedActivity(f"emoji_puzzle: {_emoji_skip}")
                session_id = await start_emoji_night(
                    context,
                    group_id,
                    msg.get("channel_topic_id"),
                    force=True,
                    media_types=payload.get("media_types") or None,
                    theme_label=payload.get("theme_label") or None,
                )
                if session_id is None:
                    raise RuntimeError("Emoji Night did not start")
                sent = SimpleNamespace(message_id=session_id)
            elif msg.get("message_type") == "free_games":
                summary = await send_free_games(context, force=True)
                if not summary or int(summary.get("posted") or 0) <= 0:
                    if summary and (summary.get("error") in {None, "blackout date", "disabled"}):
                        raise SkippedActivity(f"free_games: {summary}")
                    raise RuntimeError(f"free_games did not post: {summary}")
                sent = SimpleNamespace(message_id=1)
            elif msg.get("message_type") in {"facts_tidbit", "facts_spooky"}:
                pool = msg.get("message_type", "").removeprefix("facts_")
                fact_id = None
                try:
                    payload = json.loads(msg.get("poll_options") or "{}")
                    fact_id = str(payload.get("fact_id") or "").strip() or None
                except Exception:
                    fact_id = None
                sent_ok = await send_scheduled_fact(
                    bot,
                    db,
                    pool=pool,
                    chat_id=group_id,
                    thread_id=msg.get("channel_topic_id"),
                    fact_id=fact_id,
                )
                if not sent_ok:
                    raise RuntimeError(f"facts {pool} did not send")
                sent = SimpleNamespace(message_id=1)
            elif msg.get("message_type") == "weekly_roundup":
                sent = SimpleNamespace(
                    message_id=_require_message_id(
                        await send_weekly_roundup(context, force=True),
                        "weekly_roundup",
                    )
                )
            elif msg.get("message_type") == "weekly_leaderboard":
                sent = SimpleNamespace(
                    message_id=_require_message_id(
                        await send_weekly_leaderboard(context),
                        "weekly_leaderboard",
                    )
                )
            elif msg.get("message_type", "").startswith("emoji_puzzle_"):
                sent = await send_scheduled_emoji_message(bot, db, msg)
            elif msg.get("message_type") == "event":
                event_id_for_rsvp = await _create_event_row_from_scheduled(db, msg)
                sent = await send_message_with_optional_cover(
                    bot,
                    db=db,
                    chat_id=group_id,
                    text=msg["text"],
                    message_thread_id=msg.get("channel_topic_id"),
                    cover_path=msg.get("cover_path"),
                )
                # Attach RSVP buttons once the message exists so the bot's
                # rsvp_yes_/rsvp_maybe_ handler can update this message.
                try:
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    from .dm_menu import deep_link_button
                    _ev_rows = [[
                        InlineKeyboardButton("✅ מגיע/ה", callback_data=f"rsvp_yes_{event_id_for_rsvp}"),
                        InlineKeyboardButton("🤔 אולי", callback_data=f"rsvp_maybe_{event_id_for_rsvp}"),
                    ]]
                    _dl = deep_link_button()
                    if _dl:
                        _ev_rows.append([_dl])
                    await bot.edit_message_reply_markup(
                        chat_id=group_id,
                        message_id=sent.message_id,
                        reply_markup=InlineKeyboardMarkup(_ev_rows),
                    )
                except Exception as e:
                    logger.warning("[events] failed to attach RSVP buttons to %d: %s", msg["id"], e)
                # Persist message_id so the RSVP handler can edit this exact message.
                await db.update_event(event_id_for_rsvp, message_id=sent.message_id)
            elif msg.get("message_type") == "trivia_warmup_rsvp":
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                from .dm_menu import deep_link_button
                _wu_rows = [[
                    InlineKeyboardButton("🙋 אני בפנים!", callback_data=f"trivint_{msg['id']}"),
                ]]
                _dl = deep_link_button()
                if _dl:
                    _wu_rows.append([_dl])
                markup = InlineKeyboardMarkup(_wu_rows)
                sent = await safe_send(
                    bot,
                    db,
                    "send_message",
                    chat_id=group_id,
                    text=msg["text"],
                    message_thread_id=msg.get("channel_topic_id"),
                    reply_markup=markup,
                )
            elif msg.get("message_type") == "warmup_reminder":
                payload = _parse_payload(msg.get("poll_options"))
                marker = str(payload.get("warmup_marker") or "").strip()
                if not marker:
                    raise SkippedActivity("warmup_reminder: missing warmup_marker")
                async with db._db.execute(
                    """SELECT id, sent_message_id, poll_options
                       FROM scheduled_messages
                       WHERE message_type = 'trivia_warmup_rsvp' AND status = 'sent'
                       ORDER BY id DESC""",
                ) as cur:
                    candidates = await cur.fetchall()
                ann = None
                for cand in candidates:
                    try:
                        cand_payload = json.loads(cand["poll_options"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if str(cand_payload.get("warmup_marker") or "") == marker:
                        ann = cand
                        break
                if not ann:
                    raise SkippedActivity(f"warmup_reminder: parent announcement not sent for marker={marker}")
                announcement_id = int(ann["id"])
                threshold = int(payload.get("min_ready_players") or 0)
                async with db._db.execute(
                    "SELECT COUNT(*) AS n FROM trivia_interest_responses WHERE scheduled_msg_id = ?",
                    (announcement_id,),
                ) as cur:
                    crow = await cur.fetchone()
                rsvp_count = int(crow["n"]) if crow else 0
                if threshold > 0 and rsvp_count >= threshold:
                    raise SkippedActivity(
                        f"warmup_reminder: threshold {threshold} already met (count={rsvp_count})"
                    )
                ann_message_id = ann["sent_message_id"]
                kwargs = {
                    "chat_id": group_id,
                    "text": msg["text"],
                    "message_thread_id": msg.get("channel_topic_id"),
                }
                if ann_message_id:
                    kwargs["reply_to_message_id"] = int(ann_message_id)
                sent = await safe_send(bot, db, "send_message", **kwargs)
            else:
                poll_options = _parse_poll_options(msg.get("poll_options"))
                if msg.get("message_type") == "poll" and len(poll_options) >= 2:
                    sent = await send_poll_message(
                        bot,
                        db=db,
                        chat_id=group_id,
                        question=msg["text"],
                        options=poll_options,
                        message_thread_id=msg.get("channel_topic_id"),
                        duration_hours=msg.get("poll_duration"),
                        cover_path=msg.get("cover_path"),
                    )
                else:
                    if msg.get("message_type") == "poll":
                        logger.warning(
                            "Scheduled poll %d has no valid options — sending as text",
                            msg["id"],
                        )
                    sent = await send_message_with_optional_cover(
                        bot,
                        db=db,
                        chat_id=group_id,
                        text=msg["text"],
                        message_thread_id=msg.get("channel_topic_id"),
                        cover_path=msg.get("cover_path"),
                    )

            # Auto-pin if requested
            if msg.get("auto_pin") and sent.message_id:
                try:
                    await bot.pin_chat_message(
                        chat_id=group_id,
                        message_id=sent.message_id,
                        disable_notification=True,
                    )
                except Exception as e:
                    logger.warning("Failed to pin message %d: %s", sent.message_id, e)

            await db.mark_message_sent(msg["id"], sent.message_id)

            # Opt-in DM heads-up: notify users who toggled this activity type
            # on in their personal menu. Awaited directly (not offloaded) — the
            # community is small and sends use AIORateLimiter; wrapped so a
            # notify failure can never break the dispatch loop. If opt-in lists
            # ever grow large, offload via context.job_queue.run_once.
            try:
                from .dm_menu import notify_opted_in_users
                await notify_opted_in_users(context, db, msg, event_id=event_id_for_rsvp)
            except Exception as e:  # noqa: BLE001
                logger.warning("dm_menu: notify hook failed for msg %s: %s", msg.get("id"), e)

            # Gap 11: include subject markers in the activity_log description
            # so populate rotation can read the actually-ran history (parallel
            # to scheduled_messages.poll_options, which only catches rows that
            # weren't pruned). Marker format mirrors get_recent_activity_subjects'
            # `<key>:<value>` pattern (already used by facts via fact_id).
            mtype = msg.get("message_type", "custom")
            markers = _subject_markers_for_log(mtype, msg.get("poll_options"))
            desc = f"שלח: {msg['text'][:50]}"
            if markers:
                desc = f"{desc} [{markers}]"
            await db.log_activity(
                mtype,
                desc,
                target_channel=str(msg.get("channel_topic_id") or "general"),
            )
            logger.info("Sent scheduled message %d: %s", msg["id"], msg["text"][:40])

            # Handle recurrence — create next occurrence
            recurrence = msg.get("recurrence")
            if recurrence:
                today = date.fromisoformat(current_date)
                if recurrence == "daily":
                    next_date = today + timedelta(days=1)
                elif recurrence == "weekdays":
                    next_d = today + timedelta(days=1)
                    while next_d.weekday() >= 5:  # Skip Sat/Sun
                        next_d += timedelta(days=1)
                    next_date = next_d
                elif recurrence == "weekly" and msg.get("recurrence_days"):
                    days = json.loads(msg["recurrence_days"])
                    next_date = _next_matching_day(today, days)
                else:
                    next_date = None

                if next_date:
                    await db.create_scheduled_message(
                        text=msg["text"],
                        message_type=msg.get("message_type", "custom"),
                        channel_topic_id=msg.get("channel_topic_id"),
                        target_group=msg.get("target_group", "main"),
                        scheduled_date=next_date.isoformat(),
                        scheduled_time=msg.get("scheduled_time", "09:00"),
                        recurrence=recurrence,
                        recurrence_days=msg.get("recurrence_days"),
                        auto_pin=bool(msg.get("auto_pin")),
                        created_by="recurrence",
                        cover_path=msg.get("cover_path"),
                        poll_options=msg.get("poll_options"),
                        poll_duration=msg.get("poll_duration"),
                    )
                    logger.info("Created next occurrence for %d on %s", msg["id"], next_date)

        except SkippedActivity as e:
            mark_skipped = getattr(db, "mark_message_skipped", None)
            if mark_skipped:
                await mark_skipped(msg["id"], str(e))
            else:
                await db.mark_message_failed(msg["id"], f"skipped: {e}")
            logger.info("Skipped scheduled message %d: %s", msg["id"], e)
        except Exception as e:
            await db.mark_message_failed(msg["id"], str(e))
            logger.error("Failed to send scheduled message %d: %s", msg["id"], e)
