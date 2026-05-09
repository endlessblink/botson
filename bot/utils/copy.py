"""Centralized loader for user-facing copy.

Every Hebrew template a user sees should surface through `load_copy()` so
the guardian test (`tests/test_no_hardcoded_content.py`) can allowlist this
module as a known config-read. The "No Hardcoded User-Facing Content" rule
in `CLAUDE.md` and `AGENTS.md` is enforced by that test.

Reads from `config/settings.yaml` under the `copy.<namespace>.<key>` tree
by default. Long-form templates (welcome, events) live in sibling
`config/copy/<namespace>.yaml` files when settings.yaml gets too dense.

Cached implicitly via the existing settings reload mechanism — the bot's
`reload_watcher` job picks up settings.yaml edits within ~5s, so editing
copy in place propagates without restart.
"""
from __future__ import annotations

import logging
from typing import Any

from .config import CONFIG_DIR, get_settings, load_yaml

logger = logging.getLogger(__name__)

_warned_keys: set[str] = set()
_external_files: dict[str, dict] = {}


def _load_external(namespace: str) -> dict:
    """Lazy-load `config/copy/<namespace>.yaml` if present, else {}."""
    if namespace in _external_files:
        return _external_files[namespace]
    path = CONFIG_DIR / "copy" / f"{namespace}.yaml"
    if not path.exists():
        _external_files[namespace] = {}
        return {}
    try:
        data = load_yaml(f"copy/{namespace}.yaml") or {}
    except Exception as e:
        logger.warning("copy: failed to load %s: %s", path, e)
        data = {}
    _external_files[namespace] = data
    return data


def load_copy(namespace: str, key: str, *, default: str | None = None, **fmt: Any) -> str:
    """Load a user-facing template and format with `fmt` kwargs.

    Lookup order:
      1. `config/settings.yaml:copy.<namespace>.<key>`
      2. `config/copy/<namespace>.yaml:<key>`
      3. `default` arg, or the visible placeholder `[copy missing: ns.key]`.

    On missing key the warning is emitted once per (namespace, key) pair
    so logs stay readable. Format errors fall back to the unformatted
    template so the user sees Hebrew, not a Python traceback.
    """
    settings = get_settings() or {}
    copy_root = (settings.get("copy") or {}).get(namespace) or {}
    template = copy_root.get(key)
    if template is None:
        external = _load_external(namespace)
        template = external.get(key)
    if template is None:
        marker = f"{namespace}.{key}"
        if marker not in _warned_keys:
            logger.warning("copy: missing key %s — falling back to placeholder", marker)
            _warned_keys.add(marker)
        template = default if default is not None else f"[copy missing: {marker}]"
    if not fmt:
        return template
    try:
        return template.format(**fmt)
    except (KeyError, IndexError, ValueError) as e:
        logger.warning("copy: format error for %s.%s: %s", namespace, key, e)
        return template
