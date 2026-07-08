"""LLM-backed auto-refill for Emoji Night puzzle pools."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pwd
import shutil

from ..database.db import Database
from .config import get_anthropic_config
from .game_categories import canonical_emoji_media_type

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 90


def _media_label(media_type: str) -> str:
    return {
        "movie": "movie",
        "series": "TV series",
        "book": "book",
        "song": "song or album",
        "game": "video game",
    }.get(canonical_emoji_media_type(media_type), "well-known work")


async def _generate_via_claude(prompt: str) -> str | None:
    claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not claude_bin or not os.path.exists(claude_bin):
        return None
    try:
        try:
            real_home = pwd.getpwuid(os.geteuid()).pw_dir
        except Exception:
            real_home = os.path.expanduser("~")
        proc = await asyncio.create_subprocess_exec(
            claude_bin,
            "-p",
            prompt,
            "--model",
            os.getenv("BOTSON_EMOJI_REFILL_CLI_MODEL", "haiku"),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "HOME": real_home},
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        logger.warning("emoji_puzzle_refill: Claude CLI timed out")
        return None
    except Exception as exc:
        logger.warning("emoji_puzzle_refill: Claude CLI failed: %s", exc)
        return None
    if proc.returncode != 0:
        logger.warning("emoji_puzzle_refill: Claude CLI rc=%s stderr=%s", proc.returncode, stderr.decode(errors="replace")[:300])
        return None
    return stdout.decode(errors="replace").strip() or None


async def _generate_via_api(prompt: str) -> str | None:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import httpx

        api_url, model = get_anthropic_config()
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            response = await client.post(
                api_url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": 1600,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["content"][0]["text"].strip()
    except Exception as exc:
        logger.warning("emoji_puzzle_refill: Anthropic API failed: %s", exc)
        return None


def _parse_generated(raw: str) -> list[dict]:
    start = raw.find("[")
    end = raw.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        parsed = json.loads(raw[start:end + 1])
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    items: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        emoji_prompt = str(item.get("emoji_prompt") or "").strip()
        answer_he = str(item.get("answer_he") or "").strip()
        answer_en = str(item.get("answer_en") or "").strip()
        aliases = item.get("aliases") if isinstance(item.get("aliases"), list) else []
        if emoji_prompt and answer_he and answer_en:
            items.append({
                "emoji_prompt": emoji_prompt,
                "answer_he": answer_he,
                "answer_en": answer_en,
                "aliases": [str(a).strip() for a in aliases if str(a).strip()],
            })
    return items


async def _existing_answers(db: Database) -> set[str]:
    answers: set[str] = set()
    try:
        async with db._db.execute("SELECT answer_he, answer_en FROM emoji_puzzles") as cursor:
            async for row in cursor:
                for key in ("answer_he", "answer_en"):
                    value = str(row[key] or "").strip().casefold()
                    if value:
                        answers.add(value)
    except Exception as exc:
        logger.warning("emoji_puzzle_refill: existing answer lookup failed: %s", exc)
    return answers


def _build_prompt(count: int, media_type: str, existing: set[str]) -> str:
    media_label = _media_label(media_type)
    sample = sorted(existing)[:120]
    avoid = "\n".join(f"- {answer}" for answer in sample)
    return f"""Create {count} new emoji puzzle rows for a Telegram community of Israeli adults.

Target answer type: {media_label}.

Rules:
- Each puzzle represents one well-known {media_label}.
- The emoji_prompt must be a short visual emoji sequence, no letter emoji.
- answer_he must be the common Hebrew title or artist/title as Hebrew text.
- answer_en must be the common international English name.
- aliases must include 2 to 4 likely typed answers.
- Do not repeat any existing answer listed below.

Existing answers to avoid:
{avoid}

Return only valid JSON, an array of objects:
[{{"emoji_prompt":"...", "answer_he":"...", "answer_en":"...", "aliases":["..."]}}]"""


async def generate_emoji_puzzles(db: Database, *, media_type: str, count: int) -> int:
    """Generate and insert up to ``count`` fresh puzzle rows."""
    if count <= 0:
        return 0
    canonical = canonical_emoji_media_type(media_type)
    existing = await _existing_answers(db)
    prompt = _build_prompt(max(count, 3), canonical, existing)
    raw = await _generate_via_claude(prompt)
    if raw is None:
        raw = await _generate_via_api(prompt)
    if raw is None:
        logger.warning("emoji_puzzle_refill: no LLM provider available for media_type=%s", canonical)
        return 0

    inserted = 0
    for item in _parse_generated(raw):
        key_he = item["answer_he"].casefold()
        key_en = item["answer_en"].casefold()
        if key_he in existing or key_en in existing:
            continue
        try:
            await db.create_emoji_puzzle(
                emoji_prompt=item["emoji_prompt"],
                answer_he=item["answer_he"],
                answer_en=item["answer_en"],
                aliases=json.dumps(item["aliases"], ensure_ascii=False),
                difficulty=2,
                media_type=canonical,
            )
        except Exception as exc:
            logger.warning("emoji_puzzle_refill: insert failed for %r: %s", item.get("answer_en"), exc)
            continue
        existing.add(key_he)
        existing.add(key_en)
        inserted += 1
        if inserted >= count:
            break
    logger.info("emoji_puzzle_refill: inserted=%d requested=%d media_type=%s", inserted, count, canonical)
    return inserted
