"""Deploy-durable location for the operator-preferences store.

Why this module exists
----------------------
`config/operator_prefs.md` is a **git-tracked** file. Every auto-learned
rule, every canonized ⭐/🚫 anchor example, and every `/teach-bot` write
used to land there at runtime — and `scripts/deploy.sh` runs
`git reset --hard origin/main`, which discards working-tree changes to
tracked files. Net effect observed 2026-07-25: the learning loop worked
end-to-end, but every deploy silently reset the bot's knowledge to the
last committed snapshot (mid-May), so rejections never compounded.

The fix: the **live** store is `data/operator_prefs.md` (the `data/`
directory is gitignored and survives deploys). `config/operator_prefs.md`
stays in git as the **baseline** — the hand-authored, code-reviewed seed
and the symlink target for the cross-tool
`noam-personal-preferences` skill.

Reconciliation (runs once per process, on first path resolution):
  * runtime file missing  → copy the baseline verbatim.
  * runtime file present  → append any baseline bullet that is absent
    from the corresponding runtime section. Additive only: a hand-edit
    committed to the baseline reaches production, while learned rules
    are never clobbered.
  * bullets recorded in the tombstone file (written by `untrain`) are
    never re-added, so removing a baseline rule stays removed across
    restarts.

Override the runtime path with `BOTSON_PREFS_PATH` (tests, dev).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_PREFS_PATH = _REPO_ROOT / "config" / "operator_prefs.md"
DEFAULT_RUNTIME_PREFS_PATH = _REPO_ROOT / "data" / "operator_prefs.md"

# Sections whose bullets are reconciled from the baseline. Everything
# else in the baseline (prose, motion grammar, workflow rules) is copied
# on first seed and then left alone — those are agent-facing, not
# bot-prompt-facing.
RECONCILED_HEADINGS = (
    "### Hebrew content rules",
    "### Good examples — Hebrew content",
    "### Bad examples — Hebrew content",
)

_TOMBSTONE_NAME = "operator_prefs_removed.json"

_state: dict[str, Path | None] = {"resolved": None}


def runtime_prefs_path() -> Path:
    """Return the live prefs file, seeding/reconciling it on first call."""
    cached = _state.get("resolved")
    if cached is not None:
        return cached
    path = Path(os.environ.get("BOTSON_PREFS_PATH") or DEFAULT_RUNTIME_PREFS_PATH)
    try:
        _ensure_runtime_file(path)
    except Exception as e:  # never let prefs plumbing break a bot start
        logger.warning("[prefs-store] seeding %s failed: %s", path, e)
        if not path.exists():
            # Fall back to the baseline so the bot still has its rules,
            # even though writes to it won't survive the next deploy.
            _state["resolved"] = BASE_PREFS_PATH
            return BASE_PREFS_PATH
    _state["resolved"] = path
    return path


def tombstone_path(runtime_path: Path | None = None) -> Path:
    base = runtime_path or runtime_prefs_path()
    return base.parent / _TOMBSTONE_NAME


def record_removed_bullets(bullets: list[str], runtime_path: Path | None = None) -> None:
    """Remember bullets the operator removed so reconciliation won't
    resurrect them from the git baseline on the next restart."""
    cleaned = [b.strip() for b in bullets if b and b.strip()]
    if not cleaned:
        return
    path = tombstone_path(runtime_path)
    existing = _read_tombstones(path)
    merged = existing | set(cleaned)
    if merged == existing:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sorted(merged), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("[prefs-store] tombstone write failed: %s", e)


def reset_cache() -> None:
    """Test hook — forces the next `runtime_prefs_path()` to re-resolve."""
    _state["resolved"] = None


# ── internals ────────────────────────────────────────────────────────


def _read_tombstones(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()
    if not isinstance(data, list):
        return set()
    return {str(x).strip() for x in data if str(x).strip()}


def _ensure_runtime_file(path: Path) -> None:
    if not BASE_PREFS_PATH.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        shutil.copyfile(BASE_PREFS_PATH, path)
        logger.info("[prefs-store] seeded runtime prefs at %s", path)
        return
    merged = _reconcile(
        base_text=BASE_PREFS_PATH.read_text(encoding="utf-8"),
        runtime_text=path.read_text(encoding="utf-8"),
        removed=_read_tombstones(tombstone_path(path)),
    )
    if merged is not None:
        path.write_text(merged, encoding="utf-8")


def _section_span(text: str, heading: str) -> tuple[int, int] | None:
    """Return (body_start, body_end) for `heading`, matched at line start."""
    if text.startswith(heading):
        start = len(heading)
    else:
        idx = text.find("\n" + heading)
        if idx < 0:
            return None
        start = idx + 1 + len(heading)
    after = text[start:]
    end = len(after)
    for marker in ("\n### ", "\n## "):
        found = after.find(marker)
        if found >= 0:
            end = min(end, found)
    return start, start + end


def _bullets(body: str) -> list[str]:
    return [ln.strip() for ln in body.splitlines() if ln.strip().startswith("- ")]


def _reconcile(*, base_text: str, runtime_text: str, removed: set[str]) -> str | None:
    """Append baseline bullets missing from the runtime file.

    Returns the new runtime text, or None when nothing changed.
    """
    out = runtime_text
    changed = False
    for heading in RECONCILED_HEADINGS:
        base_span = _section_span(base_text, heading)
        run_span = _section_span(out, heading)
        if base_span is None or run_span is None:
            continue
        base_bullets = _bullets(base_text[base_span[0]:base_span[1]])
        run_body = out[run_span[0]:run_span[1]]
        present = set(_bullets(run_body))
        missing = [b for b in base_bullets if b not in present and b not in removed]
        if not missing:
            continue
        new_body = run_body.rstrip() + "\n" + "\n".join(missing) + "\n\n"
        out = out[:run_span[0]] + new_body + out[run_span[1]:]
        changed = True
        logger.info(
            "[prefs-store] reconciled %d baseline bullet(s) into %r", len(missing), heading
        )
    return out if changed else None
