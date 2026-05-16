"""T-189 guardian: rules in operator_prefs.md must be abstractions, not
verbatim quotes of rejected drafts.

If any rule line contains >40 consecutive characters from any draft text
in recent content_feedback rows, that's memorization, not learning. Fail
CI. See CLAUDE.md ⚠ "Abstraction over enumeration" for context.

Hooked into scripts/deploy.sh so a regression to mechanical concat
blocks deploy.
"""
from __future__ import annotations

import asyncio
import pathlib
import sqlite3
import sys
import unittest


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PREFS_PATH = REPO_ROOT / "config" / "operator_prefs.md"


def _hebrew_section_lines() -> list[str]:
    """Pull the bullet lines out of `### Hebrew content rules`."""
    if not PREFS_PATH.exists():
        return []
    text = PREFS_PATH.read_text(encoding="utf-8")
    needle = "\n### Hebrew content rules"
    idx = text.find(needle)
    if idx < 0:
        return []
    after = text[idx + len(needle):]
    end = after.find("\n### ")
    section = after if end < 0 else after[:end]
    return [
        ln.strip() for ln in section.splitlines()
        if ln.strip().startswith("- ") or ln.strip().startswith("• ")
    ]


def _draft_texts_from_db() -> list[str]:
    """Recent original_text values from content_feedback. Used as the
    'don't quote these verbatim' reference set."""
    db_path = REPO_ROOT / "data" / "bot.db"
    if not db_path.exists():
        return []
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.execute(
            "SELECT original_text FROM content_feedback "
            "WHERE verdict IN ('rejected','bad_wording') "
            "ORDER BY created_at DESC LIMIT 200"
        )
        return [(r[0] or "").strip() for r in cur.fetchall() if r[0]]
    except Exception:
        return []
    finally:
        con.close()


def _longest_common_substring_len(a: str, b: str) -> int:
    """Length of the longest substring shared by a and b. O(n*m) DP, but
    n,m are <500 in practice."""
    if not a or not b:
        return 0
    m, n = len(a), len(b)
    prev = [0] * (n + 1)
    best = 0
    for i in range(1, m + 1):
        cur = [0] * (n + 1)
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


# Anything > this many consecutive characters shared between a rule and
# a draft text is memorization, not abstraction. The threshold is
# generous — short common phrases like "אל תייצר" or "בערוץ" can
# legitimately overlap by 10-20 chars.
VERBATIM_THRESHOLD = 40


class NoVerbatimQuotesInRules(unittest.TestCase):
    """The 100th-loop guardian. Forbids the deterministic-concat
    regression that shipped the 'אל תייצרו טקסט בסגנון: "..."' pattern."""

    def test_rules_do_not_quote_drafts_verbatim(self):
        rules = _hebrew_section_lines()
        drafts = _draft_texts_from_db()
        if not rules or not drafts:
            self.skipTest("no rules or no recent rejections — nothing to compare")
        offenders: list[tuple[str, str, int]] = []
        for rule in rules:
            for draft in drafts:
                shared = _longest_common_substring_len(rule, draft)
                if shared > VERBATIM_THRESHOLD:
                    offenders.append((rule[:100], draft[:80], shared))
                    break  # one offence per rule is enough
        if offenders:
            lines = [
                f"  rule: {r}\n  matched draft: {d}\n  shared chars: {n}"
                for r, d, n in offenders[:5]
            ]
            self.fail(
                f"\n\nFound {len(offenders)} rule(s) that verbatim-quote recent "
                f"rejected drafts (>{VERBATIM_THRESHOLD} consecutive shared chars). "
                f"This is memorization, not learning.\n\n"
                f"First {min(5, len(offenders))} offender(s):\n" + "\n\n".join(lines) +
                "\n\nFix: ensure the LLM-abstraction path (_llm_abstract_rules) is "
                "writing these rules, not a deterministic concatenator. See CLAUDE.md "
                "⚠ 'Abstraction over enumeration'."
            )

    def test_summarize_feedback_to_guidance_raises_loudly(self):
        """The stub `_summarize_feedback_to_guidance` must raise when
        called, not silently return text. Prevents accidental
        resurrection of the deleted function."""
        sys.path.insert(0, str(REPO_ROOT))
        from dashboard.app import _summarize_feedback_to_guidance
        with self.assertRaises(RuntimeError) as ctx:
            _summarize_feedback_to_guidance([{"reason": "test", "original_text": "x", "verdict": "rejected"}])
        self.assertIn("DELETED", str(ctx.exception).upper())
        self.assertIn("_llm_abstract_rules", str(ctx.exception))


class LlmAbstractRulesShape(unittest.IsolatedAsyncioTestCase):
    """Smoke test for the LLM abstraction shape — mocks the API call."""

    async def test_empty_input_returns_empty(self):
        sys.path.insert(0, str(REPO_ROOT))
        from dashboard.app import _llm_abstract_rules
        self.assertEqual(await _llm_abstract_rules([]), "")

    async def test_llm_failure_returns_empty_not_fallback(self):
        """When the LLM call raises, we must return '' — NOT fall back
        to a deterministic quote-the-text path."""
        sys.path.insert(0, str(REPO_ROOT))
        from unittest.mock import patch
        from dashboard import app as dashboard_app
        rows = [{
            "reason": "test reason", "original_text": "test text",
            "verdict": "rejected", "topic_key": "movies", "content_type": "discussion",
        }]
        async def boom(prompt):
            raise RuntimeError("simulated API failure")
        with patch.object(dashboard_app, "_generate_via_api", boom):
            result = await dashboard_app._llm_abstract_rules(rows)
        self.assertEqual(result, "")  # NOT "אל תייצרו טקסט בסגנון..."


if __name__ == "__main__":
    unittest.main()
