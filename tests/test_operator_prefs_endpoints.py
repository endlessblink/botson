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
        # T-189: mock the LLM call so tests don't hit Anthropic API.
        # Returns a synthetic abstract rule that contains a unique token
        # so tests can assert "the LLM path ran" without asserting on the
        # specific draft text (which would defeat the abstraction).
        self._orig_generate_via_api = dashboard_app._generate_via_api
        self._orig_generate_via_cli = dashboard_app._generate_via_cli

        async def _mock_llm(prompt: str) -> str:
            # Echo the test's expected token if the prompt mentions it,
            # otherwise return a generic abstract rule.
            if "AUTO_LEARN_TEST" in prompt:
                return "- LLM_ABSTRACTED: אל תייצר שאלות גנריות שמתאימות לכל ערוץ."
            if "AUTO_CORRECTED_TOKEN" in prompt:
                return "- LLM_ABSTRACTED: השתמש בניסוח חבר-בקבוצה כפי שהאופרטור הראה."
            if "PROMOTE_TEST" in prompt:
                return "- LLM_ABSTRACTED: כלל שנלמד מהדחייה (לא ציטוט מילולי)."
            return "- LLM_ABSTRACTED: כלל מופשט גנרי לבדיקה."

        # Patch both — the production path tries CLI first, then API.
        dashboard_app._generate_via_api = _mock_llm
        dashboard_app._generate_via_cli = _mock_llm
        # T-189: collapse debounce to ~instant for tests.
        self._orig_debounce = dashboard_app._ABSTRACTION_DEBOUNCE_SECONDS
        dashboard_app._ABSTRACTION_DEBOUNCE_SECONDS = 0.01

    def tearDown(self):
        dashboard_app._OPERATOR_PREFS_PATH = self._orig_prefs_path
        dashboard_app._OPERATOR_PREFS_CACHE.update(
            {"section": None, "mtime": 0.0, "loaded_at": 0.0}
        )
        dashboard_app._generate_via_api = self._orig_generate_via_api
        dashboard_app._generate_via_cli = self._orig_generate_via_cli
        dashboard_app._ABSTRACTION_DEBOUNCE_SECONDS = self._orig_debounce
        # Make sure any in-flight debounced task finishes before next test.
        task = dashboard_app._PENDING_ABSTRACTION_TASK
        if task is not None and not task.done():
            task.cancel()
        dashboard_app._PENDING_ABSTRACTION_IDS.clear()

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

    # ── T-188 (Gap 2 v2): autonomous learning on rejection ──

    def test_substantive_rejection_auto_promotes_to_rule(self):
        """A rejection with a real reason (>15 chars) schedules an LLM
        abstraction. After the debounced task fires, the file has an
        ABSTRACTED rule (not a verbatim quote of the rejected text)."""
        import time
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post(
                "/api/content-feedback",
                json={
                    "source": "planner_ai_suggest", "content_type": "discussion",
                    "topic_key": "movies",
                    "original_text": "AUTO_LEARN_TEST — שאלת קלות גנרית",
                    "verdict": "rejected",
                    "reason": "good direction but bad wording — say מקרר not סל, pantry/freezer questions are good",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["auto_promoted"], body)
            # T-189: excerpt is "pending" — the actual write happens
            # asynchronously via the debounce task.
            self.assertIn("pending", (body["promoted_excerpt"] or "").lower())
            # Wait for the debounced LLM task to land the rule.
            for _ in range(20):
                time.sleep(0.05)
                if "LLM_ABSTRACTED" in self.prefs_path.read_text(encoding="utf-8"):
                    break
            text = self.prefs_path.read_text(encoding="utf-8")
            # T-189: rule is ABSTRACTED, not a verbatim quote.
            self.assertIn("LLM_ABSTRACTED", text)
            # Anti-regression: the rejected draft text must NOT appear verbatim.
            self.assertNotIn("AUTO_LEARN_TEST — שאלת קלות גנרית", text)

    def test_trivial_rejection_does_not_auto_promote(self):
        """An empty or near-empty reason stays in working memory only —
        not auto-promoted. Avoids noise from bare qa_score=1 verdicts."""
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post(
                "/api/content-feedback",
                json={
                    "source": "qa_scoring", "content_type": "discussion",
                    "topic_key": "art", "original_text": "trivial",
                    "verdict": "rejected", "reason": "qa_score=1",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["auto_promoted"])

    def test_corrected_text_always_auto_promotes(self):
        """When the operator provides corrected_text — even with a short
        reason — that's the highest-signal case and must auto-promote."""
        import time
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post(
                "/api/content-feedback",
                json={
                    "source": "planner_ai_suggest", "content_type": "discussion",
                    "topic_key": "vegan", "original_text": "X",
                    "verdict": "rejected", "reason": "wording",
                    "corrected_text": "AUTO_CORRECTED_TOKEN — better text",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["auto_promoted"])
            # Wait for debounce + mock LLM to write the abstracted rule.
            for _ in range(20):
                time.sleep(0.05)
                if "LLM_ABSTRACTED" in self.prefs_path.read_text(encoding="utf-8"):
                    break
            text = self.prefs_path.read_text(encoding="utf-8")
            self.assertIn("LLM_ABSTRACTED", text)

    def test_accepted_verdict_does_not_auto_promote(self):
        """Acceptances feed working memory as positive anchors, but
        don't trigger rule writes (asymmetric by design)."""
        with self._client()[0], self._client()[1] as client:
            self._login(client)
            r = client.post(
                "/api/content-feedback",
                json={
                    "source": "qa_scoring", "content_type": "discussion",
                    "topic_key": "movies", "original_text": "good draft",
                    "verdict": "accepted", "reason": "qa_score=5 · great anchor and shape",
                },
            )
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["auto_promoted"])

    # ── T-187 (Gap 2): promote-feedback endpoint ──

    def test_promote_feedback_appends_rule_immediately(self):
        """A single rejection promotes via /promote-feedback. T-189:
        rule content is LLM-abstracted (not verbatim quotes)."""
        with self._client()[0], self._client()[1] as client:
            self._login(client)
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
            r = client.post(f"/api/operator-prefs/promote-feedback/{feedback_id}")
            self.assertEqual(r.status_code, 200, r.text)
            data = r.json()
            self.assertTrue(data["ok"])
            self.assertEqual(data["feedback_id"], feedback_id)
            self.assertGreater(data["appended_chars"], 0)
            text = self.prefs_path.read_text(encoding="utf-8")
            self.assertIn("planner deny → promote-now", text)
            # T-189: rule is LLM-abstracted (mock returns LLM_ABSTRACTED token).
            self.assertIn("LLM_ABSTRACTED", text)
            # Anti-regression: the rejected text must NOT appear verbatim.
            self.assertNotIn("PROMOTE_TEST — בדיקת קידום", text)

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
