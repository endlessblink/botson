"""Browser-level E2E coverage for dragging planner events.

These tests prove the real FullCalendar browser interaction, not just the
backend PUT route. They spin up the dashboard against a temporary SQLite DB,
drag an event in the time-grid view, and assert both the UI after refresh and
the persisted database row.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
IL_TZ = ZoneInfo("Asia/Jerusalem")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_http(url: str, timeout: float = 15.0) -> bool:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status < 500:
                    return True
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(0.2)
    return False


def _playwright_available() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    cache = Path(os.path.expanduser("~/.cache/ms-playwright"))
    if not cache.exists():
        return False
    return any(p.name.startswith("chromium-") for p in cache.iterdir() if p.is_dir())


def _bootstrap_db(db_path: Path) -> tuple[str, int, int]:
    import sys

    sys.path.insert(0, str(ROOT))
    from bot.database.db import Database

    base_date = (datetime.now(IL_TZ) + timedelta(days=1)).date().isoformat()

    async def _init() -> tuple[int, int]:
        db = Database(str(db_path))
        await db.init()
        try:
            scheduled_id = await db.create_scheduled_message(
                text="Drag E2E scheduled event",
                message_type="custom",
                channel_topic_id=None,
                target_group="test",
                scheduled_date=base_date,
                scheduled_time="10:00",
                status="scheduled",
                created_by="drag-e2e",
            )
            sent_id = await db.create_scheduled_message(
                text="Drag E2E sent event",
                message_type="custom",
                channel_topic_id=None,
                target_group="test",
                scheduled_date=base_date,
                scheduled_time="11:00",
                status="sent",
                created_by="drag-e2e",
            )
            return scheduled_id, sent_id
        finally:
            await db.close()

    scheduled_id, sent_id = asyncio.run(_init())
    return base_date, scheduled_id, sent_id


@unittest.skipUnless(
    _playwright_available(),
    "playwright + chromium not installed — planner drag E2E skipped",
)
class PlannerDragBrowserTests(unittest.TestCase):
    proc: Any = None
    port: int = 0
    workdir: Path = Path()
    db_path: Path = Path()
    event_date: str = ""
    scheduled_id: int = 0
    sent_id: int = 0

    @classmethod
    def setUpClass(cls):
        cls.workdir = Path(tempfile.mkdtemp(prefix="botson-drag-e2e-"))
        cls.db_path = cls.workdir / "bot.db"
        try:
            cls.event_date, cls.scheduled_id, cls.sent_id = _bootstrap_db(cls.db_path)
        except Exception as e:
            shutil.rmtree(cls.workdir, ignore_errors=True)
            raise unittest.SkipTest(f"DB bootstrap failed: {e}")

        cls.port = _free_port()
        env = os.environ.copy()
        env["DB_PATH"] = str(cls.db_path)
        env["DASHBOARD_PASSWORD"] = "botson-admin"
        env["DASHBOARD_PORT"] = str(cls.port)

        venv_python = ROOT / ".venv" / "bin" / "python"
        python_bin = str(venv_python) if venv_python.exists() else os.environ.get("PYTHON", "python3")
        cls.proc = subprocess.Popen(
            [python_bin, "-m", "uvicorn", "dashboard.app:app", "--host", "127.0.0.1", "--port", str(cls.port), "--log-level", "warning"],
            cwd=str(ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not _wait_for_http(f"http://127.0.0.1:{cls.port}/login", timeout=15):
            cls.proc.terminate()
            cls.proc.wait(timeout=5)
            shutil.rmtree(cls.workdir, ignore_errors=True)
            raise unittest.SkipTest(f"dashboard didn't start on port {cls.port}")

    @classmethod
    def tearDownClass(cls):
        proc = getattr(cls, "proc", None)
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if getattr(cls, "workdir", None):
            shutil.rmtree(cls.workdir, ignore_errors=True)

    def _login_and_open(self, page):
        page.goto(f"http://127.0.0.1:{self.port}/login", wait_until="networkidle")
        page.fill("input[name='password']", "botson-admin")
        page.click("button[type='submit']")
        page.wait_for_url(lambda url: "/login" not in url, timeout=5_000)
        page.goto(f"http://127.0.0.1:{self.port}/planner", wait_until="networkidle")
        page.wait_for_selector(".fc-toolbar", timeout=10_000)
        page.evaluate("date => calendar.gotoDate(date)", self.event_date)
        page.click(".fc-timeGridWeek-button")
        page.wait_for_selector(".fc-timegrid-event", timeout=10_000)

    def _row_time(self, msg_id: int) -> str | None:
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute(
                "SELECT scheduled_time FROM scheduled_messages WHERE id = ?",
                (msg_id,),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def _wait_for_row_time(self, msg_id: int, expected: str, timeout: float = 5.0) -> str | None:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self._row_time(msg_id)
            if last == expected:
                return last
            time.sleep(0.1)
        return last

    def _drag_event_to_time(self, page, text: str, target_time: str):
        event = page.locator(".fc-timegrid-event", has_text=text).first
        self.assertTrue(event.is_visible(timeout=10_000), f"event not visible: {text}")
        event_box = event.bounding_box()
        self.assertIsNotNone(event_box, f"event has no bounding box: {text}")
        slot_box = page.locator(f".fc-timegrid-slot[data-time='{target_time}:00']").first.bounding_box()
        self.assertIsNotNone(slot_box, f"target slot has no bounding box: {target_time}")

        start_x = event_box["x"] + event_box["width"] / 2
        start_y = event_box["y"] + event_box["height"] / 2
        target_x = start_x
        target_y = slot_box["y"] + slot_box["height"] / 2
        page.mouse.move(start_x, start_y)
        page.mouse.down()
        page.mouse.move(target_x, target_y, steps=12)
        page.mouse.up()

    def test_drag_scheduled_event_updates_display_and_survives_refresh(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            try:
                self._login_and_open(page)
                self._drag_event_to_time(page, "Drag E2E scheduled event", "12:00")
                page.wait_for_function(
                    "id => fetch('/api/calendar?start=2000-01-01&end=2099-12-31').then(r => r.json()).then(rows => rows.find(e => e.id === String(id))?.start.includes('T12:00:00'))",
                    arg=self.scheduled_id,
                    timeout=10_000,
                )
                self.assertEqual(self._wait_for_row_time(self.scheduled_id, "12:00"), "12:00")

                page.reload(wait_until="networkidle")
                page.wait_for_selector(".fc-toolbar", timeout=10_000)
                page.evaluate("date => calendar.gotoDate(date)", self.event_date)
                page.click(".fc-timeGridWeek-button")
                moved = page.locator(".fc-timegrid-event", has_text="Drag E2E scheduled event").first
                self.assertTrue(moved.is_visible(timeout=10_000))
                self.assertIn("12:00", moved.inner_text())
                self.assertIn("יישלח", moved.inner_text())
            finally:
                browser.close()

    def test_sent_event_does_not_drag_or_persist_new_time(self):
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
            page = browser.new_page(viewport={"width": 1600, "height": 1000})
            try:
                self._login_and_open(page)
                self._drag_event_to_time(page, "Drag E2E sent event", "13:00")
                page.wait_for_timeout(800)
                self.assertEqual(self._row_time(self.sent_id), "11:00")
                sent = page.locator(".fc-timegrid-event", has_text="Drag E2E sent event").first
                self.assertIn("11:00", sent.inner_text())
                self.assertIn("נשלח", sent.inner_text())
            finally:
                browser.close()


if __name__ == "__main__":
    unittest.main()
