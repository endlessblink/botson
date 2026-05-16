"""Phase 0 backfill: integration tests for /api/operator-prefs/* endpoints
and the qa_scoring score → content_feedback wire.

These cover the HTTP-glue gap: helper functions already have unit tests
(see tests/test_operator_prefs_canonical.py), but the endpoints
themselves were shipped without direct end-to-end coverage. Failure
mode without these: a refactor of the endpoint body could 500 in
production and the unit tests would still pass.
"""
from __future__ import annotations

import os
import pathlib
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from dashboard import app as dashboard_app


def _seed_prefs_file(tmpdir: pathlib.Path) -> pathlib.Path:
    """Create a minimal operator_prefs.md with the Hebrew section."""
    p = tmpdir / "operator_prefs.md"
    p.write_text(
        "---\nname: test\n---\n\n"
        "# Test\n\n"
        "## Rules\n\n"
        "### Hebrew content rules\n\n"
        "- existing rule 1\n"
        "- existing rule 2\n\n"
        "### Motion grammar\n\n"
        "- a motion rule that must not leak into hebrew prompts\n",
        encoding="utf-8",
    )
    return p


class OperatorPrefsEndpointsTest(unittest.TestCase):
    """Cover teach / untrain / proposed-rule / apply-proposal /
    qa_scoring score → content_feedback wire."""

    def setUp(self):
        self.tmpdir = pathlib.Path(tempfile.mkdtemp())
        self.db_path = self.tmpdir / "test.db"
        self.prefs_path = _seed_prefs_file(self.tmpdir)
        # Repoint module-level state at our tmp file. Each test method
        # invalidates the cache and restores in tearDown.
        self._orig_prefs_path = dashboard_app._OPERATOR_PREFS_PATH
        dashboard_app._OPERATOR_PREFS_PATH = self.prefs_path
        dashboard_app._OPERATOR_PREFS_CACHE.update(
            {"section": None, "mtime": 0.0, "loaded_at": 0.0}
        )

    def tearDown(self):
        dashboard_app._OPERATOR_PREFS_PATH = self._orig_prefs_path
        dashboard_app._OPERATOR_PREFS_CACHE.update(
            {"section": None, "mtime": 0.0, "loaded_at": 0.0}
        )

    def _client(self):
        return patch.object(dashboard_app, "DB_PATH", self.db_path), TestClient(dashboard_app.app)

    def _login(self, client: TestClient):
        r = client.post(
            "/login",
            data={"password": dashboard_app.DASHBOARD_PASSWORD},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 303, r.text)

    # ── /api/operator-prefs/hebrew (the GET we built first) ──

    def test_get_hebrew_returns_parsed_section(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.get("/api/operator-prefs/hebrew")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertEqual(data["section_name"], "Hebrew content rules")
            self.assertIn("existing rule 1", data["guidance"])
            self.assertIn("existing rule 2", data["guidance"])
            # Motion rules must not leak into the Hebrew section.
            self.assertNotIn("motion rule", data["guidance"])
            self.assertEqual(data["rule_count"], 2)

    # ── /api/operator-prefs/teach ──

    def test_teach_appends_rule_with_citation(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post(
                "/api/operator-prefs/teach",
                json={"rule": "TEST_TEACH_TOKEN — אסור.", "source": "unit test"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["ok"])
            text = self.prefs_path.read_text(encoding="utf-8")
            self.assertIn("TEST_TEACH_TOKEN", text)
            # Citation must be present.
            self.assertIn("unit test", text)
            # Motion section must be intact.
            self.assertIn("motion rule that must not leak", text)

    def test_teach_rejects_empty_rule(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post("/api/operator-prefs/teach", json={"rule": ""})
            self.assertEqual(r.status_code, 400, r.text)

    def test_teach_requires_auth(self):
        with self._client()[0], self._client()[1] as client:
            r = client.post("/api/operator-prefs/teach", json={"rule": "x"})
            self.assertEqual(r.status_code, 401)

    # ── /api/operator-prefs/untrain ──

    def test_untrain_removes_matching_lines(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            # Seed a removable line via /teach so we exercise both endpoints.
            client.post(
                "/api/operator-prefs/teach",
                json={"rule": "REMOVABLE_TOKEN — בדיקה.", "source": "unit"},
            )
            self.assertIn("REMOVABLE_TOKEN", self.prefs_path.read_text(encoding="utf-8"))
            r = client.post(
                "/api/operator-prefs/untrain",
                json={"substring": "REMOVABLE_TOKEN"},
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["removed_count"], 1)
            self.assertNotIn("REMOVABLE_TOKEN", self.prefs_path.read_text(encoding="utf-8"))

    def test_untrain_with_no_match_is_zero_op(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post(
                "/api/operator-prefs/untrain",
                json={"substring": "NONEXISTENT_TOKEN_XYZ"},
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["removed_count"], 0)
            self.assertEqual(r.json()["matches"], [])

    # ── /api/operator-prefs/proposed-rule ──

    def test_proposed_rule_returns_not_ready_below_threshold(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.get("/api/operator-prefs/proposed-rule")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertFalse(data["ready"])
            self.assertEqual(data["new_feedback_count"], 0)

    def test_proposed_rule_becomes_ready_after_5_rejections(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            for i in range(5):
                rr = client.post(
                    "/api/content-feedback",
                    json={
                        "source": "test",
                        "content_type": "discussion",
                        "topic_key": "movies",
                        "original_text": f"bad draft {i}",
                        "verdict": "rejected",
                        "reason": f"reason {i}",
                    },
                )
                self.assertEqual(rr.status_code, 200, rr.text)
            r = client.get("/api/operator-prefs/proposed-rule")
            data = r.json()
            self.assertTrue(data["ready"], data)
            self.assertGreaterEqual(data["new_feedback_count"], 5)
            self.assertTrue(data["proposed_guidance"].strip())

    # ── /api/operator-prefs/apply-proposal ──

    def test_apply_proposal_writes_to_file(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            # Trigger a feedback row for source-id tracking, then apply.
            client.post(
                "/api/content-feedback",
                json={
                    "source": "test", "content_type": "discussion",
                    "topic_key": "art", "original_text": "bad art draft",
                    "verdict": "rejected", "reason": "off-tone",
                },
            )
            r = client.post(
                "/api/operator-prefs/apply-proposal",
                json={
                    "guidance": "- TEST_APPLIED_RULE — אסור.",
                    "source_feedback_ids": [1],
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["ok"])
            text = self.prefs_path.read_text(encoding="utf-8")
            self.assertIn("TEST_APPLIED_RULE", text)
            self.assertIn("dashboard proposal approved", text)

    def test_apply_proposal_rejects_empty_guidance(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post(
                "/api/operator-prefs/apply-proposal",
                json={"guidance": "", "source_feedback_ids": []},
            )
            self.assertEqual(r.status_code, 400, r.text)

    # ── qa_scoring score → content_feedback wire ──

    def test_qa_scoring_score_1_writes_rejected_feedback(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            # Seed a draft via the (file-backed) qa_drafts store. Use the
            # module helpers directly so we don't depend on LLM availability.
            with patch.object(dashboard_app, "QA_DRAFTS_PATH", self.tmpdir / "qa_drafts.json"):
                dashboard_app._save_qa_drafts([{
                    "id": 999, "draft_type": "discussion", "category": "movies",
                    "text": "QA_TEST_DRAFT_TEXT_for_rejection",
                    "target_date": "2026-05-20", "target_time": "20:00",
                    "target_day_name": "רביעי",
                    "generated_at": "2026-05-16T10:00:00",
                    "score": None, "score_comment": None, "scored_at": None,
                }])
                r = client.post("/api/qa-scoring/999/score", json={"score": 1, "comment": "test"})
                self.assertEqual(r.status_code, 200, r.text)
            # Confirm content_feedback row was inserted with verdict=rejected.
            fb = client.get("/api/content-feedback?limit=10").json()
            matching = [
                row for row in fb["feedback"]
                if row.get("original_text") == "QA_TEST_DRAFT_TEXT_for_rejection"
            ]
            self.assertEqual(len(matching), 1, fb)
            self.assertEqual(matching[0]["verdict"], "rejected")
            self.assertEqual(matching[0]["source"], "qa_scoring")
            self.assertEqual(matching[0]["topic_key"], "movies")

    def test_qa_scoring_score_5_writes_accepted_feedback(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            with patch.object(dashboard_app, "QA_DRAFTS_PATH", self.tmpdir / "qa_drafts.json"):
                dashboard_app._save_qa_drafts([{
                    "id": 888, "draft_type": "morning", "category": None,
                    "text": "QA_TEST_DRAFT_TEXT_for_acceptance",
                    "target_date": "2026-05-21", "target_time": "08:00",
                    "target_day_name": "חמישי",
                    "generated_at": "2026-05-16T10:00:00",
                    "score": None, "score_comment": None, "scored_at": None,
                }])
                r = client.post("/api/qa-scoring/888/score", json={"score": 5, "comment": ""})
                self.assertEqual(r.status_code, 200, r.text)
            fb = client.get("/api/content-feedback?limit=10").json()
            matching = [
                row for row in fb["feedback"]
                if row.get("original_text") == "QA_TEST_DRAFT_TEXT_for_acceptance"
            ]
            self.assertEqual(len(matching), 1, fb)
            self.assertEqual(matching[0]["verdict"], "accepted")

    def test_qa_scoring_score_3_does_not_create_feedback(self):
        """Score 3 is neutral — must not pollute the feedback journal."""
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            with patch.object(dashboard_app, "QA_DRAFTS_PATH", self.tmpdir / "qa_drafts.json"):
                dashboard_app._save_qa_drafts([{
                    "id": 777, "draft_type": "evening", "category": None,
                    "text": "QA_TEST_DRAFT_TEXT_neutral",
                    "target_date": "2026-05-22", "target_time": "21:00",
                    "target_day_name": "שישי",
                    "generated_at": "2026-05-16T10:00:00",
                    "score": None, "score_comment": None, "scored_at": None,
                }])
                r = client.post("/api/qa-scoring/777/score", json={"score": 3})
                self.assertEqual(r.status_code, 200, r.text)
            fb = client.get("/api/content-feedback?limit=10").json()
            matching = [
                row for row in fb["feedback"]
                if row.get("original_text") == "QA_TEST_DRAFT_TEXT_neutral"
            ]
            self.assertEqual(matching, [])

    # ── /qa-scoring page renders (banner smoke) ──

    # ── T-184 (Gap 4): session-report endpoint ──

    def test_session_report_returns_structured_summary(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            # Seed a rejection + a teach so both surfaces have data.
            client.post(
                "/api/content-feedback",
                json={
                    "source": "test", "content_type": "discussion",
                    "topic_key": "movies", "original_text": "bad draft for report",
                    "verdict": "rejected", "reason": "test",
                },
            )
            client.post(
                "/api/operator-prefs/teach",
                json={"rule": "SESSION_REPORT_TEST_RULE", "source": "test"},
            )
            r = client.get("/api/operator-prefs/session-report")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertIn("since", data)
            self.assertIn("now", data)
            self.assertIn("rules_added", data)
            self.assertIn("feedback_summary", data)
            self.assertIn("working_memory", data)
            # The just-inserted rule should appear in rules_added.
            rule_texts = [
                (c.get("after_excerpt") or "") for c in data["rules_added"]
            ]
            self.assertTrue(
                any("SESSION_REPORT_TEST_RULE" in t for t in rule_texts),
                rule_texts,
            )
            # Feedback summary should count the rejection.
            self.assertGreaterEqual(data["feedback_summary"]["total"], 1)
            self.assertGreaterEqual(
                data["feedback_summary"]["by_verdict"].get("rejected", 0), 1,
            )

    def test_session_report_respects_since_filter(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            client.post(
                "/api/operator-prefs/teach",
                json={"rule": "OLDER_RULE", "source": "test"},
            )
            # Query with a future timestamp → should see zero rules.
            future = "2099-01-01T00:00:00"
            r = client.get(f"/api/operator-prefs/session-report?since={future}")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["rules_added"], [])

    def test_pool_health_returns_per_pool_breakdown(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.get("/api/operator-prefs/pool-health")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertIn("pools", data)
            for pool_key in ("facts_tidbit", "facts_spooky"):
                self.assertIn(pool_key, data["pools"], data)
                p = data["pools"][pool_key]
                # Each pool must report all five counts.
                for k in ("total", "excluded_recent", "excluded_rejected",
                          "excluded_scheduled", "usable", "exhausted"):
                    self.assertIn(k, p)

    # ── T-187 (Gap 2): promote-feedback endpoint ──

    def test_promote_feedback_appends_rule_immediately(self):
        """A single rejection becomes a permanent rule via /promote-feedback
        — no N=5 wait. Reuses _summarize_feedback_to_guidance and the
        same write path as /apply-proposal."""
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            # Seed one detailed rejection with a non-trivial reason (this is
            # the realistic case Gap 2 was built for).
            fb = client.post(
                "/api/content-feedback",
                json={
                    "source": "planner_ai_suggest", "content_type": "discussion",
                    "topic_key": "vegan",
                    "original_text": "PROMOTE_TEST — בדיקת קידום",
                    "verdict": "rejected",
                    "reason": "good direction but bad wording — instead of סל maybe מקרר?",
                },
            )
            self.assertEqual(fb.status_code, 200, fb.text)
            feedback_id = fb.json()["id"]
            # Promote it.
            r = client.post(f"/api/operator-prefs/promote-feedback/{feedback_id}")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["feedback_id"], feedback_id)
            self.assertGreater(data["appended_chars"], 0)
            # File must contain the rejection's text or reason.
            text = self.prefs_path.read_text(encoding="utf-8")
            self.assertIn("planner deny → promote-now", text)
            # The summary must include the operator's reason.
            self.assertIn("bad wording", text)

    def test_promote_feedback_404_for_unknown_id(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post("/api/operator-prefs/promote-feedback/999999")
            self.assertEqual(r.status_code, 404, r.text)

    def test_promote_feedback_requires_auth(self):
        with self._client()[0], self._client()[1] as client:
            r = client.post("/api/operator-prefs/promote-feedback/1")
            self.assertEqual(r.status_code, 401)

    def test_qa_scoring_page_renders_with_banner_markup(self):
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.get("/qa-scoring")
            self.assertEqual(r.status_code, 200, r.text)
            # Banner markup is present even when hidden.
            self.assertIn("prefs-banner", r.text)
            self.assertIn("checkProposalBanner", r.text)


if __name__ == "__main__":
    unittest.main()
