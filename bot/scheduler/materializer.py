"""Shared schedule materializer — single source of truth for text-content scheduling.

Both the bot and the dashboard call `compute_week_previews` to derive the set
of morning/evening/discussion slots for a given week. The bot additionally
calls `materialize_forward` on startup, on config reload, and once daily to
upsert those slots into `scheduled_messages` with created_by='auto', so the
calendar_checker job is the only code path that actually sends anything.

This eliminates the prior dual-system drift where APScheduler cron jobs and
the dashboard preview generator could disagree (e.g., the Hebrew-days bug).
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from ..database.db import Database
from ..utils.config import load_yaml, get_settings, is_auto_blocked_on, is_feature_enabled

logger = logging.getLogger(__name__)


def compute_week_previews(
    sunday_iso: str,
    committed_index: dict,
    used_discussion_texts: set[str] | None = None,
    skipped_slots: set[tuple[str, str, str]] | None = None,
) -> list[dict]:
    """Compute preview rows for a single week starting at sunday_iso (YYYY-MM-DD).

    committed_index: dict keyed by (date_iso, "HH:MM", type) -> committed row,
    used to skip slots already present in scheduled_messages.

    used_discussion_texts: optional set of discussion question strings that have
    already been sent or are still queued. Those are excluded from the
    discussion pool before picking, so a question never repeats across weeks.
    Morning/evening pools rotate deterministically and are unaffected — the
    user wants those recurring.

    skipped_slots: optional set of (date_iso, "HH:MM", type) keys that the user
    has explicitly cleared via the skip-slot UI. Those slots produce no preview.

    Returns list of dicts: {date, time, type, text, topic_id, category}.
    """
    sunday = date.fromisoformat(sunday_iso)
    used_discussion_texts = used_discussion_texts or set()
    skipped_slots = skipped_slots or set()

    settings = get_settings()
    schedule = settings.get("schedule", {})
    topic_ids = settings.get("topics", {}).get("discussions", {})
    goals_topic = settings.get("topics", {}).get("goals")

    try:
        prompts_pool = load_yaml("prompts.yaml")
    except Exception:
        prompts_pool = {}
    try:
        discussions_pool = load_yaml("discussions.yaml")
    except Exception:
        discussions_pool = {}

    morning_queue = list(prompts_pool.get("morning", []))
    evening_queue = list(prompts_pool.get("evening", []))
    active_categories = [c for c in discussions_pool if c in topic_ids and topic_ids[c]]

    previews: list[dict] = []

    # Content selection is SEEDED BY DATE, not by an in-call counter. This
    # guarantees:
    #   1. Same date always yields same content (idempotent rematerialize).
    #   2. Consecutive days never share content (as long as pool > 1 item).
    #   3. Already-committed days don't shift the rotation for other days.
    for i in range(7):
        day_date = sunday + timedelta(days=i)
        day_iso = day_date.isoformat()
        day_ord = day_date.toordinal()
        auto_blocked = is_auto_blocked_on(day_iso)

        # Morning
        if (not auto_blocked) and i in schedule.get("morning_prompt", {}).get("days", []):
            m_time = schedule["morning_prompt"].get("time", "09:00")
            slot_key = (day_iso, m_time, "morning")
            if (slot_key not in committed_index
                    and slot_key not in skipped_slots
                    and morning_queue):
                text = morning_queue[day_ord % len(morning_queue)]
                previews.append({
                    "date": day_iso, "time": m_time, "type": "morning",
                    "text": text, "topic_id": goals_topic, "category": None,
                })

        # Discussion (multiple times per day possible)
        if (not auto_blocked) and i in schedule.get("discussion_prompt", {}).get("days", []):
            times = schedule["discussion_prompt"].get("times", ["18:00"])
            for time_idx, t in enumerate(times):
                slot_key = (day_iso, t, "discussion")
                if slot_key in committed_index or slot_key in skipped_slots:
                    continue
                if not active_categories or not discussions_pool:
                    continue
                # Deterministic slot sequence: unique per (date, time_slot).
                slot_seq = day_ord * 10 + time_idx
                cat = active_categories[slot_seq % len(active_categories)]
                cat_questions = discussions_pool.get(cat, [])
                # Filter out already-used questions so discussions never repeat.
                # Uses stable-ordered list (YAML order) so the modulo pick is
                # reproducible run-to-run.
                if used_discussion_texts:
                    cat_questions = [q for q in cat_questions if q not in used_discussion_texts]
                if not cat_questions:
                    logger.warning(
                        "[materializer] discussion pool exhausted for category=%s on %s %s — "
                        "add more questions to config/discussions.yaml",
                        cat, day_iso, t,
                    )
                    continue
                q_idx = (slot_seq // len(active_categories)) % len(cat_questions)
                text = cat_questions[q_idx]
                previews.append({
                    "date": day_iso, "time": t, "type": "discussion",
                    "text": text, "topic_id": topic_ids.get(cat),
                    "category": cat,
                })

        # Evening — same date-seeded pick, offset from morning to avoid
        # accidental alignment even if both queues happen to be same length.
        if (not auto_blocked) and i in schedule.get("evening_prompt", {}).get("days", []):
            e_time = schedule["evening_prompt"].get("time", "21:00")
            slot_key = (day_iso, e_time, "evening")
            if (slot_key not in committed_index
                    and slot_key not in skipped_slots
                    and evening_queue):
                text = evening_queue[(day_ord + 3) % len(evening_queue)]
                previews.append({
                    "date": day_iso, "time": e_time, "type": "evening",
                    "text": text, "topic_id": goals_topic, "category": None,
                })

    return previews


def _feature_for_type(message_type: str) -> str:
    return {
        "morning": "morning_prompt",
        "evening": "evening_prompt",
        "discussion": "discussions",
    }.get(message_type, message_type)


async def _build_committed_index(
    db: Database, date_from: str, date_to: str
) -> tuple[dict, set[tuple[str, str, str]]]:
    """Build the (date, HH:MM, type) -> row index from existing scheduled_messages.

    Returns (committed_index, skipped_slots).

    Cancelled rows go into skipped_slots — they represent slots the user
    explicitly cleared (via skip-slot UI). The materializer must NOT refill
    them; otherwise the slot keeps coming back from the pool.

    Sent rows still count — we don't want to re-materialize an already-sent slot.
    """
    rows = await db.get_scheduled_messages(date_from, date_to, include_cancelled=True)
    index: dict = {}
    skipped: set[tuple[str, str, str]] = set()
    for r in rows:
        key = (
            r.get("scheduled_date"),
            (r.get("scheduled_time") or "")[:5],
            r.get("message_type"),
        )
        if r.get("status") == "cancelled":
            skipped.add(key)
            continue
        index[key] = r
    return index, skipped


async def materialize_forward(db: Database, days_ahead: int = 14) -> int:
    """Upsert missing morning/evening/discussion slots for the next N days.

    Only creates rows where:
      - (date, time, type) is not already in scheduled_messages (any status except cancelled)
      - the corresponding feature is enabled in settings.yaml

    Rows are tagged created_by='auto' so they can be purged on config reload
    without touching user-committed rows.

    Returns the number of rows inserted.
    """
    from datetime import date as _date, datetime as _datetime
    from zoneinfo import ZoneInfo
    _tz = ZoneInfo("Asia/Jerusalem")
    now = _datetime.now(_tz)
    today = now.date()
    current_hhmm = now.strftime("%H:%M")
    days_since_sunday = (today.weekday() + 1) % 7  # Python Mon=0, Hebrew Sun=0
    first_sunday = today - timedelta(days=days_since_sunday)
    end_date = today + timedelta(days=days_ahead)

    # Each week-iteration can touch up to sunday+6. The committed_index must
    # cover every date the loop will look at so previously-materialized rows
    # past `end_date` (from earlier runs) are still deduped — otherwise we'd
    # insert duplicates on every restart.
    last_sunday = first_sunday
    while last_sunday + timedelta(days=7) <= end_date:
        last_sunday += timedelta(days=7)
    index_end = last_sunday + timedelta(days=6)

    committed_index, skipped_slots = await _build_committed_index(
        db, first_sunday.isoformat(), index_end.isoformat()
    )

    # Load the set of discussion texts already sent or queued so we don't
    # re-pick them when materializing new weeks.
    used_discussion_texts = await db.get_used_discussion_texts()

    inserted = 0
    current_sunday = first_sunday
    while current_sunday <= end_date:
        previews = compute_week_previews(
            current_sunday.isoformat(),
            committed_index,
            used_discussion_texts,
            skipped_slots=skipped_slots,
        )
        for p in previews:
            slot_date = _date.fromisoformat(p["date"])
            # Skip past dates entirely.
            if slot_date < today:
                continue
            # Skip today's slots whose time has already passed — otherwise
            # calendar_checker would fire them immediately, sending prompts
            # hours late.
            if slot_date == today and p["time"] <= current_hhmm:
                continue

            msg_type = p["type"]
            if not is_feature_enabled(_feature_for_type(msg_type)):
                continue

            text = p["text"]
            msg_id = await db.create_scheduled_message(
                text=text,
                message_type=msg_type,
                channel_topic_id=p.get("topic_id"),
                target_group="main",
                scheduled_date=p["date"],
                scheduled_time=p["time"],
                recurrence=None,
                recurrence_days=None,
                auto_pin=(msg_type == "morning"),
                created_by="auto",
            )
            # Track in index so subsequent weeks' previews respect it.
            committed_index[(p["date"], p["time"], msg_type)] = {"id": msg_id}
            # Track discussion texts so the next week doesn't re-pick the
            # same question we just inserted for this week.
            if msg_type == "discussion":
                used_discussion_texts.add(text)
            inserted += 1

        current_sunday += timedelta(days=7)

    logger.info(
        "[materializer] materialized %d rows (today..+%d days)",
        inserted, days_ahead,
    )
    return inserted


async def purge_future_auto_rows(db: Database) -> int:
    """Cancel future created_by='auto' scheduled rows.

    Called before re-materializing on config reload so stale rows (old times,
    days, or content) don't linger. User-committed rows (created_by='dashboard'
    or anything else) are left untouched.

    Uses status='cancelled' rather than DELETE so history is preserved and
    committed_index will correctly skip them on next materialize.
    """
    from datetime import date as _date
    today_iso = _date.today().isoformat()
    count = await db.cancel_future_auto_scheduled_messages(today_iso)
    logger.info("[materializer] purged %d future auto rows", count)
    return count
