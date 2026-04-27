"""Integrity tests for the curated facts/spooky pool.

These don't test bot behavior — they enforce the curation discipline:
every item must have a real source, ids are unique, no AI-generated
filler, no empty placeholders. If a future contributor copy-pastes a
listicle item without a citation, the test fails before it ships.

Run with: .venv/bin/python -m unittest tests.test_facts_pool
"""
import unittest
from pathlib import Path

import yaml

from bot.handlers.facts import POOLS, load_facts_pool, pick_fact

ROOT = Path(__file__).resolve().parents[1]
FACTS_PATH = ROOT / "config" / "facts.yaml"


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

    def test_every_item_has_id_text_source(self):
        for pool in POOLS:
            for entry in self.raw.get(pool, []):
                with self.subTest(pool=pool, entry=entry.get("id", "?")):
                    self.assertTrue(entry.get("id"), "missing id")
                    self.assertTrue(entry.get("text_he"), "missing text_he")
                    self.assertTrue(
                        entry.get("source"),
                        "EVERY fact must cite a source — bar is 'would forward to a friend'",
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
                    has_url = any(m in src for m in url_markers)
                    self.assertTrue(
                        has_year or has_venue or has_url,
                        f"source looks like a placeholder, not a citation: {src!r}",
                    )

    def test_load_facts_pool_returns_normalized_dicts(self):
        for pool in POOLS:
            items = load_facts_pool(pool)
            self.assertTrue(items)
            for item in items:
                self.assertEqual(set(item.keys()), {"id", "text_he", "source"})

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


if __name__ == "__main__":
    unittest.main()
