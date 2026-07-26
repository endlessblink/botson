"""Visual regression test for the planner page.

Catches the class of bug where:
  - the modal title is clipped behind the fixed sidebar (commit 1d83f72)
  - inline JS has a syntax error that prevents the calendar from initializing
    (commit f49794b — unescaped apostrophe in `אמוג'י` inside a single-quoted
    string)

Skipped automatically when Playwright or Chromium isn't available, so CI
boxes without browsers don't fail. To run locally:

  .venv/bin/python -m unittest tests.test_planner_visual
"""
from __future__ import annotations

import os
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    cache = Path(os.path.expanduser("~/.cache/ms-playwright"))
    if not cache.exists():
        return False
    return any(p.name.startswith("chromium-") for p in cache.iterdir() if p.is_dir())


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(
    _playwright_available(),
    "playwright + chromium not installed — visual test skipped",
)
class PlannerVisualTests(unittest.TestCase):
    """Real-browser layout assertions against a locally-spawned dashboard."""

    @classmethod
    def setUpClass(cls):
        # Bring up the dashboard on a random port so this test runs even if
        # 8080 is in use. Reuse the project venv's python.
        cls.port = _free_port()
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.db_path = str(Path(cls.tmpdir.name) / "planner-visual.db")
        env = {**os.environ, "DASHBOARD_PORT": str(cls.port), "DB_PATH": cls.db_path}
        cls.log = tempfile.NamedTemporaryFile(suffix=".log", delete=False, mode="w")
        venv_python = Path(".venv/bin/python")
        python_bin = str(venv_python) if venv_python.exists() else sys.executable
        cls.proc = subprocess.Popen(
            [python_bin, "-m", "dashboard.server"],
            stdout=cls.log,
            stderr=subprocess.STDOUT,
            env=env,
        )

        # Probe until the server is listening or 10s is up.
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", cls.port), timeout=0.5):
                    break
            except OSError:
                time.sleep(0.2)
        else:
            cls.proc.terminate()
            raise unittest.SkipTest(f"dashboard didn't start on port {cls.port}")

        # Prefer the same test-only password inherited by the subprocess.
        # Fall back to .env for direct unittest runs outside pytest.
        env_path = Path(".env")
        cls.password = env.get("DASHBOARD_PASSWORD", "").strip()
        if not cls.password and env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("DASHBOARD_PASSWORD"):
                    cls.password = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if not cls.password:
            cls.tearDownClass()
            raise unittest.SkipTest("DASHBOARD_PASSWORD missing — cannot authenticate")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "proc", None):
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        if getattr(cls, "tmpdir", None):
            cls.tmpdir.cleanup()

    def _open_planner(self, ctx):
        page = ctx.new_page()
        js_errors: list[str] = []
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))
        page.goto(f"http://localhost:{self.port}/login", wait_until="networkidle")
        page.fill('input[name="password"]', self.password)
        page.click('button[type="submit"]')
        page.wait_for_load_state("networkidle")
        page.goto(f"http://localhost:{self.port}/planner", wait_until="networkidle")
        return page, js_errors

    def test_planner_page_loads_without_js_errors(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, js_errors = self._open_planner(ctx)
                # Calendar widget should have inited — its container has fc-* classes.
                self.assertGreater(
                    page.locator(".fc-view-harness, .fc-toolbar").count(),
                    0,
                    "FullCalendar didn't initialize — likely a JS syntax error in planner.html",
                )
                self.assertEqual(
                    js_errors, [],
                    f"unexpected JS errors on /planner: {js_errors!r}",
                )
            finally:
                browser.close()

    def test_review_drafts_modal_title_clears_sidebar(self):
        """The review-drafts modal must center inside the main content column,
        not behind the fixed sidebar. Regression for commit 1d83f72.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, _ = self._open_planner(ctx)
                # Force-show the modal directly — we don't need the digest call
                # to succeed for layout assertions.
                page.evaluate(
                    "document.getElementById('review-drafts-modal').classList.remove('hidden')"
                )
                page.wait_for_timeout(200)
                title_box = page.locator("#review-drafts-modal h3").bounding_box()
                sidebar_box = page.locator("aside.lg\\:fixed").first.bounding_box()
                self.assertIsNotNone(title_box, "modal h3 not present")
                self.assertIsNotNone(sidebar_box, "desktop sidebar not present")
                title_right = title_box["x"] + title_box["width"]
                self.assertLess(
                    title_right, sidebar_box["x"],
                    f"modal title (right={title_right:.0f}) overlaps sidebar (left={sidebar_box['x']:.0f}) — modal needs lg:right-56 on its container",
                )
            finally:
                browser.close()

    def test_toolbar_has_three_visible_ai_buttons(self):
        """Single Populate (week) + day-level today + day-level specific.
        Pool-growth buttons (emoji, facts) folded in. Cancel button is hidden
        unless a day-fill is in progress.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, _ = self._open_planner(ctx)
                expected_visible = (
                    "#ai-fill-today-btn",
                    "#ai-fill-day-btn",
                    "#ai-fill-all-btn",
                )
                for sel in expected_visible:
                    self.assertTrue(
                        page.locator(sel).is_visible(),
                        f"expected toolbar button {sel} to be visible",
                    )
                self.assertEqual(
                    page.locator("#ai-suggest-emoji-btn").count(), 0,
                    "🎬 emoji button is back — it should be folded into Populate",
                )
                self.assertEqual(
                    page.locator("#ai-suggest-facts-btn").count(), 0,
                    "🔎 facts button is back — it should be folded into Populate",
                )
            finally:
                browser.close()

    def test_weekly_ai_suggest_button_renders_real_board_flow(self):
        """Clicking the actual week Populate button must render the board.

        This catches stale regressions where the synthetic board helpers pass
        but the real async suggest path still opens the old flat modal.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            requests = []
            try:
                page, js_errors = self._open_planner(ctx)

                def fulfill_ai_suggest(route):
                    req = route.request
                    requests.append({"method": req.method, "url": req.url, "body": req.post_data})
                    if req.method == "POST" and req.url.endswith("/api/weekplan/ai-suggest"):
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({"job_id": "job-week"}),
                        )
                        return
                    if req.method == "GET" and req.url.endswith("/api/weekplan/ai-suggest/job-week"):
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({
                                "status": "completed",
                                "result": {
                                    "window": {
                                        "start": "2099-01-04",
                                        "end": "2099-01-10",
                                        "scope": "week",
                                    },
                                    "suggestions": [
                                        {
                                            "key": "week-card",
                                            "date": "2099-01-04",
                                            "time": "18:00",
                                            "message_type": "discussion",
                                            "topic_id": 111,
                                            "topic_name": "סרטים",
                                            "category": "movies",
                                            "text": "מה הסרט שחזרתם אליו השבוע?",
                                            "rationale": "בדיקת שבוע",
                                            "source": "ai-fill",
                                            "quality_failures": [],
                                            "validation_failures": [],
                                        }
                                    ],
                                },
                            }),
                        )
                        return
                    route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "unexpected"}))

                page.route("**/api/weekplan/ai-suggest**", fulfill_ai_suggest)
                page.click("#ai-fill-all-btn")
                page.wait_for_selector("[data-ai-suggest-board='1']", timeout=6000)

                self.assertEqual(page.locator("[data-ai-suggest-board='1']").count(), 1)
                self.assertEqual(page.locator(".ai-suggest-day-lane").count(), 7)
                self.assertEqual(
                    page.locator("[data-ai-suggest-date='2099-01-04'] [data-suggest-card='week-card']").count(),
                    1,
                )
                self.assertEqual(
                    page.locator("#ai-suggest-list > [data-suggest-card='week-card']").count(),
                    0,
                    "week suggestions rendered as the old flat list instead of inside day lanes",
                )
                posted = json.loads(next(r["body"] for r in requests if r["method"] == "POST"))
                self.assertEqual(posted["window_mode"], "rolling")
                self.assertEqual(js_errors, [], f"unexpected JS errors on /planner: {js_errors!r}")
            finally:
                browser.close()

    def test_day_ai_suggest_flow_stays_flat(self):
        """Day-level populate keeps the old review list instead of the weekly board."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, js_errors = self._open_planner(ctx)

                def fulfill_ai_suggest(route):
                    req = route.request
                    if req.method == "POST" and req.url.endswith("/api/weekplan/ai-suggest"):
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({"job_id": "job-day"}),
                        )
                        return
                    if req.method == "GET" and req.url.endswith("/api/weekplan/ai-suggest/job-day"):
                        route.fulfill(
                            status=200,
                            content_type="application/json",
                            body=json.dumps({
                                "status": "completed",
                                "result": {
                                    "window": {
                                        "start": "2099-01-04",
                                        "end": "2099-01-04",
                                        "scope": "day",
                                    },
                                    "suggestions": [
                                        {
                                            "key": "day-card",
                                            "date": "2099-01-04",
                                            "time": "18:00",
                                            "message_type": "discussion",
                                            "topic_id": 111,
                                            "topic_name": "סרטים",
                                            "category": "movies",
                                            "text": "מה הסרט שחזרתם אליו השבוע?",
                                            "rationale": "בדיקת יום",
                                            "source": "ai-fill",
                                            "quality_failures": [],
                                            "validation_failures": [],
                                        }
                                    ],
                                },
                            }),
                        )
                        return
                    route.fulfill(status=404, content_type="application/json", body=json.dumps({"detail": "unexpected"}))

                page.route("**/api/weekplan/ai-suggest**", fulfill_ai_suggest)
                page.evaluate("aiFillSpecificDaySelected('2099-01-04')")
                page.wait_for_selector("[data-suggest-card='day-card']", timeout=6000)

                self.assertEqual(page.locator("[data-ai-suggest-board='1']").count(), 0)
                self.assertEqual(page.locator("#ai-suggest-list > [data-suggest-card='day-card']").count(), 1)
                self.assertEqual(js_errors, [], f"unexpected JS errors on /planner: {js_errors!r}")
            finally:
                browser.close()

    def test_create_drawer_type_switches_do_not_leak_state(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, js_errors = self._open_planner(ctx)
                page.evaluate(
                    """
                    () => {
                        openDrawer('2099-01-04');
                        selectType('morning');
                        wizardState.text = 'generated morning text that must not leak';
                        wizardState.coverPath = 'covers/leak.jpg';
                        wizardState.coverUrl = 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
                        document.getElementById('create-text').value = wizardState.text;
                        renderPreview();
                    }
                    """
                )
                page.evaluate("selectType('event')")
                page.wait_for_timeout(200)
                event_after_morning = page.evaluate(
                    """
                    () => {
                        renderPreview();
                        return {
                            type: wizardState.type,
                            text: wizardState.text,
                            coverUrl: wizardState.coverUrl,
                            coverHidden: document.getElementById('preview-cover').classList.contains('hidden'),
                            rsvpHidden: document.getElementById('preview-rsvp').classList.contains('hidden'),
                            rsvpText: document.getElementById('preview-rsvp').textContent,
                            previewText: document.getElementById('create-preview').textContent,
                        };
                    }
                    """
                )
                self.assertEqual(event_after_morning["type"], "event")
                self.assertEqual(event_after_morning["text"], "")
                self.assertIsNone(event_after_morning["coverUrl"])
                self.assertTrue(event_after_morning["coverHidden"])
                self.assertTrue(event_after_morning["rsvpHidden"])
                self.assertEqual(event_after_morning["rsvpText"], "")
                self.assertNotIn("generated morning text", event_after_morning["previewText"])

                page.evaluate(
                    """
                    () => {
                        wizardState.text = 'event text';
                        wizardState.eventLocation = 'leaky venue';
                        wizardState.eventMaxAttendees = 9;
                        document.getElementById('event-location').value = wizardState.eventLocation;
                        document.getElementById('event-max').value = String(wizardState.eventMaxAttendees);
                        renderPreview();
                    }
                    """
                )
                page.evaluate("selectType('poll')")
                page.wait_for_timeout(200)
                poll_after_event = page.evaluate(
                    """
                    () => {
                        renderPreview();
                        return {
                            type: wizardState.type,
                            text: wizardState.text,
                            eventLocation: wizardState.eventLocation,
                            eventMaxAttendees: wizardState.eventMaxAttendees,
                            eventInfoHidden: document.getElementById('preview-event-info').classList.contains('hidden'),
                            eventLocationInput: document.getElementById('event-location').value,
                            eventMaxInput: document.getElementById('event-max').value,
                            pollOptions: wizardState.pollOptions.slice(),
                        };
                    }
                    """
                )
                self.assertEqual(poll_after_event["type"], "poll")
                self.assertEqual(poll_after_event["text"], "")
                self.assertEqual(poll_after_event["eventLocation"], "")
                self.assertIsNone(poll_after_event["eventMaxAttendees"])
                self.assertTrue(poll_after_event["eventInfoHidden"])
                self.assertEqual(poll_after_event["eventLocationInput"], "")
                self.assertEqual(poll_after_event["eventMaxInput"], "")
                self.assertEqual(poll_after_event["pollOptions"], ["", ""])

                page.evaluate(
                    """
                    () => {
                        wizardState.pollOptions = ['yes leak', 'no leak'];
                        renderPreview();
                    }
                    """
                )
                page.evaluate("selectType('morning')")
                page.wait_for_timeout(200)
                morning_after_poll = page.evaluate(
                    """
                    () => {
                        renderPreview();
                        return {
                            type: wizardState.type,
                            pollOptions: wizardState.pollOptions.slice(),
                            pollPreviewHidden: document.getElementById('preview-poll-options').classList.contains('hidden'),
                            pollPreviewText: document.getElementById('preview-poll-options').textContent,
                        };
                    }
                    """
                )
                self.assertEqual(morning_after_poll["type"], "morning")
                self.assertEqual(morning_after_poll["pollOptions"], ["", ""])
                self.assertTrue(morning_after_poll["pollPreviewHidden"])
                self.assertNotIn("yes leak", morning_after_poll["pollPreviewText"])

                page.evaluate(
                    """
                    () => {
                        selectType('custom');
                        wizardState.text = 'custom text that must not become an event';
                        wizardState.coverPath = 'covers/custom.jpg';
                        wizardState.coverUrl = 'data:image/gif;base64,R0lGODlhAQABAAAAACw=';
                        renderPreview();
                    }
                    """
                )
                page.evaluate("selectType('event')")
                page.wait_for_timeout(200)
                event_after_custom = page.evaluate(
                    """
                    () => {
                        renderPreview();
                        return {
                            type: wizardState.type,
                            text: wizardState.text,
                            coverUrl: wizardState.coverUrl,
                            coverHidden: document.getElementById('preview-cover').classList.contains('hidden'),
                            rsvpHidden: document.getElementById('preview-rsvp').classList.contains('hidden'),
                            rsvpText: document.getElementById('preview-rsvp').textContent,
                            previewText: document.getElementById('create-preview').textContent,
                        };
                    }
                    """
                )
                self.assertEqual(event_after_custom["type"], "event")
                self.assertEqual(event_after_custom["text"], "")
                self.assertIsNone(event_after_custom["coverUrl"])
                self.assertTrue(event_after_custom["coverHidden"])
                self.assertTrue(event_after_custom["rsvpHidden"])
                self.assertEqual(event_after_custom["rsvpText"], "")
                self.assertNotIn("custom text", event_after_custom["previewText"])
                self.assertEqual(js_errors, [], f"unexpected JS errors on /planner: {js_errors!r}")
            finally:
                browser.close()

    def test_weekly_ai_suggest_board_move_updates_approval_payload(self):
        """Week populate review is a pre-commit board: dragging between days
        mutates the pending approval payload, not the DB.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            captured = {}
            try:
                page, js_errors = self._open_planner(ctx)

                def capture_commit(route):
                    captured["payload"] = json.loads(route.request.post_data or "{}")
                    route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({"inserted": 1, "ids": [123], "by_type": {"discussion": 1}, "errors": []}),
                    )

                page.route("**/api/weekplan/ai-suggest-commit", capture_commit)
                page.evaluate(
                    """
                    () => {
                        openAiSuggestModal();
                        _aiSuggestState.boardMode = true;
                        _aiSuggestState.boardWindow = {start: '2099-01-04', end: '2099-01-10', scope: 'week'};
                        _aiSuggestState.suggestions = [
                            {
                                key: 'move-me',
                                date: '2099-01-04',
                                time: '18:00',
                                message_type: 'discussion',
                                topic_id: 111,
                                topic_name: 'סרטים',
                                category: 'movies',
                                text: 'מה הסרט שחזרתם אליו השבוע?',
                                rationale: 'בדיקה',
                                source: 'ai-fill',
                                quality_failures: [],
                                validation_failures: []
                            }
                        ];
                        _aiSuggestState.checked = {'move-me': true};
                        _aiSuggestRenderCurrent();
                        _aiSuggestMoveSuggestionToDate('move-me', '2099-01-06');
                    }
                    """
                )
                self.assertEqual(page.locator("[data-ai-suggest-board='1']").count(), 1)
                self.assertEqual(
                    page.locator("[data-ai-suggest-date='2099-01-06'] [data-suggest-card='move-me']").count(),
                    1,
                )
                page.evaluate("aiSuggestApprove()")
                page.wait_for_function("() => !document.getElementById('ai-suggest-modal').classList.contains('hidden') === false")
                self.assertEqual(captured["payload"]["approved"][0]["date"], "2099-01-06")
                self.assertEqual(captured["payload"]["approved"][0]["message_type"], "discussion")
                self.assertEqual(js_errors, [], f"unexpected JS errors on /planner: {js_errors!r}")
            finally:
                browser.close()

    def test_weekly_ai_suggest_board_append_preserves_existing_state(self):
        """Per-day generate-more uses append semantics; existing board cards
        and unchecked choices must survive the merge. Exact duplicates are
        dropped, but same-minute distinct rows are preserved for the existing
        planner game-companion contract.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, js_errors = self._open_planner(ctx)
                result = page.evaluate(
                    """
                    () => {
                        openAiSuggestModal();
                        _aiSuggestState.boardMode = true;
                        _aiSuggestState.boardWindow = {start: '2099-01-04', end: '2099-01-10', scope: 'week'};
                        _aiSuggestState.suggestions = [
                            {
                                key: 'existing',
                                date: '2099-01-04',
                                time: '09:00',
                                message_type: 'morning',
                                topic_id: 2184,
                                topic_name: 'יומיום',
                                text: 'בוקר בדיקה',
                                rationale: 'קיים',
                                source: 'ai-fill',
                                quality_failures: [],
                                validation_failures: []
                            }
                        ];
                        _aiSuggestState.checked = {existing: false};
                        _aiSuggestRenderCurrent();
                        _aiSuggestMergeSuggestions([
                            {
                                key: 'dupe',
                                date: '2099-01-04',
                                time: '09:00',
                                message_type: 'morning',
                                topic_id: 2184,
                                topic_name: 'יומיום',
                                text: 'בוקר בדיקה',
                                rationale: 'כפול',
                                source: 'ai-fill',
                                quality_failures: [],
                                validation_failures: []
                            },
                            {
                                key: 'new-day-card',
                                date: '2099-01-04',
                                time: '12:00',
                                message_type: 'facts_tidbit',
                                topic_id: 341,
                                topic_name: 'הפינה',
                                text: 'עובדה חדשה',
                                rationale: 'נוסף',
                                source: 'ai-fill-pool-row',
                                quality_failures: [],
                                validation_failures: []
                            },
                            {
                                key: 'same-minute-distinct',
                                date: '2099-01-04',
                                time: '09:00',
                                message_type: 'trivia_round',
                                topic_id: 4037,
                                topic_name: 'משחקים',
                                text: '',
                                rationale: 'משחק באותו זמן',
                                source: 'ai-fill-trivia',
                                poll_options_json: JSON.stringify({theme_label: 'בדיקה'}),
                                quality_failures: [],
                                validation_failures: []
                            }
                        ]);
                        _aiSuggestRenderCurrent();
                        return {
                            total: _aiSuggestState.suggestions.length,
                            existingChecked: _aiSuggestState.checked.existing,
                            newChecked: _aiSuggestState.checked['new-day-card'],
                            cards: document.querySelectorAll("[data-ai-suggest-date='2099-01-04'] [data-suggest-card]").length
                        };
                    }
                    """
                )
                self.assertEqual(result["total"], 3)
                self.assertFalse(result["existingChecked"])
                self.assertTrue(result["newChecked"])
                self.assertEqual(result["cards"], 3)
                self.assertEqual(js_errors, [], f"unexpected JS errors on /planner: {js_errors!r}")
            finally:
                browser.close()

    def test_weekly_ai_suggest_board_deny_stays_in_board_mode(self):
        """Feedback learning must still work from the board without
        collapsing the UI back into the old flat list.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, js_errors = self._open_planner(ctx)
                page.route(
                    "**/api/content-feedback",
                    lambda route: route.fulfill(
                        status=200,
                        content_type="application/json",
                        body=json.dumps({"id": 1, "auto_promoted": False}),
                    ),
                )
                result = page.evaluate(
                    """
                    async () => {
                        openAiSuggestModal();
                        _aiSuggestState.boardMode = true;
                        _aiSuggestState.boardWindow = {start: '2099-01-04', end: '2099-01-10', scope: 'week'};
                        _aiSuggestState.suggestions = [
                            {
                                key: 'deny-me',
                                date: '2099-01-04',
                                time: '18:00',
                                message_type: 'discussion',
                                topic_id: 111,
                                topic_name: 'סרטים',
                                category: 'movies',
                                text: 'שאלה לדחייה',
                                rationale: 'בדיקה',
                                source: 'ai-fill',
                                quality_failures: [],
                                validation_failures: []
                            }
                        ];
                        _aiSuggestState.checked = {'deny-me': true};
                        _aiSuggestRenderCurrent();
                        await _aiSuggestDenyOne('deny-me', {reason: 'לא מתאים לערוץ', silent: true});
                        return {
                            boardCount: document.querySelectorAll("[data-ai-suggest-board='1']").length,
                            cardInLane: document.querySelectorAll("[data-ai-suggest-date='2099-01-04'] [data-suggest-card='deny-me']").length,
                            checked: _aiSuggestState.checked['deny-me'],
                            denied: _aiSuggestState.suggestions[0]._denied === true
                        };
                    }
                    """
                )
                self.assertEqual(result["boardCount"], 1)
                self.assertEqual(result["cardInLane"], 1)
                self.assertFalse(result["checked"])
                self.assertTrue(result["denied"])
                self.assertEqual(js_errors, [], f"unexpected JS errors on /planner: {js_errors!r}")
            finally:
                browser.close()

    def test_weekly_ai_suggest_board_moves_linked_game_rows_together(self):
        """Trivia/Emoji warmup rows share a marker with the game row; moving
        one card must keep that companion set together before approval.
        """
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            ctx = browser.new_context(viewport={"width": 1600, "height": 900})
            try:
                page, js_errors = self._open_planner(ctx)
                result = page.evaluate(
                    """
                    () => {
                        const marker = 'warmup-rsvp:trivia:2099-01-04:21:00';
                        openAiSuggestModal();
                        _aiSuggestState.boardMode = true;
                        _aiSuggestState.boardWindow = {start: '2099-01-04', end: '2099-01-10', scope: 'week'};
                        _aiSuggestState.suggestions = [
                            {
                                key: 'warmup',
                                date: '2099-01-04',
                                time: '20:00',
                                message_type: 'trivia_warmup_rsvp',
                                topic_id: 111,
                                topic_name: 'סרטים',
                                text: 'מי בפנים?',
                                rationale: 'חימום',
                                source: 'ai-fill-trivia',
                                poll_options_json: JSON.stringify({warmup_marker: marker, game_time: '21:00'}),
                                quality_failures: [],
                                validation_failures: []
                            },
                            {
                                key: 'game',
                                date: '2099-01-04',
                                time: '21:00',
                                message_type: 'trivia_round',
                                topic_id: 4037,
                                topic_name: 'משחקים',
                                text: '',
                                rationale: 'משחק',
                                source: 'ai-fill-trivia',
                                poll_options_json: JSON.stringify({warmup_marker: marker, game_time: '21:00'}),
                                quality_failures: [],
                                validation_failures: []
                            }
                        ];
                        _aiSuggestState.checked = {warmup: true, game: true};
                        _aiSuggestRenderCurrent();
                        _aiSuggestMoveSuggestionToDate('game', '2099-01-07');
                        return _aiSuggestState.suggestions.map((s) => ({
                            key: s.key,
                            date: s.date,
                            marker: JSON.parse(s.poll_options_json).warmup_marker
                        }));
                    }
                    """
                )
                self.assertEqual({row["date"] for row in result}, {"2099-01-07"})
                self.assertEqual(
                    {row["marker"] for row in result},
                    {"warmup-rsvp:trivia:2099-01-07:21:00"},
                )
                self.assertEqual(js_errors, [], f"unexpected JS errors on /planner: {js_errors!r}")
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
