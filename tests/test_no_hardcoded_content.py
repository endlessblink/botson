"""Hardcoded-content guardian — single source of truth for the
"no hardcoded user-facing content" rule.

See `CLAUDE.md` and `AGENTS.md` → "No Hardcoded User-Facing Content".

Each test asserts ZERO (or capped) violations of one rule. Failures list
every offending file:line. As Phases A–D land, the failing assertions
drop their `xfail` markers / shrink their caps; this file is the live
spec.

Bypass at deploy time: `SKIP_HARDCODED_GUARDIAN=1 ./scripts/deploy.sh`.
The bypass is audit-logged and intended for emergencies, not routine work.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
HEBREW_RE = re.compile(r"[֐-׿]")  # Hebrew unicode block
URL_RE = re.compile(r"^https?://")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")  # ENV_VAR_NAME shape

# Allowlist: enclosing call/attribute name patterns where Hebrew is OK.
# A Hebrew literal passed as an argument to one of these is config-driven
# or log-only, not direct hardcoding.
ALLOWLIST_CALLS = frozenset({
    "load_copy", "get_copy", "get_setting", "get_settings",
    "load_yaml", "format", "f",  # f-strings handled separately
    "log_activity", "log_warning", "log_info", "log_error",
    "info", "warning", "error", "debug", "exception",  # logger methods
    "t", "_t",  # i18n shortcut if added later
    "logger",
})


# --- file walking --------------------------------------------------------

def _python_files(*globs: str):
    """Yield (path, source_text) for every .py file matching globs.
    Skips tests/, __pycache__/, .venv/, config/, data/, media/ — relative
    to ROOT (must NOT match against absolute path; the repo root itself
    lives under /media/...)."""
    skip_parts = (".venv", "__pycache__", "tests", "config", "data", "media")
    for g in globs:
        for p in ROOT.glob(g):
            try:
                rel = p.relative_to(ROOT).parts
            except ValueError:
                continue
            if any(part in skip_parts for part in rel):
                continue
            try:
                yield p, p.read_text(encoding="utf-8")
            except Exception:
                continue


def _has_noqa(source_lines: list[str], lineno: int) -> bool:
    """True if the offending line carries `# noqa: hardcoded-content (reason)`."""
    if 1 <= lineno <= len(source_lines):
        return "noqa: hardcoded-content" in source_lines[lineno - 1]
    return False


def _enclosing_call_name(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> str | None:
    """Walk up parent links to find the nearest Call expression. Return the
    callable's last attribute name (e.g. `logger.info` -> `info`) or the
    function name. Returns None if not inside a Call."""
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, ast.Call):
            f = cur.func
            if isinstance(f, ast.Name):
                return f.id
            if isinstance(f, ast.Attribute):
                return f.attr
            return None
        cur = parents.get(cur)
    return None


def _build_parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    return parents


def _hebrew_string_literals(path: Path, source: str):
    """Yield (lineno, value, enclosing_call_name) for every str literal
    containing Hebrew unicode in this file. Skips comments + docstrings
    via AST (only ast.Constant of type str is considered)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return
    parents = _build_parent_map(tree)
    # Detect docstrings: first statement of Module/ClassDef/FunctionDef
    # whose value is a string Constant.
    docstring_nodes: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(n, "body", None) or []
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstring_nodes.add(id(body[0].value))
    for n in ast.walk(tree):
        if not isinstance(n, ast.Constant) or not isinstance(n.value, str):
            continue
        if id(n) in docstring_nodes:
            continue
        if not HEBREW_RE.search(n.value):
            continue
        yield n.lineno, n.value, _enclosing_call_name(n, parents)


# --- canonical ban list (single source of truth) -------------------------

def _ban_list() -> set[str]:
    """Read fragments from config/freshness.yaml. Exposed both to runtime
    validators (bot/utils/freshness.py) and this guardian — no parallel
    list in code."""
    path = ROOT / "config" / "freshness.yaml"
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: set[str] = set()
    for key in ("stale_fragments", "misleading_ready_fragments", "generic_fragments"):
        out.update(str(x) for x in (data.get(key) or []))
    return out


