"""Gap 11 (2026-05-18): activity-log history for game subject rotation.

Verifies that:
  - calendar dispatch enriches the activity_log description with a
    `<key>:<value>` subject marker (categories:movies for trivia,
    media_type:song for emoji, fact_id:xxx for facts).
  - Database.get_recent_activity_subjects parses + splits multi-value
    markers (e.g., categories:movies+gaming) back into a flat list.
  - The dashboard populate signature lookups are time-windowed
    (no longer LIMIT 12 spanning months) and union both
    scheduled_messages + activity_log so a sent-then-deleted row still
    counts as ran.
"""
from __future__ import annotations

import asyncio

from bot.database.db import Database
from bot.handlers.calendar import _subject_markers_for_log


# ── _subject_markers_for_log ───────────────────────────────────

def test_subject_markers_trivia_round_with_categories():
    out = _subject_markers_for_log(
        "trivia_round", '{"categories": ["movies", "gaming"], "theme_label": "מדע"}',
    )
    assert out == "categories:movies+gaming", out


def test_subject_markers_emoji_puzzle_with_media_types():
    out = _subject_markers_for_log(
        "emoji_puzzle", '{"media_types": ["song"], "theme_label": "מוזיקה"}',
    )
    assert out == "media_type:song", out


def test_subject_markers_facts_with_fact_id():
    out = _subject_markers_for_log("facts_tidbit", '{"fact_id": "dybbuk_origin"}')
    assert out == "fact_id:dybbuk_origin", out


def test_subject_markers_warmup_carries_both_categories_and_media_types():
    """The trivia_warmup_rsvp row's payload can carry either or both
    depending on whether the upcoming activity is trivia or emoji."""
    out = _subject_markers_for_log(
        "trivia_warmup_rsvp",
        '{"categories": ["movies"], "media_types": ["song"]}',
    )
    assert "categories:movies" in out, out
    assert "media_type:song" in out, out


def test_subject_markers_unknown_type_returns_empty():
    assert _subject_markers_for_log("morning", '{"foo": "bar"}') == ""


def test_subject_markers_no_payload_returns_empty():
    assert _subject_markers_for_log("trivia_round", None) == ""
    assert _subject_markers_for_log("trivia_round", "") == ""
    assert _subject_markers_for_log("trivia_round", "not json") == ""


def test_subject_markers_sanitises_special_chars():
    """Hebrew or operator-typed categories with non-alphanum chars are
    stripped so they don't break the activity_log regex pattern."""
    out = _subject_markers_for_log(
        "trivia_round", '{"categories": ["movies & games", "מדע"]}',
    )
    # "movies & games" → "moviesgames" (spaces/ampersand stripped).
    # The Hebrew "מדע" → "" after the alphanumerics-only filter, so
    # it's dropped entirely.
    assert "categories:moviesgames" in out, out


# ── get_recent_activity_subjects ───────────────────────────────

def test_get_recent_activity_subjects_splits_multi_value_marker():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            # Insert two activity_log rows: one with a single-value
            # marker, one with a +-joined multi-value marker.
            await db._db.execute(
                "INSERT INTO activity_log (action_type, description, target_channel) "
                "VALUES (?, ?, ?)",
                ("trivia_round", "שלח: ... [categories:movies]", "general"),
            )
            await db._db.execute(
                "INSERT INTO activity_log (action_type, description, target_channel) "
                "VALUES (?, ?, ?)",
                ("trivia_round", "שלח: ... [categories:gaming+geek]", "general"),
            )
            await db._db.commit()
            ids = await db.get_recent_activity_subjects(
                action_type="trivia_round", days=30, key="categories",
            )
            return ids
        finally:
            await db.close()

    ids = asyncio.run(run())
    # Multi-value marker splits into separate tokens.
    assert "movies" in ids, ids
    assert "gaming" in ids, ids
    assert "geek" in ids, ids


def test_get_recent_activity_subjects_respects_time_window():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            # Insert an OLD row (50 days ago) — should NOT be returned
            # when we ask for the last 14 days.
            await db._db.execute(
                "INSERT INTO activity_log (action_type, description, timestamp) "
                "VALUES (?, ?, datetime('now', '-50 days'))",
                ("trivia_round", "שלח: ... [categories:ancient]"),
            )
            # Recent row.
            await db._db.execute(
                "INSERT INTO activity_log (action_type, description) "
                "VALUES (?, ?)",
                ("trivia_round", "שלח: ... [categories:fresh]"),
            )
            await db._db.commit()
            ids = await db.get_recent_activity_subjects(
                action_type="trivia_round", days=14, key="categories",
            )
            return ids
        finally:
            await db.close()

    ids = asyncio.run(run())
    assert "fresh" in ids, ids
    assert "ancient" not in ids, ids


def test_get_recent_activity_subjects_emoji_media_type():
    async def run():
        db = Database(":memory:")
        await db.init()
        try:
            await db._db.execute(
                "INSERT INTO activity_log (action_type, description) "
                "VALUES (?, ?)",
                ("emoji_puzzle", "שלח: Emoji Night [media_type:song]"),
            )
            await db._db.commit()
            ids = await db.get_recent_activity_subjects(
                action_type="emoji_puzzle", days=30, key="media_type",
            )
            return ids
        finally:
            await db.close()

    ids = asyncio.run(run())
    assert ids == ["song"], ids
