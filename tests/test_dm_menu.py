"""Tests for the private DM menu: preference storage, opt-in event
notification fan-out (dedupe / Forbidden / opt-out), helper mapping, and the
dashboard parity view."""
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
        self.assertIsNone(dm_menu._msg_type_to_activity("trivia_warmup_rsvp"))
        self.assertIsNone(dm_menu._msg_type_to_activity("discussion"))

    def test_fmt_when(self):
        self.assertEqual(dm_menu._fmt_when("2026-05-25", "19:30:00"), "25/05 19:30")
        self.assertEqual(dm_menu._fmt_when("2026-05-25", None), "25/05")

    def test_deep_link_button_none_without_username(self):
        with patch.object(dm_menu, "deep_link", return_value=""):
            self.assertIsNone(dm_menu.deep_link_button())

    def test_game_deep_link_button_targets_specific_warmup(self):
        with patch.object(dm_menu, "deep_link", return_value="https://t.me/bot?start=game_123") as dl:
            btn = dm_menu.game_deep_link_button(123)
        dl.assert_called_once_with("game_123")
        self.assertIsNotNone(btn)
        self.assertEqual(btn.url, "https://t.me/bot?start=game_123")


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

    def _event_msg(self):
        return {"id": 600, "message_type": "event", "text": "כותרת\nפרטים"}

    async def test_game_warmup_does_not_send_automatic_dm(self):
        await self.db.set_activity_preference(1, "games", True)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, self._game_msg())
        ctx.bot.send_message.assert_not_awaited()

    async def test_redispatch_is_deduped(self):
        await self.db.set_activity_preference(1, "events", True)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, self._event_msg(), event_id=42)
        await dm_menu.notify_opted_in_users(ctx, self.db, self._event_msg(), event_id=42)
        ctx.bot.send_message.assert_awaited_once()

    async def test_opted_out_user_not_notified(self):
        await self.db.set_activity_preference(1, "events", False)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, self._event_msg(), event_id=42)
        ctx.bot.send_message.assert_not_awaited()

    async def test_forbidden_is_swallowed_and_marked(self):
        await self.db.set_activity_preference(1, "events", True)
        ctx = self._ctx()
        ctx.bot.send_message.side_effect = Forbidden("bot was blocked by the user")
        # Must not raise.
        await dm_menu.notify_opted_in_users(ctx, self.db, self._event_msg(), event_id=42)
        # Marked notified so we never retry a user who blocked us.
        self.assertTrue(await self.db.was_notified(600, 1))

    async def test_event_requires_event_id(self):
        await self.db.set_activity_preference(1, "events", True)
        ctx = self._ctx()
        # No event_id → no DM (can't build the RSVP button).
        await dm_menu.notify_opted_in_users(ctx, self.db, self._event_msg(), event_id=None)
        ctx.bot.send_message.assert_not_awaited()
        # With event_id → DM with rsvp button.
        await dm_menu.notify_opted_in_users(ctx, self.db, self._event_msg(), event_id=42)
        ctx.bot.send_message.assert_awaited_once()
        cb = ctx.bot.send_message.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        self.assertEqual(cb, "dmmenu_evy_42")

    async def test_unmapped_message_type_is_noop(self):
        await self.db.set_activity_preference(1, "games", True)
        ctx = self._ctx()
        await dm_menu.notify_opted_in_users(ctx, self.db, {"id": 1, "message_type": "discussion"})
        ctx.bot.send_message.assert_not_awaited()


class PersistentKeyboardTests(unittest.IsolatedAsyncioTestCase):
    def test_persistent_kb_has_two_pinned_buttons(self):
        kb = dm_menu.persistent_kb()
        labels = [b.text for row in kb.keyboard for b in row]
        up, prefs = dm_menu.kb_labels()
        self.assertEqual(labels, [up, prefs])
        self.assertTrue(kb.is_persistent)
        self.assertTrue(kb.resize_keyboard)

    async def test_menu_text_routes_to_subscreens(self):
        up, prefs = dm_menu.kb_labels()
        calls = []

        async def fake_upcoming(u, c): calls.append("upcoming")
        async def fake_prefs(u, c): calls.append("prefs")

        from telegram.ext import ApplicationHandlerStop
        with patch.object(dm_menu, "show_upcoming", fake_upcoming), \
             patch.object(dm_menu, "show_prefs", fake_prefs):
            for label, expected in [(up, "upcoming"), (prefs, "prefs")]:
                upd = SimpleNamespace(message=SimpleNamespace(text=label))
                # Routes, then stops propagation so antispam/levels don't also
                # process the menu-button text.
                with self.assertRaises(ApplicationHandlerStop):
                    await dm_menu.handle_menu_text(upd, SimpleNamespace())
        self.assertEqual(calls, ["upcoming", "prefs"])


class AnytimeSignupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()
        os.remove(self.tmp.name)

    async def _make_scheduled_warmup(self):
        return await self.db.create_scheduled_message(
            text="warmup", message_type="trivia_warmup_rsvp", channel_topic_id=None,
            target_group="main", scheduled_date="2099-01-01", scheduled_time="20:00",
            poll_options=json.dumps({"theme_label": "מדע", "min_ready_players": 1}),
            status="scheduled",  # NOT yet posted to the group
        )

    async def test_scheduled_warmup_shows_live_signup_button(self):
        wid = await self._make_scheduled_warmup()
        captured = {}

        async def reply(text, reply_markup=None):
            captured["text"] = text
            captured["markup"] = reply_markup

        upd = SimpleNamespace(
            callback_query=None,
            message=SimpleNamespace(reply_text=reply),
            effective_user=SimpleNamespace(id=1, username="u"),
            effective_chat=SimpleNamespace(id=1, type="private"),
        )
        ctx = SimpleNamespace(bot_data={"db": self.db})
        await dm_menu.show_upcoming(upd, ctx)
        cbs = [b.callback_data for row in captured["markup"].inline_keyboard for b in row]
        # A real sign-up button, not the old info-only noop.
        self.assertIn(f"dmmenu_tr_{wid}", cbs)
        self.assertNotIn("dmmenu_noop", cbs)

    async def test_signed_up_shows_signoff_button_and_toggles_off(self):
        wid = await self._make_scheduled_warmup()
        await self.db.upsert_member(1, "u", "U1")
        await self.db.add_trivia_interest_response(wid, 1, "U1")
        cap = {}

        async def reply(text, reply_markup=None):
            cap["markup"] = reply_markup

        ctx = SimpleNamespace(bot_data={"db": self.db}, bot=AsyncMock())
        upd = SimpleNamespace(
            callback_query=None, message=SimpleNamespace(reply_text=reply),
            effective_user=SimpleNamespace(id=1, username="u"),
            effective_chat=SimpleNamespace(id=1, type="private"),
        )
        await dm_menu.show_upcoming(upd, ctx)
        cbs = [b.callback_data for row in cap["markup"].inline_keyboard for b in row]
        # Signed-up user sees the sign-off button, not the sign-up one.
        self.assertIn(f"dmmenu_troff_{wid}", cbs)
        self.assertNotIn(f"dmmenu_tr_{wid}", cbs)

        # Sign off → response removed and the re-rendered button flips back.
        async def answer(text=None, show_alert=False): pass
        async def edit(text=None, reply_markup=None): cap["after"] = reply_markup
        q = SimpleNamespace(data=f"dmmenu_troff_{wid}", answer=answer, edit_message_text=edit)
        upd2 = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=1, username="u"))
        await dm_menu._handle_trivia_signoff(upd2, ctx)
        self.assertFalse(await self.db.has_trivia_interest_response(wid, 1))
        cbs2 = [b.callback_data for row in cap["after"].inline_keyboard for b in row]
        self.assertIn(f"dmmenu_tr_{wid}", cbs2)

    async def test_game_deep_link_requires_explicit_private_signup(self):
        wid = await self._make_scheduled_warmup()
        sent = {}

        async def reply_text(text, reply_markup=None):
            sent["text"] = text
            sent["markup"] = reply_markup

        user = SimpleNamespace(id=10, username="u", first_name="U", last_name="")
        upd = SimpleNamespace(
            message=SimpleNamespace(reply_text=reply_text),
            effective_user=user,
            effective_chat=SimpleNamespace(id=10, type="private"),
        )
        ctx = SimpleNamespace(bot_data={"db": self.db}, bot=AsyncMock())

        await dm_menu.show_game_subscription(upd, ctx, wid)

        self.assertFalse(await self.db.has_trivia_interest_response(wid, 10))
        cbs = [b.callback_data for row in sent["markup"].inline_keyboard for b in row]
        self.assertIn(f"dmmenu_gsub_{wid}", cbs)

        async def answer(text=None, show_alert=False): pass
        async def edit(text=None, reply_markup=None): sent["after"] = reply_markup
        q = SimpleNamespace(data=f"dmmenu_gsub_{wid}", answer=answer, edit_message_text=edit)
        await dm_menu._handle_game_subscribe(
            SimpleNamespace(callback_query=q, effective_user=user), ctx,
        )

        self.assertTrue(await self.db.has_trivia_interest_response(wid, 10))
        cbs_after_signup = [b.callback_data for row in sent["after"].inline_keyboard for b in row]
        self.assertIn(f"dmmenu_gunsub_{wid}", cbs_after_signup)

        q = SimpleNamespace(data=f"dmmenu_gunsub_{wid}", answer=answer, edit_message_text=edit)
        await dm_menu._handle_game_unsubscribe(
            SimpleNamespace(callback_query=q, effective_user=user), ctx,
        )

        self.assertFalse(await self.db.has_trivia_interest_response(wid, 10))
        cbs2 = [b.callback_data for row in sent["after"].inline_keyboard for b in row]
        self.assertIn(f"dmmenu_gsub_{wid}", cbs2)

    async def test_warmup_posted_but_game_upcoming_stays_listed(self):
        # Regression: warm-up posted an hour ago (post time passed) but the game
        # kicks off in 30 min — it must STAY in the menu, not show "nothing
        # scheduled". (Old query filtered on warm-up post time and dropped it.)
        import json as _json
        from datetime import timedelta
        now = dm_menu._il_now()
        post = now - timedelta(minutes=60)
        kickoff = now + timedelta(minutes=30)
        marker = "mk-future"
        wid = await self.db.create_scheduled_message(
            text="wu", message_type="trivia_warmup_rsvp", channel_topic_id=None,
            target_group="main", scheduled_date=post.date().isoformat(),
            scheduled_time=post.strftime("%H:%M"),
            poll_options=_json.dumps({"activity_label": "טריוויה", "warmup_marker": marker}),
            status="sent",
        )
        await self.db.create_scheduled_message(
            text="game", message_type="trivia_round", channel_topic_id=None,
            target_group="main", scheduled_date=kickoff.date().isoformat(),
            scheduled_time=kickoff.strftime("%H:%M"),
            poll_options=_json.dumps({"warmup_marker": marker}), status="scheduled",
        )
        cap = {}
        async def reply(text, reply_markup=None): cap["text"], cap["markup"] = text, reply_markup
        upd = SimpleNamespace(
            callback_query=None, message=SimpleNamespace(reply_text=reply),
            effective_user=SimpleNamespace(id=1, username="u"),
            effective_chat=SimpleNamespace(id=1, type="private"),
        )
        await dm_menu.show_upcoming(upd, SimpleNamespace(bot_data={"db": self.db}, bot=AsyncMock()))
        self.assertIn("טריוויה", cap["text"])
        self.assertIsNotNone(cap["markup"])
        cbs = [b.callback_data for row in cap["markup"].inline_keyboard for b in row]
        self.assertIn(f"dmmenu_tr_{wid}", cbs)

    async def test_started_game_is_dropped_from_list(self):
        import json as _json
        from datetime import timedelta
        now = dm_menu._il_now()
        marker = "mk-past"
        await self.db.create_scheduled_message(
            text="wu", message_type="trivia_warmup_rsvp", channel_topic_id=None,
            target_group="main", scheduled_date=(now - timedelta(minutes=90)).date().isoformat(),
            scheduled_time=(now - timedelta(minutes=90)).strftime("%H:%M"),
            poll_options=_json.dumps({"activity_label": "טריוויה", "game_time": (now - timedelta(minutes=10)).strftime("%H:%M"), "warmup_marker": marker}),
            status="sent",
        )
        cap = {}
        async def reply(text, reply_markup=None): cap["text"], cap["markup"] = text, reply_markup
        upd = SimpleNamespace(
            callback_query=None, message=SimpleNamespace(reply_text=reply),
            effective_user=SimpleNamespace(id=2, username="u"),
            effective_chat=SimpleNamespace(id=2, type="private"),
        )
        await dm_menu.show_upcoming(upd, SimpleNamespace(bot_data={"db": self.db}, bot=AsyncMock()))
        # Game already started → not listed (empty-state, no markup).
        self.assertIsNone(cap["markup"])

    async def test_record_interest_for_scheduled_row_without_group_confirmation(self):
        from bot.handlers.trivia_interest import record_trivia_interest
        wid = await self._make_scheduled_warmup()
        bot = AsyncMock()
        user = SimpleNamespace(id=7, username="x", first_name="X", last_name="")
        result = await record_trivia_interest(self.db, bot, wid, user)
        self.assertIsNotNone(result)
        self.assertFalse(result.get("closed"))
        self.assertEqual(result["count"], 1)
        # threshold met (min=1) but warm-up not 'sent' → no group confirmation.
        bot.send_message.assert_not_called()
        # interest is persisted, so it counts when the game runs.
        responses = await self.db.get_trivia_interest_responses(wid)
        self.assertEqual([r["user_id"] for r in responses], [7])
        self.assertEqual(await self.db.get_reminder_leads(7), {30})


class GameReminderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db = Database(self.tmp.name)
        await self.db.init()

    async def asyncTearDown(self):
        await self.db.close()
        os.remove(self.tmp.name)

    async def _warmup_in(self, minutes_to_kickoff, responders, *, marker="m1",
                         warmup_game_time=None, leads=(30,)):
        """Create a paired warm-up + game row sharing one marker, with the game
        row's scheduled time = kickoff. Sign up `responders` on the warm-up and
        give each the `leads` reminder times (default {30}; pass () for none).

        `warmup_game_time` lets a test set a *stale* game_time on the warm-up to
        prove the reminder anchors on the authoritative game row, not the warm-up.
        """
        import json as _json
        from datetime import timedelta
        kickoff = dm_menu._il_now() + timedelta(minutes=minutes_to_kickoff)
        wpayload = {"activity_label": "טריוויה על מדע", "warmup_marker": marker}
        if warmup_game_time is not None:
            wpayload["game_time"] = warmup_game_time
        wid = await self.db.create_scheduled_message(
            text="warmup", message_type="trivia_warmup_rsvp", channel_topic_id=None,
            target_group="main", scheduled_date=kickoff.date().isoformat(),
            scheduled_time="00:00", poll_options=_json.dumps(wpayload), status="sent",
        )
        await self.db.create_scheduled_message(
            text="game", message_type="trivia_round", channel_topic_id=None,
            target_group="main", scheduled_date=kickoff.date().isoformat(),
            scheduled_time=kickoff.strftime("%H:%M"),
            poll_options=_json.dumps({"warmup_marker": marker}), status="scheduled",
        )
        for uid in responders:
            await self.db.upsert_member(uid, f"u{uid}", f"U{uid}")
            await self.db.add_trivia_interest_response(wid, uid, f"U{uid}")
            for lead in leads:
                await self.db.toggle_reminder_lead(uid, lead)
        return wid

    def _ctx(self):
        return SimpleNamespace(bot_data={"db": self.db},
                               bot=SimpleNamespace(send_message=AsyncMock()))

    async def test_reminds_signed_up_user_once_then_dedupes(self):
        # Game in 15 min; default lead 30 → past lead time → should fire now.
        wid = await self._warmup_in(15, [1])
        ctx = self._ctx()
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_awaited_once()
        self.assertEqual(ctx.bot.send_message.await_args.kwargs["chat_id"], 1)
        markup = ctx.bot.send_message.await_args.kwargs["reply_markup"]
        cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn(f"dmmenu_gunsub_{wid}", cbs)
        # Second tick: deduped.
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_awaited_once()

    async def test_signup_for_one_game_does_not_subscribe_to_other_games(self):
        first = await self._warmup_in(15, [1], marker="game-a")
        second = await self._warmup_in(15, [], marker="game-b")
        ctx = self._ctx()

        await dm_menu.send_due_game_reminders(ctx)

        ctx.bot.send_message.assert_awaited_once()
        self.assertTrue(await self.db.was_game_reminded(first, 1, 30))
        self.assertFalse(await self.db.was_game_reminded(second, 1, 30))

    async def test_not_yet_due_does_not_fire(self):
        # Game in 90 min, user lead 30 → remind_at is still 60 min away.
        wid = await self._warmup_in(90, [1], leads=(30,))
        ctx = self._ctx()
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_not_awaited()

    async def test_user_with_no_leads_is_skipped(self):
        # No reminder times selected → no DM.
        wid = await self._warmup_in(15, [1], leads=())
        ctx = self._ctx()
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_not_awaited()

    async def test_multi_select_fires_due_lead_and_keeps_future_one(self):
        # Game in 45 min; user picked 60 + 30. Only the 60-min lead is due now
        # (kickoff-60 is past); 30 is still 15 min away. One DM; 60 marked, 30 not.
        wid = await self._warmup_in(45, [1], leads=(60, 30))
        ctx = self._ctx()
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_awaited_once()
        self.assertTrue(await self.db.was_game_reminded(wid, 1, 60))
        self.assertFalse(await self.db.was_game_reminded(wid, 1, 30))
        # Re-tick: 30 still not due → no second DM.
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_awaited_once()

    async def test_anchors_on_game_row_not_stale_warmup_game_time(self):
        # Game row fires in 15 min, but the warm-up carries a stale game_time
        # 5 hours out. With default lead 30, anchoring on the game row → fires
        # now; anchoring on the stale warm-up time → would wrongly stay silent.
        await self._warmup_in(15, [1], warmup_game_time="23:59")
        ctx = self._ctx()
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_awaited_once()

    async def test_past_kickoff_is_skipped(self):
        wid = await self._warmup_in(-5, [1])  # already started
        ctx = self._ctx()
        await dm_menu.send_due_game_reminders(ctx)
        ctx.bot.send_message.assert_not_awaited()

    async def test_lead_multiselect_round_trip(self):
        self.assertEqual(await self.db.get_reminder_leads(1), set())
        self.assertTrue(await self.db.toggle_reminder_lead(1, 60))   # add
        self.assertTrue(await self.db.toggle_reminder_lead(1, 10))   # add another
        self.assertEqual(await self.db.get_reminder_leads(1), {10, 60})
        self.assertFalse(await self.db.toggle_reminder_lead(1, 60))  # remove
        self.assertEqual(await self.db.get_reminder_leads(1), {10})
        await self.db.clear_reminder_leads(1)
        self.assertEqual(await self.db.get_reminder_leads(1), set())

    async def test_toggle_lead_handler_persists_and_answers(self):
        from bot.utils.copy import load_copy
        answered = {}
        async def answer(text=None, show_alert=False): answered["text"] = text
        async def edit_markup(reply_markup=None): pass
        q = SimpleNamespace(data="dmmenu_lead_60", answer=answer,
                            edit_message_reply_markup=edit_markup)
        upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=5))
        ctx = SimpleNamespace(bot_data={"db": self.db})
        await dm_menu.toggle_lead(upd, ctx)
        self.assertEqual(await self.db.get_reminder_leads(5), {60})
        self.assertEqual(answered["text"], load_copy("dm_menu", "reminder_saved"))
        # toggling again removes it
        await dm_menu.toggle_lead(upd, ctx)
        self.assertEqual(await self.db.get_reminder_leads(5), set())

    async def test_clear_leads_handler(self):
        await self.db.toggle_reminder_lead(9, 30)
        async def answer(text=None, show_alert=False): pass
        async def edit_markup(reply_markup=None): pass
        q = SimpleNamespace(data="dmmenu_leadclear", answer=answer,
                            edit_message_reply_markup=edit_markup)
        upd = SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=9))
        await dm_menu.clear_leads(upd, SimpleNamespace(bot_data={"db": self.db}))
        self.assertEqual(await self.db.get_reminder_leads(9), set())

    def test_prefs_markup_multiselect_lead_row(self):
        from bot.utils.copy import load_copy
        m = dm_menu._prefs_markup({"games": True, "events": False}, leads={30})
        cbs = [b.callback_data for row in m.inline_keyboard for b in row]
        self.assertIn("dmmenu_leadclear", cbs)   # clear-all
        for opt in (0, 5, 10, 30, 60):
            self.assertIn(f"dmmenu_lead_{opt}", cbs)
        # the selected lead (30) is marked
        mark = load_copy("dm_menu", "pref_on")
        labels = [b.text for row in m.inline_keyboard for b in row]
        self.assertTrue(any(mark in t and "30" in t for t in labels))


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
