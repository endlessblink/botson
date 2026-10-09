"""Botson rows targeted at WhatsApp are delivered through Botty's WAHA session."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from bot.handlers import calendar
from bot.utils import whatsapp_sender
from test_calendar_scheduled_games import FakeScheduledDb, _base_row

WA_ENV = {
    "WHATSAPP_WAHA_URL": "http://127.0.0.1:3051",
    "WHATSAPP_WAHA_API_KEY": "k",
    "WHATSAPP_GROUP_ID": "111@g.us",
    "WHATSAPP_TEST_GROUP_ID": "222@g.us",
    "GROUP_ID": "-100",
    "TEST_GROUP_ID": "-200",
    "BOT_TOKEN": "123:test",
}


class WhatsAppDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, row, env=None, ack=2):
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={"db": db}, bot=AsyncMock())
        text = AsyncMock(return_value="wa-text-id")
        poll = AsyncMock(return_value="wa-poll-id")
        tg_send = AsyncMock()
        self.alert = AsyncMock(return_value=1)
        with patch.dict("os.environ", env or WA_ENV, clear=False), \
             patch.object(whatsapp_sender, "send_text", new=text), \
             patch.object(whatsapp_sender, "send_poll", new=poll), \
             patch.object(whatsapp_sender, "confirm_delivery", new=AsyncMock(return_value=ack)), \
             patch.object(calendar, "notify_admins", new=self.alert), \
             patch.object(calendar, "_conversation_gate", new=AsyncMock(return_value=None)), \
             patch("bot.utils.freshness.freshness_rejection", return_value=None), \
             patch.object(calendar, "send_message_with_optional_cover", new=tg_send), \
             patch.object(calendar, "send_poll_message", new=tg_send):
            await calendar.check_and_send_due_messages(context)
        return db, text, poll, tg_send

    async def test_poll_goes_to_whatsapp_as_native_poll(self):
        row = _base_row("poll")
        row.update(target_group="whatsapp", text="מה עדיף?", poll_options=json.dumps(["א", "ב"]))
        db, text, poll, tg_send = await self.dispatch(row)
        poll.assert_awaited_once_with("111@g.us", "מה עדיף?", ["א", "ב"])
        text.assert_not_awaited()
        tg_send.assert_not_awaited()
        self.assertEqual(db.sent, [(123, None)])

    async def test_discussion_goes_to_whatsapp_test_group_as_text(self):
        row = _base_row("discussion")
        row.update(target_group="whatsapp_test", text="שאלה")
        db, text, poll, tg_send = await self.dispatch(row)
        text.assert_awaited_once_with("222@g.us", "שאלה")
        tg_send.assert_not_awaited()
        self.assertEqual(db.sent, [(123, None)])

    async def test_live_game_is_skipped_on_whatsapp(self):
        row = _base_row("trivia_warmup_rsvp")
        row.update(target_group="whatsapp")
        db, text, poll, tg_send = await self.dispatch(row)
        text.assert_not_awaited()
        poll.assert_not_awaited()
        self.assertIn("whatsapp_unsupported_type", db.skipped[0][1])

    async def test_generated_conversation_row_is_never_sent(self):
        row = _base_row("morning")
        row.update(target_group="whatsapp", created_by="ai-fill-today")
        db, text, _, _ = await self.dispatch(row)
        text.assert_not_awaited()
        self.assertIn("conversation_not_scheduler_authored", db.skipped[0][1])

    async def test_rejected_delivery_is_failed_and_alerted_not_sent(self):
        for ack in (-1, 0, None):
            with self.subTest(ack=ack):
                row = _base_row("custom")
                row.update(target_group="whatsapp_test", text="שלום")
                db, text, _, _ = await self.dispatch(row, ack=ack)
                text.assert_awaited_once()
                self.assertFalse(db.sent)
                self.assertIn("delivery_unconfirmed", db.failed[0][1])
                self.alert.assert_awaited_once()

    async def test_missing_group_id_fails_without_sending(self):
        row = _base_row("custom")
        row.update(target_group="whatsapp")
        env = {**WA_ENV, "WHATSAPP_GROUP_ID": ""}
        db, text, _, _ = await self.dispatch(row, env)
        text.assert_not_awaited()
        self.assertFalse(db.sent)
        self.assertTrue(db.failed)


class WhatsAppDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def _db_with_row(self, message_type="custom", target="main"):
        import tempfile
        from bot.database.db import Database

        self._tmp = tempfile.NamedTemporaryFile(suffix=".db")
        db = Database(self._tmp.name)
        await db.init()
        msg_id = await db.create_scheduled_message(
            text="שאלה לבדיקה", message_type=message_type, channel_topic_id=4037,
            target_group=target, scheduled_date="2099-01-01", scheduled_time="18:00", status="draft",
        )
        return db, msg_id

    async def _row(self, db, msg_id):
        async with db._db.execute(
            "SELECT target_group, channel_topic_id FROM scheduled_messages WHERE id = ?", (msg_id,),
        ) as cur:
            return await cur.fetchone()

    async def test_edit_to_whatsapp_clears_topic(self):
        from dashboard import app as dashboard_app
        from test_planner_coercion_and_chips import FakeCalendarRequest

        db, msg_id = await self._db_with_row("custom")
        try:
            await dashboard_app.update_calendar_item(msg_id, FakeCalendarRequest({"target_group": "whatsapp"}), db)
            row = await self._row(db, msg_id)
            self.assertEqual(row["target_group"], "whatsapp")
            self.assertIsNone(row["channel_topic_id"])
        finally:
            await db.close()
            self._tmp.close()

    async def test_edit_game_to_whatsapp_is_refused(self):
        from dashboard import app as dashboard_app
        from test_planner_coercion_and_chips import FakeCalendarRequest

        db, msg_id = await self._db_with_row("trivia_warmup_rsvp")
        try:
            with self.assertRaises(HTTPException) as ctx:
                await dashboard_app.update_calendar_item(msg_id, FakeCalendarRequest({"target_group": "whatsapp"}), db)
            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual((await self._row(db, msg_id))["target_group"], "main")
        finally:
            await db.close()
            self._tmp.close()

    async def test_calendar_marks_whatsapp_rows(self):
        from dashboard import app as dashboard_app
        from test_planner_coercion_and_chips import FakeCalendarRequest

        db, msg_id = await self._db_with_row("custom", target="whatsapp")
        try:
            req = FakeCalendarRequest({})
            req.query_params = {"start": "2099-01-01", "end": "2099-01-02"}
            events = await dashboard_app.get_calendar(req, db)
            events = events if isinstance(events, list) else events.get("events", [])
            ev = next(e for e in events if e.get("id") == str(msg_id))
            self.assertEqual(ev["extendedProps"]["targetGroup"], "whatsapp")
            self.assertIn("WhatsApp", ev["title"])
        finally:
            await db.close()
            self._tmp.close()

    async def test_throttle_report_requires_login_and_config(self):
        from dashboard import app as dashboard_app

        anon = SimpleNamespace(session={})
        with self.assertRaises(HTTPException) as ctx:
            await dashboard_app.whatsapp_throttle_report(anon)
        self.assertEqual(ctx.exception.status_code, 401)
        authed = SimpleNamespace(session={"authenticated": True})
        with patch.dict("os.environ", {"WHATSAPP_THROTTLE_REPORT_URL": "", "WHATSAPP_WAHA_API_KEY": ""}):
            with self.assertRaises(HTTPException) as ctx:
                await dashboard_app.whatsapp_throttle_report(authed)
        self.assertEqual(ctx.exception.status_code, 503)


class WhatsAppTargetValidationTests(unittest.TestCase):
    def test_dashboard_accepts_whatsapp_targets(self):
        from dashboard.app import _validated_target_group

        self.assertEqual(_validated_target_group("whatsapp"), "whatsapp")
        self.assertEqual(_validated_target_group("whatsapp_test"), "whatsapp_test")
        with self.assertRaises(HTTPException):
            _validated_target_group("signal")


if __name__ == "__main__":
    unittest.main()
