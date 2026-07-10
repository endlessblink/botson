"""Emoji Night ran a music theme three times in a row (Jul 6, 7, 8 2026).

The rotation in `_ai_suggest_calendar` avoids recently-used themes, but the
Jul 6 music game was skipped by the RSVP gate. `_recent_emoji_signatures`
only looked at rows with status sent/scheduled/draft, so the skipped row was
invisible, the recency list came back empty, ranking fell through to
`random.random()`, and music was picked again. Twice.

Three defects, one loop:
  1. 'skipped' excluded from the recency window (the trigger).
  2. the recency rank key was inverted — among used themes it preferred the
     most recently used one.
  3. the signature helper hand-rolled `tv -> series` and missed
     `music -> song`, so alias rows never matched.

These tests drive the real `_ai_suggest_calendar` against a seeded DB.
"""

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import dashboard.app as dashboard_app
from bot.database.db import Database


def _emoji_suggestions(result: dict) -> list[dict]:
    return [s for s in result["suggestions"] if s["message_type"] == "emoji_puzzle"]


def _themes(result: dict) -> list[tuple[str, ...]]:
    """media_types per suggested emoji row, newest-scheduled first.

    The payload lives under `poll_options_json` (a JSON string), not
    `poll_options` — reading the wrong key silently yields empty tuples and
    makes every "theme X was not re-picked" assertion pass vacuously.
    """
    out = []
    for row in _emoji_suggestions(result):
        payload = json.loads(row.get("poll_options_json") or "{}")
        media = tuple(payload.get("media_types") or [])
        assert media, f"emoji suggestion carried no media_types: {row}"
        out.append(media)
    return out


class EmojiThemeRotationTest(unittest.IsolatedAsyncioTestCase):
    async def _seeded_db(self) -> Database:
        """A pool with enough puzzles in several themes, so the rotation has
        a real choice and never falls back to a single-option pool."""
        db = Database(":memory:")
        await db.init()
        for media in ("song", "movie", "series", "game"):
            for idx in range(8):
                await db.create_emoji_puzzle(
                    f"{media}-{idx}", f"תשובה {media} {idx}", f"Answer {media} {idx}",
                    media_type=media,
                )
        return db

    async def _add_emoji_row(self, db: Database, *, media: str, status: str, days_ago: int):
        day = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        await db._db.execute(
            """INSERT INTO scheduled_messages
               (message_type, text, scheduled_date, scheduled_time, status, poll_options,
                channel_topic_id, target_group)
               VALUES ('emoji_puzzle', '', ?, '22:00', ?, ?, 4037, 'main')""",
            (day, status, json.dumps({"media_types": [media], "theme_label": media})),
        )
        await db._db.commit()

    async def _suggest(self, db: Database) -> dict:
        """Run the real suggest engine with the random tiebreak pinned.

        The ranking ends in `random.random()`. Left live, a buggy ranking still
        avoids the repeated theme ~3 times in 4 and the test passes by luck.
        Pinning it to a constant makes `sorted` stable, so candidates keep pool
        order (song is seeded first) and a theme that the rotation *should*
        have demoted will deterministically win if the ranking is wrong.
        """
        async def scaffold(*a, **k):
            return "שאלה תקינה לגמרי שאין בה שום בעיה?", []

        with (
            patch.object(dashboard_app, "_generate_with_fallbacks", new=AsyncMock(side_effect=scaffold)),
            patch.object(dashboard_app, "_render_group_stats_context", new=AsyncMock(return_value="")),
            patch.object(dashboard_app.random, "random", return_value=0.0),
        ):
            return await dashboard_app._ai_suggest_calendar(db, target_date=None, week_offset=1)

    async def test_skipped_game_still_counts_as_a_recently_used_theme(self):
        """The Jul 6 reproduction: the only recent emoji row is a SKIPPED
        music game. Its theme was already announced to the group, so the
        rotation must not pick music again."""
        db = await self._seeded_db()
        try:
            await self._add_emoji_row(db, media="song", status="skipped", days_ago=1)
            result = await self._suggest(db)
            themes = _themes(result)
        finally:
            await db.close()

        self.assertTrue(themes, "expected at least one emoji suggestion")
        # Only the FIRST pick is asserted: once the other themes have run,
        # music legitimately rotates back in later in the week.
        self.assertNotIn(
            "song", themes[0],
            f"a skipped music night must still block music from the next pick; got {themes[0]}",
        )

    async def test_music_alias_row_blocks_the_canonical_song_theme(self):
        """Legacy rows carry media_types=['music']. Before canonicalization the
        signature was ('music',) and never matched the ('song',) candidate."""
        db = await self._seeded_db()
        try:
            await self._add_emoji_row(db, media="music", status="sent", days_ago=1)
            result = await self._suggest(db)
            themes = _themes(result)
        finally:
            await db.close()

        self.assertTrue(themes)
        self.assertNotIn(
            "song", themes[0],
            f"the 'music' alias must block the canonical 'song' theme; got {themes[0]}",
        )

    async def test_least_recently_used_theme_wins_over_the_most_recent(self):
        """When every theme has run, the rotation must reach for the oldest.

        movie ran yesterday, series 5 days ago, game 10, song 15. With the
        inverted rank key the pick was movie (the most recent). It must be
        song (the least recent).
        """
        db = await self._seeded_db()
        try:
            await self._add_emoji_row(db, media="movie", status="sent", days_ago=1)
            await self._add_emoji_row(db, media="series", status="sent", days_ago=5)
            await self._add_emoji_row(db, media="game", status="sent", days_ago=10)
            await self._add_emoji_row(db, media="song", status="sent", days_ago=15)
            result = await self._suggest(db)
            themes = _themes(result)
        finally:
            await db.close()

        self.assertTrue(themes)
        self.assertIn(
            "song", themes[0],
            f"least-recently-used theme must win; got {themes[0]}",
        )

    async def test_no_history_still_produces_a_theme(self):
        """Empty recency list must not crash or return a blank subject."""
        db = await self._seeded_db()
        try:
            result = await self._suggest(db)
            themes = _themes(result)
        finally:
            await db.close()

        self.assertTrue(themes)
        for theme in themes:
            self.assertTrue(theme, "a theme must always be chosen when the pool allows")

    async def test_theme_labels_come_from_config_not_code(self):
        """Hebrew labels must resolve from settings.yaml copy.emoji_puzzle.*"""
        from bot.utils.copy import load_copy
        for canonical in ("movie", "series", "book", "song", "game", "general"):
            label = load_copy("emoji_puzzle", f"theme_{canonical}")
            self.assertFalse(
                label.startswith("[copy missing"),
                f"missing config key copy.emoji_puzzle.theme_{canonical}",
            )


if __name__ == "__main__":
    unittest.main()
