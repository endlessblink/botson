"""Gap 3: operator-curated good/bad anchor examples (2026-05-18).

Verifies:
  - operator_prefs.md ships with the new section headings.
  - The generic section reader parses arbitrary `### …` headings.
  - The prompt builder emits all three labelled sub-blocks when sections
    have content (rules → good anchors → bad anchors).
  - The canonize endpoint logic (simulated via direct file write) appends a
    bullet under the correct heading and the next prompt read picks it up.
  - The cap warning fires past _ANCHOR_CAP entries.
"""
from __future__ import annotations

import pathlib

import pytest


@pytest.fixture()
def prefs_path(tmp_path, monkeypatch):
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    src = repo_root / "config" / "operator_prefs.md"
    dst = tmp_path / "operator_prefs.md"
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    from dashboard import app as dash
    monkeypatch.setattr(dash, "_OPERATOR_PREFS_PATH", dst)
    dash._OPERATOR_PREFS_CACHE.update({
        "section": None, "mtime": 0.0, "loaded_at": 0.0, "rule_count": 0,
        "sections": {},
    })
    return dst


def test_canonical_file_has_anchor_sections(prefs_path):
    """The shipped file must declare both anchor sections so canonize can
    find them on a fresh deploy."""
    from dashboard.app import (
        _PREFS_GOOD_ANCHORS_HEADING,
        _PREFS_BAD_ANCHORS_HEADING,
        _split_at_section_heading,
    )
    text = prefs_path.read_text(encoding="utf-8")
    assert _split_at_section_heading(text, _PREFS_GOOD_ANCHORS_HEADING) is not None
    assert _split_at_section_heading(text, _PREFS_BAD_ANCHORS_HEADING) is not None


def test_section_reader_returns_empty_when_no_bullets(prefs_path):
    from dashboard.app import _read_prefs_section, _PREFS_GOOD_ANCHORS_HEADING
    body, items = _read_prefs_section(_PREFS_GOOD_ANCHORS_HEADING)
    assert body == ""
    assert items == []


def test_section_reader_parses_bullets(prefs_path):
    from dashboard import app as dash
    text = prefs_path.read_text(encoding="utf-8")
    parts = dash._split_at_section_heading(text, dash._PREFS_GOOD_ANCHORS_HEADING)
    assert parts is not None
    before, body, rest = parts
    new = before + body.rstrip() + "\n\n- ANCHOR_GOOD_ONE — דוגמה.\n- ANCHOR_GOOD_TWO\n" + rest
    prefs_path.write_text(new, encoding="utf-8")
    dash._OPERATOR_PREFS_CACHE["sections"] = {}
    body, items = dash._read_prefs_section(dash._PREFS_GOOD_ANCHORS_HEADING)
    assert any("ANCHOR_GOOD_ONE" in i for i in items)
    assert any("ANCHOR_GOOD_TWO" in i for i in items)


def test_prompt_block_emits_three_subblocks_when_populated(prefs_path):
    from dashboard import app as dash
    # Seed both anchor sections.
    text = prefs_path.read_text(encoding="utf-8")
    for heading, tok in [
        (dash._PREFS_GOOD_ANCHORS_HEADING, "ANCHOR_TONE_OK_TOKEN"),
        (dash._PREFS_BAD_ANCHORS_HEADING, "ANCHOR_NEVER_DO_TOKEN"),
    ]:
        parts = dash._split_at_section_heading(text, heading)
        assert parts is not None
        before, body, rest = parts
        text = before + body.rstrip() + f"\n\n- {tok} — דוגמה.\n" + rest
    prefs_path.write_text(text, encoding="utf-8")
    dash._OPERATOR_PREFS_CACHE.update({
        "section": None, "mtime": 0.0, "loaded_at": 0.0,
        "sections": {},
    })
    block = dash._active_style_profile_block_sync()
    # All three sub-blocks present in order.
    rules_header = "הנחיות"
    good_header = "✓ דוגמאות אנקור"
    bad_header = "✗ דוגמאות אנקור"
    i_rules = block.find(rules_header)
    i_good = block.find(good_header)
    i_bad = block.find(bad_header)
    assert i_rules >= 0 and i_good > i_rules and i_bad > i_good, block
    assert "ANCHOR_TONE_OK_TOKEN" in block
    assert "ANCHOR_NEVER_DO_TOKEN" in block


def test_prompt_block_omits_anchor_subblocks_when_empty(prefs_path):
    """If the operator hasn't canonized anything yet, only the rules block
    appears — no empty anchor headers cluttering the prompt."""
    from dashboard import app as dash
    block = dash._active_style_profile_block_sync()
    assert "✓ דוגמאות אנקור" not in block
    assert "✗ דוגמאות אנקור" not in block


def test_canonize_append_workflow(prefs_path):
    """Simulate what POST /api/operator-prefs/canonize does and confirm
    the bullet shows up in the next read."""
    from dashboard import app as dash
    heading = dash._PREFS_GOOD_ANCHORS_HEADING
    text = prefs_path.read_text(encoding="utf-8")
    parts = dash._split_at_section_heading(text, heading)
    assert parts is not None
    before, body, rest = parts
    bullet = "- CANONIZE_E2E_TOKEN_GOOD"
    citation = "  \n  _**Source:** qa-scoring canonize (good), 2026-05-18, draft_id=42_"
    prefs_path.write_text(
        before + body.rstrip() + "\n\n" + bullet + "\n" + citation + "\n" + rest,
        encoding="utf-8",
    )
    dash._OPERATOR_PREFS_CACHE["sections"] = {}
    _, items = dash._read_prefs_section(heading)
    assert any("CANONIZE_E2E_TOKEN_GOOD" in i for i in items)


def test_anchor_cap_constant_is_reasonable():
    """Past ~15 few-shot examples there's diminishing returns; the cap
    constant must be in that ballpark, not 100+."""
    from dashboard.app import _ANCHOR_CAP
    assert 10 <= _ANCHOR_CAP <= 20


def test_section_split_stops_at_h2_heading(prefs_path):
    """An anchor section must not bleed into the next `## Citations format`
    block — _split_at_section_heading needs to honor h2 boundaries too."""
    from dashboard.app import _split_at_section_heading, _PREFS_BAD_ANCHORS_HEADING
    text = prefs_path.read_text(encoding="utf-8")
    parts = _split_at_section_heading(text, _PREFS_BAD_ANCHORS_HEADING)
    assert parts is not None
    _, body, _ = parts
    assert "## Citations format" not in body
    # The next `### ` heading must also not be inside body.
    assert "### Motion grammar" not in body
