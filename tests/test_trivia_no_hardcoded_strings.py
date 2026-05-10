"""T-115: trivia generation/teaser must not leak hardcoded specific strings.

Two regression sites:

1. ``dashboard/templates/planner.html:_buildDefaultTeaser`` used to substitute
   "הפינה של בוטסון" when the play-channel label was empty — leaking the
   wrong channel name into any user-themed round whose play channel was
   somewhere else. Fix: drop the "ב{channel}" clause entirely when the
   label is missing.

2. ``dashboard/app.py:build_generation_prompt`` (field='trivia') used to
   fall back to ``"נושאים מגוונים: תרבות, מדע, היסטוריה, בידור, גאוגרפיה,
   אוכל."`` whenever the operator provided neither a theme nor categories.
   Per CLAUDE.md, defaults that steer outcomes count as hardcoded
   content — this list biased generation toward six specific subjects.
   Fix: emit a topic-line with no specific subjects.

Tests pin the absence of the leaked strings + the presence of correct
behaviour for the populated case (so we don't regress to "no fallback at
all").
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from dashboard.app import build_generation_prompt


_REPO_ROOT = Path(__file__).resolve().parents[1]
_PLANNER_HTML = _REPO_ROOT / "dashboard" / "templates" / "planner.html"


class PlannerTeaserNoChannelLeak(unittest.TestCase):
    def test_planner_default_teaser_does_not_hardcode_botson_corner(self):
        src = _PLANNER_HTML.read_text(encoding="utf-8")
        match = re.search(r"_buildDefaultTeaser[\s\S]+?\n\}", src)
        self.assertIsNotNone(match, "could not locate _buildDefaultTeaser block")
        block = match.group(0)
        # Strip // line-comments so the historical reference in the
        # explanatory comment doesn't trip the substring check — only
        # *executable* JS counts as a leak.
        executable = re.sub(r"//[^\n]*", "", block)
        self.assertNotIn(
            "הפינה של בוטסון",
            executable,
            msg="_buildDefaultTeaser must not substitute a hardcoded channel name fallback in executable code",
        )

    def test_planner_default_teaser_drops_channel_clause_when_missing(self):
        # The new shape uses a `channelClause` ternary that's empty when
        # playLabel is empty.
        src = _PLANNER_HTML.read_text(encoding="utf-8")
        self.assertIn(
            "playLabel ? (' ב' + playLabel) : ''",
            src,
            msg="expected `channelClause` to drop the channel when playLabel is empty",
        )


class TriviaPromptNoTopicListLeak(unittest.TestCase):
    def test_trivia_prompt_with_no_theme_or_category_omits_specific_topic_list(self):
        prompt = build_generation_prompt(
            field="trivia",
            mode="generate",
            existing="",
            category="",
            instructions="",
        )
        # Old hardcoded list — must not appear.
        self.assertNotIn(
            "תרבות, מדע, היסטוריה, בידור, גאוגרפיה, אוכל",
            prompt,
            msg="trivia prompt fallback must not name specific subjects (content bias)",
        )

    def test_trivia_prompt_respects_explicit_categories(self):
        prompt = build_generation_prompt(
            field="trivia",
            mode="generate",
            existing="",
            category="קולינריה",
            instructions="",
        )
        # When the operator names a category, the prompt must constrain
        # to that category — not silently override with a default list.
        self.assertIn("קולינריה", prompt)
        self.assertNotIn(
            "תרבות, מדע, היסטוריה, בידור, גאוגרפיה, אוכל",
            prompt,
            msg="explicit category must not co-exist with the old default list",
        )

    def test_trivia_prompt_respects_explicit_theme(self):
        prompt = build_generation_prompt(
            field="trivia",
            mode="generate",
            existing="",
            category="",
            instructions="מטבחים בעולם",
        )
        self.assertIn("מטבחים בעולם", prompt)


if __name__ == "__main__":
    unittest.main()
