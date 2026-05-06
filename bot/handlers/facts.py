"""Daily 'did you know' / spooky-folklore poster.

Pulls from config/facts.yaml — a hand-curated pool with citation per item.
Picks one item, posts to the routing target, and records the id so the
same fact doesn't repeat for a configurable cooldown window.

Two pools: `tidbit` (weekday share-worthy facts) and `spooky` (Friday
night / weekend atmospheric folklore + documented mysteries). The caller
selects which pool to draw from; this module does NOT decide the schedule.

Status: handler is wired and tested but NOT yet attached to the scheduler.
The pool content needs review on the dashboard `/review` page before going
live (CLAUDE.md rule: never ship Hebrew text without dashboard approval).
"""
from __future__ import annotations

import logging
import os
import random
from io import BytesIO
from typing import Iterable

from ..database.db import Database
from ..utils.config import load_yaml
from ..utils.topic_guard import UnverifiedTopicError, safe_send

logger = logging.getLogger(__name__)

POOLS = ("tidbit", "spooky")
DEFAULT_COOLDOWN_DAYS = 60  # don't repeat the same fact within ~2 months


def load_facts_pool(pool: str) -> list[dict]:
    """Return the list of items from config/facts.yaml for the given pool.
    Items missing required citation/image fields are dropped with a warning —
    the YAML is hand-edited and a typo shouldn't crash the round or publish
    an uncited / imageless fact."""
    if pool not in POOLS:
        raise ValueError(f"unknown facts pool: {pool!r} (allowed: {POOLS})")
    data = load_yaml("facts.yaml") or {}
    raw = data.get(pool) or []
    items: list[dict] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        item_id = str(entry.get("id") or "").strip()
        text = str(entry.get("text_he") or "").strip()
        source = str(entry.get("source") or "").strip()
        source_url = str(entry.get("source_url") or "").strip()
        image_prompt = str(entry.get("image_prompt") or "").strip()
        image_url = str(entry.get("image_url") or "").strip()
        if not (item_id and text and source and source_url and (image_prompt or image_url)):
            logger.warning("facts.yaml: skipping incomplete entry: %s", entry)
            continue
        item = {"id": item_id, "text_he": text, "source": source, "source_url": source_url}
        if image_prompt:
            item["image_prompt"] = image_prompt
        if image_url:
            item["image_url"] = image_url
        items.append(item)
    return items


def pick_fact(pool: str, recently_sent_ids: Iterable[str]) -> dict | None:
    """Pick one fact from the pool that hasn't been sent recently.
    Returns None if the pool is empty or every item is on cooldown — in
    that case the caller should log + skip rather than send a repeat."""
    items = load_facts_pool(pool)
    if not items:
        return None
    recent = {str(i) for i in recently_sent_ids}
    eligible = [i for i in items if i["id"] not in recent]
    if not eligible:
        logger.warning(
            "facts: every item in pool=%s is within cooldown — skipping send", pool,
        )
        return None
    return random.choice(eligible)


def format_fact_message(fact: dict) -> str:
    """Render a fact with visible provenance for the Telegram post."""
    text = str(fact.get("text_he") or "").rstrip()
    source = str(fact.get("source") or "").strip()
    source_url = str(fact.get("source_url") or "").strip()
    if source and source_url:
        return f"{text}\n\nמקור: {source}\n{source_url}"
    if source:
        return f"{text}\n\nמקור: {source}"
    return text


def _build_fact_image_prompt(pool: str, fact: dict) -> str:
    explicit = str(fact.get("image_prompt") or "").strip()
    if explicit:
        return explicit
    mood = "cinematic mysterious editorial illustration" if pool == "spooky" else "curious science editorial illustration"
    text = str(fact.get("text_he") or "").replace("\n", " ").strip()
    return (
        f"{mood}, 16:9, no text, no letters, no logos. "
        f"Illustrate this Hebrew Telegram post concept visually: {text[:500]}"
    )


