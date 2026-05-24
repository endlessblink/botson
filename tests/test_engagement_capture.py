"""Engagement capture + RSVP-gate toggle (2026-05-23).

Covers the "measure engagement everywhere" capture layer:
  * replies to the bot's posts are recorded (previously not captured at all);
  * get_engagement_rollup consolidates every signal per content type;
  * the RSVP gate no longer cancels games by default (it was killing ~80% of
    them), while the RSVP button still records interest.
"""

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from bot.database.db import Database
from bot.handlers import calendar, levels
from bot.utils import config as cfg


async def _sent_row(db, message_type, *, sent_message_id):
    """Create a scheduled_messages row and mark it sent (sets sent_at)."""
    msg_id = await db.create_scheduled_message(
        text="post",
        message_type=message_type,
        channel_topic_id=4037,
        target_group="main",
        scheduled_date="2099-01-01",
        scheduled_time="09:00",
        status="scheduled",
    )
    await db.mark_message_sent(msg_id, sent_message_id)
    return msg_id


class PromptReplyCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_reply_to_bot_post_is_recorded_once_per_user(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                msg_id = await _sent_row(db, "discussion", sent_message_id=5001)

                # Lookup maps the bot's outbound message id back to its row.
                sched = await db.get_scheduled_by_sent_message_id(5001)
                self.assertIsNotNone(sched)
                self.assertEqual(int(sched["id"]), msg_id)

                await db.record_prompt_reply(msg_id, user_id=111)
                await db.record_prompt_reply(msg_id, user_id=111)  # idempotent
                await db.record_prompt_reply(msg_id, user_id=222)
                self.assertEqual(await db.get_prompt_reply_count(msg_id), 2)
                self.assertEqual(
                    await db.list_prompt_reply_counts([msg_id]), {msg_id: 2}
                )
            finally:
                await db.close()

    async def test_handler_records_reply_to_tracked_post_only(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                msg_id = await _sent_row(db, "discussion", sent_message_id=6002)

                # A reply to the bot's post (message_id 6002) is recorded.
                update = SimpleNamespace(
                    message=SimpleNamespace(reply_to_message=SimpleNamespace(message_id=6002)),
                    effective_user=SimpleNamespace(id=333),
                )
                await levels._maybe_record_prompt_reply(update, db)
                self.assertEqual(await db.get_prompt_reply_count(msg_id), 1)

                # A reply to some unrelated message is NOT recorded.
                update2 = SimpleNamespace(
                    message=SimpleNamespace(reply_to_message=SimpleNamespace(message_id=999999)),
                    effective_user=SimpleNamespace(id=444),
                )
                await levels._maybe_record_prompt_reply(update2, db)
                self.assertEqual(await db.get_prompt_reply_count(msg_id), 1)

                # A non-reply message is a no-op.
                update3 = SimpleNamespace(
                    message=SimpleNamespace(reply_to_message=None),
                    effective_user=SimpleNamespace(id=555),
                )
                await levels._maybe_record_prompt_reply(update3, db)
                self.assertEqual(await db.get_prompt_reply_count(msg_id), 1)
            finally:
                await db.close()


class EngagementRollupTests(unittest.IsolatedAsyncioTestCase):
    async def test_rollup_consolidates_every_signal_per_type(self):
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                # A discussion post that got reactions + replies.
                disc = await _sent_row(db, "discussion", sent_message_id=7001)
                await db.record_reaction_update(
                    disc, telegram_message_id=7001, channel_topic_id=4037,
                    user_id=10, new_reaction_type="👍",
                )
                await db.record_reaction_update(
                    disc, telegram_message_id=7001, channel_topic_id=4037,
                    user_id=11, new_reaction_type="🔥",
                )
                await db.record_prompt_reply(disc, user_id=12)

                # A trivia warm-up that got RSVPs.
                warm = await _sent_row(db, "trivia_warmup_rsvp", sent_message_id=7002)
                await db.add_trivia_interest_response(warm, 20, "A")
                await db.add_trivia_interest_response(warm, 21, "B")

                # A poll that got votes (poll_votes joins on sent_message_id).
                poll = await _sent_row(db, "poll", sent_message_id=7003)
                await db.set_poll_vote(7003, "opt1", 30, "C")
                await db.set_poll_vote(7003, "opt2", 31, "D")

                rollup = {r["message_type"]: r for r in await db.get_engagement_rollup(days=7)}

                self.assertEqual(rollup["discussion"]["sent"], 1)
                self.assertEqual(rollup["discussion"]["reactions"], 2)
                self.assertEqual(rollup["discussion"]["replies"], 1)

                self.assertEqual(rollup["trivia_warmup_rsvp"]["rsvps"], 2)
                self.assertEqual(rollup["poll"]["poll_votes"], 2)
            finally:
                await db.close()

    async def test_rsvps_are_credited_to_the_game_row_via_marker(self):
        # A warm-up's RSVP clicks should show on the trivia/emoji GAME row it warms
        # up (matched by shared warmup_marker), not on the warm-up announcement —
        # so "did people want to play?" reads off the game, not a scaffolding row.
        import json
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                marker = "warmup-rsvp:emoji:2099-01-01:22:00"
                game = await db.create_scheduled_message(
                    text="g", message_type="emoji_puzzle", channel_topic_id=4037,
                    target_group="main", scheduled_date="2099-01-01", scheduled_time="22:00",
                    status="scheduled", poll_options=json.dumps({"warmup_marker": marker}))
                await db.mark_message_sent(game, 8801)
                warm = await db.create_scheduled_message(
                    text="w", message_type="trivia_warmup_rsvp", channel_topic_id=341,
                    target_group="main", scheduled_date="2099-01-01", scheduled_time="21:00",
                    status="scheduled", poll_options=json.dumps({"warmup_marker": marker}))
                await db.mark_message_sent(warm, 8802)
                await db.add_trivia_interest_response(warm, 1, "A")
                await db.add_trivia_interest_response(warm, 2, "B")

                rollup = {r["message_type"]: r for r in await db.get_engagement_rollup(days=7)}
                self.assertEqual(rollup["emoji_puzzle"]["rsvps"], 2)   # credited to the game
                self.assertEqual(rollup["trivia_warmup_rsvp"]["rsvps"], 0)  # not double-counted
            finally:
                await db.close()

    async def test_admin_actions_excluded_from_scoreboard(self):
        # Admin/operator actions must not inflate the community scoreboard.
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                disc = await _sent_row(db, "discussion", sent_message_id=7201)
                await db.record_prompt_reply(disc, user_id=10)   # community
                await db.record_prompt_reply(disc, user_id=99)   # admin

                excl = {r["message_type"]: r for r in
                        await db.get_engagement_rollup(days=7, exclude_user_ids=[99])}
                self.assertEqual(excl["discussion"]["replies"], 1)   # admin dropped
                incl = {r["message_type"]: r for r in await db.get_engagement_rollup(days=7)}
                self.assertEqual(incl["discussion"]["replies"], 2)   # nobody excluded
            finally:
                await db.close()

    async def test_actors_reveal_one_person_repeated_not_many(self):
        # The whole point of "who": one person RSVPing to 5 separate warm-ups must
        # read as ONE actor with rsvps=5 — not "5 people".
        import json
        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            db = Database(tmp.name)
            await db.init()
            try:
                for i in range(5):
                    marker = f"warmup-rsvp:emoji:2099-01-0{i + 1}:22:00"
                    game = await db.create_scheduled_message(
                        text="g", message_type="emoji_puzzle", channel_topic_id=4037,
                        target_group="main", scheduled_date=f"2099-01-0{i + 1}",
                        scheduled_time="22:00", status="scheduled",
                        poll_options=json.dumps({"warmup_marker": marker}))
                    await db.mark_message_sent(game, 9000 + i)
                    warm = await db.create_scheduled_message(
                        text="w", message_type="trivia_warmup_rsvp", channel_topic_id=341,
                        target_group="main", scheduled_date=f"2099-01-0{i + 1}",
                        scheduled_time="21:00", status="scheduled",
                        poll_options=json.dumps({"warmup_marker": marker}))
                    await db.mark_message_sent(warm, 9500 + i)
                    await db.add_trivia_interest_response(warm, 7, "נועם")

                actors = await db.get_engagement_actors(days=14, admin_ids=[])
                emoji_people = actors["by_type"].get("emoji_puzzle", [])
                self.assertEqual(len(emoji_people), 1)            # one person, not five
                self.assertEqual(emoji_people[0]["name"], "נועם")
                self.assertEqual(emoji_people[0]["rsvps"], 5)     # ...who RSVP'd 5×
                self.assertFalse(emoji_people[0]["is_admin"])

                flagged = await db.get_engagement_actors(days=14, admin_ids=[7])
                self.assertTrue(flagged["by_type"]["emoji_puzzle"][0]["is_admin"])
            finally:
                await db.close()


class RsvpGateToggleTests(unittest.IsolatedAsyncioTestCase):
    def test_rsvp_gate_disabled_by_default_enabled_when_set(self):
        with patch.object(cfg, "get_settings", return_value={}):
            self.assertFalse(cfg.rsvp_gate_enabled())
        with patch.object(cfg, "get_settings", return_value={"trivia": {"rsvp_gate_enabled": True}}):
            self.assertTrue(cfg.rsvp_gate_enabled())
        with patch.object(cfg, "get_settings", return_value={"trivia": {"rsvp_gate_enabled": False}}):
            self.assertFalse(cfg.rsvp_gate_enabled())

    async def test_gate_disabled_does_not_cancel_low_rsvp_game(self):
        # With the gate off (default), a trivia row with a marker + threshold 2
        # and ZERO RSVPs must still launch — no SkippedActivity.
        msg = {"poll_options": '{"warmup_marker": "m1", "min_ready_players": 2}'}
        with patch.object(cfg, "get_settings", return_value={"trivia": {"rsvp_gate_enabled": False}}):
            # Should return without raising and without touching the DB.
            await calendar._enforce_warmup_rsvp_gate(db=None, msg=msg, bot=None, group_id=-100)


class EngagementPageRenderTests(unittest.TestCase):
    def test_engagement_page_and_api_render(self):
        import asyncio
        from fastapi.testclient import TestClient
        import dashboard.app as dashboard_app

        with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
            async def _seed():
                db = Database(tmp.name)
                await db.init()
                try:
                    await db.upsert_member(1, "noam", "נועם")
                    mid = await _sent_row(db, "discussion", sent_message_id=8001)
                    await db.record_prompt_reply(mid, user_id=1)
                    await db.record_reaction_update(
                        mid, telegram_message_id=8001, channel_topic_id=4037,
                        user_id=2, new_reaction_type="👍",
                    )
                finally:
                    await db.close()
            asyncio.run(_seed())

            with patch.object(dashboard_app, "DB_PATH", tmp.name):
                with TestClient(dashboard_app.app) as client:
                    login = client.post(
                        "/login",
                        data={"password": dashboard_app.DASHBOARD_PASSWORD},
                        follow_redirects=False,
                    )
                    self.assertEqual(login.status_code, 303)

                    page = client.get("/engagement")
                    self.assertEqual(page.status_code, 200)
                    self.assertIn("מעורבות", page.text)        # page header / nav
                    self.assertIn("שיחה", page.text)            # discussion label rendered
                    self.assertIn("מי היה מעורב השבוע", page.text)  # who-engaged section
                    self.assertIn("נועם", page.text)            # an actual person's name

                    api = client.get("/api/engagement/rollup", params={"days": 7})
                    self.assertEqual(api.status_code, 200)
                    body = api.json()
                    rows = {r["message_type"]: r for r in body["rows"]}
                    self.assertEqual(rows["discussion"]["replies"], 1)
                    self.assertEqual(rows["discussion"]["reactions"], 1)
                    self.assertEqual(rows["discussion"]["engaged"], 2)
                    names = {p["name"] for p in body["community_people"]}
                    self.assertIn("נועם", names)

                    people = client.get("/api/engagement/people", params={"days": 7})
                    self.assertEqual(people.status_code, 200)
                    self.assertIn("overall", people.json())


if __name__ == "__main__":
    unittest.main()
