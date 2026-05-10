"""Fresh text materializer for recurring content slots.

The schedule still auto-fills morning/evening/discussion rows, but it never
copies static YAML pool entries directly. YAML pools are few-shot inspiration;
the row text must be newly generated, or the slot is skipped.
"""

from __future__ import annotations

import logging
import json
import os
import random
import shutil
from datetime import date, timedelta

from ..database.db import Database
from ..utils.config import get_settings, is_auto_blocked_on, is_feature_enabled, load_yaml
from ..utils.freshness import freshness_rejection
from ..utils.quality_rules import load_quality_rules_short
from ..utils.time_context import hebrew_day_name

logger = logging.getLogger(__name__)
_CLAUDE_CLI_TIMEOUT = 90
# T-135: per-slot generation attempts; first candidate that passes the
# freshness/quality gate wins. Trades up to 3× LLM calls in failure mode
# for a higher chance of usable text before falling through to "skip".
_QUALITY_GATE_CANDIDATES = 3


def compute_week_previews(
    sunday_iso: str,
    committed_index: dict,
    used_discussion_texts: set[str] | None = None,
    skipped_slots: set[tuple[str, str, str]] | None = None,
) -> list[dict]:
    """Return no static previews in strict freshness mode."""
    _ = (sunday_iso, committed_index, used_discussion_texts, skipped_slots)
    return []


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


async def _used_texts_for_type(db: Database, message_type: str) -> set[str]:
    async with db._db.execute(
        "SELECT DISTINCT text FROM scheduled_messages WHERE message_type = ? AND text IS NOT NULL AND text != ''",
        (message_type,),
    ) as cursor:
        rows = await cursor.fetchall()
    return {r[0] for r in rows if r[0]}


def _extract_generated_text(raw: str) -> str | None:
    text = (raw or "").strip()
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end + 1])
            if isinstance(parsed, dict):
                text = str(parsed.get("text") or "").strip()
    except Exception:
        pass
    text = text.replace('"', '').replace("'", "").strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return None
    text = "\n".join(lines[:2])
    if len(text) > 220:
        text = text[:217].rstrip() + "..."
    return text if any("\u0590" <= ch <= "\u05ff" for ch in text) else None


async def _generate_with_claude(prompt: str) -> str | None:
    import asyncio
    import pwd

    claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if claude_bin and os.path.exists(claude_bin):
        try:
            try:
                real_home = pwd.getpwuid(os.geteuid()).pw_dir
            except Exception:
                real_home = os.path.expanduser("~")
            env = {**os.environ, "HOME": real_home}
            proc = await asyncio.create_subprocess_exec(
                claude_bin,
                "-p",
                prompt,
                "--model",
                "haiku",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_CLAUDE_CLI_TIMEOUT)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                logger.warning("[materializer] claude CLI timed out; trying API fallback")
            else:
                if proc.returncode == 0 and stdout.decode(errors="ignore").strip():
                    return stdout.decode(errors="ignore")
                logger.warning("[materializer] claude CLI failed: %s", stderr.decode(errors="ignore")[:200])
        except Exception as e:
            logger.warning("[materializer] claude CLI generation failed: %s", e)

    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        logger.warning("[materializer] no Claude CLI/API available; skipping fresh slot generation")
        return None
    try:
        from ..utils.config import get_anthropic_config
        api_url, model = get_anthropic_config()
        import httpx
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                api_url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return data["content"][0]["text"].strip()
    except Exception as e:
        logger.warning("[materializer] API generation failed: %s", e)
        return None


async def _generate_fresh_text(
    message_type: str,
    *,
    category: str | None,
    examples: list[str],
    used_texts: set[str],
    scheduled_date: str,
    scheduled_time: str,
) -> str | None:
    kind_he = {
        "morning": "פתיחת יום",
        "evening": "סגירת יום",
        "discussion": f"שאלה לערוץ {category or 'דיון'}",
    }.get(message_type, message_type)
    sample = random.sample(examples, min(5, len(examples))) if examples else []
    canonical_rules = load_quality_rules_short()
    canonical_block = f"\n\n{canonical_rules}" if canonical_rules else ""
    # Anchor the prompt on the actual Hebrew day-of-week so the model
    # doesn't hallucinate "Saturday" content on a Sunday row (regression
    # observed 2026-05-10 morning slot).
    day_he = hebrew_day_name(scheduled_date) or ""
    date_line = (
        f"תאריך: {scheduled_date} (יום {day_he})" if day_he else f"תאריך: {scheduled_date}"
    )
    prompt = f"""כתוב טקסט חדש בעברית לטלגרם לקהילת מבוגרים ישראלית בלי ילדים.

סוג: {kind_he}
{date_line}
שעה: {scheduled_time}
חובה: אם הטקסט מציין יום בשבוע, חייב להיות בדיוק "{day_he}" ולא יום אחר.

דוגמאות השראה בלבד - אסור להעתיק או לפרפרז קרוב:
{chr(10).join(f'- {x}' for x in sample) if sample else '- אין'}

כבר נשלח או תוזמן - אסור לחזור:
{chr(10).join(f'- {x}' for x in list(used_texts)[:25]) if used_texts else '- אין'}

חוקי פלט:
- שורה אחת או שתיים.
- לא להעתיק אף דוגמה.
- בלי הבטחות לכפתורים או פעולות שאין בהודעה.
- פלט JSON בלבד: {{"text":"..."}}{canonical_block}
"""
    avoid = {str(x).strip() for x in used_texts}
    sources = {str(x).strip() for x in examples}
    rejections: list[str] = []
    for attempt in range(_QUALITY_GATE_CANDIDATES):
        raw = await _generate_with_claude(prompt)
        text = _extract_generated_text(raw or "")
        if not text:
            rejections.append(f"attempt {attempt + 1}: empty/invalid response")
            continue
        normalized = text.strip()
        rejection = freshness_rejection(
            normalized,
            avoid_texts=avoid | {r.split(": ", 1)[-1] for r in rejections if ": " in r},
            source_examples=sources,
            scheduled_date=scheduled_date,
        )
        if rejection:
            rejections.append(f"attempt {attempt + 1}: {rejection} (text={normalized[:80]!r})")
            continue
        if attempt > 0:
            logger.info("[materializer] quality gate accepted candidate %d/%d", attempt + 1, _QUALITY_GATE_CANDIDATES)
        return normalized
    logger.warning(
        "[materializer] all %d candidates rejected for %s @ %s %s; %s",
        _QUALITY_GATE_CANDIDATES,
        message_type,
        scheduled_date,
        scheduled_time,
        " | ".join(rejections),
    )
    return None


