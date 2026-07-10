"""The auto-refill path (c4754b9) has never executed in production.

Prod evidence (2026-07-10): the Jul 9 Emoji Night logged only
``emoji_puzzle: started live session 15 with 5 puzzles`` — no
``auto-refilling`` line — because 5 fresh song puzzles still remained.
That pool is now drained, so the *next* music night is the first run that
will genuinely enter ``_ensure_fresh_emoji_pool``'s refill branch.

Existing coverage (test_emoji_puzzle_theme_routing, test_facts_dedup) mocks
``generate_emoji_puzzles`` wholesale, so the generate-parse-insert seam is
untested. These tests mock only the LLM *transport* and exercise the real
generation function end-to-end against a real Database.
"""

import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from bot.database.db import Database
from bot.handlers.emoji_puzzle import _pick_session_puzzles, emoji_skip_reason


def _llm_payload(count: int, prefix: str = "Fresh") -> str:
    return json.dumps([
        {
            "emoji_prompt": f"🎵{idx}",
            "answer_he": f"שיר חדש {idx}",
            "answer_en": f"{prefix} Song {idx}",
            "aliases": [f"{prefix} {idx}"],
        }
        for idx in range(count)
    ], ensure_ascii=False)


def _patch_transport(claude=None, api=None):
    """Mock only the two LLM transports inside emoji_puzzle_generation.

    Deliberately does NOT patch generate_emoji_puzzles — the prompt build,
    JSON parse, dedup-vs-existing and DB insert all run for real.
    """
    return (
        patch(
            "bot.utils.emoji_puzzle_generation._generate_via_claude",
            new=AsyncMock(return_value=claude),
        ),
        patch(
            "bot.utils.emoji_puzzle_generation._generate_via_api",
            new=AsyncMock(return_value=api),
        ),
    )


class DrainedSongPoolTest(unittest.IsolatedAsyncioTestCase):
    """Reproduces the exact prod state after the Jul 9 game: every song
    puzzle has been played inside the 30-day cooldown window."""

    async def _drained_db(self, tmp) -> tuple[Database, list[int]]:
        db = Database(tmp.name)
        await db.init()
        old_ids = []
        for idx in range(5):
            pid = await db.create_emoji_puzzle(
                f"🎤{idx}", f"שיר ישן {idx}", f"Old Song {idx}", media_type="song",
            )
            old_ids.append(pid)
        session_id = await db.create_emoji_session(-1001, 4037, 5)
        for offset, pid in enumerate(old_ids, start=1):
            await db.start_emoji_round(session_id, pid, -1001, 200 + offset, 4037, 0)
        return db, old_ids

    async def test_real_refill_fills_drained_pool_with_canonical_rows(self):
        """The happy path nobody has watched run: LLM answers, rows insert,
        and the launcher picks only the new ids."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db, old_ids = await self._drained_db(tmp)
            try:
                claude_patch, api_patch = _patch_transport(claude=_llm_payload(5))
                with claude_patch, api_patch:
                    # media_types=["music"] is the legacy non-canonical value
                    # real scheduled rows carry.
                    picked = await _pick_session_puzzles(db, 5, media_types=["music"])

                self.assertEqual(len(picked), 5, picked)
                self.assertFalse(
                    {p["id"] for p in picked} & set(old_ids),
                    "refill must not replay the drained puzzles",
                )
                for p in picked:
                    self.assertEqual(
                        p["media_type"], "song",
                        "refilled rows must be written with the canonical token",
                    )
            finally:
                await db.close()

    async def test_no_llm_provider_skips_the_game_rather_than_replaying(self):
        """If neither transport is reachable from the botson service user,
        generate_emoji_puzzles inserts nothing. Assert the fallback is a
        skip, not a silent replay of the drained puzzles."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db, old_ids = await self._drained_db(tmp)
            try:
                claude_patch, api_patch = _patch_transport(claude=None, api=None)
                with claude_patch, api_patch:
                    picked = await _pick_session_puzzles(db, 5, media_types=["music"])
                    reason = await emoji_skip_reason(db, -1001, 9999, media_types=["music"])

                self.assertEqual(picked, [], "must not fall back to replaying old puzzles")
                self.assertIsNotNone(reason)
                self.assertIn("auto-refill could not create enough fresh puzzles", reason or "")
            finally:
                await db.close()

    async def test_api_transport_used_when_claude_cli_unavailable(self):
        """On the VPS the `claude` CLI is unlikely to be on the botson
        user's PATH; the API fallback must carry the refill."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db, old_ids = await self._drained_db(tmp)
            try:
                claude_patch, api_patch = _patch_transport(
                    claude=None, api=_llm_payload(5, prefix="Api"),
                )
                with claude_patch, api_patch:
                    picked = await _pick_session_puzzles(db, 5, media_types=["music"])

                self.assertEqual(len(picked), 5, picked)
                self.assertFalse({p["id"] for p in picked} & set(old_ids))
            finally:
                await db.close()

    async def test_llm_echoing_existing_answers_yields_a_skip_not_a_short_round(self):
        """The dedup guard in generate_emoji_puzzles drops items whose answers
        already exist. A model that echoes the pool back inserts fewer rows
        than the deficit — the launcher must skip, never ship a short or
        replayed round."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db, old_ids = await self._drained_db(tmp)
            try:
                echoed = json.dumps([
                    {
                        "emoji_prompt": f"🎤{idx}",
                        "answer_he": f"שיר ישן {idx}",
                        "answer_en": f"Old Song {idx}",
                        "aliases": [],
                    }
                    for idx in range(5)
                ], ensure_ascii=False)
                claude_patch, api_patch = _patch_transport(claude=echoed)
                with claude_patch, api_patch:
                    picked = await _pick_session_puzzles(db, 5, media_types=["music"])

                self.assertEqual(picked, [], "duplicate answers must not be inserted")
            finally:
                await db.close()

    async def test_partial_refill_still_skips_rather_than_shipping_short(self):
        """LLM returns only 3 of the 5 needed rows."""
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db, old_ids = await self._drained_db(tmp)
            try:
                claude_patch, api_patch = _patch_transport(claude=_llm_payload(3))
                with claude_patch, api_patch:
                    picked = await _pick_session_puzzles(db, 5, media_types=["music"])

                self.assertEqual(picked, [], "a 3/5 pool must skip, not ship short")
            finally:
                await db.close()


