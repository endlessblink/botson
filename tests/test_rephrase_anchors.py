"""Rephrase = one-off wording edit, never a learned rule.

The operator can accept a draft, reject it (which teaches the bot), or
rephrase it. Rephrasing exists precisely so a wording nudge on one card
does NOT become a durable directive — that path stays exclusive to
reject + reason. These tests pin both halves: the anchors resolve from
config into the rewrite prompt, and nothing about the learning loop moves.
"""
from __future__ import annotations

import asyncio

import pytest


# ── anchors resolve from config, not from code ───────────────────────

def test_anchors_come_from_settings_yaml():
    """Every chip the operator sees is operator-editable config."""
    from dashboard import app as dash

    anchors = dash._load_rephrase_anchors()
    assert anchors, "copy.rephrase_anchors is empty — the UI would show no chips"
    for a in anchors:
        assert a["key"] and a["label"] and a["directive"]
        # The label is what the operator clicks; the directive is what the
        # model reads. Collapsing them would make the prompt depend on UI copy.
        assert a["label"] != a["directive"]


def test_malformed_anchor_entries_are_dropped(monkeypatch):
    from dashboard import app as dash
    import bot.utils.copy as copy_mod

    monkeypatch.setattr(
        copy_mod,
        "load_copy_block",
        lambda ns, default=None: [
            {"key": "ok", "label": "L", "directive": "D"},
            {"key": "", "label": "L", "directive": "D"},     # no key
            {"key": "nodir", "label": "L"},                   # no directive
            "not-a-dict",
        ],
    )
    keys = [a["key"] for a in dash._load_rephrase_anchors()]
    assert keys == ["ok"]


def test_missing_config_yields_no_anchors_not_a_hebrew_fallback(monkeypatch):
    """No hardcoded Hebrew fallback — an operator who empties the list gets
    a free-text-only modal, not stale directives baked into the code."""
    from dashboard import app as dash
    import bot.utils.copy as copy_mod

    monkeypatch.setattr(copy_mod, "load_copy_block", lambda ns, default=None: default)
    assert dash._load_rephrase_anchors() == []
    assert dash._rephrase_instructions(["less_translated"], "") == ""


# ── anchors → instructions ───────────────────────────────────────────

def test_selected_anchors_become_instructions():
    from dashboard import app as dash

    anchors = dash._load_rephrase_anchors()
    first, second = anchors[0], anchors[1]
    out = dash._rephrase_instructions([second["key"], first["key"]], "")
    assert first["directive"] in out
    assert second["directive"] in out
    # Config order, not click order — the prompt must be stable.
    assert out.index(first["directive"]) < out.index(second["directive"])


def test_unknown_anchor_keys_are_ignored():
    """A stale browser tab sending a removed key must not 500 the request."""
    from dashboard import app as dash

    assert dash._rephrase_instructions(["no_such_anchor"], "") == ""
    assert dash._rephrase_instructions(["no_such_anchor"], "בלי אמוג'י") == "בלי אמוג'י"


def test_free_text_is_appended_after_anchors():
    from dashboard import app as dash

    key = dash._load_rephrase_anchors()[0]["key"]
    out = dash._rephrase_instructions([key], "בלי אמוג'י")
    assert out.endswith("בלי אמוג'י")


def test_no_anchors_and_no_notes_is_empty():
    from dashboard import app as dash

    assert dash._rephrase_instructions([], "") == ""
    assert dash._rephrase_instructions(None, "  ") == ""


# ── the prompt keeps the idea and carries the directives ─────────────

def test_rewrite_prompt_contains_original_text_and_directives():
    from dashboard import app as dash

    original = "איזה סרט ראיתם לאחרונה שלא הצלחתם להפסיק לחשוב עליו?"
    anchor = dash._load_rephrase_anchors()[0]
    instructions = dash._rephrase_instructions([anchor["key"]], "")
    prompt = dash.build_generation_prompt(
        "discussion", "rewrite", original, "movies", instructions,
    )
    # The idea survives — this is a rewrite, not a fresh generation.
    assert original in prompt
    assert anchor["directive"] in prompt


