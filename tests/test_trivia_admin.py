import tempfile
import unittest
from pathlib import Path

from dashboard.trivia_admin import (
    TriviaVerificationError,
    build_round_trigger_payload,
    save_and_verify_trivia_questions,
)


class SaveAndVerifyTriviaQuestionsTests(unittest.TestCase):
    def test_save_and_verify_round_trips_questions(self):
        questions = [
            {
                "text": "מה בירת ישראל?",
                "options": ["חיפה", "ירושלים", "תל אביב", "באר שבע"],
                "correct": 1,
                "category": "ישראל",
            },
            {
                "text": "כמה ימים יש בשבוע?",
                "options": ["5", "6", "7", "8"],
                "correct": 2,
                "category": "כללי",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trivia.yaml"
            verification = save_and_verify_trivia_questions(path, questions)

        self.assertEqual(verification["verified_count"], 2)
        self.assertEqual(verification["first_question"], "מה בירת ישראל?")
        self.assertEqual(verification["last_question"], "כמה ימים יש בשבוע?")

    def test_save_and_verify_rejects_non_dict_question_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trivia.yaml"
            with self.assertRaises(TriviaVerificationError):
                save_and_verify_trivia_questions(path, ["not-a-dict"])

    def test_save_and_verify_rejects_invalid_question_shape(self):
        questions = [
            {
                "text": "broken",
                "options": ["a", "b", "c"],
                "correct": 0,
                "category": "כללי",
            }
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trivia.yaml"
            with self.assertRaises(TriviaVerificationError):
                save_and_verify_trivia_questions(path, questions)


class BuildRoundTriggerPayloadTests(unittest.TestCase):
    def test_main_target_requires_verified_topic_and_source(self):
        with self.assertRaises(TriviaVerificationError):
            build_round_trigger_payload(
                target="main",
                main_group_id=-1001,
                test_group_id=-1002,
                pre_roll_s=30,
                topic_id=None,
                topic_verification_source="",
                theme_label="ישראל",
                categories=["ישראל"],
                question_count=5,
            )

    def test_main_target_rejects_unknown_live_topic_id(self):
        with self.assertRaises(TriviaVerificationError):
            build_round_trigger_payload(
                target="main",
                main_group_id=-1001,
                test_group_id=-1002,
                pre_roll_s=45,
                topic_id=999,
                topic_verification_source="user-confirmed Telegram UI label",
                theme_label="ישראל",
                categories=["ישראל"],
                question_count=6,
                live_topic_ids={7, 341},
            )

    def test_main_target_payload_carries_verified_topic_and_theme(self):
        payload = build_round_trigger_payload(
            target="main",
            main_group_id=-1001,
            test_group_id=-1002,
            pre_roll_s=45,
            topic_id=7,
            topic_verification_source="user-confirmed after bot sent a dot into thread 7 (vegan)",
            theme_label="ישראל",
            categories=["ישראל"],
            question_count=6,
            live_topic_ids={7, 341},
        )

        self.assertEqual(payload["chat_id"], -1001)
        self.assertEqual(payload["thread_id"], 7)
        self.assertEqual(payload["theme_label"], "ישראל")
        self.assertEqual(payload["categories"], ["ישראל"])
        self.assertEqual(payload["question_count"], 6)
        self.assertEqual(payload["target_provenance"]["topic_id"], 7)
        self.assertIn("user-confirmed", payload["target_provenance"]["verification_source"])

    def test_teaser_topic_id_roundtrip(self):
        payload = build_round_trigger_payload(
            target="main",
            main_group_id=-1001,
            test_group_id=-1002,
            pre_roll_s=45,
            topic_id=4037,
            topic_verification_source="user-confirmed",
            theme_label="movies",
            categories=["movies"],
            question_count=5,
            live_topic_ids={4037, 54},
            teaser_topic_id=54,
        )
        self.assertEqual(payload["thread_id"], 4037)
        self.assertEqual(payload["teaser_topic_id"], 54)

    def test_teaser_topic_id_rejected_when_not_in_live_set(self):
        with self.assertRaises(TriviaVerificationError):
            build_round_trigger_payload(
                target="main",
                main_group_id=-1001,
                test_group_id=-1002,
                pre_roll_s=45,
                topic_id=4037,
                topic_verification_source="user-confirmed",
                theme_label="movies",
                categories=["movies"],
                question_count=5,
                live_topic_ids={4037},
                teaser_topic_id=999,
            )

    def test_teaser_same_as_play_thread_is_dropped_silently(self):
        payload = build_round_trigger_payload(
            target="main",
            main_group_id=-1001,
            test_group_id=-1002,
            pre_roll_s=45,
            topic_id=4037,
            topic_verification_source="user-confirmed",
            theme_label="movies",
            categories=["movies"],
            question_count=5,
            live_topic_ids={4037},
            teaser_topic_id=4037,
        )
        self.assertEqual(payload["thread_id"], 4037)
        self.assertIsNone(payload["teaser_topic_id"])

    def test_test_target_allows_missing_topic(self):
        payload = build_round_trigger_payload(
            target="test",
            main_group_id=-1001,
            test_group_id=-1002,
            pre_roll_s=15,
            topic_id=None,
            topic_verification_source="",
            theme_label="ישראל",
            categories=["ישראל"],
            question_count=5,
        )

        self.assertEqual(payload["chat_id"], -1002)
        self.assertIsNone(payload["thread_id"])
        self.assertEqual(payload["target_provenance"]["verification_source"], "test-target-no-topic")


if __name__ == "__main__":
    unittest.main()