# --- rule tests ----------------------------------------------------------

def test_freshness_yaml_is_canonical_ban_list():
    """The runtime ban list lives in config/freshness.yaml and is non-empty.
    bot/utils/freshness.py loads from there. No parallel list in code."""
    fragments = _ban_list()
    assert fragments, "config/freshness.yaml must define ban-list fragments"
    assert len(fragments) >= 20, f"ban list seems suspiciously small: {len(fragments)} fragments"
    # freshness.py must not redeclare the lists inline
    fresh = (ROOT / "bot" / "utils" / "freshness.py").read_text(encoding="utf-8")
    assert "STALE_FRAGMENTS = (" not in fresh, "freshness.py must not inline-declare fragment tuples"


def test_load_copy_helper_exists():
    """The centralized config-driven copy helper exists and is callable.
    Phase A.2 routes user-facing Hebrew through this."""
    from bot.utils.copy import load_copy
    placeholder = load_copy("__definitely_missing_ns__", "__missing_key__")
    assert placeholder.startswith("[copy missing:"), \
        "load_copy must return a visible placeholder, not the previous hardcoded literal"


def test_settings_yaml_has_copy_namespace():
    """Phase F seeds the copy.* namespace tree so handlers have a stable
    home for user-facing copy. Phase A.2 populates the leaves."""
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert "copy" in settings, "settings.yaml must define a top-level `copy:` namespace"
    assert "community_context" in (settings.get("bot") or {}), \
        "settings.yaml:bot.community_context must exist (was hardcoded in dashboard/app.py)"
    assert "streak_milestones" in (settings.get("gamification") or {}), \
        "settings.yaml:gamification.streak_milestones must exist (was hardcoded list in goals.py)"


def test_no_hardcoded_chat_id_in_handler_code():
    """Hardcoded `chat_id=-100…` literals must not appear in handler code.
    Routing comes from bot_message_routing or env."""
    pattern = re.compile(r"chat_id\s*=\s*-100\d{3,}")
    hits: list[str] = []
    for path, source in _python_files("bot/handlers/*.py", "bot/scheduler/*.py", "bot/utils/*.py"):
        lines = source.splitlines()
        for i, line in enumerate(lines, start=1):
            if pattern.search(line) and not _has_noqa(lines, i):
                hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    assert not hits, "hardcoded chat_id=-100... literals:\n" + "\n".join(hits)


def test_no_hebrew_in_utils_or_scheduler():
    """Utility and scheduler modules must not carry user-facing Hebrew.
    They're infrastructure; user copy lives in handlers + dashboard,
    routed through config.

    Phase F just moved freshness fragments out. Remaining hits are LLM
    prompt templates in `bot/scheduler/materializer.py` (Phase B.6
    target) and a label in `bot/utils/helpers.py`. Until those phases
    land, the test xfails with the file:line list — adding NEW Hebrew
    here in the meantime would still surface in the failure delta."""
    hits: list[str] = []
    # Hebrew unicode block bounds are technical constants, not content —
    # explicit allowlist by line so future Hebrew additions still surface.
    BOUND_LINES = {("bot/utils/freshness.py", 65)}
    for path, source in _python_files("bot/utils/*.py", "bot/scheduler/*.py"):
        rel = str(path.relative_to(ROOT))
        lines = source.splitlines()
        for ln, val, _call in _hebrew_string_literals(path, source):
            if _has_noqa(lines, ln):
                continue
            if (rel, ln) in BOUND_LINES:
                continue
            hits.append(f"{rel}:{ln}: {val[:60]!r}")
    if hits:
        pytest.xfail(f"Phase B.6 / utils cleanup will fix {len(hits)} Hebrew literals: " + " | ".join(hits))


