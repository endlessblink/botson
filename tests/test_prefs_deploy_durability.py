"""The learned-preferences store must survive `deploy.sh`.

Regression cover for the 2026-07-25 finding: auto-learned rules and
canonized anchors were written into the git-tracked
`config/operator_prefs.md`, and `scripts/deploy.sh` runs
`git reset --hard origin/main` — so every deploy silently reset the
bot's knowledge to the last committed snapshot.
"""
from __future__ import annotations

import pathlib

import pytest

from bot.utils import prefs_store


HEB = "### Hebrew content rules"
GOOD = "### Good examples — Hebrew content"
BAD = "### Bad examples — Hebrew content"


def _baseline(rules: list[str], good: list[str] | None = None) -> str:
    return (
        "# prefs\n\n"
        f"{HEB}\n\n"
        + "\n".join(rules)
        + "\n\n"
        + f"{GOOD}\n\n"
        + "\n".join(good or [])
        + "\n\n"
        + f"{BAD}\n\n"
        + "\n## Other\n\nprose that is not reconciled\n"
    )


@pytest.fixture()
def store(tmp_path, monkeypatch):
    base = tmp_path / "config" / "operator_prefs.md"
    base.parent.mkdir(parents=True)
    runtime = tmp_path / "data" / "operator_prefs.md"
    monkeypatch.setattr(prefs_store, "BASE_PREFS_PATH", base)
    monkeypatch.setattr(prefs_store, "DEFAULT_RUNTIME_PREFS_PATH", runtime)
    monkeypatch.delenv("BOTSON_PREFS_PATH", raising=False)
    prefs_store.reset_cache()
    yield base, runtime
    prefs_store.reset_cache()


def test_runtime_path_is_outside_git_tracked_config(store):
    """The live file must not be the one `git reset --hard` rewrites."""
    base, runtime = store
    base.write_text(_baseline(["- כלל א"]), encoding="utf-8")
    resolved = prefs_store.runtime_prefs_path()
    assert resolved == runtime
    assert "data" in resolved.parts
    assert resolved != base


def test_first_run_seeds_from_baseline(store):
    base, runtime = store
    base.write_text(_baseline(["- כלל א", "- כלל ב"]), encoding="utf-8")
    prefs_store.runtime_prefs_path()
    assert runtime.exists()
    assert runtime.read_text(encoding="utf-8") == base.read_text(encoding="utf-8")


def test_learned_rules_survive_a_deploy(store):
    """Simulate: bot learns a rule → deploy resets the tracked baseline →
    the learned rule is still in the live file."""
    base, runtime = store
    base.write_text(_baseline(["- כלל בסיס"]), encoding="utf-8")
    prefs_store.runtime_prefs_path()

    # The bot learns something at runtime (writes to the live file).
    learned = "- אסור לייצר שאלות מאמץ"
    text = runtime.read_text(encoding="utf-8")
    runtime.write_text(text.replace(f"{HEB}\n\n", f"{HEB}\n\n{learned}\n"), encoding="utf-8")

    # `git reset --hard` — the tracked baseline is untouched by runtime
    # writes, and a fresh process starts up against it.
    prefs_store.reset_cache()
    prefs_store.runtime_prefs_path()

    assert learned in runtime.read_text(encoding="utf-8")


def test_baseline_additions_reach_the_live_file(store):
    """A hand-edited rule committed to git still reaches production."""
    base, runtime = store
    base.write_text(_baseline(["- כלל בסיס"]), encoding="utf-8")
    prefs_store.runtime_prefs_path()

    base.write_text(_baseline(["- כלל בסיס", "- כלל חדש מגיט"]), encoding="utf-8")
    prefs_store.reset_cache()
    prefs_store.runtime_prefs_path()

    live = runtime.read_text(encoding="utf-8")
    assert "- כלל חדש מגיט" in live
    # and it did not duplicate the one already present
    assert live.count("- כלל בסיס") == 1


def test_untrained_baseline_rule_is_not_resurrected(store):
    base, runtime = store
    base.write_text(_baseline(["- כלל בסיס", "- כלל שנמחק"]), encoding="utf-8")
    prefs_store.runtime_prefs_path()

    # Operator untrains one of them.
    text = runtime.read_text(encoding="utf-8").replace("- כלל שנמחק\n", "")
    runtime.write_text(text, encoding="utf-8")
    prefs_store.record_removed_bullets(["- כלל שנמחק"], runtime)

    prefs_store.reset_cache()
    prefs_store.runtime_prefs_path()
    assert "- כלל שנמחק" not in runtime.read_text(encoding="utf-8")


def test_anchor_sections_reconcile_independently(store):
    base, runtime = store
    base.write_text(_baseline(["- כלל"], good=["- דוגמה טובה"]), encoding="utf-8")
    prefs_store.runtime_prefs_path()
    live = runtime.read_text(encoding="utf-8")
    good_body = live.split(GOOD, 1)[1].split("###", 1)[0]
    assert "- דוגמה טובה" in good_body


def test_env_override_wins(tmp_path, monkeypatch):
    base = tmp_path / "config" / "operator_prefs.md"
    base.parent.mkdir(parents=True)
    base.write_text(_baseline(["- כלל"]), encoding="utf-8")
    override = tmp_path / "elsewhere" / "prefs.md"
    monkeypatch.setattr(prefs_store, "BASE_PREFS_PATH", base)
    monkeypatch.setenv("BOTSON_PREFS_PATH", str(override))
    prefs_store.reset_cache()
    try:
        assert prefs_store.runtime_prefs_path() == override
        assert override.exists()
    finally:
        prefs_store.reset_cache()


def test_readers_point_at_the_runtime_store():
    """Both prompt-building surfaces must read the durable file."""
    from bot.utils import operator_anchors
    from dashboard import app as dash

    live = prefs_store.runtime_prefs_path()
    assert operator_anchors._PREFS_PATH == live
    assert dash._OPERATOR_PREFS_PATH == live
    # And the durable file is not the git-tracked one.
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    assert live != repo_root / "config" / "operator_prefs.md"


def test_deploy_script_still_hard_resets():
    """If deploy ever stops hard-resetting, this whole indirection can be
    revisited — pin the assumption so the comment can't go stale."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    deploy = (repo_root / "scripts" / "deploy.sh").read_text(encoding="utf-8")
    assert "reset --hard" in deploy
