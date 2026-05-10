"""facts captions: pool-specific preface line gets prepended.

The bot's spooky/tidbit posts now open with a short Hebrew preface that
sets the bot's voice ("🕯️ סיפור מסתורי מבוטסון" / "🔎 ידעתם?"). The text
lives in ``settings.yaml:copy.facts.preface_{pool}`` so it can be tuned
without a code change.

Tests pin: spooky pool prepends the spooky preface, tidbit pool prepends
the tidbit preface, missing pool keeps backwards-compatible (no prefix).
"""

from __future__ import annotations

import unittest

from bot.handlers.facts import _photo_caption_with_source


_FIXTURE = {
    "text_he": "בקיץ 1518 אישה רקדה במשך ימים.",
    "source": "Encyclopaedia Britannica",
    "source_url": "https://example.com/x",
}


class CaptionPrefacePerPool(unittest.TestCase):
    def test_spooky_caption_starts_with_mystery_preface(self):
        caption = _photo_caption_with_source(_FIXTURE, pool="spooky")
        self.assertTrue(
            caption.startswith("🕯️ סיפור מסתורי מבוטסון"),
            msg=f"caption did not start with spooky preface: {caption[:80]!r}",
        )
        # Body must still appear, after the preface.
        self.assertIn(_FIXTURE["text_he"], caption)
        self.assertIn("מקור: Encyclopaedia Britannica", caption)

    def test_tidbit_caption_starts_with_tidbit_preface(self):
        caption = _photo_caption_with_source(_FIXTURE, pool="tidbit")
        self.assertTrue(
            caption.startswith("🔎 ידעתם?"),
            msg=f"caption did not start with tidbit preface: {caption[:80]!r}",
        )

    def test_missing_pool_keeps_old_no_preface_behaviour(self):
        caption = _photo_caption_with_source(_FIXTURE)  # no pool kwarg
        # Body comes first when no preface.
        self.assertTrue(
            caption.startswith(_FIXTURE["text_he"]),
            msg=f"caption without pool should not have a preface; got {caption[:80]!r}",
        )

    def test_caption_respects_telegram_1024_char_budget(self):
        long_text = "א" * 5000  # absurdly long body
        fact = {**_FIXTURE, "text_he": long_text}
        caption = _photo_caption_with_source(fact, pool="spooky")
        # Telegram caption hard-limit is 1024; we budget under that.
        self.assertLessEqual(len(caption), 1024, msg=f"caption too long: {len(caption)}")
        # Source line still present.
        self.assertIn("מקור:", caption)


if __name__ == "__main__":
    unittest.main()
