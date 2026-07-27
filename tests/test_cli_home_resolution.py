"""The generation CLIs must be pointed at the home that actually holds
credentials.

2026-07-27 production faults, both from the same hardcoded
`pwd.getpwuid(...).pw_dir` lookup:

* `claude -p` ran against a home with no credentials, hung, and ate the
  full 90s timeout on every generated row — planner Populate then blew
  past the browser's 10-minute limit and showed "Aborted".
* the Codex fallback tried to `mkdir` inside a root-owned home, hit
  PermissionError, and never ran.
"""
from __future__ import annotations

import os

import pytest

from bot.utils import cli_home


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for var in ("BOTSON_CLAUDE_HOME", "BOTSON_CODEX_HOME", "CODEX_HOME"):
        monkeypatch.delenv(var, raising=False)
    cli_home.reset_cache()
    yield
    cli_home.reset_cache()


def _make_claude_home(root, name="svc") -> str:
    home = root / name
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}", encoding="utf-8")
    return str(home)


def test_prefers_the_home_that_has_credentials(tmp_path, monkeypatch):
    """The passwd home is empty; the process HOME has the real login."""
    empty = tmp_path / "passwd-home"
    empty.mkdir()
    real = _make_claude_home(tmp_path, "service-home")
    monkeypatch.setenv("HOME", real)
    monkeypatch.setattr(cli_home, "_passwd_home", lambda: str(empty))
    assert cli_home.resolve_claude_home() == real


def test_passwd_home_used_when_it_is_the_authenticated_one(tmp_path, monkeypatch):
    """The original assumption still holds when it's actually true."""
    real = _make_claude_home(tmp_path, "home-user")
    monkeypatch.setenv("HOME", str(tmp_path / "empty-workdir"))
    (tmp_path / "empty-workdir").mkdir()
    monkeypatch.setattr(cli_home, "_passwd_home", lambda: real)
    assert cli_home.resolve_claude_home() == real


def test_explicit_override_wins(tmp_path, monkeypatch):
    override = _make_claude_home(tmp_path, "override")
    other = _make_claude_home(tmp_path, "other")
    monkeypatch.setenv("HOME", other)
    monkeypatch.setenv("BOTSON_CLAUDE_HOME", override)
    assert cli_home.resolve_claude_home() == override


def test_falls_back_without_pointing_at_a_known_empty_dir(tmp_path, monkeypatch):
    """No credentials anywhere → use the process HOME and let the CLI
    fail on its own terms, rather than silently choosing an empty dir."""
    home = tmp_path / "nothing"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(cli_home, "_passwd_home", lambda: str(tmp_path / "also-nothing"))
    assert cli_home.resolve_claude_home() == str(home)


def test_claude_env_sets_home(tmp_path, monkeypatch):
    real = _make_claude_home(tmp_path, "svc")
    monkeypatch.setenv("HOME", real)
    monkeypatch.setattr(cli_home, "_passwd_home", lambda: str(tmp_path / "nope"))
    env = cli_home.claude_cli_env()
    assert env["HOME"] == real
    # The rest of the environment is preserved.
    assert env.get("PATH") == os.environ.get("PATH")


def test_codex_home_found_in_repo_local_dir(tmp_path, monkeypatch):
    codex_home = tmp_path / "repo" / ".codex-home"
    codex_home.mkdir(parents=True)
    (codex_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli_home, "_REPO_ROOT", tmp_path / "repo")
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    assert cli_home.resolve_codex_home() == str(codex_home)


def test_codex_home_unset_when_no_auth_anywhere(tmp_path, monkeypatch):
    """Better to let Codex use its own default than to pin it to a
    directory we know is empty."""
    monkeypatch.setattr(cli_home, "_REPO_ROOT", tmp_path / "repo")
    monkeypatch.setenv("HOME", str(tmp_path / "empty"))
    (tmp_path / "empty").mkdir()
    monkeypatch.setattr(cli_home, "_passwd_home", lambda: None)
    assert cli_home.resolve_codex_home() is None
    assert "CODEX_HOME" not in cli_home.codex_cli_env()


def test_stale_codex_home_env_is_not_trusted(tmp_path, monkeypatch):
    """An exported CODEX_HOME pointing at an authless dir must not win —
    that is how the fallback silently ran unauthenticated."""
    stale = tmp_path / "stale"
    stale.mkdir()
    good = tmp_path / "repo" / ".codex-home"
    good.mkdir(parents=True)
    (good / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(stale))
    monkeypatch.setattr(cli_home, "_REPO_ROOT", tmp_path / "repo")
    assert cli_home.resolve_codex_home() == str(good)


def test_no_call_site_hardcodes_the_passwd_home():
    """Guardian: the pattern that caused this must not come back."""
    import pathlib
    import re

    repo_root = pathlib.Path(__file__).resolve().parent.parent
    offenders = []
    for path in list(repo_root.glob("bot/**/*.py")) + [repo_root / "dashboard" / "app.py"]:
        if path.name == "cli_home.py":
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r'"HOME":\s*(\w+)', text):
            offenders.append(f"{path.relative_to(repo_root)}: HOME set to {match.group(1)}")
    assert not offenders, (
        "CLI env must come from bot/utils/cli_home.py, not a hand-rolled HOME override:\n"
        + "\n".join(offenders)
    )
