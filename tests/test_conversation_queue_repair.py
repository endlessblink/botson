"""Queue cleanup is reversible, scoped, and refuses stale review snapshots."""

import asyncio

import pytest

from bot.database.db import Database


async def create_row(db, **overrides):
    fields = dict(text="reviewed starter", message_type="morning", channel_topic_id=10,
                  target_group="main", scheduled_date="2026-09-13", scheduled_time="09:00",
                  created_by="auto")
    fields.update(overrides)
    msg_id = await db.create_scheduled_message(**fields)
    return msg_id, await db.get_scheduled_message(msg_id)


@pytest.mark.parametrize("kind", ["morning", "evening", "discussion"])
def test_quarantine_preserves_row_and_is_idempotent(tmp_path, kind):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            msg_id, snapshot = await create_row(db, message_type=kind)
            assert await db.quarantine_conversation_message(msg_id, snapshot) == "changed"
            expected = {**snapshot, "status": "draft", "error_message": "conversation_cleanup"}
            assert await db.get_scheduled_message(msg_id) == expected
            assert await db.quarantine_conversation_message(msg_id, snapshot) == "already_quarantined"
            assert await db.claim_scheduled_message(msg_id) is False
            await db.update_scheduled_message(msg_id, text="edited during review")
            edited = await db.get_scheduled_message(msg_id)
            assert await db.quarantine_conversation_message(msg_id, snapshot) == "conflict"
            assert await db.get_scheduled_message(msg_id) == edited
        finally:
            await db.close()
    asyncio.run(check())


@pytest.mark.parametrize("field,value", [
    ("text", "operator edit"), ("message_type", "evening"),
    ("scheduled_date", "2026-09-14"), ("scheduled_time", "10:00"),
    ("target_group", "other"), ("channel_topic_id", 11), ("created_by", "operator"),
    ("poll_options", "{}"), ("recurrence", "weekly"), ("recurrence_days", "0"),
    ("cover_path", "cover.png"), ("poll_duration", 60), ("auto_pin", 1),
    ("draft_options", "[]"), ("status", "draft"), ("sent_at", "2026-09-13"),
    ("sent_message_id", 123), ("error_message", "dispatch_claim:2026-09-13"),
])
def test_concurrent_changes_conflict_without_mutation(tmp_path, field, value):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            msg_id, snapshot = await create_row(db)
            await db.update_scheduled_message(msg_id, **{field: value})
            before = await db.get_scheduled_message(msg_id)
            assert await db.quarantine_conversation_message(msg_id, snapshot) == "conflict"
            assert await db.get_scheduled_message(msg_id) == before
        finally:
            await db.close()
    asyncio.run(check())


@pytest.mark.parametrize("overrides", [
    {"message_type": "custom"}, {"message_type": "trivia_round"},
    {"poll_options": "{}"}, {"recurrence": "weekly"}, {"recurrence_days": "0"},
    {"status": "sent"}, {"status": "draft"},
])
def test_ineligible_snapshot_conflicts(tmp_path, overrides):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            msg_id, snapshot = await create_row(db, **overrides)
            assert await db.quarantine_conversation_message(msg_id, snapshot) == "conflict"
            assert await db.get_scheduled_message(msg_id) == snapshot
        finally:
            await db.close()
    asyncio.run(check())


def test_incomplete_and_claimed_snapshots_conflict(tmp_path):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            msg_id, snapshot = await create_row(db)
            assert await db.quarantine_conversation_message(msg_id, {"text": snapshot["text"]}) == "conflict"
            assert await db.claim_scheduled_message(msg_id)
            claimed = await db.get_scheduled_message(msg_id)
            assert await db.quarantine_conversation_message(msg_id, claimed) == "conflict"
            assert await db.get_scheduled_message(msg_id) == claimed
            assert await db.quarantine_conversation_message(9999, snapshot) == "conflict"
        finally:
            await db.close()
    asyncio.run(check())


@pytest.mark.parametrize("status", ["scheduled", "draft"])
def test_reviewed_send_claim_blocks_cleanup_and_other_senders(tmp_path, status):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            msg_id, snapshot = await create_row(db, status=status)
            assert await db.claim_conversation_message_snapshot(msg_id, snapshot)
            assert not await db.claim_conversation_message_snapshot(msg_id, snapshot)
            assert await db.quarantine_conversation_message(msg_id, snapshot) == "conflict"
            await db.release_scheduled_message_claim(msg_id)
            assert await db.get_scheduled_message(msg_id) == snapshot
        finally:
            await db.close()
    asyncio.run(check())


@pytest.mark.parametrize("change", [
    {"text": "edited"}, {"status": "sent"}, {"channel_topic_id": 20},
    {"status": "draft", "error_message": "conversation_cleanup"},
    {"error_message": "dispatch_claim:2026-09-13"},
])
def test_send_claim_refuses_changed_or_held_snapshot(tmp_path, change):
    async def check():
        db = Database(str(tmp_path / "bot.db"))
        await db.init()
        try:
            msg_id, snapshot = await create_row(db)
            await db.update_scheduled_message(msg_id, **change)
            current = await db.get_scheduled_message(msg_id)
            assert not await db.claim_conversation_message_snapshot(msg_id, snapshot)
            if current["error_message"] or current["status"] == "sent":
                assert not await db.claim_conversation_message_snapshot(msg_id, current)
            assert await db.get_scheduled_message(msg_id) == current
        finally:
            await db.close()
    asyncio.run(check())
