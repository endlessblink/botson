import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.cleanup_stale_scheduled_content import cancel_rows, find_stale_rows, mutate_rows


SCHEMA = """
CREATE TABLE scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'custom',
    channel_topic_id INTEGER,
    target_group TEXT DEFAULT 'main',
    scheduled_date DATE NOT NULL,
    scheduled_time TIME NOT NULL,
    status TEXT DEFAULT 'scheduled',
    created_by TEXT DEFAULT 'dashboard',
    poll_options TEXT,
    error_message TEXT
);
"""


class StaleContentCleanupTests(unittest.TestCase):
    def _db(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "bot.db"
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)
        conn.executemany(
            """INSERT INTO scheduled_messages
               (text, message_type, scheduled_date, scheduled_time, status, created_by)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    "מתחממים לטריוויה — בעוד 35 דקות מתחילים סיבוב ישראל בפינה של בוטסון.",
                    "discussion",
                    "2099-01-01",
                    "20:25",
                    "scheduled",
                    "ai-fill-trivia",
                ),
                (
                    "[internal:trivia_round]",
                    "trivia_round",
                    "2099-01-01",
                    "21:00",
                    "scheduled",
                    "ai-fill-trivia",
                ),
                (
                    "שאלה חדשה ומקורית לגמרי לערב",
                    "discussion",
                    "2099-01-01",
                    "21:30",
                    "scheduled",
                    "ai-fill",
                ),
                (
                    # Verbatim echo of a current `prompts.yaml:morning` pool
                    # item — the static-example check should catch it. Kept
                    # in sync with the live pool; refresh if the pool rotates.
                    "בוקר טוב 🌞 מה הדבר הקטן שאם תסיימו היום יפנה לכם אוויר בראש?",
                    "morning",
                    "2099-01-02",
                    "09:00",
                    "scheduled",
                    "auto",
                ),
            ],
        )
        conn.commit()
        conn.close()
        return tmp, str(path)

    def test_find_stale_rows_reports_only_problematic_future_rows(self):
        tmp, path = self._db()
        self.addCleanup(tmp.cleanup)

        rows = find_stale_rows(path, from_date="2099-01-01")

        self.assertEqual([row["message_type"] for row in rows], ["discussion", "morning"])
        self.assertTrue(rows[0]["reasons"][0].startswith("forbidden fragment:"))
        self.assertEqual(rows[1]["reasons"], ["copied static example"])

    def test_cancel_rows_requires_explicit_apply_path(self):
        tmp, path = self._db()
        self.addCleanup(tmp.cleanup)
        rows = find_stale_rows(path, from_date="2099-01-01")

        changed = cancel_rows(path, [int(row["id"]) for row in rows])

        self.assertEqual(changed, 2)
        conn = sqlite3.connect(path)
        try:
            statuses = conn.execute(
                "SELECT text, status FROM scheduled_messages ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(statuses[0][1], "cancelled")
        self.assertEqual(statuses[1][1], "scheduled")
        self.assertEqual(statuses[2][1], "scheduled")
        self.assertEqual(statuses[3][1], "cancelled")

    def test_delete_action_removes_rows_so_materializer_can_refill(self):
        tmp, path = self._db()
        self.addCleanup(tmp.cleanup)
        rows = find_stale_rows(path, from_date="2099-01-01")

        changed = mutate_rows(path, [int(row["id"]) for row in rows], action="delete")

        self.assertEqual(changed, 2)
        conn = sqlite3.connect(path)
        try:
            remaining = conn.execute("SELECT COUNT(*) FROM scheduled_messages").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(remaining, 2)


if __name__ == "__main__":
    unittest.main()