def _photo_caption_with_source(fact: dict) -> str:
    """Telegram photo captions are limited; keep source visible."""
    text = str(fact.get("text_he") or "").strip()
    source = str(fact.get('source') or '').strip()
    source_url = str(fact.get('source_url') or '').strip()
    source_line = f"מקור: {source}\n{source_url}" if source_url else f"מקור: {source}"
    budget = 1000 - len(source_line) - 2
    if len(text) > budget:
        text = text[:max(0, budget - 1)].rstrip() + "…"
    return f"{text}\n\n{source_line}"


async def _generate_fact_image(pool: str, fact: dict) -> tuple[BytesIO, str] | None:
    api_key = os.getenv("KIE_API_KEY", "")
    if not api_key:
        return None
    try:
        from ..utils.kie_client import generate_image_sync
        data, ext = await generate_image_sync(
            api_key=api_key,
            prompt=_build_fact_image_prompt(pool, fact),
        )
    except Exception as e:
        logger.warning("facts: image generation failed; skipping fact with no image: %s", e)
        return None
    photo = BytesIO(data)
    photo.name = f"facts_{fact.get('id') or pool}.{ext or 'png'}"
    return photo, _photo_caption_with_source(fact)


async def _resolve_fact_photo(pool: str, fact: dict) -> tuple[object, str] | None:
    """Return a send_photo payload or None when no relevant image exists.

    Facts must never be pushed as text-only. A curator can attach an explicit
    image_url, or provide image_prompt and rely on configured image generation.
    """
    image_url = str(fact.get("image_url") or "").strip()
    if image_url:
        return image_url, _photo_caption_with_source(fact)
    if not str(fact.get("image_prompt") or "").strip():
        logger.warning("facts: skipping %s because it has no associated image", fact.get("id"))
        return None
    return await _generate_fact_image(pool, fact)


async def send_scheduled_fact(bot, db: Database, *, pool: str, chat_id: int,
                              thread_id: int | None) -> bool:
    """Pick + post one fact. Returns True if a message was actually sent.

    The caller (scheduler / calendar handler) is responsible for:
      - deciding which pool to draw from
      - resolving chat_id / thread_id from the routing table
      - logging the activity row after a successful send
    """
    handler_name = f"facts_{pool}"
    routing = await db.get_handler_routing(handler_name) if hasattr(db, "get_handler_routing") else None
    recent_ids = []
    if hasattr(db, "get_recent_activity_subjects"):
        # If the project later grows a per-fact dedup table, point to it
        # here. For now, the activity log carries the id in `description`.
        try:
            recent_ids = await db.get_recent_activity_subjects(
                action_type=handler_name, days=DEFAULT_COOLDOWN_DAYS,
            ) or []
        except Exception as e:  # pragma: no cover — defensive
            logger.debug("facts: recent-subjects lookup failed: %s", e)

    fact = pick_fact(pool, recent_ids)
    if fact is None:
        return False

    try:
        resolved_photo = await _resolve_fact_photo(pool, fact)
        if resolved_photo is None:
            return False
        photo, caption = resolved_photo
        await safe_send(
            bot, db, "send_photo",
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            message_thread_id=thread_id,
        )
    except UnverifiedTopicError as e:
        logger.error("facts: send refused by topic guard (%s/%s): %s", chat_id, thread_id, e)
        return False
    except Exception as e:
        logger.error("facts: send failed: %s", e)
        return False

    if hasattr(db, "log_activity"):
        try:
            await db.log_activity(
                handler_name,
                fact["id"],  # store the id so dedup can find it later
                target_channel=str(thread_id) if thread_id else None,
            )
        except Exception as e:  # pragma: no cover
            logger.debug("facts: activity log failed: %s", e)
    # Routing field reserved for future per-pool channel splits — read here
    # so its absence in the table doesn't surface as an unused-import noise.
    _ = routing
    return True
