"""T-116: the /events form must not auto-fill the title with a date string.

The form has dedicated `event-date` and `event-time` inputs. A title
that is *also* a date string is pure redundancy, and the previous
``_autoTitlePrev`` guard that tried to preserve user/poll-supplied
titles was unreliable — date strings ended up clobbering poll question
text (e.g. "Splendor או משהו אחר" → "יום שישי | 24.4 | 18:30") and
shipping to Telegram as the announcement title.

The fix removes the auto-title machinery entirely. Title is strictly
user-controlled or filled by ``_applyPollMeta`` from a poll question.
This test pins the absence of the removed code paths.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[1]
_EVENTS_HTML = _REPO_ROOT / "dashboard" / "templates" / "events.html"


def _strip_js_comments(src: str) -> str:
    """Remove // single-line comments so historical references in
    explanatory comments don't trip substring assertions on executable
    code. Block comments are left in place — none of the tests below
    look inside them.
    """
    return re.sub(r"//[^\n]*", "", src)


class EventsTitleAutoFillRemoved(unittest.TestCase):
    def setUp(self):
        self.src_executable = _strip_js_comments(_EVENTS_HTML.read_text(encoding="utf-8"))

    def test_no_autoTitleFor_function(self):
        self.assertNotIn(
            "function _autoTitleFor",
            self.src_executable,
            msg="_autoTitleFor was removed in T-116; reintroducing it brings back the date-string title overwrite bug.",
        )

    def test_no_maybeRetitleFromDateTime_function(self):
        self.assertNotIn(
            "function _maybeRetitleFromDateTime",
            self.src_executable,
            msg="_maybeRetitleFromDateTime was removed in T-116.",
        )

    def test_no_autoTitlePrev_variable(self):
        self.assertNotIn(
            "_autoTitlePrev",
            self.src_executable,
            msg="_autoTitlePrev tracking variable was removed in T-116.",
        )

    def test_no_HE_DAYS_array_in_executable(self):
        # The HE_DAYS array was only used by _autoTitleFor.
        self.assertNotIn(
            "var HE_DAYS = ['ראשון'",
            self.src_executable,
            msg="HE_DAYS array was only used by the removed title auto-filler.",
        )

    def test_onAnyFieldChange_does_not_call_retitle(self):
        # Locate the function and verify it doesn't call the removed
        # _maybeRetitleFromDateTime helper.
        match = re.search(r"function _onAnyFieldChange\(\) \{[\s\S]+?\n\}", self.src_executable)
        self.assertIsNotNone(match, "could not locate _onAnyFieldChange in events.html")
        body = match.group(0)
        self.assertNotIn("_maybeRetitleFromDateTime", body)
        # It should still call the side effects that DO matter.
        self.assertIn("saveDraft", body)
        self.assertIn("updatePreview", body)


if __name__ == "__main__":
    unittest.main()
