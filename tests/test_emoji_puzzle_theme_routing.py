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


async def _insert_legacy_puzzle(db: Database, media_type: str) -> int:
    """Bypass create_emoji_puzzle's canonicalization to seed legacy/dirty
    rows the way prod has them. Simulates pre-fix data."""
    async with db._db.execute(  # noqa: SLF001
        "INSERT INTO emoji_puzzles (emoji_prompt, answer_he, answer_en, "
        "aliases, difficulty, media_type, enabled, created_at) "
        "VALUES ('x','x','x','[]', 2, ?, 1, datetime('now'))",
        (media_type,),
    ) as cur:
        pid = cur.lastrowid
    await db._db.commit()  # noqa: SLF001
    return int(pid or 0)


def test_normalize_emoji_puzzle_media_types_rewrites_legacy_aliases():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            await _insert_legacy_puzzle(db, "music")
            await _insert_legacy_puzzle(db, "tv")
            await _insert_legacy_puzzle(db, "movies")
            await _insert_legacy_puzzle(db, "general")
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


def test_create_emoji_puzzle_canonicalizes_at_db_layer():
    """Chokepoint test: the Database method itself canonicalizes media_type,
    so every caller (dashboard endpoints, bulk import, AI seed, future
    paths) gets clean writes regardless of what they pass.
    """
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            pid_music = await db.create_emoji_puzzle("👑🎸🥁", "Queen", "Queen", media_type="music")
            pid_tv = await db.create_emoji_puzzle("📺", "Friends", "Friends", media_type="tv")
            pid_movies = await db.create_emoji_puzzle("🦁", "Lion", "Lion", media_type="movies")
            pid_empty = await db.create_emoji_puzzle("🤷", "X", "X", media_type="")
            rows = await db.list_emoji_puzzles()
            by_id = {r["id"]: r["media_type"] for r in rows}
            return by_id, pid_music, pid_tv, pid_movies, pid_empty
        finally:
            await db.close()

    by_id, pid_music, pid_tv, pid_movies, pid_empty = asyncio.run(run())
    assert by_id[pid_music] == "song", by_id
    assert by_id[pid_tv] == "series", by_id
    assert by_id[pid_movies] == "movie", by_id
    assert by_id[pid_empty] == "general", by_id


def test_update_emoji_puzzle_canonicalizes_at_db_layer():
    """Same chokepoint for UPDATE: editing a row to a legacy alias must
    still land in canonical form."""
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            pid = await db.create_emoji_puzzle("👑", "Queen", "Queen", media_type="general")
            await db.update_emoji_puzzle(pid, media_type="music")
            row = await db.get_emoji_puzzle(pid)
            return row
        finally:
            await db.close()

    row = asyncio.run(run())
    assert row["media_type"] == "song", row


def test_normalize_emoji_puzzle_media_types_is_idempotent():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            await _insert_legacy_puzzle(db, "music")
            first = await db.normalize_emoji_puzzle_media_types()
            second = await db.normalize_emoji_puzzle_media_types()
            return first, second
        finally:
            await db.close()

    first, second = asyncio.run(run())
    assert first["updated"] == 1
    assert second["updated"] == 0, second
