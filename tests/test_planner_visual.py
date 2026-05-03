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
        env = {**os.environ, "DASHBOARD_PORT": str(cls.port)}
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

        # Pull the dashboard password so we can authenticate the browser.
        env_path = Path(".env")
        cls.password = ""
        if env_path.exists():
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


if __name__ == "__main__":
    unittest.main()
