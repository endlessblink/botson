"""Free-games RSS handler — fetches GG.deals freebie feed and posts new items."""

from datetime import date
import logging
import os
from typing import Any

import feedparser  # type: ignore[import-untyped]
import httpx

from ..database.db import Database
from ..utils.config import GROUP_ID, get_settings, is_auto_blocked_on, is_feature_enabled

logger = logging.getLogger(__name__)

_UA = "botson/1.0 (+https://github.com/endlessblink/robotnik)"
_DEFAULT_FEED = "https://gg.deals/eu/news/feed/"
_FREEBIE_PATH = "/freebie/"
_MAX_CANDIDATES = 5  # how many surviving freebies to send to the LLM reranker

# Skip aggregator/roundup posts — we want single-game freebies, not lists.
_AGGREGATOR_TOKENS = (
    "roundup", "weekend", "weekly", "this week", "best deals",
    "best free", "best freebies", "top deals", "deals of",
)


def _is_aggregator(title: str) -> bool:
    t = title.lower()
    return any(tok in t for tok in _AGGREGATOR_TOKENS)


_CLAUDE_CLI_TIMEOUT = 90  # seconds — CLI has ~30s startup overhead


async def _llm_pick_best(candidates: list[dict[str, Any]]) -> int:
    """Ask Claude Haiku (via `claude` CLI) to pick the best candidate.

    Uses the local `claude -p` print mode, which runs under the user's existing
    logged-in session — no API key needed. Returns the index into `candidates`.
    On any failure (CLI missing, timeout, unparseable output), falls back to 0.
    """
    if len(candidates) <= 1:
        return 0

    import asyncio
    import shutil

    claude_bin = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    if not os.path.exists(claude_bin):
        logger.info("free_games: claude CLI not found, falling back to newest")
        return 0

    numbered = "\n".join(f"{i+1}. {c['title']}" for i, c in enumerate(candidates))
    prompt = (
        "Pick the most interesting free-game announcement for a general gaming community. "
        "Prefer well-known titles, permanent 'keep' giveaways over trials, and mainstream "
        "stores (Steam, Epic, GOG) over obscure ones.\n\n"
        f"{numbered}\n\n"
        "Reply with ONLY the number (1-based). No explanation."
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            claude_bin, "-p", prompt, "--model", "haiku",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=_CLAUDE_CLI_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("free_games: claude CLI timed out after %ds, using newest",
                           _CLAUDE_CLI_TIMEOUT)
            return 0

        if proc.returncode != 0:
            logger.warning("free_games: claude CLI exited %d: %s",
                           proc.returncode, stderr.decode()[:200])
            return 0

        text = stdout.decode().strip()
        digits = "".join(c for c in text.split()[0] if c.isdigit()) if text else ""
        if not digits:
            logger.warning("free_games: LLM returned no digit in %r, using newest", text[:100])
            return 0
        idx = int(digits) - 1
        if 0 <= idx < len(candidates):
            logger.info("free_games: LLM picked #%d of %d: %s",
                        idx + 1, len(candidates), candidates[idx]["title"][:80])
            return idx
        logger.warning("free_games: LLM picked out-of-range %d, using newest", idx + 1)
        return 0
    except Exception as e:
        logger.warning("free_games: LLM rerank failed (%s), using newest", e)
        return 0


def _resolve_target_group() -> int | None:
    """Return the first group_id for which free_games is enabled, or None."""
    from ..utils.config import TEST_GROUP_ID
    # Prefer main if enabled there, else test
    for gid in (GROUP_ID, TEST_GROUP_ID):
        if gid and is_feature_enabled("free_games", gid):
            return gid
    return None


async def fetch_and_post_once(bot, db: Database, group_id: int,
                              topic_id: int | None, feed_url: str) -> dict[str, Any]:
    """Fetch the feed once, dedup, post new freebies, return a summary.

    Returns: {"fetched": int, "posted": int, "skipped": int, "error": str|None}
    Never raises — all errors logged and returned in the summary.
    """
    summary: dict[str, Any] = {"fetched": 0, "posted": 0, "skipped": 0, "error": None}

    # Fetch the feed body
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _UA}, timeout=30) as client:
            response = await client.get(feed_url)
            response.raise_for_status()
            body = response.text
    except Exception as e:
        logger.warning("free_games: feed fetch failed (%s): %s", feed_url, e)
        summary["error"] = f"fetch failed: {e}"
        return summary

    # Parse feed
    try:
        feed = feedparser.parse(body)
    except Exception as e:
        logger.warning("free_games: feed parse failed: %s", e)
        summary["error"] = f"parse failed: {e}"
        return summary

    entries = list(getattr(feed, "entries", []) or [])
    summary["fetched"] = len(entries)

    # Build candidate list: freebie-path + not aggregator + not already posted.
    # Feed order is newest-first; collect up to _MAX_CANDIDATES then let the LLM pick.
    candidates: list[dict[str, Any]] = []
    for entry in entries:
        link = entry.get("link") or ""
        if not link or _FREEBIE_PATH not in link:
            summary["skipped"] += 1
            continue

        title = (entry.get("title") or "").strip() or link
        if _is_aggregator(title):
            summary["skipped"] += 1
            continue

        guid = entry.get("id") or link
        try:
            if await db.is_game_posted(guid):
                summary["skipped"] += 1
                continue
        except Exception as e:
            logger.warning("free_games: dedup check failed for %s: %s", guid, e)
            summary["skipped"] += 1
            continue

        candidates.append({"guid": guid, "title": title, "link": link})
        if len(candidates) >= _MAX_CANDIDATES:
            break

    candidate = None
    if candidates:
        idx = await _llm_pick_best(candidates)
        candidate = candidates[idx]
        summary["candidates_considered"] = len(candidates)
        summary["picked_by"] = "llm" if len(candidates) > 1 else "newest"

    if not candidate:
        logger.info(
            "free_games: fetched=%d posted=0 skipped=%d — no new single-game freebies",
            summary["fetched"], summary["skipped"],
        )
        return summary

    text = f"🎮 משחק חינם\n\n{candidate['title']}\n\n{candidate['link']}"
    kwargs = {"chat_id": group_id, "text": text, "disable_web_page_preview": False}
    if topic_id:
        kwargs["message_thread_id"] = topic_id

    try:
        msg = await bot.send_message(**kwargs)
    except Exception as e:
        logger.warning("free_games: send_message failed for %s: %s", candidate["guid"], e)
        summary["error"] = f"send failed: {e}"
        return summary

    try:
        await db.mark_game_posted(
            candidate["guid"], candidate["title"], "gg.deals",
            candidate["link"], getattr(msg, "message_id", None),
        )
        await db.log_activity(
            "free_games",
            f"שלח משחק חינם: {candidate['title'][:80]}",
            target_channel="gaming",
        )
    except Exception as e:
        logger.warning("free_games: DB write after send failed for %s: %s", candidate["guid"], e)

    summary["posted"] = 1

    logger.info(
        "free_games: fetched=%d posted=%d skipped=%d error=%s",
        summary["fetched"], summary["posted"], summary["skipped"], summary["error"],
    )
    return summary


async def send_free_games(context):
    """Scheduler entrypoint — called by JobQueue.run_daily."""
    if is_auto_blocked_on(date.today()):
        logger.info("free_games: blackout date, skipping automatic post")
        return

    target_gid = _resolve_target_group()
    if not target_gid:
        logger.info("free_games: disabled for all groups, skipping tick")
        return

    db: Database = context.bot_data.get("db")
    if not db:
        logger.warning("free_games: db not available in bot_data, skipping tick")
        return

    settings = get_settings()
    fg_schedule = settings.get("schedule", {}).get("free_games", {})
    feed_url = fg_schedule.get("feed_url", _DEFAULT_FEED)
    topic_id = settings.get("topics", {}).get("gaming") if target_gid == GROUP_ID else None

    await fetch_and_post_once(context.bot, db, target_gid, topic_id, feed_url)
