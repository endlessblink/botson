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
from .emoji_puzzle import (
    emoji_skip_reason,
    is_pool_exhausted_reason,
    send_scheduled_emoji_message,
    start_emoji_night,
)
from .trivia_round import start_scheduled_trivia_round
from .facts import send_scheduled_fact
from ..scheduler.dispatch_owner import CRON_OWNED_TYPES
from ..scheduler.game_contracts import EXECUTABLE_GAME_TYPES, GAME_SLOT_CLAIMING_TYPES
from ..utils.admin_alerts import notify_admins
from ..utils.config import should_skip_scheduled_message
from ..utils.copy import default_theme_label, load_copy
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
    from ..utils.config import rsvp_gate_enabled
    if not rsvp_gate_enabled():
        # Gate disabled (default): games always fire. The RSVP button still
        # posted on the warm-up and still recorded interest — we just stop
        # auto-cancelling, because the gate was killing ~80% of games for too
        # few RSVPs (2026-05-23). Re-enable via trivia.rsvp_gate_enabled.
        return

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
        # A thresholded game with no sent warm-up has no usable signup source.
        # Launching would make min_ready_players advisory, so skip instead of
        # falling back to the in-game ready gate.
        logger.warning(
            "warmup_rsvp_gate: marker=%s threshold=%s but no sent announcement — skipping launch",
            marker,
            threshold,
        )
        raise SkippedActivity(
            f"warmup_rsvp_gate: no sent warm-up announcement for marker={marker}"
        )

    # Aggregate RSVPs across EVERY sent announcement row sharing this marker.
    # Resolving a single ORDER-BY-id row undercounts when Populate committed the
    # same warm-up slot twice (duplicate rows, one marker) — that is the
    # 2026-05-22 "never fired" failure: real RSVPs landed on one row, the gate
    # counted the empty sibling and skipped.
    rsvp_users = await db.get_warmup_rsvp_user_map(marker)
    rsvp_count = len(rsvp_users)
    responses = [{"display_name": name} for name in rsvp_users.values()]
    logger.info(
        "warmup_rsvp_gate: marker=%s anchor_ann=%s rsvp_count=%s threshold=%s decision=%s",
        marker,
        int(ann["id"]),
        rsvp_count,
        threshold,
        "launch" if rsvp_count >= threshold else "skip",
    )
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


# Content types that claim their slot immediately and are never deferred by the
# same-topic spacing guard: live games, warm-up/reminder/RSVP announcements,
# polls, and event posts are time-anchored. Everything else (facts, discussion,
# morning/evening, custom) is "static" content that can wait a few minutes so it
# doesn't stack on top of another post in the same topic.
_SLOT_CLAIMING_TYPES = frozenset({
    *GAME_SLOT_CLAIMING_TYPES,
    "poll",
    "events_publish",
    "events_reminder",
})


def _dispatch_priority(msg: dict) -> tuple[str, int, int]:
    """Stable due-row ordering: time-anchored games claim their minute first."""
    mtype = str(msg.get("message_type") or "")
    return (
        str(msg.get("scheduled_time") or ""),
        0 if mtype in _SLOT_CLAIMING_TYPES else 1,
        int(msg.get("id") or 0),
    )


