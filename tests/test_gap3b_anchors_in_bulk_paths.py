"""Gap 3b (2026-05-18): operator-curated anchors reach the production
bulk-fill path (`materializer._generate_fresh_text`) and the warm-up /
reminder path (`dashboard.app._generate_activity_copy`).

Without this wiring, ⭐/🚫 canonized drafts only steered planner Populate
previews — not the next-day group sends. These tests pin both prompt
strings include the labelled anchor block when bullets are present.
"""
from __future__ import annotations

import asyncio
import pathlib

import pytest


@pytest.fixture()
def prefs_with_anchors(tmp_path, monkeypatch):
    """Point both the dashboard reader and the bot-side anchors reader at
    a tmp prefs file seeded with one good + one bad anchor."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src = repo_root / "config" / "operator_prefs.md"
    dst = tmp_path / "operator_prefs.md"
    text = src.read_text(encoding="utf-8")
    text = text.replace(
        "### Good examples — Hebrew content",
        "### Good examples — Hebrew content\n\n- ANCHOR_GOOD_BULK_TEST — דוגמה חיובית.\n",
        1,
    )
    text = text.replace(
        "### Bad examples — Hebrew content",
        "### Bad examples — Hebrew content\n\n- ANCHOR_BAD_BULK_TEST — דוגמה שלילית.\n",
        1,
    )
    dst.write_text(text, encoding="utf-8")
    from bot.utils import operator_anchors as oa
    monkeypatch.setattr(oa, "_PREFS_PATH", dst)
    oa.reset_cache()
    from dashboard import app as dash
    monkeypatch.setattr(dash, "_OPERATOR_PREFS_PATH", dst)
    dash._OPERATOR_PREFS_CACHE.update({
        "section": None, "mtime": 0.0, "loaded_at": 0.0, "rule_count": 0,
        "sections": {},
    })
    return dst


def test_render_anchor_block_includes_both_sections(prefs_with_anchors):
    from bot.utils.operator_anchors import render_anchor_block
    block = render_anchor_block()
    assert "ANCHOR_GOOD_BULK_TEST" in block
    assert "ANCHOR_BAD_BULK_TEST" in block
    assert "✓ דוגמאות אנקור" in block
    assert "✗ דוגמאות אנקור" in block


def test_render_anchor_block_empty_when_no_bullets(tmp_path, monkeypatch):
    """No anchors → empty string so callers can no-op concat."""
    from bot.utils import operator_anchors as oa
    empty = tmp_path / "empty_prefs.md"
    empty.write_text(
        "## Rules\n\n### Hebrew content rules\n\n- only rule\n\n"
        "### Good examples — Hebrew content\n\nprose only, no bullets.\n\n"
        "### Bad examples — Hebrew content\n\nprose only, no bullets.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(oa, "_PREFS_PATH", empty)
    oa.reset_cache()
    assert oa.render_anchor_block() == ""


def test_materializer_prompt_includes_anchor_block(prefs_with_anchors, monkeypatch):
    """`_generate_fresh_text` must append the anchor block to its prompt
    so the bulk-fill candidates see ⭐/🚫 examples."""
    captured: dict = {}

    async def _fake_generate(prompt: str) -> str:
        captured["prompt"] = prompt
        # Return a syntactically-valid output that the freshness gate will
        # accept-ish; the test only cares that the prompt was assembled
        # with anchors, not the gate verdict.
        return '{"text":"שאלה לבדיקה בלבד שלא נכשלת בפילטר."}'

    from bot.scheduler import materializer as mat
    monkeypatch.setattr(mat, "_generate_with_claude", _fake_generate)

    async def _run():
        await mat._generate_fresh_text(
            "discussion",
            category="movies",
            examples=["דוגמה ישנה"],
            used_texts=["טקסט שכבר נשלח"],
            scheduled_date="2026-05-20",
            scheduled_time="20:30",
        )

    asyncio.run(_run())
    assert "prompt" in captured, "materializer did not call the LLM stub"
    prompt = captured["prompt"]
    assert "ANCHOR_GOOD_BULK_TEST" in prompt, prompt[:800]
    assert "ANCHOR_BAD_BULK_TEST" in prompt, prompt[:800]
    assert "✓ דוגמאות אנקור" in prompt
    assert "✗ דוגמאות אנקור" in prompt


def test_activity_copy_prompt_includes_anchor_block(prefs_with_anchors, monkeypatch):
    """`_generate_activity_copy` (warm-up + reminder copy) must also see
    the canonized anchors so the announcement tone matches the rest of
    the operator's curated voice."""
    captured: dict = {}

    async def _fake_cli(prompt: str) -> str:
        captured["prompt"] = prompt
        return '{"text":"מתחממים לפעילות חדשה בקבוצה."}'

    from dashboard import app as dash
    monkeypatch.setattr(dash, "_generate_via_cli", _fake_cli)
    # Force the CLI path (API fallback never runs if CLI returns).

    async def _run():
        return await dash._generate_activity_copy(
            "trivia_warmup_rsvp",
            game_time="20:30",
            lead_minutes=60,
            min_ready_players=3,
            theme="movies",
        )

    asyncio.run(_run())
    assert "prompt" in captured, "activity-copy did not call the CLI stub"
    prompt = captured["prompt"]
    assert "ANCHOR_GOOD_BULK_TEST" in prompt, prompt[:800]
    assert "ANCHOR_BAD_BULK_TEST" in prompt, prompt[:800]


def test_cache_invalidates_on_file_change(prefs_with_anchors):
    """Append a new good anchor and confirm the next read picks it up
    once the cache TTL is bypassed via reset_cache."""
    import time as _time
    from bot.utils.operator_anchors import render_anchor_block, reset_cache, _PREFS_PATH
    block_before = render_anchor_block()
    assert "ANCHOR_DELTA_TOKEN" not in block_before
    text = _PREFS_PATH.read_text(encoding="utf-8")
    text = text.replace(
        "- ANCHOR_GOOD_BULK_TEST — דוגמה חיובית.\n",
        "- ANCHOR_GOOD_BULK_TEST — דוגמה חיובית.\n- ANCHOR_DELTA_TOKEN — חדש.\n",
        1,
    )
    _time.sleep(1.1)  # mtime has second resolution
    _PREFS_PATH.write_text(text, encoding="utf-8")
    reset_cache()
    assert "ANCHOR_DELTA_TOKEN" in render_anchor_block()
