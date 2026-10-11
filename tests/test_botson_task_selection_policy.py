from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_botson_instructions_preserve_user_outcome_order():
    prefs = (ROOT / "config/operator_prefs.md").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    policy = (ROOT / "docs/botson-task-routing.md").read_text(encoding="utf-8")

    assert "Follow the user's outcome order before choosing work" in prefs
    assert "latest explicit correction controls the next action" in prefs
    assert "show the exact message for approval before taking unrelated backend work" in prefs
    assert "stop the mistaken branch and deliver the corrected outcome first" in prefs
    assert "Add `tests/test_botson_task_selection_policy.py` whenever this guard changes." in agents
    assert "Read `docs/botson-task-routing.md` before selecting work" in agents
    assert "earliest unfinished outcome in the user's stated order" in policy
    assert "Do not start unrelated backend work before the immediate group-activity decision is completed." in policy
    assert "present the exact proposed text and destination before sending" in policy
