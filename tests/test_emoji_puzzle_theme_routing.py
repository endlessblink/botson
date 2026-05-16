"""Guardian: per-puzzle prompt text must match the puzzle's media_type.

BUG-1 (2026-05-17): a music-themed Emoji Night round shipped 5 music puzzles
but every per-puzzle message asked "איזה סרט או סדרה זה?" (which movie or TV
show is this?). The intro and pool query were correct; only the per-puzzle
wrapper text was hardcoded. This guardian locks the fix in place.
"""

from bot.handlers.emoji_puzzle import _format_puzzle_text


def _puzzle(emoji_prompt: str, media_type: str) -> dict:
    return {"id": 1, "emoji_prompt": emoji_prompt, "media_type": media_type}


def test_song_puzzle_asks_about_song_not_movie():
    text = _format_puzzle_text(_puzzle("👑🎸🥁", "song"), 1, 5)
    assert "שיר" in text or "אמן" in text, text
    assert "סרט" not in text and "סדרה" not in text, text


def test_music_alias_routes_to_song_wording():
    """Prod DB has rows with media_type='music' (non-canonical). The
    formatter must still produce music wording, not the general fallback.
    """
    text = _format_puzzle_text(_puzzle("👑🎸🥁", "music"), 1, 5)
    assert "שיר" in text or "אמן" in text, text
    assert "סרט" not in text and "סדרה" not in text, text


def test_tv_alias_routes_to_series_wording():
    text = _format_puzzle_text(_puzzle("👨‍👩‍👧‍👦🏠", "tv"), 1, 3)
    assert "סדרה" in text, text


def test_movies_alias_routes_to_movie_wording():
    text = _format_puzzle_text(_puzzle("🦁👑", "movies"), 1, 3)
    assert "סרט" in text, text


def test_movie_puzzle_asks_about_movie():
    text = _format_puzzle_text(_puzzle("🦁👑", "movie"), 2, 5)
    assert "סרט" in text, text
    assert "שיר" not in text, text


def test_tv_puzzle_asks_about_series():
    text = _format_puzzle_text(_puzzle("👨‍👩‍👧‍👦🏠", "tv"), 1, 3)
    assert "סדרה" in text, text


def test_book_puzzle_asks_about_book():
    text = _format_puzzle_text(_puzzle("📖🧙‍♂️", "book"), 1, 3)
    assert "ספר" in text, text


def test_unknown_media_type_falls_back_to_general():
    text = _format_puzzle_text(_puzzle("🤷", "made-up-category"), 1, 1)
    # General fallback must not be a category-specific question.
    assert "סרט" not in text and "סדרה" not in text and "שיר" not in text, text


def test_missing_media_type_falls_back_to_general():
    text = _format_puzzle_text({"id": 1, "emoji_prompt": "🤷"}, 1, 1)
    assert "סרט" not in text and "סדרה" not in text and "שיר" not in text, text


def test_header_includes_index_and_total():
    text = _format_puzzle_text(_puzzle("👑🎸🥁", "song"), 3, 5)
    assert "3/5" in text, text


def test_emoji_prompt_is_present():
    text = _format_puzzle_text(_puzzle("👑🎸🥁", "song"), 1, 5)
    assert "👑🎸🥁" in text, text


# ── Normalization endpoint coverage ──────────────────────────

import asyncio  # noqa: E402

from bot.database.db import Database  # noqa: E402


def test_normalize_emoji_puzzle_media_types_rewrites_aliases():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            await db.create_emoji_puzzle("👑🎸🥁", "Queen", "Queen", media_type="music")
            await db.create_emoji_puzzle("📺", "Friends", "Friends", media_type="tv")
            await db.create_emoji_puzzle("🦁", "Lion King", "Lion King", media_type="movies")
            await db.create_emoji_puzzle("📖", "Hobbit", "Hobbit", media_type="general")
            report = await db.normalize_emoji_puzzle_media_types()
            after = report["after"]
            return report, after
        finally:
            await db.close()

    report, after = asyncio.run(run())
    assert report["updated"] == 3, report
    assert after.get("song") == 1, after
    assert after.get("series") == 1, after
    assert after.get("movie") == 1, after
    assert after.get("general") == 1, after
    assert "music" not in after and "tv" not in after and "movies" not in after, after


def test_normalize_emoji_puzzle_media_types_is_idempotent():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            await db.create_emoji_puzzle("👑🎸🥁", "Queen", "Queen", media_type="music")
            first = await db.normalize_emoji_puzzle_media_types()
            second = await db.normalize_emoji_puzzle_media_types()
            return first, second
        finally:
            await db.close()

    first, second = asyncio.run(run())
    assert first["updated"] == 1
    assert second["updated"] == 0, second
