"""One-off: send the first emoji-puzzle round to Sherlocks Den (test group).

This is a throwaway trial runner, not the production handler. If the round
lands, the real feature becomes a proper handler + scheduler + pool later.

Why a script and not a handler:
  - The production bot is already polling getUpdates on the VPS; a second
    polling client would fight over the lock.
  - For this test we only need to SEND. Replies and winner/reveal posts are
    handled manually by the admin (copy-paste templates from the dashboard
    /review page) — no watcher loop needed.

Flow:
  1. Admin approves the three `emoji-puzzle-r1-*` items on the dashboard
     /review page (VPS: http://84.46.253.137:8080/review).
  2. Admin runs this script from the project root:
         .venv/bin/python scripts/emoji_puzzle_test.py
  3. Script sends the intro+puzzle message to Sherlocks Den.
  4. Admin watches the Den in Telegram.
     - On first correct reply: copy the winner template from /review,
       replace {name} with the guesser's display name, reply to their guess.
     - If no one solves within 24h: post the reveal template.

Answer for round #1:   מלך האריות / The Lion King
Accepted aliases:      Lion King · lion king · ממלכת האריות · המלך האריה
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from telegram import Bot

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))
_ = load_dotenv()

TEST_GROUP_ID = -1003747545764  # Sherlocks Den
TOPIC_ID: int | None = None     # Sherlocks Den is a regular group, no forum topics

PUZZLE_MESSAGE = (
    "🎬 חידת אימוג'י — משחק חדש!\n"
    "\n"
    "האימוג'ים למטה מתארים סרט או סדרה.\n"
    "הראשונ/ה שפותר/ת — זוכה בהכרה ציבורית 🎉\n"
    "\n"
    "איך משחקים:\n"
    "• השיבו עם הניחוש (reply להודעה הזו)\n"
    "• עברית או אנגלית — שניהם עובדים\n"
    "• 24 שעות לפתור\n"
    "\n"
    "זו ההרצה הראשונה — בלי נקודות הפעם, רק בשביל הכיף.\n"
    "אם יאהבו — נהפוך את זה לקבוע שבועי.\n"
    "\n"
    "──────\n"
    "\n"
    "🦁👑\n"
    "\n"
    "מה זה?"
)


async def main() -> None:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        print("ERROR: BOT_TOKEN not set in .env", file=sys.stderr)
        sys.exit(1)

    bot = Bot(token=token)

    send_kwargs: dict[str, Any] = {
        "chat_id": TEST_GROUP_ID,
        "text": PUZZLE_MESSAGE,
    }
    if TOPIC_ID is not None:
        send_kwargs["message_thread_id"] = TOPIC_ID

    msg = await bot.send_message(**send_kwargs)
    print(f"Sent puzzle to Sherlocks Den ({TEST_GROUP_ID}).")
    print(f"  message_id: {msg.message_id}")
    print(f"  sent_at:    {msg.date.isoformat()}")
    print()
    print("Next steps:")
    print("  - Watch replies in the Den.")
    print("  - On first correct guess: copy the winner template from")
    print("    /review, replace {name}, reply to the guess.")
    print("  - No reply after 24h → post the reveal template.")


if __name__ == "__main__":
    asyncio.run(main())