async def _enforce_warmup_announcement_present(db: Database, msg: dict) -> None:
    """Orphan-game guard (2026-05-23): a trivia/emoji row that carries a
    `warmup_marker` must have a matching warm-up announcement row before it may
    launch. A marker with no announcement means the game was scheduled without
    its warm-up pair — it would solo-launch with no heads-up (and, that night,
    landed on top of an unrelated story). Skip it instead.

    No-op when the row has no marker (legacy / coerced natural-language launches)
    or when `trivia.require_warmup_announcement` is false. Raises SkippedActivity
    to mark the row 'skipped' — the operator sees it in the dashboard + log; we
    deliberately do NOT post a cancel notice to the group (nothing was announced,
    so there is nothing for members to be told about).
    """
    from ..utils.config import require_warmup_announcement
    if not require_warmup_announcement():
        return
    payload = _parse_payload(msg.get("poll_options"))
    marker = str(payload.get("warmup_marker") or "").strip()
    if not marker:
        return
    if await db.warmup_announcement_exists(marker):
        return
    logger.warning(
        "orphan_game_guard: %s msg=%s has warmup_marker=%s but no warm-up "
        "announcement was ever scheduled — skipping solo-launch. Re-schedule "
        "the game via the warm-up flow, or set trivia.require_warmup_announcement=false.",
        msg.get("message_type"), msg.get("id"), marker,
    )
    raise SkippedActivity(
        f"orphan_game_guard: warmup_marker={marker} has no scheduled warm-up announcement"
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
    if message_type in EXECUTABLE_GAME_TYPES:
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


async def _game_warmup_thread_id(db: Database, msg: dict) -> int | None:
    """Use the scheduled topic for public game warm-ups.

    The warm-up is the teaser that should appear in the content-relevant forum
    topic. The executable game row still launches in the game play topic.
    """
    topic = msg.get("channel_topic_id")
    return int(topic) if topic is not None else None


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


def _next_recurrence_date_after(
    *,
    recurrence: str,
    recurrence_days: str | None,
    base_date: date,
    scheduled_time: str,
    now: datetime,
) -> date | None:
    """Return the next recurrence slot strictly after the current scheduler time."""
    hhmm = (scheduled_time or "09:00")[:5]

    def candidate_dt(day: date) -> datetime:
        return datetime.strptime(f"{day.isoformat()} {hhmm}", "%Y-%m-%d %H:%M").replace(tzinfo=_IL_TZ)

    def advance(day: date) -> date | None:
        if recurrence == "daily":
            return day + timedelta(days=1)
        if recurrence == "weekdays":
            next_d = day + timedelta(days=1)
            while next_d.weekday() >= 5:  # Skip Sat/Sun
                next_d += timedelta(days=1)
            return next_d
        if recurrence == "weekly" and recurrence_days:
            days = json.loads(recurrence_days)
            return _next_matching_day(day, days)
        return None

    next_date = advance(base_date)
    for _ in range(400):
        if not next_date:
            return None
        if candidate_dt(next_date) > now:
            return next_date
        next_date = advance(next_date)
    logger.warning("recurrence advance exceeded safety bound for base date %s", base_date)
    return None


async def _alert_admin_on_dead_emoji_pool(bot, payload: dict, reason: str) -> bool:
    """DM the admins when an Emoji Night will be skipped because its puzzle
    pool could not be refilled. Returns True when an alert was sent.

    Called from the dispatch pre-flight, *before* `start_emoji_night`, so the
    operator learns the game is dead before the group would have seen it.
    Only the pool-exhausted cause alerts: an active session resolves itself,
    and an RSVP-gate skip already posts a visible notice in the warm-up topic.
    """
    if not is_pool_exhausted_reason(reason):
        return False
    await notify_admins(bot, load_copy(
        "emoji_puzzle", "admin_pool_exhausted_alert",
        theme=payload.get("theme_label") or default_theme_label(),
        reason=reason,
    ))
    return True


async def check_and_send_due_messages(context: ContextTypes.DEFAULT_TYPE):
    """Runs every minute. Checks for due messages and sends them."""
    now = datetime.now(_IL_TZ)
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")

    db: Database = context.bot_data["db"]
    due = await db.get_due_messages(current_date, current_time)

    if not due:
        return
    due.sort(key=_dispatch_priority)

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

    slot_claims_this_tick: set[tuple[str, str, str, int | None]] = set()

    for msg in due:
        if hasattr(db, "get_scheduled_message"):
            fresh_msg = await db.get_scheduled_message(int(msg["id"]))
            if not fresh_msg:
                logger.info("dispatch_race: msg=%s disappeared before send", msg.get("id"))
                continue
            if fresh_msg.get("status") != "scheduled":
                logger.info(
                    "dispatch_race: msg=%s status changed to %s before send; skipping",
                    msg.get("id"), fresh_msg.get("status"),
                )
                continue
            claim_scheduled = getattr(db, "claim_scheduled_message", None)
            if claim_scheduled and not await claim_scheduled(int(msg["id"])):
                logger.info("dispatch_race: msg=%s already claimed by another dispatcher", msg.get("id"))
                continue
            if claim_scheduled:
                fresh_msg = await db.get_scheduled_message(int(msg["id"]))
            msg = fresh_msg

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
        elif target == "main":
            group_id = main_group
        else:
            await db.mark_message_failed(msg["id"], f"Unsupported target_group '{target}'")
            continue

        if not group_id:
            await db.mark_message_failed(msg["id"], f"No group ID for target '{target}'")
            continue

        msg = await _coerce_due_game_row(db, msg, target)
        mtype = msg.get("message_type")
        slot_key = (
            str(msg.get("scheduled_date") or ""),
            str(msg.get("scheduled_time") or "")[:5],
            str(msg.get("target_group") or "main"),
            msg.get("channel_topic_id"),
        )
        if mtype not in _SLOT_CLAIMING_TYPES and slot_key in slot_claims_this_tick:
            logger.warning(
                "same_slot_collision: skipping static msg=%s type=%s because a game/event already claimed %s %s topic=%s",
                msg.get("id"), mtype, slot_key[0], slot_key[1], slot_key[3],
            )
            await db.mark_message_skipped(msg["id"], "same_slot_collision: game/event already claimed this minute")
            continue

        # Same-topic spacing guard (2026-05-23): keep non-time-critical content
        # from stacking onto a topic that just received a post (that night an
        # emoji game at 22:00 and a story at 22:01 piled into botson_corner).
        # Live games / warm-ups / reminders / polls / event posts claim their
        # slot and are never deferred; static content (facts, discussion,
        # morning/evening, custom) waits until the topic has been quiet for
        # min_topic_spacing minutes, then sends on a later tick. No row is
        # dropped — it stays 'scheduled' and is re-evaluated every minute.
        try:
            from bot.utils.config import min_topic_spacing_minutes as _spacing_cfg
            _spacing = _spacing_cfg()
        except Exception:
            _spacing = 0
        _spacing_topic = msg.get("channel_topic_id")
        _last_topic_send = getattr(db, "last_topic_send_dt", None)
        if (
            _spacing > 0
            and _spacing_topic is not None
            and _last_topic_send is not None
            and msg.get("message_type") not in _SLOT_CLAIMING_TYPES
        ):
            _last_send = await _last_topic_send(
                _spacing_topic, msg.get("target_group", "main")
            )
            if _last_send is not None:
                _quiet_min = (now - _last_send).total_seconds() / 60.0
                if 0 <= _quiet_min < _spacing:
                    logger.info(
                        "topic_spacing: deferring %s msg=%s in topic %s — last "
                        "send %.1f min ago (< %d min); retrying next tick",
                        msg.get("message_type"), msg.get("id"),
                        _spacing_topic, _quiet_min, _spacing,
                    )
                    release_claim = getattr(db, "release_scheduled_message_claim", None)
                    if release_claim:
                        await release_claim(int(msg["id"]))
                    continue

        try:
            bot = Bot(bot_token)
            msg["_resolved_chat_id"] = group_id
            event_id_for_rsvp: int | None = None
            if msg.get("message_type") in CRON_OWNED_TYPES:
                # These types are owned by the APScheduler cron jobs (jobs.py), not
                # the calendar dispatcher. Firing both posted the weekly leaderboard
                # twice (2026-05-23). Any such row self-skips here, sending nothing.
                # Ownership is declared centrally in bot/scheduler/dispatch_owner.py
                # and enforced by tests/test_no_dual_dispatch.py.
                raise SkippedActivity(
                    f"{msg.get('message_type')}: owned by cron job, not the calendar dispatcher"
                )
            if msg.get("message_type") == "trivia_round":
                await _enforce_warmup_announcement_present(db, msg)
                await _enforce_warmup_rsvp_gate(db, msg, bot, group_id)
                sent = SimpleNamespace(
                    message_id=_require_message_id(
                        await start_scheduled_trivia_round(context, msg),
                        "trivia_round",
                    )
                )
            elif msg.get("message_type") == "emoji_puzzle":
                await _enforce_warmup_announcement_present(db, msg)
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
                    # Runs before start_emoji_night, so the admin hears about a
                    # dead pool before the group would have seen anything.
                    await _alert_admin_on_dead_emoji_pool(bot, payload, _emoji_skip)
                    raise SkippedActivity(f"emoji_puzzle: {_emoji_skip}")
                session_id = await start_emoji_night(
                    context,
                    group_id,
                    msg.get("channel_topic_id"),
                    force=True,
                    media_types=payload.get("media_types") or None,
                    theme_label=payload.get("theme_label") or None,
                    return_launch_info=True,
                )
                if session_id is None:
                    raise RuntimeError("Emoji Night did not start")
                if not isinstance(session_id, dict):
                    raise RuntimeError("Emoji Night did not return launch info with Telegram message_id")
                sent = SimpleNamespace(message_id=_require_message_id(session_id, "emoji_puzzle"))
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
                    # Legitimate facts no-ops raise SkippedActivity inside
                    # send_scheduled_fact; False here means a real send failure.
                    raise RuntimeError(f"facts {pool} did not send")
                sent = SimpleNamespace(message_id=1)
            elif msg.get("message_type") in {"weekly_roundup", "weekly_leaderboard"}:
                # Single source of truth: weekly_roundup/weekly_leaderboard are
                # dynamic recurring content owned by the APScheduler cron jobs in
                # bot/scheduler/jobs.py (see the design note at the top of that
                # file). They must NOT also fire from a scheduled_messages row —
                # doing both is what posted the leaderboard twice (2026-05-23).
                # Any such row (legacy, or created before the dashboard stopped
                # offering these types) self-skips here, sending nothing.
                raise SkippedActivity(
                    f"{msg.get('message_type')}: owned by cron job, not the calendar dispatcher"
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
                from .dm_menu import game_deep_link_button
                warmup_thread_id = await _game_warmup_thread_id(db, msg)
                _wu_rows = [[
                    InlineKeyboardButton("🙋 אני בפנים!", callback_data=f"trivint_{msg['id']}"),
                ]]
                _dl = game_deep_link_button(int(msg["id"]))
                if _dl:
                    _wu_rows.append([_dl])
                markup = InlineKeyboardMarkup(_wu_rows)
                sent = await safe_send(
                    bot,
                    db,
                    "send_message",
                    chat_id=group_id,
                    text=msg["text"],
                    message_thread_id=warmup_thread_id,
                    reply_markup=markup,
                )
            elif msg.get("message_type") == "warmup_reminder":
                raise SkippedActivity("warmup_reminder: public group reminders disabled; personal DM reminders handle sign-ups")
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
            if mtype in _SLOT_CLAIMING_TYPES:
                slot_claims_this_tick.add(slot_key)

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
            try:
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
            except Exception as e:  # noqa: BLE001
                logger.warning("activity_log failed for sent msg %s: %s", msg.get("id"), e)
            logger.info("Sent scheduled message %d: %s", msg["id"], msg["text"][:40])

            # Handle recurrence — create next occurrence
            try:
                recurrence = msg.get("recurrence")
                if recurrence:
                    try:
                        base_date = date.fromisoformat(str(msg.get("scheduled_date") or current_date))
                    except ValueError:
                        base_date = date.fromisoformat(current_date)
                    next_date = _next_recurrence_date_after(
                        recurrence=recurrence,
                        recurrence_days=msg.get("recurrence_days"),
                        base_date=base_date,
                        scheduled_time=msg.get("scheduled_time", "09:00"),
                        now=now,
                    )

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
            except Exception as e:  # noqa: BLE001
                logger.warning("recurrence creation failed after sent msg %s: %s", msg.get("id"), e)

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


async def cleanup_public_warmup_announcements(context: ContextTypes.DEFAULT_TYPE):
    """Delete old public sign-up announcements after the configured window.

    The database row remains sent/history. Cleanup outcome is recorded in
    error_message so failures are visible and are not retried forever.
    """
    db: Database = context.bot_data["db"]
    try:
        from bot.utils.config import get_settings as _get_settings
        minutes = int(((_get_settings().get("trivia") or {}).get("warmup_public_cleanup_minutes")) or 20)
    except Exception:
        minutes = 20
    if minutes <= 0:
        return
    rows = await db.get_warmup_announcements_due_for_cleanup(older_than_minutes=minutes)
    if not rows:
        return

    import os
    main_group = int(os.getenv("GROUP_ID", "0"))
    test_group = int(os.getenv("TEST_GROUP_ID", "0"))
    for row in rows:
        target = row.get("target_group", "main")
        chat_id = test_group if target == "test" else main_group
        message_id = int(row.get("sent_message_id") or 0)
        if not chat_id or message_id <= 0:
            await db.mark_warmup_cleanup_result(int(row["id"]), "missing-target")
            continue
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("warmup_cleanup: failed for row=%s message=%s: %s", row.get("id"), message_id, e)
            await db.mark_warmup_cleanup_result(int(row["id"]), "failed")
            continue
        await db.mark_warmup_cleanup_result(int(row["id"]), "deleted")