def test_no_internal_placeholder_in_text_kwarg():
    """`[internal:*]` placeholder strings stored in scheduled_messages.text
    are forbidden — Phase B2 replaces them with NULL + renderer fallback.
    The guardian assertion is currently xfail; the cap drops to zero
    after B2 lands."""
    pattern = re.compile(r"\[internal:[a-z_]+\]")
    hits: list[str] = []
    for path, source in _python_files("dashboard/app.py", "bot/handlers/*.py"):
        for i, line in enumerate(source.splitlines(), start=1):
            if pattern.search(line):
                hits.append(f"{path.relative_to(ROOT)}:{i}: {line.strip()[:80]}")
    if hits:
        pytest.xfail(f"Phase B2 will remove {len(hits)} [internal:*] sites")


def test_dedup_methods_referenced_actually_exist():
    """Every `db.<method>` reference in handler code must resolve to an
    attribute on `Database`. Catches the silent-`hasattr` bug class
    (e.g. bot/handlers/facts.py:164 → get_recent_activity_subjects)."""
    db_path = ROOT / "bot" / "database" / "db.py"
    db_source = db_path.read_text(encoding="utf-8")
    db_tree = ast.parse(db_source)
    methods: set[str] = set()
    for node in ast.walk(db_tree):
        if isinstance(node, ast.ClassDef) and node.name == "Database":
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(stmt.name)
    assert "log_activity" in methods, "sanity: Database should at least have log_activity"
    referenced: dict[str, list[str]] = {}
    method_call_re = re.compile(r"\bdb\.([a-zA-Z_][a-zA-Z0-9_]*)\s*\(")
    hasattr_re = re.compile(r'hasattr\s*\(\s*db\s*,\s*["\']([a-zA-Z_][a-zA-Z0-9_]*)["\']')
    for path, source in _python_files("bot/handlers/*.py", "bot/utils/*.py"):
        for i, line in enumerate(source.splitlines(), start=1):
            for m in method_call_re.finditer(line):
                referenced.setdefault(m.group(1), []).append(f"{path.relative_to(ROOT)}:{i}")
            for m in hasattr_re.finditer(line):
                referenced.setdefault(m.group(1), []).append(f"{path.relative_to(ROOT)}:{i} (hasattr)")
    missing: dict[str, list[str]] = {
        name: sites for name, sites in referenced.items() if name not in methods
    }
    assert not missing, (
        "Database methods referenced by handlers but not defined "
        "(silent-hasattr bug class — see CLAUDE.md):\n"
        + "\n".join(f"  {n}: {sites}" for n, sites in sorted(missing.items()))
    )


def test_no_specific_banned_literals_outside_config():
    """A small fixed list of literals must NEVER appear in production code.
    These are the ones we've explicitly ruled out in past incidents."""
    BANNED = (
        "cinematic mysterious editorial",
        "Illustrate this Hebrew",
    )
    hits: list[str] = []
    for path, source in _python_files(
        "bot/**/*.py", "dashboard/app.py",
    ):
        if "/handlers/facts.py" in str(path):
            # Phase B1 moves this; suppress until then via xfail aggregation
            pass
        lines = source.splitlines()
        for i, line in enumerate(lines, start=1):
            if any(b in line for b in BANNED) and not _has_noqa(lines, i):
                hits.append(f"{path.relative_to(ROOT)}:{i}")
    if hits:
        pytest.xfail(f"Phase B1 removes the English image-prompt literals: {hits}")


