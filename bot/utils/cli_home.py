"""Resolve where the Claude / Codex CLIs actually keep their credentials.

Why this module exists
----------------------
Every CLI call site used to do the same thing:

    real_home = pwd.getpwuid(os.geteuid()).pw_dir     # → /home/botson
    env = {**os.environ, "HOME": real_home}

That was written for the opposite failure (systemd sets HOME to
WorkingDirectory, so `claude` couldn't find `~/.claude`). On this VPS it
is backwards, and produced two long-running production faults observed
2026-07-27:

* `/home/botson` is **root-owned and not writable by the service user**,
  and holds no credentials. The real, auto-refreshing logins live under
  the service HOME: `/opt/robotnik/.claude/.credentials.json` and
  `/opt/robotnik/.codex-home/auth.json`.
* So every `claude -p` ran unauthenticated and hung until the 90s
  timeout — one wasted minute and a half per generated row, which is why
  planner Populate blew past the 10-minute client limit. Seven days of
  journal show zero non-zero exits: always the timeout, never an error.
* And the Codex guard tried to `mkdir /home/botson/.codex`, hit
  PermissionError, and raised — so the fallback never ran at all.

The fix is to stop *guessing* the home and instead **probe for the
credentials**, preferring an explicit operator override. Never require
write access to a home we don't own.

Overrides: `BOTSON_CLAUDE_HOME`, `BOTSON_CODEX_HOME` / `CODEX_HOME`.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

CLAUDE_CREDENTIALS_RELPATH = Path(".claude") / ".credentials.json"
CODEX_CREDENTIALS_FILENAME = "auth.json"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Resolution is logged once per process per CLI — enough to diagnose from
# the journal, quiet enough to not spam a per-row generation loop.
_logged: set[str] = set()


def _passwd_home() -> str | None:
    try:
        import pwd

        return pwd.getpwuid(os.geteuid()).pw_dir
    except Exception:
        return None


def _log_once(key: str, message: str, *args) -> None:
    if key in _logged:
        return
    _logged.add(key)
    logger.info(message, *args)


def _candidates(explicit: str | None) -> list[Path]:
    """Ordered homes to probe: explicit override, the process HOME (what
    systemd gives us), then the passwd home."""
    out: list[Path] = []
    seen: set[str] = set()
    for raw in (explicit, os.environ.get("HOME"), _passwd_home(), str(_REPO_ROOT)):
        if not raw:
            continue
        p = Path(raw)
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def resolve_claude_home() -> str:
    """Return the HOME under which `claude` will find real credentials.

    Falls back to the process HOME when nothing is authenticated — the
    CLI then fails on its own terms instead of us silently pointing it
    at a directory we know is empty.
    """
    explicit = os.environ.get("BOTSON_CLAUDE_HOME", "").strip() or None
    for home in _candidates(explicit):
        if (home / CLAUDE_CREDENTIALS_RELPATH).is_file():
            _log_once("claude", "[cli-home] claude credentials found under %s", home)
            return str(home)
    fallback = os.environ.get("HOME") or _passwd_home() or str(_REPO_ROOT)
    _log_once(
        "claude",
        "[cli-home] no claude credentials found in any candidate home; "
        "falling back to %s (expect auth failures)",
        fallback,
    )
    return fallback


def resolve_codex_home() -> str | None:
    """Return a CODEX_HOME containing `auth.json`, or None if none found.

    Codex reads `CODEX_HOME` when set and `$HOME/.codex` otherwise, so a
    None result means "leave it to the CLI's own default".
    """
    explicit = (
        os.environ.get("BOTSON_CODEX_HOME", "").strip()
        or os.environ.get("CODEX_HOME", "").strip()
        or None
    )
    if explicit and (Path(explicit) / CODEX_CREDENTIALS_FILENAME).is_file():
        _log_once("codex", "[cli-home] codex auth found under %s (explicit)", explicit)
        return explicit
    probes: list[Path] = [_REPO_ROOT / ".codex-home"]
    probes += [home / ".codex" for home in _candidates(None)]
    for path in probes:
        if (path / CODEX_CREDENTIALS_FILENAME).is_file():
            _log_once("codex", "[cli-home] codex auth found under %s", path)
            return str(path)
    _log_once("codex", "[cli-home] no codex auth found; leaving CODEX_HOME unset")
    return None


def claude_cli_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for spawning the Claude CLI."""
    env = {**(base if base is not None else os.environ)}
    env["HOME"] = resolve_claude_home()
    return env


def codex_cli_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for spawning the Codex CLI.

    HOME follows the Claude resolution (both CLIs cache under the same
    service home here), and CODEX_HOME is set only when a real auth file
    was found — an unset value lets Codex use its own default rather
    than being pinned to a directory we know is empty.
    """
    env = {**(base if base is not None else os.environ)}
    env["HOME"] = resolve_claude_home()
    codex_home = resolve_codex_home()
    if codex_home:
        env["CODEX_HOME"] = codex_home
    else:
        env.pop("CODEX_HOME", None)
    return env


def reset_cache() -> None:
    """Test hook — re-enables the one-shot resolution logging."""
    _logged.clear()


# ── call timing ──────────────────────────────────────────────────────
#
# The open question on 2026-07-27 was whether `claude -p` calls that hit
# the timeout were *hanging* or merely *slow* — 16 timeouts against zero
# non-zero exits couldn't distinguish the two, and answering it by hand
# would mean spending the operator's account on a probe. Instead every
# call now logs its duration on both paths, so the next occurrence
# answers the question from real traffic. `vps-admin.sh llm-doctor`
# summarises these lines.
#
# Format is fixed and greppable — do not reword without updating the
# doctor's parser:
#   [cli-timing] <cli> ok in 12.3s (ctx=planner)
#   [cli-timing] <cli> TIMEOUT after 90.0s (ctx=planner)
#   [cli-timing] <cli> error in 3.1s (ctx=planner)

TIMING_PREFIX = "[cli-timing]"


def log_cli_timing(cli: str, outcome: str, seconds: float, context: str = "-") -> None:
    """Emit one greppable timing line. `outcome` is ok | TIMEOUT | error."""
    logger.info(
        "%s %s %s in %.1fs (ctx=%s)"
        if outcome != "TIMEOUT"
        else "%s %s %s after %.1fs (ctx=%s)",
        TIMING_PREFIX, cli, outcome, seconds, context,
    )


def cli_timeout_seconds(cli: str, default: int) -> int:
    """Per-CLI time budget, operator-tunable without a code change.

    `settings.llm.cli_timeouts.{claude_seconds,codex_seconds}`. Kept here
    rather than in the callers so every generation path shares one knob.
    """
    try:
        from bot.utils.config import get_settings

        timeouts = ((get_settings().get("llm") or {}).get("cli_timeouts") or {})
        value = timeouts.get(f"{cli}_seconds")
        if value:
            return max(10, int(value))
    except Exception:
        pass
    return default
