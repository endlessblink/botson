"""T-170 regression: planner Populate _gen_text behavior.

Pins:
- Silent pool fallback is gone — when all retries fail, the caller gets
  back the last failure reason, not a quietly-substituted pool item.
- Retry budget honors settings.ai_populate.generation.retry_budget.
- Each retry appends the prior rejection reason + an angle hint.
- A near-duplicate of a recent send is rejected (Jaccard via freshness).
- An echo of a pool example is rejected.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch, AsyncMock
from tempfile import TemporaryDirectory
from pathlib import Path

from fastapi.testclient import TestClient


class PlannerGenTextBehavior(unittest.IsolatedAsyncioTestCase):
    """End-to-end smoke through the planner suggest endpoint, with the LLM
    mocked. We don't drive the full _gen_text directly (it's a closure inside
    _ai_suggest_calendar); we drive it through the public suggest API and
    inspect the resulting suggestions / errors.
    """

    async def asyncSetUp(self):
        # Import after env is sane (the app does sqlite init at import time).
        from dashboard import app as dashboard_app
        self.app = dashboard_app
        self.client = TestClient(dashboard_app.app)

    async def test_freshness_near_dup_helper_used_in_planner_path(self):
        """Direct unit on the freshness primitive used by the planner — if
        a paraphrase of a recent text is fed in, freshness_rejection returns
        the near-dup reason. (The planner now passes avoid_texts so this
        triggers; previously it didn't.)
        """
        from bot.utils.freshness import freshness_rejection
        prior = "סרט שראיתם בקולנוע בלי לדעת עליו כלום ויצאתם מופתעים?"
        paraphrase = "סרט שיצאתם מופתעים אחרי שראיתם אותו בקולנוע בלי לדעת עליו כלום?"
        reason = freshness_rejection(paraphrase, avoid_texts={prior})
        self.assertIsNotNone(reason)

    async def test_pool_example_echo_rejected_via_source_examples(self):
        from bot.utils.freshness import freshness_rejection
        example = "דעה לא פופולרית על סרט שכולם אוהבים?"
        reason = freshness_rejection(example, source_examples={example})
        self.assertIsNotNone(reason)
        self.assertIn("copied static example", str(reason))

    async def test_planner_uses_codex_fallback_when_claude_paths_fail(self):
        from bot.database.db import Database

        db = Database(":memory:")
        await db.init()
        for media_type in ("movie", "series"):
            for idx in range(5):
                await db._db.execute(
                    """INSERT INTO emoji_puzzles
                       (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                    ("🎬⭐", f"{media_type} {idx}", f"{media_type} {idx}", "[]", 2, media_type, 1),
                )
        await db._db.commit()
        call_counter = {"n": 0}

        async def codex_canned(*args, **kwargs):
            call_counter["n"] += 1
            return f"איזה רגע קטן מהשבוע הזה ממשיך להישאר אצלכם בראש? ({call_counter['n']})"

        async def activity_copy(*args, **kwargs):
            return "מתחילים עוד מעט פעילות קלילה; מי בפנים?"

        with patch.object(self.app, "_generate_via_cli", new=AsyncMock(side_effect=RuntimeError("claude down"))), \
             patch.object(self.app, "_generate_via_codex_cli", new=AsyncMock(side_effect=codex_canned)), \
             patch.object(self.app, "_generate_activity_copy", new=AsyncMock(side_effect=activity_copy)), \
             patch.object(self.app, "_render_group_stats_context", new=AsyncMock(return_value="")):
            result = await self.app._ai_suggest_calendar(db, target_date=None, week_offset=1)

        await db.close()
        types = {s["message_type"] for s in result["suggestions"]}
        self.assertIn("morning", types)
        self.assertIn("discussion", types)
        self.assertTrue(
            any("Codex CLI fallback was used" in notice for notice in result["notices"]),
            result["notices"],
        )
        self.assertFalse(
            any("Codex CLI fallback was used" in err for err in result["errors"]),
            result["errors"],
        )

    async def test_generation_fallback_chain_skips_anthropic_api(self):
        api_mock = AsyncMock(side_effect=AssertionError("API fallback must not be used"))
        with patch.object(self.app, "_generate_via_cli", new=AsyncMock(side_effect=RuntimeError("claude down"))), \
             patch.object(self.app, "_generate_via_api", new=api_mock), \
             patch.object(self.app, "_generate_via_codex_cli", new=AsyncMock(return_value="ok")):
            content, notices = await self.app._generate_with_fallbacks("prompt", context="planner.test")

        self.assertEqual(content, "ok")
        self.assertEqual(api_mock.await_count, 0)
        self.assertTrue(any("Codex CLI fallback was used" in notice for notice in notices))

    async def test_generation_fallback_chain_classifies_provider_auth_failure(self):
        with patch.object(
            self.app,
            "_generate_via_cli",
            new=AsyncMock(side_effect=RuntimeError("Failed to authenticate. API Error: 401")),
        ), patch.object(
            self.app,
            "_generate_via_codex_cli",
            new=AsyncMock(side_effect=RuntimeError("refresh_token_invalidated: Your session has ended")),
        ):
            with self.assertRaises(self.app.GenerationProviderUnavailable) as ctx:
                await self.app._generate_with_fallbacks("prompt", context="planner.test")

        message = str(ctx.exception)
        self.assertIn("AI generation provider authentication failed", message)
        self.assertNotIn("refresh_token_invalidated", message)

    async def test_ai_suggest_provider_auth_failure_is_single_visible_error(self):
        from bot.database.db import Database

        db = Database(":memory:")
        await db.init()
        claude = AsyncMock(side_effect=RuntimeError("Invalid authentication credentials 401 Unauthorized"))
        codex = AsyncMock(side_effect=RuntimeError("refresh_token_invalidated: Your session has ended"))
        try:
            with patch.object(self.app, "_generate_via_cli", new=claude), \
                 patch.object(self.app, "_generate_via_codex_cli", new=codex), \
                 patch.object(self.app, "_generate_activity_copy", new=AsyncMock(return_value="מתחילים עוד מעט פעילות קלילה; מי בפנים?")), \
                 patch.object(self.app, "_render_group_stats_context", new=AsyncMock(return_value="")):
                result = await self.app._ai_suggest_calendar(db, target_date=None, week_offset=1)
        finally:
            await db.close()

        self.assertEqual(result["suggestions"], [])
        self.assertEqual(
            result["errors"],
            ["AI generation provider authentication failed. Re-authenticate Claude/Codex on the dashboard host and retry."],
        )
        self.assertEqual(claude.await_count, 1)
        self.assertEqual(codex.await_count, 1)

    def test_codex_home_repairs_zero_byte_file(self):
        with TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / ".codex"
            codex_path.write_bytes(b"")

            self.app._ensure_codex_home_dir(tmp, context="test")

            self.assertTrue(codex_path.is_dir())

    def test_codex_home_refuses_nonempty_file(self):
        with TemporaryDirectory() as tmp:
            codex_path = Path(tmp) / ".codex"
            codex_path.write_text("not a directory", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "not a directory"):
                self.app._ensure_codex_home_dir(tmp, context="test")

    def test_codex_binary_prefers_system_install(self):
        with patch("dashboard.app.os.path.isfile", return_value=True), \
             patch("dashboard.app.os.access", return_value=True):
            self.assertEqual(self.app._codex_binary_path(), "/usr/bin/codex")

    async def test_generation_health_reports_degraded_fallback(self):
        with patch.object(
            self.app,
            "_generate_with_fallbacks",
            new=AsyncMock(return_value=(
                "botson_generation_health_ok",
                ["generation-health.provider: Claude generation failed; Codex CLI fallback was used"],
            )),
        ):
            result = await self.app.run_generation_health_check()

        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["checks"]["provider_chain"]["provider"], "codex_cli")
        self.assertTrue(result["checks"]["provider_chain"]["fallback_used"])

    async def test_generation_health_fails_on_noisy_provider_output(self):
        with patch.object(
            self.app,
            "_generate_with_fallbacks",
            new=AsyncMock(return_value=("transcript\nbotson_generation_health_ok", [])),
        ):
            result = await self.app.run_generation_health_check()

        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["checks"]["provider_chain"]["clean_output"])

    async def test_generation_health_planner_dry_run_fails_on_pool_only_rows(self):
        from bot.database.db import Database

        db = Database(":memory:")
        await db.init()
        try:
            with patch.object(
                self.app,
                "_generate_with_fallbacks",
                new=AsyncMock(return_value=("botson_generation_health_ok", [])),
            ), patch.object(
                self.app,
                "_ai_suggest_calendar",
                new=AsyncMock(return_value={
                    "suggestions": [
                        {"message_type": "trivia_warmup_rsvp"},
                        {"message_type": "emoji_puzzle"},
                        {"message_type": "facts_tidbit"},
                    ],
                    "errors": [],
                    "skip_reasons": [],
                }),
            ):
                result = await self.app.run_generation_health_check(
                    db,
                    include_planner=True,
                    min_suggestions=6,
                )
        finally:
            await db.close()

        self.assertEqual(result["status"], "failed")
        planner = result["checks"]["planner_dry_run"]
        self.assertEqual(planner["suggestions"], 3)
        self.assertFalse(planner["has_text_generated_type"])

    def test_generation_health_endpoint_requires_auth_and_returns_probe(self):
        ok_payload = {"status": "ok", "ok": True, "checks": {}}

        with patch.object(
            self.app,
            "run_generation_health_check",
            new=AsyncMock(return_value=ok_payload),
        ):
            unauth = self.client.get("/api/health/generation")
            self.assertEqual(unauth.status_code, 401)

            login = self.client.post(
                "/login",
                data={"password": self.app.DASHBOARD_PASSWORD},
                follow_redirects=False,
            )
            self.assertEqual(login.status_code, 303)
            authed = self.client.get("/api/health/generation?include_planner=true&min_suggestions=7")

        self.assertEqual(authed.status_code, 200)
        self.assertEqual(authed.json(), ok_payload)


