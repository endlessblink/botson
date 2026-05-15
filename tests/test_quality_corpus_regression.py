"""T-173 regression corpus: every text in fixtures/quality_corpus/bad must
fail the freshness gate (against the live pool as `source_examples` for the
matching field); every text in fixtures/quality_corpus/good must pass.

Seeded from real `data/bot.db` cycling auto-rows on 2026-05-15. Grow the
corpus over time as operators reject suggestions in `/review` — once
T-174 (style-profile learning) is live, `content_feedback` entries with
verdict='rejected' should be promoted into bad/ here.

If a `good/*.txt` starts failing, investigate the freshness rule that
changed — it may have over-fit. If a `bad/*.txt` starts passing, the
corresponding ban list shrunk or the near-dup threshold drifted.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from bot.utils.freshness import freshness_rejection
from bot.utils.config import load_yaml

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "quality_corpus"


def _load_pool_for_field(text: str) -> set[str]:
    """Best-effort pool detection so the corpus catches verbatim-echo bad
    outputs. We pool together discussion (all categories), morning, and
    evening; that's the matrix the corpus targets today.
    """
    pool: set[str] = set()
    try:
        prompts = load_yaml("prompts.yaml") or {}
        for items in (prompts.get("morning") or []), (prompts.get("evening") or []):
            pool.update(str(x) for x in items)
    except Exception:
        pass
    try:
        discussions = load_yaml("discussions.yaml") or {}
        for items in discussions.values():
            if isinstance(items, list):
                pool.update(str(x) for x in items)
    except Exception:
        pass
    return pool


def _all_used_texts() -> set[str]:
    """Treat every good fixture as a hypothetical prior send, so the
    `bad/*.txt` near-dup paraphrases that bot history catches show up
    in the regression — this lets us pin paraphrase rejection without
    standing up a SQLite fixture for every test.
    """
    used: set[str] = set()
    good_dir = FIXTURE_ROOT / "good"
    if good_dir.is_dir():
        for path in good_dir.glob("*.txt"):
            used.add(path.read_text(encoding="utf-8").strip())
    return used


class BadCorpusMustReject(unittest.TestCase):
    def test_every_bad_text_is_rejected(self):
        bad_dir = FIXTURE_ROOT / "bad"
        self.assertTrue(bad_dir.is_dir(), f"missing {bad_dir}")
        pool = _load_pool_for_field("")
        avoid = _all_used_texts()
        failures: list[str] = []
        for path in sorted(bad_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            reason = freshness_rejection(text, avoid_texts=avoid, source_examples=pool)
            if not reason:
                failures.append(f"{path.name}: not rejected (text={text[:80]!r})")
        self.assertEqual(failures, [], msg="\n  ".join(["bad corpus leaks:"] + failures))


class GoodCorpusMustPass(unittest.TestCase):
    def test_every_good_text_passes_freshness(self):
        good_dir = FIXTURE_ROOT / "good"
        self.assertTrue(good_dir.is_dir(), f"missing {good_dir}")
        # For good fixtures we don't pass them in as avoid (that would
        # trivially reject by exact match). We pass an EMPTY pool so the
        # source-example check doesn't reject pool-derived good texts —
        # those are pool items by design, and the corpus represents the
        # ideal *generated* text shape, not what the LLM would produce
        # given the current pool.
        failures: list[str] = []
        for path in sorted(good_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8").strip()
            if not text:
                continue
            reason = freshness_rejection(text, avoid_texts=set(), source_examples=set())
            if reason:
                failures.append(f"{path.name}: rejected — {reason}")
        self.assertEqual(failures, [], msg="\n  ".join(["good corpus regressions:"] + failures))


class CorpusHasContent(unittest.TestCase):
    """Guard against the corpus getting silently emptied — protects T-174's
    style-profile learning, which expects this dir to grow over time."""

    def test_minimum_corpus_size(self):
        bad = list((FIXTURE_ROOT / "bad").glob("*.txt"))
        good = list((FIXTURE_ROOT / "good").glob("*.txt"))
        self.assertGreaterEqual(len(bad), 4, msg=f"bad corpus too small: {len(bad)}")
        self.assertGreaterEqual(len(good), 4, msg=f"good corpus too small: {len(good)}")


if __name__ == "__main__":
    unittest.main()
