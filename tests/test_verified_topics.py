import unittest

from dashboard.verified_topics import (
    VerifiedTopicError,
    build_verified_topic_index,
    merge_observed_and_verified_topics,
    normalize_verified_topic_entry,
    resolve_verified_topic_id,
)


class NormalizeVerifiedTopicEntryTests(unittest.TestCase):
    def test_normalizes_valid_entry(self):
        entry = normalize_verified_topic_entry(
            topic_id="341",
            verified_name="עדכונים",
            category_key="welcome",
            verification_source="user confirmed in Telegram UI",
        )
        self.assertEqual(entry["topic_id"], 341)
        self.assertEqual(entry["verified_name"], "עדכונים")
        self.assertEqual(entry["category_key"], "welcome")

    def test_rejects_missing_name(self):
        with self.assertRaises(VerifiedTopicError):
            normalize_verified_topic_entry(
                topic_id=341,
                verified_name="",
                category_key="welcome",
                verification_source="user confirmed",
            )


class VerifiedTopicIndexTests(unittest.TestCase):
    def test_build_index_and_resolve_topic_id(self):
        rows = [
            {"topic_id": 101, "verified_name": "Verified Topic A", "category_key": "welcome", "verification_source": "manual confirm"},
            {"topic_id": 202, "verified_name": "Verified Topic B", "category_key": "vegan", "verification_source": "manual confirm"},
        ]
        index = build_verified_topic_index(rows)
        self.assertEqual(index["welcome"]["topic_id"], 101)
        self.assertEqual(resolve_verified_topic_id("vegan", rows), 202)

    def test_resolve_verified_topic_id_returns_none_when_missing(self):
        self.assertIsNone(resolve_verified_topic_id("general", []))

    def test_merge_observed_and_verified_topics_marks_verified_rows(self):
        observed = [{"topic_id": 101, "name": "Observed Name", "last_seen_at": "2026-04-22 10:00:00"}]
        verified = [{"topic_id": 101, "verified_name": "Trusted Name", "category_key": "welcome", "verification_source": "manual confirm", "verified_at": "2026-04-22 10:01:00"}]
        merged = merge_observed_and_verified_topics(observed, verified)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["is_verified"])
        self.assertEqual(merged[0]["verified_name"], "Trusted Name")
        self.assertEqual(merged[0]["observed_name"], "Observed Name")


if __name__ == "__main__":
    unittest.main()
