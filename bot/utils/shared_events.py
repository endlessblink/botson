"""Events shared between the Telegram group and the WhatsApp group.

An event created on the dashboard can also go to WhatsApp: the event card as
text, then a sign-up poll. Botty receives WhatsApp poll votes from WAHA and
forwards them to Botson, which stores them per event. The Telegram card,
reminders and the dashboard then show WhatsApp sign-ups as counts only —
WhatsApp members' names never appear in the Telegram group.
"""
from __future__ import annotations

import logging

from . import whatsapp_sender
from .copy import load_copy

logger = logging.getLogger(__name__)

NAMES_MARKER = "\n\n✅ מגיעים"  # noqa: hardcoded-content (marker of the existing Telegram RSVP names block)


def stanza_of(msg_id: str | None) -> str:
    """WhatsApp message key without the chat/sender parts (same as Botty's stanzaOf)."""
    value = str(msg_id or "")
    parts = value.split("_")
    return parts[2] if len(parts) >= 3 else value


def poll_options() -> list[str]:
    return [load_copy("whatsapp", k) for k in ("event_poll_yes", "event_poll_maybe", "event_poll_no")]


def status_for_option(option: str | None) -> str | None:
    """Map a WhatsApp poll answer to 'yes' / 'maybe'; anything else clears the sign-up."""
    yes, maybe, _no = poll_options()
    option = str(option or "").strip()
    if option == yes:
        return "yes"
    if option == maybe:
        return "maybe"
    return None


async def publish_to_whatsapp(db, event_id: int, target: str, card_text: str) -> str:
    """Send the event card and its sign-up poll to WhatsApp. Returns the poll key."""
    chat_id = whatsapp_sender.resolve_chat_id(target)
    await whatsapp_sender.send_text(chat_id, card_text)
    poll_id = await whatsapp_sender.send_poll(chat_id, load_copy("whatsapp", "event_poll_question"), poll_options())
    stanza = stanza_of(poll_id)
    if not stanza:
        raise RuntimeError("WhatsApp did not return a poll id")
    await db.set_event_whatsapp_poll(event_id, target, stanza)
    return stanza


async def whatsapp_line(db, event: dict) -> str:
    """The WhatsApp count line for a shared event, or '' for a Telegram-only one."""
    if not event or not event.get("wa_target"):
        return ""
    counts = await db.get_event_whatsapp_counts(event["id"])
    return load_copy("whatsapp", "event_whatsapp_counts", yes=counts["yes"], maybe=counts["maybe"])


def _wa_marker() -> str:
    template = load_copy("whatsapp", "event_whatsapp_counts")
    return "\n\n" + template.split("{", 1)[0]


def strip_rsvp_blocks(text: str) -> str:
    """The card without the sign-up blocks this module and the RSVP handler append."""
    text = str(text or "")
    for marker in (NAMES_MARKER, _wa_marker()):
        text = text.split(marker, 1)[0]
    return text


def card_with_rsvps(base: str, yes_names: list[str], maybe_names: list[str], wa_line: str) -> str:
    text = strip_rsvp_blocks(base)
    if yes_names or maybe_names:
        maybe_label = load_copy("whatsapp", "event_rsvp_maybe_label")
        text += (
            f"{NAMES_MARKER} ({len(yes_names)}): {', '.join(yes_names) if yes_names else '—'}\n"
            f"{maybe_label} ({len(maybe_names)}): {', '.join(maybe_names) if maybe_names else '—'}"
        )
    if wa_line:
        text += f"\n\n{wa_line}"
    return text
