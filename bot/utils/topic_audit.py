"""Startup audit: walk every place a topic_id can be configured and
warn if any refers to a topic that isn't in verified_forum_topics.

Non-fatal. Writes WARN lines to data/bot.log (default logger) so
operators can spot drift before sends start getting refused by the
topic_guard at runtime. Called once after DB init in bot/main.py.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from ..database.db import Database

logger = logging.getLogger(__name__)


CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


async def _verified_ids(db: Database) -> set[int]:
    rows = await db.get_verified_forum_topics()
    return {int(r["topic_id"]) for r in rows if r.get("topic_id") is not None}


def _collect_settings_topics() -> list[tuple[int, str]]:
    """Return (topic_id, source_label) pairs from settings.yaml's topics.*"""
    out: list[tuple[int, str]] = []
    path = CONFIG_DIR / "settings.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("topic_audit: failed to read settings.yaml: %s", e)
        return out
    topics: dict[str, Any] = data.get("topics") or {}
    for key, value in topics.items():
        if isinstance(value, int):
            out.append((int(value), f"settings.topics.{key}"))
        elif isinstance(value, dict):
            for sub_key, sub_value in value.items():
                if isinstance(sub_value, int):
                    out.append((int(sub_value), f"settings.topics.{key}.{sub_key}"))
    return out


def _collect_topic_rules() -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    path = CONFIG_DIR / "topic_rules.yaml"
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("topic_audit: failed to read topic_rules.yaml: %s", e)
        return out
    for rule in (data.get("topics") or []):
        tid = rule.get("topic_id") if isinstance(rule, dict) else None
        if isinstance(tid, int):
            out.append((int(tid), f"topic_rules.topics[{rule.get('category_key')}].topic_id"))
    return out


async def _collect_routing(db: Database) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for row in await db.list_handler_routings():
        if row.get("play_topic_id") is not None:
            out.append((int(row["play_topic_id"]), f"bot_message_routing.{row['handler']}.play_topic_id"))
        for tid in row.get("teaser_topic_ids") or []:
            out.append((int(tid), f"bot_message_routing.{row['handler']}.teaser_topic_ids"))
    return out


async def _collect_scheduled_messages(db: Database) -> list[tuple[int, str]]:
    """Pending scheduled_messages with non-null channel_topic_id."""
    out: list[tuple[int, str]] = []
    try:
        async with db._db.execute(
            "SELECT id, channel_topic_id FROM scheduled_messages "
            "WHERE channel_topic_id IS NOT NULL AND sent_at IS NULL"
        ) as cursor:
            rows = await cursor.fetchall()
        for row in rows:
            out.append((int(row["channel_topic_id"]), f"scheduled_messages[{row['id']}].channel_topic_id"))
    except Exception as e:
        logger.debug("topic_audit: scheduled_messages walk skipped: %s", e)
    return out


async def run(db: Database) -> None:
    try:
        verified = await _verified_ids(db)
    except Exception as e:
        logger.warning("topic_audit: could not load verified_forum_topics: %s", e)
        return

    refs: list[tuple[int, str]] = []
    refs.extend(_collect_settings_topics())
    refs.extend(_collect_topic_rules())
    refs.extend(await _collect_routing(db))
    refs.extend(await _collect_scheduled_messages(db))

    warnings = 0
    for topic_id, source in refs:
        if topic_id not in verified:
            logger.warning(
                "topic_audit: topic_id=%s referenced by %s is not in verified_forum_topics; sends will be refused",
                topic_id,
                source,
            )
            warnings += 1
    if warnings == 0:
        logger.info("topic_audit: %d topic references checked, all verified", len(refs))
    else:
        logger.warning("topic_audit: %d/%d topic references are unverified", warnings, len(refs))
