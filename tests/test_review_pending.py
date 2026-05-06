import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import dashboard.app as dashboard_app


class ReviewPendingHelpersTests(unittest.TestCase):
    def test_review_page_has_no_demo_seed_source(self):
        src = Path(dashboard_app.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "emoji-puzzle-seed",
            "Emoji Night seed review",
            "פוסט קהילתי — למה בוטסון קיים",
            "סלוט 1/5",
            "trivia-israel-announce",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, src)

    def test_clear_all_keeps_pending_reviews_empty_after_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending_path = Path(tmp) / "pending_reviews.json"
            cleared_flag = Path(tmp) / ".pending_reviews_cleared"

            with patch.object(dashboard_app, "PENDING_REVIEWS_PATH", pending_path), \
                 patch.object(dashboard_app, "PENDING_REVIEWS_CLEARED_FLAG", cleared_flag):
                seeded_items = dashboard_app._load_pending_reviews()
                self.assertEqual(seeded_items, [])

                dashboard_app._clear_all_pending_reviews()

                self.assertEqual(dashboard_app._load_pending_reviews(), [])
                self.assertEqual(pending_path.read_text(encoding="utf-8").strip(), "[]")
                self.assertTrue(cleared_flag.exists())


class ReviewPendingApiTests(unittest.TestCase):
    def test_clear_all_endpoint_empties_review_queue(self):
        with tempfile.TemporaryDirectory() as tmp:
            pending_path = Path(tmp) / "pending_reviews.json"
            cleared_flag = Path(tmp) / ".pending_reviews_cleared"

            with patch.object(dashboard_app, "PENDING_REVIEWS_PATH", pending_path), \
                 patch.object(dashboard_app, "PENDING_REVIEWS_CLEARED_FLAG", cleared_flag):
                dashboard_app._save_pending_reviews([
                    {
                        "id": "one",
                        "title": "One",
                        "preview": "hello",
                    },
                    {
                        "id": "two",
                        "title": "Two",
                        "preview": "world",
                    },
                ])

                with TestClient(dashboard_app.app) as client:
                    login = client.post("/login", data={"password": dashboard_app.DASHBOARD_PASSWORD}, follow_redirects=False)
                    self.assertEqual(login.status_code, 303)

                    visible_count = len(dashboard_app._load_pending_reviews())
                    response = client.post("/api/review/clear-all")
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["cleared"], visible_count)

                    review_page = client.get("/review")
                    self.assertEqual(review_page.status_code, 200)
                    self.assertIn("אין הודעות ממתינות לאישור", review_page.text)


if __name__ == "__main__":
    unittest.main()
