"""T-181: canonical operator_prefs.md drives prompts and supports HITL writes."""
from __future__ import annotations

import importlib
import pathlib
import re
from unittest.mock import patch

import pytest


@pytest.fixture()
def prefs_path(tmp_path, monkeypatch):
    """Redirect the dashboard module's _OPERATOR_PREFS_PATH to a tmp copy."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src = repo_root / "config" / "operator_prefs.md"
    dst = tmp_path / "operator_prefs.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    from dashboard import app as dash
    monkeypatch.setattr(dash, "_OPERATOR_PREFS_PATH", dst)
    # Bust the cache so the tmp file is read.
    dash._OPERATOR_PREFS_CACHE.update({"section": None, "mtime": 0.0, "loaded_at": 0.0, "rule_count": 0})
    return dst


def test_hebrew_section_is_parsed_from_canonical_file(prefs_path):
    from dashboard.app import _read_operator_prefs_hebrew_section, _OPERATOR_PREFS_CACHE
    section = _read_operator_prefs_hebrew_section()
    assert section, "expected non-empty Hebrew section"
    assert _OPERATOR_PREFS_CACHE["rule_count"] >= 5
    # Motion rules MUST NOT leak into the Hebrew section.
    assert "Per-shot duration" not in section
    assert "TimeFrame" not in section


def test_motion_section_does_not_leak_into_prompt(prefs_path):
    from dashboard.app import _active_style_profile_block_sync
    block = _active_style_profile_block_sync()
    assert "Per-shot duration" not in block
    assert "cubic-bezier" not in block
    # Hebrew header should be present.
    assert "הנחיות" in block


def test_teach_appends_rule_with_citation(prefs_path):
    """The /teach-bot path appends one rule and the next prompt reads it."""
    from dashboard.app import (
        _read_operator_prefs_hebrew_section,
        _OPERATOR_PREFS_CACHE,
        _OPERATOR_PREFS_PATH,
    )
    # Direct write simulating what the /api/operator-prefs/teach endpoint does.
    text = _OPERATOR_PREFS_PATH.read_text(encoding="utf-8")
    from dashboard.app import _split_at_hebrew_heading
    parts = _split_at_hebrew_heading(text)
    assert parts is not None
    before, section_body, rest = parts
    rule_line = "- TEST_TOKEN_XYZ_123 — אסור במוחלט."
    new_text = before + section_body.rstrip() + "\n\n" + rule_line + "\n" + rest
    _OPERATOR_PREFS_PATH.write_text(new_text, encoding="utf-8")
    # Cache invalidation.
    _OPERATOR_PREFS_CACHE.update({"section": None, "mtime": 0.0, "loaded_at": 0.0})
    section = _read_operator_prefs_hebrew_section()
    assert "TEST_TOKEN_XYZ_123" in section


def test_untrain_removes_matching_rule(prefs_path):
    from dashboard.app import _read_operator_prefs_hebrew_section, _OPERATOR_PREFS_CACHE, _OPERATOR_PREFS_PATH
    # Seed a token.
    text = _OPERATOR_PREFS_PATH.read_text(encoding="utf-8")
    from dashboard.app import _split_at_hebrew_heading
    parts = _split_at_hebrew_heading(text)
    assert parts is not None
    before, section_body, rest = parts
    section_body = section_body.rstrip() + "\n\n- REMOVE_ME_TOKEN — בדיקה.\n"
    _OPERATOR_PREFS_PATH.write_text(before + section_body + rest, encoding="utf-8")
    _OPERATOR_PREFS_CACHE.update({"section": None, "mtime": 0.0, "loaded_at": 0.0})
    assert "REMOVE_ME_TOKEN" in _read_operator_prefs_hebrew_section()
    # Now simulate the untrain operation.
    text = _OPERATOR_PREFS_PATH.read_text(encoding="utf-8")
    parts = _split_at_hebrew_heading(text)
    assert parts is not None
    before, sb, rest = parts
    kept = [ln for ln in sb.splitlines() if not (ln.strip().startswith("- ") and "REMOVE_ME_TOKEN" in ln)]
    _OPERATOR_PREFS_PATH.write_text(before + "\n".join(kept) + rest, encoding="utf-8")
    _OPERATOR_PREFS_CACHE.update({"section": None, "mtime": 0.0, "loaded_at": 0.0})
    assert "REMOVE_ME_TOKEN" not in _read_operator_prefs_hebrew_section()


def test_working_memory_category_filter_and_injection(prefs_path):
    """T-182: a rejection on category X must appear in next prompt for X."""
    from dashboard import app as dash
    dash._RECENT_FEEDBACK_CACHE.clear()
    dash._RECENT_FEEDBACK_CACHE["__global__"] = []
    dash._record_feedback_to_cache({
        "id": 101, "topic_key": "movies",
        "original_text": "TEST_REJECTED_movies_token",
        "verdict": "rejected", "reason": "test rejection",
    })
    dash._record_feedback_to_cache({
        "id": 102, "topic_key": "gaming",
        "original_text": "TEST_REJECTED_gaming_token",
        "verdict": "rejected", "reason": "different cat",
    })
    # movies prompt should see movies rejection AND globally-recent gaming.
    p = dash.build_generation_prompt(
        "discussion", "append", "", "movies",
        scheduled_date="2026-05-17", scheduled_time="20:00",
    )
    assert "TEST_REJECTED_movies_token" in p
    # gaming rejection is globally recent → fills top-3 global slot.
    assert "TEST_REJECTED_gaming_token" in p
    # Singles prompt should NOT see movies-specific feedback by default,
    # but globally-recent rows DO appear cross-category (drift signal).
    # That's by design — verify it's labeled correctly.
    p2 = dash.build_generation_prompt(
        "discussion", "append", "", "singles",
        scheduled_date="2026-05-17", scheduled_time="20:00",
    )
    assert "דוגמאות שנדחו" in p2  # the labeled rejection header


def test_working_memory_cache_caps(prefs_path):
    from dashboard import app as dash
    dash._RECENT_FEEDBACK_CACHE.clear()
    dash._RECENT_FEEDBACK_CACHE["__global__"] = []
    # Push 100 movies rejections; cache should cap at 50.
    for i in range(100):
        dash._record_feedback_to_cache({
            "id": 1000 + i, "topic_key": "movies",
            "original_text": f"rejection_{i}", "verdict": "rejected",
        })
    assert len(dash._RECENT_FEEDBACK_CACHE.get("movies", [])) == 50
    assert len(dash._RECENT_FEEDBACK_CACHE.get("__global__", [])) == 30


def test_cache_invalidates_on_mtime_change(prefs_path):
    """When the file changes, the next read picks it up (no stale prompts)."""
    import time
    from dashboard.app import _read_operator_prefs_hebrew_section, _OPERATOR_PREFS_CACHE, _OPERATOR_PREFS_PATH
    _read_operator_prefs_hebrew_section()
    initial = _OPERATOR_PREFS_CACHE["rule_count"]
    # Touch + edit.
    text = _OPERATOR_PREFS_PATH.read_text(encoding="utf-8")
    text = text.replace("### Hebrew content rules\n",
                        "### Hebrew content rules\n\n- CACHE_INVALIDATION_TEST_TOKEN — בדיקה.\n", 1)
    time.sleep(1.1)  # ensure mtime ticks forward (second resolution)
    _OPERATOR_PREFS_PATH.write_text(text, encoding="utf-8")
    section = _read_operator_prefs_hebrew_section()
    assert "CACHE_INVALIDATION_TEST_TOKEN" in section
    assert _OPERATOR_PREFS_CACHE["rule_count"] == initial + 1