# ── the guardrail: rephrasing teaches nothing ────────────────────────

def test_rephrase_does_not_touch_the_learning_loop(monkeypatch):
    """`/api/generate` must never record feedback or schedule rule
    abstraction. If this test fails, a stylistic one-off is being promoted
    into a permanent Hebrew rule — see CLAUDE.md "Autonomous learning"."""
    from dashboard import app as dash

    calls: list[str] = []

    monkeypatch.setattr(
        dash, "_schedule_rule_abstraction",
        lambda *a, **k: calls.append("abstract"),
    )
    monkeypatch.setattr(
        dash, "_record_feedback_to_cache",
        lambda *a, **k: calls.append("feedback_cache"),
    )

    async def _fake_cli(prompt):
        return "ניסוח חדש לגמרי"

    monkeypatch.setattr(dash, "_generate_via_cli", _fake_cli)

    class _Req:
        session = {"authenticated": True}

        async def json(self):
            return {
                "field": "discussion",
                "mode": "rewrite",
                "existing": "טקסט מקורי",
                "category": "movies",
                "anchors": [dash._load_rephrase_anchors()[0]["key"]],
            }

    class _DB:
        async def get_recent_sent_texts(self, *a, **k):
            return []

    monkeypatch.setattr(dash, "_fetch_recent_sent_for_dedup", _fake_recent)
    monkeypatch.setattr(dash, "_topic_display_name", _fake_topic_name)

    result = asyncio.run(dash.generate_text_content(_Req(), _DB()))
    assert result["content"] == "ניסוח חדש לגמרי"
    assert calls == [], f"rephrase touched the learning loop: {calls}"


async def _fake_recent(*a, **k):
    return []


async def _fake_topic_name(*a, **k):
    return "סרטים"


def test_generate_returns_quality_failures_for_the_card_badge(monkeypatch):
    """The card shows a quality badge; a rephrase must refresh it rather
    than leaving the pre-rephrase verdict on new text."""
    from dashboard import app as dash

    async def _fake_cli(prompt):
        return "שאלה חדשה"

    monkeypatch.setattr(dash, "_generate_via_cli", _fake_cli)
    monkeypatch.setattr(dash, "_fetch_recent_sent_for_dedup", _fake_recent)
    monkeypatch.setattr(dash, "_topic_display_name", _fake_topic_name)

    class _Req:
        session = {"authenticated": True}

        async def json(self):
            return {
                "field": "discussion", "mode": "rewrite",
                "existing": "טקסט", "category": "movies",
            }

    result = asyncio.run(dash.generate_text_content(_Req(), object()))
    assert isinstance(result["quality_failures"], list)


# ── the shared modal keeps its Hebrew in config ──────────────────────

def test_rephrase_modal_template_has_no_hebrew_literals():
    """Guardian: the shared modal renders labels from
    /api/rephrase-anchors. Inlining Hebrew here would silently override
    the operator's settings.yaml edits on one surface only."""
    from pathlib import Path
    import re

    root = Path(__file__).resolve().parents[1]
    body = (root / "dashboard/templates/_rephrase_modal.html").read_text(encoding="utf-8")
    # Comments explain the rule in English; strip them before scanning.
    body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    hebrew = re.findall(r"[֐-׿]+", body)
    assert not hebrew, f"hardcoded Hebrew in the rephrase modal: {hebrew[:5]}"


def test_rephrase_ui_copy_keys_all_exist():
    """Every key the modal asks for must resolve, or the operator sees a
    blank button."""
    from bot.utils.copy import load_copy_block

    ui = load_copy_block("rephrase_ui", default={}) or {}
    required = {
        "button_label", "modal_title", "modal_subtitle", "anchors_label",
        "free_text_label", "free_text_placeholder", "apply_label",
        "cancel_label", "working_label", "undo_label", "replaced_badge",
        "error_label",
    }
    missing = required - set(ui)
    assert not missing, f"copy.rephrase_ui is missing: {sorted(missing)}"
