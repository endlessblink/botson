"""Shared loader for `config/question_quality.md`.

Both the dashboard and the bot's materializer build LLM generation prompts
that must reference the same canonical rules. Hosting the loader here keeps
one source of truth and avoids dashboard→bot reverse imports.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_QUALITY_RULES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "question_quality.md"
_full_cache: str | None = None
_short_cache: str | None = None


def load_quality_rules() -> str:
    """Lazy-load the full rules file. Empty string if missing."""
    global _full_cache
    if _full_cache is not None:
        return _full_cache
    try:
        _full_cache = _QUALITY_RULES_PATH.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("[quality-rules] could not load %s: %s", _QUALITY_RULES_PATH, e)
        _full_cache = ""
    return _full_cache


def load_quality_rules_short() -> str:
    """"Hard rules" + "Concrete failures to refuse" sections only.

    For prompt builders under a tight token budget. Drops Anti-patterns,
    Pattern mix, Per-channel hint, and Output rules — those are guidance
    for human curators / per-row outputs and aren't load-bearing for
    short single-shot generations.
    """
    global _short_cache
    if _short_cache is not None:
        return _short_cache
    full = load_quality_rules()
    if not full:
        _short_cache = ""
        return ""
    start = full.find("## Hard rules")
    end = full.find("\n## Anti-patterns")
    if start < 0:
        start = 0
    if end < 0:
        end = len(full)
    _short_cache = full[start:end].rstrip()
    return _short_cache


def reset_cache() -> None:
    """Test hook — clears the loader cache so reloads pick up edits."""
    global _full_cache, _short_cache
    _full_cache = None
    _short_cache = None