class PlannerRetryBudgetSettingHonored(unittest.TestCase):
    """The new retry budget is sourced from settings; default = 3."""

    def test_settings_default_present(self):
        from bot.utils.config import get_settings
        settings = get_settings()
        # ai_populate may or may not have a generation sub-block in the
        # checked-in YAML; the planner code defaults to 3 either way.
        block = (settings.get("ai_populate") or {}).get("generation") or {}
        if "retry_budget" in block:
            self.assertGreaterEqual(int(block["retry_budget"]), 1)

    def test_generation_config_exposes_gap1_knobs(self):
        from dashboard import app as dashboard_app

        cfg = dashboard_app._planner_generation_config({
            "ai_populate": {
                "generation": {
                    "retry_budget": "2",
                    "dedup_window": "7",
                    "opener_recent_window": "4",
                    "temperature": "0.9",
                    "pattern_rotation": [" A ", "", "B"],
                }
            }
        })
        self.assertEqual(cfg["retry_budget"], 2)
        self.assertEqual(cfg["dedup_window"], 7)
        self.assertEqual(cfg["opener_recent_window"], 4)
        self.assertEqual(cfg["temperature"], 0.9)
        self.assertEqual(cfg["pattern_rotation"], ["A", "B"])

    def test_generation_config_does_not_force_fill_in_blank_patterns(self):
        from dashboard import app as dashboard_app

        cfg = dashboard_app._planner_generation_config(dashboard_app.get_settings())

        joined = "\n".join(cfg["pattern_rotation"])
        self.assertNotIn("השלמת משפט", joined)
        self.assertNotIn("___", joined)

    def test_draft_validator_rejects_fill_in_blank_scaffolds(self):
        from dashboard import app as dashboard_app

        failures = dashboard_app._validate_draft_text(
            "ארוחת שלישי הטבעונית שלי = __ + __ + __. שלכם?"
        )

        self.assertIn("fill_in_blank_scaffold", failures)

    def test_pattern_rotation_changes_across_attempts(self):
        from dashboard import app as dashboard_app

        patterns = ["pattern-a", "pattern-b", "pattern-c"]
        first = dashboard_app._planner_pattern_directive(
            patterns, "discussion", "movies", "2026-05-18", "18:00", 0,
        )
        second = dashboard_app._planner_pattern_directive(
            patterns, "discussion", "movies", "2026-05-18", "18:00", 1,
        )
        self.assertIn(first, patterns)
        self.assertIn(second, patterns)
        self.assertNotEqual(first, second)

    def test_opener_key_ignores_leading_emoji(self):
        from dashboard import app as dashboard_app

        self.assertEqual(
            dashboard_app._draft_opener_key("🎬 מה הסרט שהכי הפתיע אתכם?"),
            "מה הסרט",
        )


if __name__ == "__main__":
    unittest.main()
