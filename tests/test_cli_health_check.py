"""The watchdog, not the operator, reads the CLI timing verdict.

Operator instruction 2026-07-28: "I dont want to read any verdict — you
need to be able or a watchdog should be able to get this content." So the
health guard classifies the generation CLIs itself and alerts through the
existing Telegram path when they degrade.
"""
from __future__ import annotations

import argparse
import pathlib

import pytest

from scripts import botson_health_guard as guard

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture()
def args():
    return argparse.Namespace(mode="daily")


def _samples(monkeypatch, rows):
    monkeypatch.setattr(guard, "_cli_timing_samples", lambda hours: rows)


def _budget(monkeypatch, seconds=90):
    import bot.utils.cli_home as ch

    monkeypatch.setattr(ch, "cli_timeout_seconds", lambda cli, default: seconds)


def test_hanging_calls_are_flagged_as_a_failure(args, monkeypatch):
    """Fast successes alongside ceiling hits = the call hangs."""
    _budget(monkeypatch)
    rows = [("claude", "ok", 8.0, "planner") for _ in range(6)]
    rows += [("claude", "TIMEOUT", 90.0, "planner") for _ in range(4)]
    _samples(monkeypatch, rows)

    check = guard.check_cli_health(args)
    assert check.status == "fail"
    assert "HANGING" in check.detail
    # The remediation is named, not left to the reader.
    assert "Lower llm.cli_timeouts.claude_seconds" in check.detail


def test_uniformly_slow_calls_are_flagged_as_slow(args, monkeypatch):
    _budget(monkeypatch)
    _samples(monkeypatch, [("claude", "ok", 75.0, "planner") for _ in range(8)])

    check = guard.check_cli_health(args)
    assert check.status == "warn"
    assert "SLOW" in check.detail
    assert "Raise llm.cli_timeouts.claude_seconds" in check.detail


def test_healthy_calls_pass_quietly(args, monkeypatch):
    _budget(monkeypatch)
    _samples(monkeypatch, [("claude", "ok", 9.0, "planner") for _ in range(10)])

    check = guard.check_cli_health(args)
    assert check.status == "ok"
    assert "HANGING" not in check.detail
    assert "SLOW" not in check.detail


def test_idle_window_is_not_an_alarm(args, monkeypatch):
    """No calls in the window must not page the operator."""
    _samples(monkeypatch, [])
    check = guard.check_cli_health(args)
    assert check.status == "ok"


def test_each_cli_is_judged_separately(args, monkeypatch):
    _budget(monkeypatch)
    rows = [("claude", "ok", 8.0, "planner") for _ in range(6)]
    rows += [("claude", "TIMEOUT", 90.0, "planner") for _ in range(4)]
    rows += [("codex", "ok", 10.0, "planner") for _ in range(5)]
    _samples(monkeypatch, rows)

    check = guard.check_cli_health(args)
    assert check.status == "fail"
    assert "claude: HANGING" in check.detail
    assert "codex: ok" in check.detail
    assert check.data["clis"]["codex"]["timeouts"] == 0


def test_failure_reaches_the_alerting_path(args, monkeypatch):
    """A failing cli_health check must make the whole run 'failed' — that
    is what triggers the existing Telegram alert."""
    _budget(monkeypatch)
    rows = [("claude", "ok", 5.0, "planner") for _ in range(6)]
    rows += [("claude", "TIMEOUT", 90.0, "planner") for _ in range(6)]
    _samples(monkeypatch, rows)

    check = guard.check_cli_health(args)
    assert check.status == "fail"
    assert not check.ok, "a failed check must not count as ok, or no alert fires"


def test_check_is_registered_in_the_daily_run():
    source = (REPO_ROOT / "scripts" / "botson_health_guard.py").read_text(encoding="utf-8")
    run_block = source.split("def run_guard", 1)[1].split("def ", 1)[0]
    assert "check_cli_health(args)" in run_block, "check exists but never runs"
