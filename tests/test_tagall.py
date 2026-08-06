from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from bot.handlers import tagall


def test_announcement_mentions_preserve_members_with_spaces():
    mentions = [tagall._mention(1, "First Person"), tagall._mention(2, "Second Person")]

    messages = tagall._announcement_messages("Read this", mentions)

    assert len(messages) == 1
    assert "First Person" in messages[0]
    assert "Second Person" in messages[0]



class TagAllCommandTests(IsolatedAsyncioTestCase):
    async def test_preview_checks_members_without_sending(self):
        user = SimpleNamespace(id=101)
        message = SimpleNamespace(reply_text=AsyncMock(), message_thread_id=None)
        update = SimpleNamespace(
            message=message,
            effective_user=user,
            effective_chat=SimpleNamespace(id=-1001, type="supergroup"),
        )
        bot = SimpleNamespace(
            get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
            send_message=AsyncMock(),
        )
        db = SimpleNamespace(
            upsert_chat_member=AsyncMock(),
            get_chat_members_for_tagging=AsyncMock(return_value=[
                {"user_id": 7, "username": "u", "display_name": "A Person"},
            ])
        )
        context = SimpleNamespace(args=["Read", "this"], bot=bot, bot_data={"db": db})

        original_is_admin = tagall.is_admin
        tagall.is_admin = lambda user_id: True
        try:
            await tagall.tagall_command(update, context)
        finally:
            tagall.is_admin = original_is_admin

        message.reply_text.assert_awaited_once()
        bot.send_message.assert_not_awaited()
        assert "1" in message.reply_text.await_args.args[0]