class DeadPoolAdminAlertTest(unittest.IsolatedAsyncioTestCase):
    """When refill fails the game is skipped silently — the group sees nothing,
    so the operator must be told by DM, before any publish happens."""

    async def test_pool_exhausted_reason_is_recognized(self):
        from bot.handlers.emoji_puzzle import (
            POOL_EXHAUSTED_REASON_PREFIX,
            is_pool_exhausted_reason,
        )
        exhausted = f"{POOL_EXHAUSTED_REASON_PREFIX} (0/5) for media_types=['music']"
        self.assertTrue(is_pool_exhausted_reason(exhausted))
        self.assertFalse(is_pool_exhausted_reason("active session in this thread"))
        self.assertFalse(is_pool_exhausted_reason(None))

    async def test_admin_is_dmed_with_theme_and_reason(self):
        from bot.handlers.calendar import _alert_admin_on_dead_emoji_pool
        from bot.handlers.emoji_puzzle import POOL_EXHAUSTED_REASON_PREFIX

        reason = f"{POOL_EXHAUSTED_REASON_PREFIX} (0/5) for media_types=['music']"
        with patch("bot.handlers.calendar.notify_admins", new=AsyncMock(return_value=1)) as notify:
            alerted = await _alert_admin_on_dead_emoji_pool(
                object(), {"theme_label": "מוזיקה"}, reason,
            )

        self.assertTrue(alerted)
        notify.assert_awaited_once()
        message = notify.await_args.args[1]
        self.assertIn("מוזיקה", message, "alert must name the theme")
        self.assertIn(reason, message, "alert must carry the technical cause")
        self.assertNotIn("[copy missing", message, "copy key must exist in settings.yaml")

    async def test_ordinary_skips_do_not_page_the_admin(self):
        """An active session or RSVP-gate skip is normal. Only a dead pool pages."""
        from bot.handlers.calendar import _alert_admin_on_dead_emoji_pool

        with patch("bot.handlers.calendar.notify_admins", new=AsyncMock()) as notify:
            alerted = await _alert_admin_on_dead_emoji_pool(
                object(), {"theme_label": "מוזיקה"}, "active session in this thread",
            )

        self.assertFalse(alerted)
        notify.assert_not_awaited()

    def test_alert_fires_before_the_game_is_published(self):
        """The operator's requirement: the DM must precede any group message.

        Guards the dispatch branch's *structure* — the alert call, then the
        SkippedActivity raise, then (unreachable) start_emoji_night. Reordering
        these so the group is published to before the admin hears anything is
        the regression this locks out.
        """
        import inspect

        from bot.handlers.calendar import check_and_send_due_messages

        source = inspect.getsource(check_and_send_due_messages)
        branch = source.split('message_type") == "emoji_puzzle"', 1)
        self.assertEqual(len(branch), 2, "emoji_puzzle dispatch branch not found")
        body = branch[1].split("elif msg.get(", 1)[0]

        alert_at = body.find("_alert_admin_on_dead_emoji_pool")
        raise_at = body.find("raise SkippedActivity")
        publish_at = body.find("start_emoji_night(")

        self.assertNotEqual(alert_at, -1, "dispatch must alert the admin on a dead pool")
        self.assertNotEqual(publish_at, -1, "dispatch must still launch the game normally")
        self.assertLess(alert_at, raise_at, "alert must precede the skip")
        self.assertLess(raise_at, publish_at, "skip must precede any publish to the group")


if __name__ == "__main__":
    unittest.main()