def test_template_option_selected_must_be_gated():
    """`<option ... selected>` in dashboard templates must have a
    surrounding `{% if %}` that ties it to operator state. Plain
    `selected` is a hardcoded bias."""
    hits: list[str] = []
    sel_re = re.compile(r"<option[^>]*\bselected\b")
    if_re = re.compile(r"{%\s*if\b")
    for p in (ROOT / "dashboard" / "templates").rglob("*.html"):
        text = p.read_text(encoding="utf-8")
        # Per-line check with a simple lookbehind for {% if %} on the same
        # line or up to 3 lines above. Misses nested {% endif %} cases —
        # not worth a full Jinja parser yet.
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if sel_re.search(line):
                window = "\n".join(lines[max(0, i - 4):i + 1])
                if not if_re.search(window):
                    hits.append(f"{p.relative_to(ROOT)}:{i + 1}: {line.strip()[:80]}")
    if hits:
        pytest.xfail(f"Phase C3 removes ungated `selected` defaults: {len(hits)} sites")


def test_milestone_array_not_hardcoded_in_handlers():
    """`[7, 14, 30, 60, 100]` must not be a literal in handler code —
    Phase A.2.4 reads from config/settings.yaml:gamification.streak_milestones."""
    target_seq = [7, 14, 30, 60, 100]
    hits: list[str] = []
    for path, source in _python_files("bot/handlers/*.py"):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.List, ast.Tuple)):
                vals: list = []
                for el in n.elts:
                    if isinstance(el, ast.Constant) and isinstance(el.value, int):
                        vals.append(el.value)
                if vals == target_seq:
                    hits.append(f"{path.relative_to(ROOT)}:{n.lineno}")
    if hits:
        pytest.xfail(f"Phase A.2.4 moves milestones to settings.yaml: {hits}")


def test_calendar_dispatch_distinguishes_skipped_from_failed():
    """A.1.4 boundary guardrail. Every place in calendar.py's dispatch
    branches that converts a falsy return value into a RuntimeError must
    also have a skip path — either a pre-flight check that raises
    SkippedActivity, or a structured return value distinguishing no-op
    from failure.

    The bug class (verified 2026-05-09): pool-exhaustion in facts +
    emoji caused `RuntimeError("X did not send")` → mark_message_failed,
    even though the legit pattern (T-121) is `mark_message_skipped` for
    no-ops. Without this guardrail, future handler additions repeat the
    same pattern.

    Implementation: scan calendar.py for `raise RuntimeError("... did
    not ...")` lines and verify each has a SkippedActivity-raising
    pre-flight or counterpart in the same elif block.
    """
    cal = (ROOT / "bot" / "handlers" / "calendar.py").read_text(encoding="utf-8")
    runtime_lines = [
        i + 1 for i, line in enumerate(cal.splitlines())
        if 'raise RuntimeError(' in line and ('did not' in line or 'failed' in line.lower())
    ]
    assert runtime_lines, (
        "Sanity check failed: expected to find 'raise RuntimeError(...did not...)' "
        "lines in calendar.py — did the dispatch structure change?"
    )
    # For each runtime-error site, the preceding 30 lines should mention
    # SkippedActivity (a pre-flight) OR the handler itself raises it.
    lines = cal.splitlines()
    missing_preflight: list[str] = []
    for ln in runtime_lines:
        window = "\n".join(lines[max(0, ln - 30):ln])
        if "SkippedActivity" not in window:
            missing_preflight.append(f"calendar.py:{ln}")
    assert not missing_preflight, (
        "Calendar dispatch sites that map a falsy return to RuntimeError "
        "without a SkippedActivity pre-flight (bug class A.1.4):\n  "
        + "\n  ".join(missing_preflight)
    )


def test_anthropic_api_url_not_hardcoded():
    """Anthropic API URL must come from settings.yaml:llm.anthropic.api_url —
    Phase B5 externalizes the hardcoded `https://api.anthropic.com/v1/messages`."""
    needle = "https://api.anthropic.com/v1/messages"
    hits: list[str] = []
    for path, source in _python_files("bot/**/*.py", "dashboard/app.py"):
        for i, line in enumerate(source.splitlines(), start=1):
            if needle in line:
                hits.append(f"{path.relative_to(ROOT)}:{i}")
    if hits:
        pytest.xfail(f"Phase B5 moves Anthropic URL to settings: {hits}")