async def materialize_forward(db: Database, days_ahead: int = 14) -> int:
    """Generate fresh morning/evening/discussion rows for the next N days."""
    from datetime import datetime as _datetime
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Jerusalem")
    now = _datetime.now(tz)
    today = now.date()
    current_hhmm = now.strftime("%H:%M")
    days_since_sunday = (today.weekday() + 1) % 7
    first_sunday = today - timedelta(days=days_since_sunday)
    end_date = today + timedelta(days=days_ahead)
    last_sunday = first_sunday
    while last_sunday + timedelta(days=7) <= end_date:
        last_sunday += timedelta(days=7)
    index_end = last_sunday + timedelta(days=6)

    committed_index, skipped_slots = await _build_committed_index(
        db, first_sunday.isoformat(), index_end.isoformat()
    )
    settings = get_settings()
    schedule = settings.get("schedule", {}) or {}
    topics = settings.get("topics", {}) or {}
    goals_topic = topics.get("goals")
    topic_ids = topics.get("discussions", {}) or {}
    try:
        prompts_pool = load_yaml("prompts.yaml") or {}
    except Exception:
        prompts_pool = {}
    try:
        discussions_pool = load_yaml("discussions.yaml") or {}
    except Exception:
        discussions_pool = {}

    used_by_type = {
        "morning": await _used_texts_for_type(db, "morning"),
        "evening": await _used_texts_for_type(db, "evening"),
        "discussion": await db.get_used_discussion_texts(),
    }
    inserted = 0
    day = today
    while day <= end_date:
        day_iso = day.isoformat()
        day_idx = (day.weekday() + 1) % 7
        if is_auto_blocked_on(day_iso):
            day += timedelta(days=1)
            continue

        candidates: list[dict] = []
        if day_idx in (schedule.get("morning_prompt") or {}).get("days", []):
            candidates.append({
                "type": "morning",
                "time": str((schedule.get("morning_prompt") or {}).get("time") or "09:00")[:5],
                "topic": goals_topic,
                "examples": list(prompts_pool.get("morning") or []),
                "category": None,
            })
        if day_idx in (schedule.get("evening_prompt") or {}).get("days", []):
            candidates.append({
                "type": "evening",
                "time": str((schedule.get("evening_prompt") or {}).get("time") or "21:00")[:5],
                "topic": goals_topic,
                "examples": list(prompts_pool.get("evening") or []),
                "category": None,
            })
        if day_idx in (schedule.get("discussion_prompt") or {}).get("days", []):
            active_categories = [c for c in discussions_pool if c in topic_ids and topic_ids[c]]
            times = (schedule.get("discussion_prompt") or {}).get("times") or ["18:00"]
            for time_idx, time_s in enumerate(times):
                if not active_categories:
                    continue
                cat = active_categories[(day.toordinal() * 10 + time_idx) % len(active_categories)]
                candidates.append({
                    "type": "discussion",
                    "time": str(time_s)[:5],
                    "topic": topic_ids.get(cat),
                    "examples": list(discussions_pool.get(cat) or []),
                    "category": cat,
                })

        for candidate in candidates:
            msg_type = candidate["type"]
            time_s = candidate["time"]
            key = (day_iso, time_s, msg_type)
            if key in committed_index or key in skipped_slots:
                continue
            if day == today and time_s <= current_hhmm:
                continue
            if not candidate.get("topic"):
                continue
            if not is_feature_enabled(_feature_for_type(msg_type)):
                continue
            text = await _generate_fresh_text(
                msg_type,
                category=candidate.get("category"),
                examples=candidate.get("examples") or [],
                used_texts=used_by_type[msg_type],
                scheduled_date=day_iso,
                scheduled_time=time_s,
            )
            if not text:
                continue
            msg_id = await db.create_scheduled_message(
                text=text,
                message_type=msg_type,
                channel_topic_id=candidate.get("topic"),
                target_group="main",
                scheduled_date=day_iso,
                scheduled_time=time_s,
                recurrence=None,
                recurrence_days=None,
                auto_pin=(msg_type == "morning"),
                created_by="auto",
            )
            committed_index[key] = {"id": msg_id}
            used_by_type[msg_type].add(text)
            inserted += 1
        day += timedelta(days=1)

    logger.info("[materializer] generated %d fresh auto rows (today..+%d days)", inserted, days_ahead)
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
