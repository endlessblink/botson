"""T-159: smoke-validate the e2e_den_smoke.py dry-run path.

The full --send mode requires a live dashboard + a real Telegram target, so
it cannot run in CI. These tests cover the dry-run behavior end-to-end:

  * The default invocation exits 0 and prints a plan per safe message type.
  * --only filters plans by message_type and exits 2 when nothing matches.
  * --json emits a parseable summary that names mode + plans.
  * The safe-types set deliberately excludes stateful games unless explicitly
    requested with --include-games.
"""

from __future__ import annotations

import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

# scripts/ is not a package — import the module by path.
_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "e2e_den_smoke.py"
sys.path.insert(0, str(_SCRIPT.parent))
import e2e_den_smoke as smoke  # noqa: E402


class DryRunBehaviorTests(unittest.TestCase):

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = smoke.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_default_invocation_is_dry_run_and_exits_zero(self):
        code, out, _ = self._run([])
        self.assertEqual(code, 0)
        self.assertIn("--dry-run", out)
        self.assertIn("No HTTP calls made", out)

    def test_dry_run_renders_one_block_per_safe_plan(self):
        code, out, _ = self._run([])
        self.assertEqual(code, 0)
        for label in ("plain custom text", "poll with three options",
                      "facts_tidbit from pool", "free_games digest"):
            self.assertIn(label, out)

    def test_dry_run_does_not_advertise_stateful_game_types(self):
        # trivia_round / emoji_puzzle have dedicated tests; the smoke harness
        # deliberately skips them by default. Pin this so a future default
        # expansion has to update the test and reconsider cleanup/safety.
        _, out, _ = self._run([])
        self.assertNotIn("trivia_round", out)
        self.assertNotIn("emoji_puzzle", out)

    def test_include_games_adds_explicit_emoji_probe_only_when_requested(self):
        code, out, _ = self._run(["--include-games", "--only", "emoji_puzzle"])
        self.assertEqual(code, 0)
        self.assertIn("emoji puzzle game", out)
        self.assertIn("emoji_puzzle", out)
        self.assertIn("poll_payload", out)
        self.assertIn("media_types", out)
        self.assertNotIn("plain custom text", out)

    def test_only_filter_narrows_to_named_types(self):
        code, out, _ = self._run(["--only", "custom,poll"])
        self.assertEqual(code, 0)
        self.assertIn("plain custom text", out)
        self.assertIn("poll with three options", out)
        self.assertNotIn("facts_tidbit", out)
        self.assertNotIn("free_games", out)

    def test_only_filter_with_no_match_exits_two(self):
        code, _, err = self._run(["--only", "trivia_round"])
        self.assertEqual(code, 2)
        self.assertIn("matched no safe plans", err)

    def test_json_mode_emits_parseable_summary(self):
        code, out, _ = self._run(["--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "dry-run")
        self.assertEqual(len(payload["plans"]), 4)
        types = [p["message_type"] for p in payload["plans"]]
        self.assertEqual(types, ["custom", "poll", "facts_tidbit", "free_games"])

    def test_json_mode_can_emit_explicit_game_probe(self):
        code, out, _ = self._run(["--include-games", "--only", "emoji_puzzle", "--json"])
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(payload["mode"], "dry-run")
        self.assertEqual(len(payload["plans"]), 1)
        plan = payload["plans"][0]
        self.assertEqual(plan["message_type"], "emoji_puzzle")
        self.assertEqual(plan["poll_payload"]["media_types"], ["movie", "tv"])
        self.assertEqual(plan["poll_payload"]["theme_label"], "סרטים וסדרות")

    def test_marker_prefix_is_present_in_every_plan_text(self):
        # Cleanup safety: if the harness crashes mid-run, operators can grep
        # by marker to find leftover rows.
        _, out, _ = self._run([])
        self.assertGreaterEqual(out.count(smoke.SMOKE_MARKER), 4)


if __name__ == "__main__":
    unittest.main()
