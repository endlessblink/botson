"""Auto-learned rule lists must stay applicable as they grow.

Rules compound; prompts don't. Past a few dozen directives the model
averages over them instead of applying them, so the list is folded back
under a cap by LLM synthesis — never by mechanical truncation.
"""
from __future__ import annotations

import asyncio

import pytest

HEB = "### Hebrew content rules"


class _FakeDB:
    def __init__(self):
        self.changes = []

    async def record_prefs_change(self, **kw):
        self.changes.append(kw)


def _prefs_text(rule_count: int) -> str:
    rules = "\n".join(f"- כלל מספר {i}" for i in range(1, rule_count + 1))
    return f"# prefs\n\n{HEB}\n\nהקדמה שאיננה כלל.\n\n{rules}\n\n## Other\n\nprose\n"


@pytest.fixture()
def prefs(tmp_path, monkeypatch):
    from dashboard import app as dash
    path = tmp_path / "operator_prefs.md"
    monkeypatch.setattr(dash, "_OPERATOR_PREFS_PATH", path)
    dash._OPERATOR_PREFS_CACHE.update({"section": None, "mtime": 0.0, "loaded_at": 0.0})
    return path


def _rules_in(path) -> list[str]:
    body = path.read_text(encoding="utf-8").split(HEB, 1)[1].split("\n## ", 1)[0]
    return [ln.strip() for ln in body.splitlines() if ln.strip().startswith("- ")]


def test_no_consolidation_below_cap(prefs, monkeypatch):
    from dashboard import app as dash
    prefs.write_text(_prefs_text(10), encoding="utf-8")
    monkeypatch.setattr(dash, "_hebrew_rules_cap", lambda: 40)

    async def _boom(*a, **kw):
        raise AssertionError("consolidation must not run below the cap")

    monkeypatch.setattr(dash, "_llm_consolidate_hebrew_rules", _boom)
    assert asyncio.run(dash._maybe_consolidate_hebrew_rules(_FakeDB())) == 0
    assert len(_rules_in(prefs)) == 10


def test_over_cap_consolidates_and_keeps_prose(prefs, monkeypatch):
    from dashboard import app as dash
    prefs.write_text(_prefs_text(50), encoding="utf-8")
    monkeypatch.setattr(dash, "_hebrew_rules_cap", lambda: 40)

    async def _fake(rules, target):
        assert len(rules) == 50
        assert target == 24  # 60% of the cap
        return "\n".join(f"- כלל מאוחד {i}" for i in range(1, 6))

    monkeypatch.setattr(dash, "_llm_consolidate_hebrew_rules", _fake)
    db = _FakeDB()
    removed = asyncio.run(dash._maybe_consolidate_hebrew_rules(db))

    assert removed == 45
    rules = _rules_in(prefs)
    assert len(rules) == 5
    assert all("מאוחד" in r for r in rules)
    # Section prose and neighbouring sections survive.
    text = prefs.read_text(encoding="utf-8")
    assert "הקדמה שאיננה כלל." in text
    assert "## Other" in text
    assert db.changes and db.changes[0]["change_kind"] == "consolidate"


def test_llm_failure_keeps_every_rule(prefs, monkeypatch):
    """A failed consolidation must never truncate the list — an over-long
    rule set beats a silently halved one."""
    from dashboard import app as dash
    prefs.write_text(_prefs_text(50), encoding="utf-8")
    monkeypatch.setattr(dash, "_hebrew_rules_cap", lambda: 40)

    async def _empty(rules, target):
        return ""

    monkeypatch.setattr(dash, "_llm_consolidate_hebrew_rules", _empty)
    assert asyncio.run(dash._maybe_consolidate_hebrew_rules(_FakeDB())) == 0
    assert len(_rules_in(prefs)) == 50


def test_single_line_llm_output_is_refused(monkeypatch):
    """Collapsing dozens of rules into one line is a failed call."""
    from dashboard import app as dash

    async def _one_liner(prompt):
        return "- כלל יחיד"

    monkeypatch.setattr(dash, "_generate_via_cli", _one_liner)
    out = asyncio.run(
        dash._llm_consolidate_hebrew_rules([f"- כלל {i}" for i in range(50)], 24)
    )
    assert out == ""


def test_cap_is_operator_configurable():
    from bot.utils.config import get_settings
    from dashboard.app import _hebrew_rules_cap
    assert (get_settings().get("learning") or {}).get("max_hebrew_rules")
    assert _hebrew_rules_cap() >= 10
