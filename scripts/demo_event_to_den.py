"""One-shot: send a demo event card to the Sherlocks Den test group.

End-to-end check of the events feature shape:
  1. Generate a cover image via kie.ai (Flux 2 Pro 16:9).
  2. Save it to media/covers/ using the same naming convention as
     dashboard/app.py /api/covers/generate.
  3. Send the event message (photo + caption) to Sherlocks Den
     (-1003747545764) via send_message_with_optional_cover.
  4. Attach RSVP inline buttons (rsvp_yes / rsvp_maybe — handled by
     bot/handlers/events.py callback handler in the running bot).
  5. Pin the message.

Usage:  .venv/bin/python3 scripts/demo_event_to_den.py
Requires:  BOT_TOKEN, KIE_API_KEY in .env (auto-loaded).

This is a throwaway runner — the production flow is the dashboard
form at /events. After viewing the demo, unpin/delete via Telegram.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
import time
from datetime import date, timedelta
from pathlib import Path

# Make the bot package importable when run as a script
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

# Auto-load .env so BOT_TOKEN / KIE_API_KEY are available
try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(_REPO / ".env")
except ImportError:
    pass

from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup

from bot.handlers.calendar import send_message_with_optional_cover
from bot.utils.kie_client import generate_image_sync


DEN_CHAT_ID = -1003747545764
COVERS_DIR = Path(os.getenv("MEDIA_DIR", str(_REPO / "media"))) / "covers"


def _cover_filename(tag: str, ext: str) -> str:
    """Same scheme as dashboard/app.py: {ts}_{tag}_{hex}.{ext}"""
    return f"{int(time.time())}_{tag}_{secrets.token_hex(4)}.{ext}"


async def _generate_cover(prompt: str) -> str:
    api_key = os.getenv("KIE_API_KEY", "")
    if not api_key:
        raise RuntimeError("KIE_API_KEY not set in environment")
    print(f"[demo] generating cover via kie.ai…  prompt={prompt!r}")
    image_bytes, ext = await generate_image_sync(api_key=api_key, prompt=prompt)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    fname = _cover_filename("ai", ext)
    full = COVERS_DIR / fname
    full.write_bytes(image_bytes)
    print(f"[demo] saved cover: {full}  ({len(image_bytes):,} bytes)")
    # Return the path relative to MEDIA_DIR — same shape used by /api/covers/* responses
    return f"covers/{fname}"


def _format_event_text(*, title: str, description: str, when: str, location: str) -> str:
    return (
        f"📅 *{title}*\n"
        f"🕒 {when}\n"
        f"📍 {location}\n"
        "\n"
        f"{description}"
    )


def _rsvp_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ מגיע/ה", callback_data="rsvp_yes"),
        InlineKeyboardButton("🤔 אולי", callback_data="rsvp_maybe"),
    ]])


async def main():
    bot_token = os.getenv("BOT_TOKEN", "")
    if not bot_token:
        print("ERROR: BOT_TOKEN not set", file=sys.stderr)
        sys.exit(1)

    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    title = "🎬 ערב סרטים — בדיקה"
    description = (
        "סרט אחד, פופקורן, אווירה רגועה.\n"
        "(זה אירוע בדיקה — לא חייבים להגיב, רק רוצים לראות שהפיצ'ר עובד.)"
    )
    when = f"{tomorrow} · 20:00"
    location = "זום"

    cover_prompt = (
        "cozy living-room movie-night scene, popcorn bowls on a coffee table, "
        "soft warm lighting, projector glow, cinematic 16:9, no text"
    )

    cover_path = await _generate_cover(cover_prompt)

    bot = Bot(bot_token)
    text = _format_event_text(
        title=title, description=description, when=when, location=location,
    )

    print(f"[demo] posting event to Den ({DEN_CHAT_ID})…")
    sent = await send_message_with_optional_cover(
        bot,
        chat_id=DEN_CHAT_ID,
        text=text,
        message_thread_id=None,  # general topic
        cover_path=cover_path,
    )
    print(f"[demo] message_id = {sent.message_id}")

    # Attach RSVP buttons (the live bot handles rsvp_yes / rsvp_maybe callbacks)
    try:
        await bot.edit_message_reply_markup(
            chat_id=DEN_CHAT_ID,
            message_id=sent.message_id,
            reply_markup=_rsvp_markup(),
        )
        print("[demo] RSVP buttons attached")
    except Exception as e:
        print(f"[demo] WARN: failed to attach RSVP buttons: {e}")

    # Pin
    try:
        await bot.pin_chat_message(
            chat_id=DEN_CHAT_ID,
            message_id=sent.message_id,
            disable_notification=True,
        )
        print("[demo] message pinned ✓")
    except Exception as e:
        print(f"[demo] WARN: failed to pin: {e}")

    print()
    print(f"DONE  cover={cover_path}  message_id={sent.message_id}")


if __name__ == "__main__":
    asyncio.run(main())
