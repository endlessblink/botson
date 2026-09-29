from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, call, patch

from bot.handlers import welcome


class WelcomeBatchTests(IsolatedAsyncioTestCase):
    def test_configured_public_copy_is_name_free_and_dm_templates_format(self):
        public_text = welcome.load_copy("welcome", "public_batch")
        dm_text = welcome.load_copy("welcome", "dm_single", name="A new member")

        self.assertIn("A new member", dm_text)
        self.assertNotIn("{name}", dm_text)
        self.assertNotIn("A new member", public_text)
        self.assertNotIn("{name}", public_text)

    async def test_batch_posts_one_generic_welcome_to_configured_topic(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        db = SimpleNamespace(
            upsert_member=AsyncMock(),
            upsert_chat_member=AsyncMock(),
            record_member_activity=AsyncMock(),
            log_activity=AsyncMock(),
            claim_action_cooldown=AsyncMock(return_value=True),
        )
        context = SimpleNamespace(bot=bot, bot_data={"db": db})
        joins = [
            {"user_id": 101, "username": "one", "name": "Person One"},
            {"user_id": 202, "username": "two", "name": "Person Two"},
        ]

        with (
            patch.object(welcome, "_pending_joins", joins),
            patch.object(welcome, "_batch_task", object()),
            patch.object(welcome, "get_settings", _public_welcome_enabled),
            patch("bot.handlers.welcome.load_copy", create=True, side_effect=lambda ns, key, **_: f"{ns}.{key}"),
        ):
            await welcome._flush_pending(context, chat_id=-100123, topic_id=341)

        public_posts = [
            call.kwargs for call in bot.send_message.await_args_list
            if call.kwargs.get("message_thread_id") == 341
        ]
        self.assertEqual(len(public_posts), 1)
        self.assertEqual(public_posts[0]["chat_id"], -100123)
        self.assertEqual(public_posts[0]["text"], "welcome.public_batch")
        self.assertNotIn("Person One", public_posts[0]["text"])
        self.assertNotIn("Person Two", public_posts[0]["text"])
        db.claim_action_cooldown.assert_awaited_once_with(
            "public_welcome:-100123:341", 86400
        )

    async def test_recent_public_welcome_is_suppressed_but_join_is_recorded(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        db = SimpleNamespace(
            upsert_member=AsyncMock(),
            upsert_chat_member=AsyncMock(),
            record_member_activity=AsyncMock(),
            log_activity=AsyncMock(),
            claim_action_cooldown=AsyncMock(return_value=False),
        )
        context = SimpleNamespace(bot=bot, bot_data={"db": db})

        with (
            patch.object(welcome, "_pending_joins", [{"user_id": 303, "username": None, "name": "New Person"}]),
            patch.object(welcome, "get_settings", _public_welcome_enabled),
            patch.object(welcome, "_batch_task", None),
            patch("bot.handlers.welcome.load_copy", create=True, side_effect=lambda ns, key, **_: f"{ns}.{key}"),
        ):
            await welcome._flush_pending(context, chat_id=-100123, topic_id=341)

        db.upsert_chat_member.assert_awaited_once_with(-100123, 303, None, "New Person")
        db.record_member_activity.assert_awaited_once_with(-100123, 303, "join", "303")
        db.claim_action_cooldown.assert_awaited_once_with(
            "public_welcome:-100123:341", 86400
        )
        self.assertFalse(any(
            call.kwargs.get("chat_id") == -100123
            and call.kwargs.get("message_thread_id") == 341
            for call in bot.send_message.await_args_list
        ))

    async def test_public_welcome_is_sent_even_when_private_messages_fail(self):
        async def send_message(*, chat_id, **kwargs):
            if chat_id == -100123:
                return None
            raise RuntimeError("DM unavailable")

        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send_message))
        db = SimpleNamespace(
            upsert_member=AsyncMock(),
            upsert_chat_member=AsyncMock(),
            record_member_activity=AsyncMock(),
            log_activity=AsyncMock(),
            claim_action_cooldown=AsyncMock(return_value=True),
        )
        context = SimpleNamespace(bot=bot, bot_data={"db": db})

        with (
            patch.object(welcome, "_pending_joins", [{"user_id": 101, "username": None, "name": "Person"}]),
            patch.object(welcome, "_batch_task", None),
            patch.object(welcome, "get_settings", _public_welcome_enabled),
            patch("bot.handlers.welcome.load_copy", create=True, side_effect=lambda ns, key, **_: f"{ns}.{key}"),
        ):
            await welcome._flush_pending(context, chat_id=-100123, topic_id=341)

        bot.send_message.assert_any_await(
            chat_id=-100123, message_thread_id=341, text="welcome.public_batch"
        )
        self.assertEqual(bot.send_message.await_count, 2)

    async def test_no_public_welcome_without_configured_topic(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        db = SimpleNamespace(
            upsert_member=AsyncMock(),
            upsert_chat_member=AsyncMock(),
            record_member_activity=AsyncMock(),
            log_activity=AsyncMock(),
            claim_action_cooldown=AsyncMock(return_value=True),
        )
        context = SimpleNamespace(bot=bot, bot_data={"db": db})

        with (
            patch.object(welcome, "_pending_joins", [{"user_id": 101, "username": None, "name": "Person"}]),
            patch.object(welcome, "_batch_task", None),
            patch("bot.handlers.welcome.load_copy", create=True, side_effect=lambda ns, key, **_: f"{ns}.{key}"),
        ):
            await welcome._flush_pending(context, chat_id=-100123, topic_id=None)

        bot.send_message.assert_has_awaits([
            call(chat_id=101, text="welcome.dm_single"),
            call(chat_id=101, text="welcome.rules"),
        ])
        self.assertFalse(any("message_thread_id" in call.kwargs for call in bot.send_message.await_args_list))


def _public_welcome_enabled():
    """Real settings with the (default-off) public greeting switched on."""
    from bot.utils.config import get_settings
    settings = get_settings()
    return {**settings, "welcome": {**settings["welcome"], "public_enabled": True}}


class PublicWelcomeOffByDefaultTests(IsolatedAsyncioTestCase):
    async def test_join_is_recorded_but_nothing_is_posted_publicly(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        db = SimpleNamespace(
            upsert_member=AsyncMock(), upsert_chat_member=AsyncMock(),
            record_member_activity=AsyncMock(), log_activity=AsyncMock(),
            claim_action_cooldown=AsyncMock(return_value=True),
        )
        context = SimpleNamespace(bot=bot, bot_data={"db": db})
        with (
            patch.object(welcome, "_pending_joins", [{"user_id": 404, "username": None, "name": "Newcomer"}]),
            patch.object(welcome, "_batch_task", object()),
            patch("bot.handlers.welcome.load_copy", create=True, side_effect=lambda ns, key, **_: f"{ns}.{key}"),
        ):
            await welcome._flush_pending(context, chat_id=-100123, topic_id=341)
        db.upsert_member.assert_awaited()
        db.claim_action_cooldown.assert_not_awaited()
        public = [c for c in bot.send_message.await_args_list if c.kwargs.get("message_thread_id") == 341]
        self.assertEqual(public, [])
