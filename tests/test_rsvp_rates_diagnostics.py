from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from bot.database.db import Database


class RsvpRatesDiagnosticsTests(unittest.TestCase):
    def test_duplicate_warmups_report_marker_level_rsvps_and_game_message(self):
        async def seed(db_path: str) -> None:
            db = Database(db_path)
            await db.init()
            try:
                marker = "warmup-rsvp:trivia:2099-01-01:21:00"
                payload = json.dumps({
                    "warmup_marker": marker,
                    "min_ready_players": 2,
                    "theme_label": "מדע",
                }, ensure_ascii=False)
                first = await db.create_scheduled_message(
                    text="warmup 1",
                    message_type="trivia_warmup_rsvp",
                    channel_topic_id=341,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="20:00",
                    poll_options=payload,
                    status="scheduled",
                )
                await db.mark_message_sent(first, 7001)
                await db.add_trivia_interest_response(first, 101, "Lotem")
                await db.add_trivia_interest_response(first, 202, "Refeli")

                duplicate = await db.create_scheduled_message(
                    text="warmup duplicate",
                    message_type="trivia_warmup_rsvp",
                    channel_topic_id=341,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="20:00",
                    poll_options=payload,
                    status="scheduled",
                )
                await db.mark_message_sent(duplicate, 7002)
                await db.add_trivia_interest_response(duplicate, 101, "Lotem")

                game_payload = json.dumps({
                    "warmup_marker": marker,
                    "min_ready_players": 2,
                }, ensure_ascii=False)
                game = await db.create_scheduled_message(
                    text="game",
                    message_type="trivia_round",
                    channel_topic_id=4037,
                    target_group="main",
                    scheduled_date="2099-01-01",
                    scheduled_time="21:00",
                    poll_options=game_payload,
                    status="scheduled",
                )
                await db.mark_message_sent(game, 9001)
            finally:
                await db.close()

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            asyncio.run(seed(tmp.name))
            script = Path(__file__).resolve().parents[1] / "scripts" / "rsvp_rates.py"
            env = {**os.environ, "DB_PATH": tmp.name}
            result = subprocess.run(
                [sys.executable, str(script), "10"],
                env=env,
                check=True,
                text=True,
                capture_output=True,
            )

        self.assertIn("marker", result.stdout)
        self.assertIn("g_topic", result.stdout)
        self.assertIn("g_msg", result.stdout)
        self.assertRegex(result.stdout, re.compile(r"\bsent\s+1\s+2\s+2\s+sent\s+4037\s+9001\b"))
        self.assertRegex(result.stdout, re.compile(r"\bsent\s+2\s+2\s+2\s+sent\s+4037\s+9001\b"))
        self.assertNotRegex(result.stdout, re.compile(r"\bsent\s+\d+\s+3\s+2\s+sent\s+4037\s+9001\b"))
        self.assertIn("avg marker RSVPs", result.stdout)


if __name__ == "__main__":
    unittest.main()
