"""Retired starter pools must not seed, recycle, or erase historical rows."""

import asyncio

import pytest
import yaml
from pathlib import Path

from bot.database.db import Database


def test_startup_does_not_seed_retired_prompts(tmp_path):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            await db.seed_prompts({"morning": ["legacy morning"], "evening": ["legacy evening"]})
            async with db._db.execute("SELECT COUNT(*) FROM daily_prompts") as cursor:
                assert (await cursor.fetchone())[0] == 0
        finally:
            await db.close()

    asyncio.run(check())


def test_no_reusable_question_sources_remain_in_runtime_config():
    root = Path(__file__).resolve().parent.parent
    prompts = yaml.safe_load((root / "config" / "prompts.yaml").read_text(encoding="utf-8")) or {}
    discussions = yaml.safe_load((root / "config" / "discussions.yaml").read_text(encoding="utf-8")) or {}
    baseline = yaml.safe_load((root / "config" / "discussion_pool_baseline.yaml").read_text(encoding="utf-8")) or {}

    assert prompts.get("morning") == []
    assert prompts.get("evening") == []
    assert all(items == [] for items in discussions.values())
    # Category-only references are permitted; concrete reusable questions are
    # not. Adding a configured category must not weaken or break this invariant.
    allowlist = baseline.get("allowlist") or {}
    assert {"art", "funny", "general"} <= set(allowlist)
    assert all(entries == ["<category itself>"] for entries in allowlist.values())


def test_weekly_review_requires_explicit_audience_and_activation():
    settings = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config" / "settings.yaml").read_text(encoding="utf-8")
    ) or {}
    review = settings.get("weekly_state_review") or {}
    assert review.get("enabled") is False
    # The owner-approved editable invitation is allowed; the public template
    # cannot send until an exact audience and activation are configured.
    assert review.get("tag_usernames") == []
    assert review.get("selected_members") == []


@pytest.mark.parametrize("prompt_type", ["morning", "evening"])
@pytest.mark.parametrize("last_used", [None, "2026-08-01 09:00:00"])
def test_existing_prompts_cannot_send_or_recycle_after_restart(tmp_path, prompt_type, last_used):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            await db._db.execute(
                "INSERT INTO daily_prompts (type, text, last_used_at) VALUES (?, ?, ?)",
                (prompt_type, "historical starter", last_used),
            )
            await db._db.commit()
            async with db._db.execute("SELECT * FROM daily_prompts") as cursor:
                before = [tuple(row) for row in await cursor.fetchall()]
            for _ in range(2):
                await db.seed_prompts({prompt_type: ["replacement starter"]})
                assert await db.get_random_prompt(prompt_type) == ""
                async with db._db.execute("SELECT * FROM daily_prompts") as cursor:
                    assert [tuple(row) for row in await cursor.fetchall()] == before
                await db.close()
                await db.init()
        finally:
            await db.close()

    asyncio.run(check())
