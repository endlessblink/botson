"""Tests for the private DM menu: preference storage, opt-in notification
fan-out (dedupe / Forbidden / opt-out), helper mapping, and the dashboard
parity view."""
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Forbidden

from bot.database.db import Database
from bot.handlers import dm_menu


class PreferenceStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()
        os.remove(self.tmp.name)

    async def test_default_policy_is_opt_in_required(self):
        # No row → False (a user only gets DMs for types they toggled on).
        self.assertFalse(await self.db.get_activity_preference(1, "games"))

    async def test_set_get_and_list(self):
        await self.db.set_activity_preference(1, "games", True)
        await self.db.set_activity_preference(2, "games", True)
        await self.db.set_activity_preference(2, "events", True)
        self.assertTrue(await self.db.get_activity_preference(1, "games"))
        self.assertEqual(await self.db.get_user_preferences(2), {"games": True, "events": True})
        self.assertCountEqual(await self.db.list_opted_in_users("games"), [1, 2])
        self.assertEqual(await self.db.list_opted_in_users("events"), [2])

    async def test_toggle_off_excludes_from_list(self):
        await self.db.set_activity_preference(1, "games", True)
        await self.db.set_activity_preference(1, "games", False)
        self.assertEqual(await self.db.list_opted_in_users("games"), [])

    async def test_notification_log_dedupe_is_idempotent(self):
        self.assertFalse(await self.db.was_notified(10, 1))
        await self.db.mark_notified(10, 1)
        await self.db.mark_notified(10, 1)  # no error, no dup
        self.assertTrue(await self.db.was_notified(10, 1))

    async def test_upcoming_games_returns_future_warmups_only(self):
        future = await self.db.create_scheduled_message(
            text="warmup", message_type="trivia_warmup_rsvp", channel_topic_id=None,
            target_group="main", scheduled_date="2099-01-01", scheduled_time="20:00",
            poll_options=json.dumps({"theme_label": "מוזיקה"}), status="scheduled",
        )
        await self.db.create_scheduled_message(
            text="old warmup", message_type="trivia_warmup_rsvp", channel_topic_id=None,
            target_group="main", scheduled_date="2000-01-01", scheduled_time="20:00",
            status="scheduled",
        )
        # A non-warmup future row must not appear.
        await self.db.create_scheduled_message(
            text="disc", message_type="discussion", channel_topic_id=None,
            target_group="main", scheduled_date="2099-01-01", scheduled_time="21:00",
            status="scheduled",
        )
        rows = await self.db.get_upcoming_scheduled_games("2098-01-01", "00:00")
        ids = [r["id"] for r in rows]
        self.assertEqual(ids, [future])


class HelperTests(unittest.TestCase):
    def test_msg_type_to_activity(self):
        self.assertEqual(dm_menu._msg_type_to_activity("event"), "events")
        self.assertEqual(dm_menu._msg_type_to_activity("trivia_warmup_rsvp"), "games")
        self.assertIsNone(dm_menu._msg_type_to_activity("discussion"))

    def test_fmt_when(self):
        self.assertEqual(dm_menu._fmt_when("2026-05-25", "19:30:00"), "25/05 19:30")
        self.assertEqual(dm_menu._fmt_when("2026-05-25", None), "25/05")

    def test_deep_link_button_none_without_username(self):
        with patch.object(dm_menu, "deep_link", return_value=""):
            self.assertIsNone(dm_menu.deep_link_button())


class NotifyOptedInTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()
        os.remove(self.tmp.name)

    def _ctx(self):
        return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    def _game_msg(self):
        return {
            "id": 500, "message_type": "trivia_warmup_rsvp",
            "poll_options": json.dumps({"activity_label": "טריוויה על מוזיקה"}),
            "text": "warmup",
        }

    async def test_game_notifies_opted_in_once(self):
        await self.db.set_activity_preference(1, "games", True)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, self._game_msg())
        ctx.bot.send_message.assert_awaited_once()
        kwargs = ctx.bot.send_message.await_args.kwargs
        self.assertEqual(kwargs["chat_id"], 1)
        # Sign-up button keys on the warm-up id.
        cb = kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        self.assertEqual(cb, "dmmenu_tr_500")

    async def test_redispatch_is_deduped(self):
        await self.db.set_activity_preference(1, "games", True)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, self._game_msg())
        await dm_menu.notify_opted_in_users(ctx, self.db, self._game_msg())
        ctx.bot.send_message.assert_awaited_once()

    async def test_opted_out_user_not_notified(self):
        await self.db.set_activity_preference(1, "games", False)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, self._game_msg())
        ctx.bot.send_message.assert_not_awaited()

    async def test_forbidden_is_swallowed_and_marked(self):
        await self.db.set_activity_preference(1, "games", True)
        ctx = self._ctx()
        ctx.bot.send_message.side_effect = Forbidden("bot was blocked by the user")
        # Must not raise.
        await dm_menu.notify_opted_in_users(ctx, self.db, self._game_msg())
        # Marked notified so we never retry a user who blocked us.
        self.assertTrue(await self.db.was_notified(500, 1))

    async def test_event_requires_event_id(self):
        await self.db.set_activity_preference(1, "events", True)
        ctx = self._ctx()
        event_msg = {"id": 600, "message_type": "event", "text": "כותרת\nפרטים"}
        # No event_id → no DM (can't build the RSVP button).
        await dm_menu.notify_opted_in_users(ctx, self.db, event_msg, event_id=None)
        ctx.bot.send_message.assert_not_awaited()
        # With event_id → DM with rsvp button.
        await dm_menu.notify_opted_in_users(ctx, self.db, event_msg, event_id=42)
        ctx.bot.send_message.assert_awaited_once()
        cb = ctx.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        self.assertEqual(cb, "dmmenu_evy_42")

    async def test_unmapped_message_type_is_noop(self):
        await self.db.set_activity_preference(1, "games", True)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, {"id": 1, "message_type": "discussion"})
        ctx.bot.send_message.assert_not_awaited()


class PreferencesDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_preferences_page_lists_opted_in_members(self):
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db = Database(tmp.name)
        await db.init()
        await db.upsert_member(111, "noam", "נועם")
        await db.set_activity_preference(111, "games", True)
        await db.close()

        from fastapi.testclient import TestClient
        import dashboard.app as da

        with patch.object(da, "DB_PATH", tmp.name), TestClient(da.app) as client:
            # Unauthenticated → redirect.
            r0 = client.get("/preferences", follow_redirects=False)
            self.assertEqual(r0.status_code, 303)
            client.post("/login", data={"password": da.DASHBOARD_PASSWORD}, follow_redirects=False)
            r = client.get("/preferences")
            self.assertEqual(r.status_code, 200, r.text[:300])
            self.assertIn("נועם", r.text)
        os.remove(tmp.name)


if __name__ == "__main__":
    unittest.main()
