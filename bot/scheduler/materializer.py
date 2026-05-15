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
from ..utils.copy import load_copy
from ..utils.freshness import freshness_rejection
from ..utils.quality_rules import load_quality_rules_short
from ..utils.time_context import hebrew_day_name

logger = logging.getLogger(__name__)
_CLAUDE_CLI_TIMEOUT = 90


def _materializer_setting(key: str, default):
    """Read materializer-specific knobs from settings.materializer.*."""
    try:
        block = (get_settings().get("materializer") or {})
    except Exception:
        return default
    value = block.get(key)
    return value if value is not None else default


def _quality_gate_candidates() -> int:
    # T-135 default kept at 3; operator can raise via settings.materializer.retry_budget.
    return int(_materializer_setting("retry_budget", 3))


def _used_texts_window_days() -> int:
    # T-169: bound dedup history to a window so the LLM gets a complete
    # "do not repeat" list it can actually reason about, instead of the
    # legacy `list(set)[:25]` slice that dropped older repeats silently.
    return int(_materializer_setting("used_texts_window_days", 45))


def _examples_per_prompt() -> int:
    # T-169: fewer in-prompt examples = less verbatim echo. Default 2.
    return int(_materializer_setting("examples_per_prompt", 2))


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


async def _used_texts_for_type(
    db: Database, message_type: str, *, window_days: int | None = None
) -> list[str]:
    """Return distinct texts for `message_type`, most-recent-first.

    T-169: this used to return an unbounded `set[str]` from which callers
    sliced `[:25]` via non-deterministic set iteration — the slice silently
    dropped older repeats so the same questions cycled every 7 days. Now
    ordered by recency and bounded by `window_days` (default from
    `settings.materializer.used_texts_window_days`).
    """
    window = int(window_days if window_days is not None else _used_texts_window_days())
    if window <= 0:
        query = (
            "SELECT text, MAX(COALESCE(created_at, scheduled_date)) AS last_at "
            "FROM scheduled_messages "
            "WHERE message_type = ? AND text IS NOT NULL AND text != '' "
            "GROUP BY text ORDER BY last_at DESC"
        )
        params: tuple = (message_type,)
    else:
        cutoff = (date.today() - timedelta(days=window)).isoformat()
        query = (
            "SELECT text, MAX(COALESCE(created_at, scheduled_date)) AS last_at "
            "FROM scheduled_messages "
            "WHERE message_type = ? AND text IS NOT NULL AND text != '' "
            "AND COALESCE(scheduled_date, '') >= ? "
            "GROUP BY text ORDER BY last_at DESC"
        )
        params = (message_type, cutoff)
    async with db._db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
    seen: set[str] = set()
    ordered: list[str] = []
    for r in rows:
        text = r[0]
        if text and text not in seen:
            seen.add(text)
            ordered.append(text)
    return ordered


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
    return text if any(0x0590 <= ord(ch) <= 0x05FF for ch in text) else None


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
    used_texts: list[str] | set[str],
    scheduled_date: str,
    scheduled_time: str,
) -> str | None:
    default_category = load_copy("materializer", "discussion_default_category", default="discussion")
    kind_he = {
        "morning": load_copy("materializer", "kind_morning", default="morning"),
        "evening": load_copy("materializer", "kind_evening", default="evening"),
        "discussion": load_copy(
            "materializer", "kind_discussion", default="discussion {category}",
            category=category or default_category,
        ),
    }.get(message_type, message_type)
    # T-169: fewer in-prompt examples (default 2, was 5). Each shot is an
    # anchor the model gravitates toward; cap the bias surface.
    shots = _examples_per_prompt()
    sample = random.sample(examples, min(shots, len(examples))) if examples else []
    canonical_rules = load_quality_rules_short()
    canonical_block = f"\n\n{canonical_rules}" if canonical_rules else ""
    # Anchor the prompt on the actual Hebrew day-of-week so the model
    # doesn't hallucinate "Saturday" content on a Sunday row (regression
    # observed 2026-05-10 morning slot).
    day_he = hebrew_day_name(scheduled_date) or ""
    date_line = load_copy(
        "materializer", "date_with_day", default="date: {date} ({day})",
        date=scheduled_date, day=day_he,
    ) if day_he else load_copy(
        "materializer", "date_without_day", default="date: {date}", date=scheduled_date,
    )
    no_examples = load_copy("materializer", "no_examples", default="none")
    examples_block = chr(10).join(f"- {x}" for x in sample) if sample else f"- {no_examples}"
    # T-169: the previous `list(set)[:25]` slice was the proximate cause of
    # 7-day cycling — set iteration order silently dropped older repeats so
    # the LLM never saw them in the "do not repeat" block. Pass the full
    # ordered window now (most-recent-first); the freshness gate already
    # protects against prompt bloat by running post-hoc.
    used_iter = list(used_texts) if used_texts else []
    used_block = chr(10).join(f"- {x}" for x in used_iter) if used_iter else f"- {no_examples}"
    prompt = load_copy(
        "materializer", "prompt", default="{kind}\n{date_line}\n{time}\n{examples}\n{used_texts}{canonical_block}",
        kind=kind_he,
        date_line=date_line,
        time=scheduled_time,
        day=day_he,
        examples=examples_block,
        used_texts=used_block,
        canonical_block=canonical_block,
    )
    avoid = {str(x).strip() for x in used_texts}
    sources = {str(x).strip() for x in examples}
    rejections: list[str] = []
    gate_candidates = _quality_gate_candidates()
    for attempt in range(gate_candidates):
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
            logger.info("[materializer] quality gate accepted candidate %d/%d", attempt + 1, gate_candidates)
        return normalized
    logger.warning(
        "[materializer] all %d candidates rejected for %s @ %s %s; %s",
        gate_candidates,
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
            # Prepend the freshly-inserted text so it appears most-recent-first
            # for the next slot in this same materialization pass.
            used_by_type[msg_type].insert(0, text)
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
