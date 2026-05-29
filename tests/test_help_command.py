import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.error import Forbidden

from bot import main


class HelpCommandTests(unittest.IsolatedAsyncioTestCase):
    def _update(self, chat_type="group", user_id=123):
        return SimpleNamespace(
            effective_chat=SimpleNamespace(type=chat_type),
            effective_user=SimpleNamespace(id=user_id) if user_id is not None else None,
            message=SimpleNamespace(reply_text=AsyncMock()),
        )

    def _context(self):
        return SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))

    async def test_private_help_replies_in_private_chat(self):
        update = self._update(chat_type="private")
        context = self._context()

        await main.help_command(update, context)

        update.message.reply_text.assert_awaited_once()
        context.bot.send_message.assert_not_awaited()

    async def test_group_help_sends_dm_without_public_reply(self):
        update = self._update(chat_type="supergroup", user_id=456)
        context = self._context()

        await main.help_command(update, context)

        context.bot.send_message.assert_awaited_once()
        self.assertEqual(context.bot.send_message.await_args.kwargs["chat_id"], 456)
        update.message.reply_text.assert_not_awaited()

    async def test_group_help_posts_open_dm_notice_when_dm_forbidden(self):
        update = self._update(chat_type="group", user_id=789)
        context = self._context()
        context.bot.send_message.side_effect = Forbidden("bot was blocked by the user")

        with patch.object(main, "deep_link", return_value="https://t.me/bot?start=menu"):
            await main.help_command(update, context)

        context.bot.send_message.assert_awaited_once()
        update.message.reply_text.assert_awaited_once()
        self.assertIn("https://t.me/bot?start=menu", update.message.reply_text.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
