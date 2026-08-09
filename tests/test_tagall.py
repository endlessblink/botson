from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from bot.handlers import tagall


def test_announcement_uses_compact_visual_label():
    messages = tagall._announcement_messages("Read this", "@all")

    assert len(messages) == 1
    assert "@all" in messages[0]



class TagAllCommandTests(IsolatedAsyncioTestCase):
    async def test_preview_checks_members_without_sending(self):
        user = SimpleNamespace(id=101)
        progress = SimpleNamespace(edit_text=AsyncMock())
        message = SimpleNamespace(reply_text=AsyncMock(return_value=progress), message_thread_id=None)
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
        progress.edit_text.assert_awaited_once()
        bot.send_message.assert_not_awaited()
        assert "1" in progress.edit_text.await_args.args[0]
