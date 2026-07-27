"""Generation calls must report how long they took.

2026-07-27 left an unresolved question: Claude CLI calls hit the 90s
timeout 16 times in a week with zero non-zero exits, which cannot
distinguish "hanging" from "legitimately slower than the budget".
Answering it by hand would mean spending the operator's account on a
probe, so the calls report their own durations instead and
`vps-admin.sh llm-doctor` aggregates them.
"""
from __future__ import annotations

import logging
import re

import pytest

from bot.utils import cli_home

# Must stay in sync with the parser in scripts/vps-admin.sh.
DOCTOR_PATTERN = re.compile(
    r"\[cli-timing\] (\w+) (\w+) (?:in|after) ([\d.]+)s \(ctx=([\w-]+)\)"
)


def test_ok_line_matches_the_doctor_parser(caplog):
    with caplog.at_level(logging.INFO, logger="bot.utils.cli_home"):
        cli_home.log_cli_timing("claude", "ok", 12.34, "planner")
    match = DOCTOR_PATTERN.search(caplog.text)
    assert match, caplog.text
    assert match.groups() == ("claude", "ok", "12.3", "planner")


def test_timeout_line_matches_the_doctor_parser(caplog):
    with caplog.at_level(logging.INFO, logger="bot.utils.cli_home"):
        cli_home.log_cli_timing("codex", "TIMEOUT", 120.0, "materializer")
    match = DOCTOR_PATTERN.search(caplog.text)
    assert match, caplog.text
    assert match.group(2) == "TIMEOUT"
    assert match.group(4) == "materializer"


def test_budget_defaults_when_unset(monkeypatch):
    monkeypatch.setattr(
        cli_home, "cli_timeout_seconds", cli_home.cli_timeout_seconds, raising=False
    )
    assert cli_home.cli_timeout_seconds("nonexistent-cli", 77) == 77


def test_budget_reads_settings():
    from bot.utils.config import get_settings

    configured = ((get_settings().get("llm") or {}).get("cli_timeouts") or {})
    assert configured.get("claude_seconds"), "operator knob must exist in settings"
    assert cli_home.cli_timeout_seconds("claude", 1) == int(configured["claude_seconds"])


def test_budget_has_a_floor(monkeypatch):
    """A typo'd 0 must not turn every call into an instant failure."""
    import bot.utils.config as cfg

    monkeypatch.setattr(cfg, "get_settings", lambda: {"llm": {"cli_timeouts": {"claude_seconds": 1}}})
    assert cli_home.cli_timeout_seconds("claude", 90) >= 10


@pytest.mark.parametrize("path", ["dashboard/app.py", "bot/scheduler/materializer.py"])
def test_call_sites_are_instrumented(path):
    """Every CLI spawn point reports timing — an uninstrumented one is a
    blind spot exactly where the question gets asked."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    text = (repo_root / path).read_text(encoding="utf-8")
    assert "log_cli_timing" in text, f"{path} spawns a CLI without timing instrumentation"


def test_no_hardcoded_timeout_literals_left_in_messages():
    """The error text must reflect the configured budget, not a stale 90."""
    import pathlib

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    text = (repo_root / "dashboard" / "app.py").read_text(encoding="utf-8")
    assert '"CLI timed out after 90s"' not in text
    assert '"Codex CLI timed out after 120s"' not in text
