"""Single source of truth for game category / media_type taxonomies.

Every interactive game in this repo (emoji_puzzle, trivia, and any
future game type) MUST route its category/media_type values through
this module before rendering user-facing text, querying pools, or
storing in poll_options. Drift between "what the announcement says"
and "what the pool query filters by" is the BUG-1 (2026-05-17) class
of regression.

Two complementary tools live here:

  * canonicalize(category, taxonomy) — map any historical alias to the
    canonical token for that game type. Idempotent.

  * EMOJI_PUZZLE_TAXONOMY / TRIVIA_TAXONOMY — explicit lists of the
    canonical tokens each game accepts. New games add a new TAXONOMY
    constant here, not in their handler.

Companion guardian: tests/test_no_hardcoded_game_categories.py prevents
any bot/handlers/<game>.py file from carrying category-specific Hebrew
literals (e.g., 'איזה סרט או סדרה זה?') outside config. The fix lives
in config/settings.yaml:copy.<game>.*, not in code.
"""
from __future__ import annotations

# Canonical tokens accepted by emoji_puzzle.media_type.
# Matches the dropdown in dashboard/templates/puzzles.html.
EMOJI_PUZZLE_TAXONOMY: tuple[str, ...] = (
    "general",
    "movie",
    "series",
    "book",
    "song",
    "game",
)

# Alias map for emoji puzzle media_type. Keys are values found in
# production (operator edits, AI populate, legacy seeds); values are
# the canonical token from EMOJI_PUZZLE_TAXONOMY. Add to this when a
# new alias is discovered — never branch on it inside a handler.
EMOJI_PUZZLE_ALIASES: dict[str, str] = {
    "": "general",
    "music": "song",
    "songs": "song",
    "tv": "series",
    "shows": "series",
    "show": "series",
    "movies": "movie",
    "film": "movie",
    "films": "movie",
    "books": "book",
    "games": "game",
}


def canonical_emoji_media_type(value: str | None) -> str:
    """Map an emoji puzzle media_type to its canonical token.

    Unknown values pass through (so a new category added to puzzles.html
    works immediately); empty / None becomes 'general'. Lowercase +
    trimmed before lookup so 'Music', ' music ', 'MUSIC' all collapse.
    """
    m = str(value or "").strip().lower()
    return EMOJI_PUZZLE_ALIASES.get(m, m) or "general"


# Trivia uses Hebrew category names sourced from the operator-curated
# config/trivia.yaml; there is intentionally no canonical taxonomy list
# here because the operator owns that file as data. Future games that
# add an English-token taxonomy should follow the EMOJI_PUZZLE_TAXONOMY
# pattern above (canonical tuple + alias dict + canonical_<game>() fn).
