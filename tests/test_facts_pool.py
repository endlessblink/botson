"""Integrity tests for the curated facts/spooky pool.

These don't test bot behavior — they enforce the curation discipline:
every item must have a real source, ids are unique, no AI-generated
filler, no empty placeholders. If a future contributor copy-pastes a
listicle item without a citation, the test fails before it ships.

Run with: .venv/bin/python -m unittest tests.test_facts_pool
"""
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import yaml

from bot.handlers import facts as facts_handler
from bot.handlers.facts import POOLS, format_fact_message, load_facts_pool, pick_fact

ROOT = Path(__file__).resolve().parents[1]
FACTS_PATH = ROOT / "config" / "facts.yaml"
DISCUSSIONS_PATH = ROOT / "config" / "discussions.yaml"
MIN_FACTS_PER_POOL = 40
MIN_DISCUSSIONS_PER_CATEGORY = 25


class FactsPoolIntegrityTests(unittest.TestCase):
    """Hand-curation rules that the test suite enforces, not a code style guide."""

    @classmethod
    def setUpClass(cls):
        with FACTS_PATH.open(encoding="utf-8") as f:
            cls.raw = yaml.safe_load(f) or {}

    def test_all_pools_present_and_nonempty(self):
        for pool in POOLS:
            with self.subTest(pool=pool):
                self.assertIn(pool, self.raw, f"facts.yaml missing pool: {pool}")
                self.assertTrue(self.raw[pool], f"pool {pool} is empty")

    def test_facts_and_discussion_pools_are_large_enough_for_cooldowns(self):
        for pool in POOLS:
            with self.subTest(pool=pool):
                self.assertGreaterEqual(
                    len(self.raw.get(pool, [])),
                    MIN_FACTS_PER_POOL,
                    f"facts.yaml:{pool} must keep at least {MIN_FACTS_PER_POOL} curated items",
                )

        with DISCUSSIONS_PATH.open(encoding="utf-8") as f:
            discussions = yaml.safe_load(f) or {}
        for category, items in discussions.items():
            with self.subTest(category=category):
                self.assertGreaterEqual(
                    len(items or []),
                    MIN_DISCUSSIONS_PER_CATEGORY,
                    f"discussions.yaml:{category} must keep at least {MIN_DISCUSSIONS_PER_CATEGORY} prompts",
                )

    def test_every_item_has_id_text_source_and_image(self):
        for pool in POOLS:
            for entry in self.raw.get(pool, []):
                with self.subTest(pool=pool, entry=entry.get("id", "?")):
                    self.assertTrue(entry.get("id"), "missing id")
                    self.assertTrue(entry.get("text_he"), "missing text_he")
                    self.assertTrue(
                        entry.get("source"),
                        "EVERY fact must cite a source — bar is 'would forward to a friend'",
                    )
                    self.assertTrue(entry.get("source_url"), "EVERY fact must include a source URL")
                    self.assertTrue(
                        entry.get("image_url") or entry.get("image_prompt"),
                        "EVERY fact must have a relevant associated image_url or image_prompt",
                    )

    def test_ids_unique_within_pool(self):
        for pool in POOLS:
            ids = [e.get("id") for e in self.raw.get(pool, [])]
            self.assertEqual(
                len(ids), len(set(ids)),
                f"duplicate ids in pool {pool}: {[i for i in ids if ids.count(i) > 1]}",
            )

    def test_text_is_hebrew_substantive(self):
        """Each item must contain Hebrew characters and be at least ~20 chars —
        guards against an empty / English-only / placeholder slip-through."""
        hebrew_range = range(0x0590, 0x05FF + 1)

        def _has_hebrew(s: str) -> bool:
            return any(ord(c) in hebrew_range for c in s)

        for pool in POOLS:
            for entry in self.raw.get(pool, []):
                text = entry.get("text_he", "")
                with self.subTest(pool=pool, id=entry.get("id")):
                    self.assertTrue(_has_hebrew(text), "text_he must contain Hebrew")
                    self.assertGreaterEqual(
                        len(text.strip()), 50,
                        "fact too short to be substantive (curation bar)",
                    )

    def test_spooky_items_have_context_not_just_atmosphere(self):
        """Spooky posts are mini-articles, not one-line ghost bait.

        They need enough setup to explain where the story comes from, and they
        must separate documented facts from folklore/uncertainty.
        """
        grounding_markers = (
            "לפי", "אין תיעוד", "לא ברור", "מסורת", "פולקלור",
            "מחקר", "החקירה", "ספריית", "פרופ", "מתוארך", "ב-",
        )
        banned_shallow_phrases = (
            "מאות שנים של סיפורים",
            "דיווחו תושבים לאורך עשרות שנים",
        )
        for entry in self.raw.get("spooky", []):
            text = entry.get("text_he", "")
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            with self.subTest(id=entry.get("id")):
                self.assertGreaterEqual(
                    len(lines), 3,
                    "spooky item needs setup + story + caveat/context, not a short claim dump",
                )
                self.assertTrue(
                    any(marker in text for marker in grounding_markers),
                    "spooky item must include a source/folklore/uncertainty marker in the Hebrew text",
                )
                for phrase in banned_shallow_phrases:
                    self.assertNotIn(phrase, text)

    def test_facts_hebrew_rejects_known_bad_wording(self):
        banned_fragments = (
            "אישתו",
            "שם השם המפורש",
            "המאות ה-9-10",
            "כדי לעוף ממנו",
            "שדת לידה",
        )
        for pool in POOLS:
            for entry in self.raw.get(pool, []):
                text = entry.get("text_he", "")
                with self.subTest(pool=pool, id=entry.get("id")):
                    for fragment in banned_fragments:
                        self.assertNotIn(fragment, text)

    def test_source_looks_like_real_citation(self):
        """A real citation has at least one of: author surname, year (4 digits),
        journal/venue/library name, or URL. Not bulletproof — meant to catch
        'TODO' and 'wikipedia' as sole-source entries."""
        import re
        year_re = re.compile(r"\b(?:19|20)\d{2}\b")
        venue_keywords = (
            "Press", "Journal", "Nature", "Science", "Library", "University",
            "Museum", "Post", "Magazine", "Society", "Review", "Communications",
            "Atlas", "Encyclopedia", "Wikipedia", "Ministry", "Archive",
            "Foundation", "Institute", "Israel", "France", "Britain",
        )
        url_markers = (".com", ".org", ".il", ".edu", ".gov", ".fr", ".uk", ".net", "http")
        for pool in POOLS:
            for entry in self.raw.get(pool, []):
                src = entry.get("source", "")
                with self.subTest(pool=pool, id=entry.get("id")):
                    has_year = bool(year_re.search(src))
                    has_venue = any(k in src for k in venue_keywords)
                    source_url = entry.get("source_url", "")
                    has_url = any(m in src for m in url_markers)
                    self.assertTrue(
                        has_year or has_venue or has_url,
                        f"source looks like a placeholder, not a citation: {src!r}",
                    )
                    self.assertRegex(source_url, r"^https?://", "source_url must point to an actual source")

    def test_load_facts_pool_returns_normalized_dicts(self):
        for pool in POOLS:
            items = load_facts_pool(pool)
            self.assertTrue(items)
            for item in items:
                self.assertTrue({"id", "text_he", "source", "source_url"}.issubset(set(item.keys())))
                self.assertTrue(item.get("image_url") or item.get("image_prompt"))

    def test_format_fact_message_includes_source(self):
        msg = format_fact_message({
            "text_he": "עובדה מסקרנת",
            "source": "Journal Example (2024).",
            "source_url": "https://example.org/paper",
        })
        self.assertIn("עובדה מסקרנת", msg)
        self.assertIn("מקור: Journal Example (2024).", msg)
        self.assertIn("https://example.org/paper", msg)

    def test_pick_fact_excludes_recently_sent(self):
        items = load_facts_pool("tidbit")
        all_ids = [i["id"] for i in items]
        # Mark all but one as recent — the picker must return the remaining one.
        recent = all_ids[:-1]
        picked = pick_fact("tidbit", recent)
        self.assertIsNotNone(picked)
        self.assertEqual(picked["id"], all_ids[-1])

    def test_pick_fact_returns_none_when_all_on_cooldown(self):
        """If every fact is in the cooldown window, the picker must NOT
        silently pick a repeat — it must return None and let the caller skip."""
        items = load_facts_pool("spooky")
        all_ids = [i["id"] for i in items]
        self.assertIsNone(pick_fact("spooky", all_ids))

    def test_pick_fact_rejects_unknown_pool(self):
        with self.assertRaises(ValueError):
            load_facts_pool("not_a_real_pool")


class FactsSendTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_scheduled_fact_uses_photo_with_source_when_generation_available(self):
        class FakeDb:
            async def get_handler_routing(self, handler):
                return None

            async def log_activity(self, *args, **kwargs):
                return None

        picked = {
            "id": "x",
            "text_he": "עובדה מסקרנת עם מקור",
            "source": "Science Example (2024).",
            "source_url": "https://example.org/paper",
            "image_prompt": "Relevant image of the fact, no text.",
        }
        with patch.object(facts_handler, "pick_fact", return_value=picked), \
             patch.dict("os.environ", {"KIE_API_KEY": "key"}), \
             patch("bot.utils.kie_client.generate_image_sync", new=AsyncMock(return_value=(b"img", "png"))), \
             patch.object(facts_handler, "safe_send", new=AsyncMock()) as safe_send:
            sent = await facts_handler.send_scheduled_fact(
                object(), FakeDb(), pool="tidbit", chat_id=-1001, thread_id=4037,
            )

        self.assertTrue(sent)
        safe_send.assert_awaited_once()
        kwargs = safe_send.await_args.kwargs
        self.assertEqual(safe_send.await_args.args[2], "send_photo")
        self.assertIn("מקור: Science Example (2024).", kwargs["caption"])
        self.assertIn("https://example.org/paper", kwargs["caption"])
        self.assertEqual(kwargs["message_thread_id"], 4037)

    async def test_send_scheduled_fact_uses_curated_image_url_with_source(self):
        class FakeDb:
            async def get_handler_routing(self, handler):
                return None

            async def log_activity(self, *args, **kwargs):
                return None

        picked = {
            "id": "x",
            "text_he": "עובדה מסקרנת עם מקור",
            "source": "Science Example (2024).",
            "source_url": "https://example.org/paper",
            "image_url": "https://example.org/relevant-image.jpg",
        }
        with patch.object(facts_handler, "pick_fact", return_value=picked), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(facts_handler, "safe_send", new=AsyncMock()) as safe_send:
            sent = await facts_handler.send_scheduled_fact(
                object(), FakeDb(), pool="spooky", chat_id=-1001, thread_id=4037,
            )

        self.assertTrue(sent)
        self.assertEqual(safe_send.await_args.args[2], "send_photo")
        self.assertEqual(safe_send.await_args.kwargs["photo"], "https://example.org/relevant-image.jpg")
        self.assertIn("מקור: Science Example (2024).", safe_send.await_args.kwargs["caption"])
        self.assertIn("https://example.org/paper", safe_send.await_args.kwargs["caption"])

    async def test_send_scheduled_fact_skips_when_no_image_can_be_resolved(self):
        class FakeDb:
            async def get_handler_routing(self, handler):
                return None

            async def log_activity(self, *args, **kwargs):
                raise AssertionError("activity should not be logged for skipped fact")

        picked = {
            "id": "x",
            "text_he": "עובדה מסקרנת עם מקור",
            "source": "Science Example (2024).",
            "source_url": "https://example.org/paper",
            "image_prompt": "Relevant image of the fact, no text.",
        }
        with patch.object(facts_handler, "pick_fact", return_value=picked), \
             patch.dict("os.environ", {}, clear=True), \
             patch.object(facts_handler, "safe_send", new=AsyncMock()) as safe_send:
            sent = await facts_handler.send_scheduled_fact(
                object(), FakeDb(), pool="spooky", chat_id=-1001, thread_id=4037,
            )

        self.assertFalse(sent)
        safe_send.assert_not_awaited()

    def test_loader_drops_fact_without_source_url_or_image(self):
        sample = {
            "tidbit": [
                {"id": "no_source_url", "text_he": "עובדה בעברית מספיק ארוכה לבדיקה", "source": "Science Example (2024).", "image_prompt": "Relevant image"},
                {"id": "no_image", "text_he": "עובדה בעברית מספיק ארוכה לבדיקה", "source": "Science Example (2024).", "source_url": "https://example.org/paper"},
                {"id": "ok", "text_he": "עובדה בעברית מספיק ארוכה לבדיקה", "source": "Science Example (2024).", "source_url": "https://example.org/paper", "image_prompt": "Relevant image"},
            ]
        }
        with patch.object(facts_handler, "load_yaml", return_value=sample):
            self.assertEqual([item["id"] for item in load_facts_pool("tidbit")], ["ok"])


if __name__ == "__main__":
    unittest.main()
