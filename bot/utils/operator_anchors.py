"""Shared loader for operator-curated good/bad anchor examples.

Gap 3b (2026-05-18): both the dashboard's `_active_style_profile_block_sync`
and the bot-side prompt builders (`bot/scheduler/materializer.py`,
`dashboard/app.py:_generate_activity_copy`) must inject the same canonized
anchors. Hosting the reader here keeps one source of truth across the two
processes and avoids a dashboard→bot reverse import.

The canonical sections live in `config/operator_prefs.md`:
  - `### Good examples — Hebrew content`
  - `### Bad examples — Hebrew content`

Each is a flat list of `- ...` bullets, capped around 15 entries (the
diminishing-returns ceiling for few-shot examples). 60-second mtime-based
cache; safe to call from every prompt build.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_PREFS_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "operator_prefs.md"
_GOOD_HEADING = "### Good examples — Hebrew content"
_BAD_HEADING = "### Bad examples — Hebrew content"
_TTL_SECONDS = 60.0

_cache: dict = {
    "mtime": 0.0,
    "loaded_at": 0.0,
    "good": [],
    "bad": [],
}


def _split_at_section(text: str, heading: str) -> str:
    """Return the body of the `heading` section (between the heading line
    and the next h3/h2), or empty string if absent. Start-of-line match only.
    """
    needle = "\n" + heading
    if text.startswith(heading):
        heading_end = len(heading)
    else:
        idx = text.find(needle)
        if idx < 0:
            return ""
        heading_end = idx + 1 + len(heading)
    after = text[heading_end:]
    next_h3 = after.find("\n### ")
    next_h2 = after.find("\n## ")
    end = -1
    for cand in (next_h3, next_h2):
        if cand >= 0 and (end < 0 or cand < end):
            end = cand
    return after if end < 0 else after[:end]


def _load() -> None:
    now = time.monotonic()
    try:
        st = _PREFS_PATH.stat()
    except FileNotFoundError:
        return
    if (now - _cache["loaded_at"] < _TTL_SECONDS
            and _cache["mtime"] == st.st_mtime):
        return
    try:
        text = _PREFS_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[operator-anchors] read failed: %s", e)
        return
    def _bullets(body: str) -> list[str]:
        return [ln.strip() for ln in body.splitlines() if ln.strip().startswith("- ")]
    _cache["good"] = _bullets(_split_at_section(text, _GOOD_HEADING))
    _cache["bad"] = _bullets(_split_at_section(text, _BAD_HEADING))
    _cache["mtime"] = st.st_mtime
    _cache["loaded_at"] = now


def load_anchors() -> tuple[list[str], list[str]]:
    """Return (good_bullets, bad_bullets). Empty lists if file missing."""
    _load()
    return list(_cache["good"]), list(_cache["bad"])


def render_anchor_block() -> str:
    """Hebrew prompt fragment with both labelled blocks, ready to append.

    Returns "" when both sections are empty so callers can no-op concat.
    Format mirrors the dashboard-side `_active_style_profile_block_sync`
    so the operator sees consistent labels across surfaces.
    """
    good, bad = load_anchors()
    if not good and not bad:
        return ""
    from bot.utils.copy import load_copy
    parts: list[str] = []
    if good:
        header = load_copy(
            "planner", "good_anchors_header",
            default="✓ דוגמאות אנקור — זה הכיוון, חקה את הטון:",  # noqa: hardcoded-content (Hebrew header, fallback only)
        )
        parts.append(header + "\n" + "\n".join(good))
    if bad:
        header = load_copy(
            "planner", "bad_anchors_header",
            default="✗ דוגמאות אנקור — אסור לשחזר:",  # noqa: hardcoded-content (Hebrew header, fallback only)
        )
        parts.append(header + "\n" + "\n".join(bad))
    return "\n\n".join(parts)


def reset_cache() -> None:
    """Test hook — clears the loader cache so reloads pick up edits."""
    _cache["mtime"] = 0.0
    _cache["loaded_at"] = 0.0
    _cache["good"] = []
    _cache["bad"] = []
