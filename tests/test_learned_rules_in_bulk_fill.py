"""Learned rules must reach the surface that writes most of the content.

2026-07-25: the operator reported filler questions ("☀️ ראשון בבוקר — מה
הדבר שהשבוע הזה חייב להיות?") being auto-populated despite explicit
learned rules banning exactly that register. Root cause: the
materializer — the daily bulk fill that produces the morning / evening /
discussion rows the group actually sees — injected the static
`question_quality.md` rules and the ⭐/🚫 anchors, but never the learned
`### Hebrew content rules`. Rules extracted from operator rejections
steered the planner's one-off generations and nothing else.
"""
from __future__ import annotations

import asyncio

import pytest

RULE_TOKEN = "LEARNED_RULE_BULK_TEST"


@pytest.fixture()
def prefs_with_rule(tmp_path, monkeypatch):
    path = tmp_path / "operator_prefs.md"
    path.write_text(
        "# prefs\n\n"
        "### Hebrew content rules\n\n"
        f"- {RULE_TOKEN} — אסור לייצר שאלות פילר.\n\n"
        "### Good examples — Hebrew content\n\nprose only.\n\n"
        "### Bad examples — Hebrew content\n\nprose only.\n",
        encoding="utf-8",
    )
    from bot.utils import operator_anchors as oa
    monkeypatch.setattr(oa, "_PREFS_PATH", path)
    oa.reset_cache()
    yield path
    oa.reset_cache()


def test_rules_block_renders(prefs_with_rule):
    from bot.utils.operator_anchors import render_learned_rules_block
    block = render_learned_rules_block()
    assert RULE_TOKEN in block
    assert "כללים שנלמדו" in block


def test_rules_block_empty_when_nothing_learned(tmp_path, monkeypatch):
    from bot.utils import operator_anchors as oa
    empty = tmp_path / "empty.md"
    empty.write_text(
        "### Hebrew content rules\n\nprose only, no bullets.\n\n"
        "### Good examples — Hebrew content\n\nprose.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(oa, "_PREFS_PATH", empty)
    oa.reset_cache()
    try:
        assert oa.render_learned_rules_block() == ""
    finally:
        oa.reset_cache()


@pytest.mark.parametrize("message_type", ["morning", "evening", "discussion"])
def test_materializer_prompt_carries_learned_rules(prefs_with_rule, monkeypatch, message_type):
    """Every bulk-fill content type must see the learned directives."""
    captured: dict = {}

    async def _fake_generate(prompt: str) -> str:
        captured["prompt"] = prompt
        return '{"text":"שאלה לבדיקה בלבד שלא נכשלת בפילטר."}'

    from bot.scheduler import materializer as mat
    monkeypatch.setattr(mat, "_generate_with_claude", _fake_generate)

    asyncio.run(mat._generate_fresh_text(
        message_type,
        category="movies" if message_type == "discussion" else None,
        examples=["דוגמה ישנה"],
        used_texts=["טקסט שכבר נשלח"],
        scheduled_date="2026-07-26",
        scheduled_time="09:00",
    ))

    assert "prompt" in captured, "materializer did not call the LLM stub"
    assert RULE_TOKEN in captured["prompt"], captured["prompt"][:800]


def test_rules_precede_anchor_examples(prefs_with_rule, monkeypatch):
    """Directives before illustrations — an example read as a template is
    how the model ends up echoing the anchor instead of obeying the rule."""
    path = prefs_with_rule
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "### Good examples — Hebrew content\n\nprose only.",
            "### Good examples — Hebrew content\n\n- ANCHOR_TOKEN — דוגמה.",
        ),
        encoding="utf-8",
    )
    from bot.utils import operator_anchors as oa
    oa.reset_cache()

    captured: dict = {}

    async def _fake_generate(prompt: str) -> str:
        captured["prompt"] = prompt
        return '{"text":"שאלה לבדיקה בלבד שלא נכשלת בפילטר."}'

    from bot.scheduler import materializer as mat
    monkeypatch.setattr(mat, "_generate_with_claude", _fake_generate)
    asyncio.run(mat._generate_fresh_text(
        "morning", category=None, examples=[], used_texts=[],
        scheduled_date="2026-07-26", scheduled_time="09:00",
    ))

    prompt = captured["prompt"]
    assert prompt.index(RULE_TOKEN) < prompt.index("ANCHOR_TOKEN")
