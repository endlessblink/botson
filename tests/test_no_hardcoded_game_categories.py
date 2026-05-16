"""Cross-game guardian: no category-specific Hebrew literals in game handlers.

BUG-1 (2026-05-17): a music-themed Emoji Night shipped puzzles whose
per-puzzle wrapper text said "איזה סרט או סדרה זה?" because that
string was hardcoded in bot/handlers/emoji_puzzle.py. The fix moved
all per-category wording into config/settings.yaml:copy.<game>.* with
keys suffixed by the canonical media_type.

This guardian generalizes the protection: every interactive game's
handler (current emoji_puzzle / trivia / trivia_round AND any future
game handler matching the GAME_HANDLER_PATTERNS) must keep its
category-specific Hebrew text in config, not source. Inline categorical
literals are forbidden; non-categorical literals (UI chrome like
"חידת אימוג'י", standard prefixes) are allowed.

How to add a new game handler:
  1. Add the file's basename to GAME_HANDLER_PATTERNS below.
  2. Put per-category wording into config/settings.yaml:copy.<game>.*
     keyed by the canonical token from bot.utils.game_categories.
  3. Use load_copy() at render time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
HANDLERS_DIR = REPO_ROOT / "bot" / "handlers"

# Add new game handler basenames here when adding a new game. The
# guardian then enforces the same rule for them automatically.
GAME_HANDLER_PATTERNS: tuple[str, ...] = (
    "emoji_puzzle.py",
    "trivia.py",
    "trivia_round.py",
    "trivia_interest.py",
)

# Category-specific Hebrew tokens that must NEVER appear as literals in
# game handler source (case-insensitive). Each entry is a substring; a
# match means the file embedded a category-specific phrase that should
# have come from config.copy.<game>.question_<canonical>.
CATEGORICAL_TOKENS: tuple[str, ...] = (
    "איזה סרט",          # which movie
    "איזו סדרה",          # which series
    "סרט או סדרה",       # movie or TV show
    "איזה שיר",           # which song
    "איזה אמן",           # which artist
    "איזה ספר",           # which book
    "איזה משחק",          # which game (the noun, not the verb)
)


def _strip_strings_and_comments(text: str) -> tuple[str, str]:
    """Return (sources_only, strings_only) for crude scanning.

    Lazy heuristic — fine for guardian purposes; we only care about
    presence of forbidden substrings, not exact AST positions.
    """
    return text, text


@pytest.mark.parametrize("handler", GAME_HANDLER_PATTERNS)
def test_game_handler_has_no_hardcoded_category_text(handler: str) -> None:
    path = HANDLERS_DIR / handler
    if not path.exists():
        pytest.skip(f"{handler} not present in repo yet")
    source = path.read_text(encoding="utf-8")
    offenders: list[str] = []
    for token in CATEGORICAL_TOKENS:
        # Allow tokens that appear inside a `# noqa: hardcoded-content`
        # comment line — operator-acknowledged exceptions.
        for line_no, line in enumerate(source.splitlines(), start=1):
            if token in line and "# noqa: hardcoded-content" not in line:
                offenders.append(f"{handler}:{line_no}: {line.strip()[:120]}")
    assert not offenders, (
        "Categorical Hebrew literal in game handler — move to "
        "config/settings.yaml:copy.<game>.* and read with load_copy(). "
        f"Offenders:\n  " + "\n  ".join(offenders)
    )


def test_emoji_puzzle_handler_imports_canonical_helper() -> None:
    """The shared taxonomy lives in bot.utils.game_categories — emoji_puzzle
    must route through it, not branch on raw media_type strings."""
    src = (HANDLERS_DIR / "emoji_puzzle.py").read_text(encoding="utf-8")
    assert "canonical_emoji_media_type" in src, (
        "emoji_puzzle.py must use canonical_emoji_media_type() from "
        "bot.utils.game_categories to normalize media_type before lookup."
    )


def test_game_categories_module_exposes_canonical_set() -> None:
    """Future-proof: any game adding a new taxonomy declares the canonical
    tokens here so handlers + guardians + dashboard all see the same list."""
    from bot.utils.game_categories import (
        EMOJI_PUZZLE_ALIASES,
        EMOJI_PUZZLE_TAXONOMY,
        canonical_emoji_media_type,
    )

    # Every alias must point at a canonical token.
    for alias, canonical in EMOJI_PUZZLE_ALIASES.items():
        assert canonical in EMOJI_PUZZLE_TAXONOMY, (
            f"Alias {alias!r} → {canonical!r} not in EMOJI_PUZZLE_TAXONOMY"
        )

    # Identity round-trip for all canonical tokens.
    for token in EMOJI_PUZZLE_TAXONOMY:
        assert canonical_emoji_media_type(token) == token, token

    # Empty + None collapse to general.
    assert canonical_emoji_media_type(None) == "general"
    assert canonical_emoji_media_type("") == "general"
    assert canonical_emoji_media_type("   ") == "general"
