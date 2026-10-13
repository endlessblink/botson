import asyncio

from telegram.ext import ExtBot

from bot.utils import duplicate_guard


def test_same_text_to_same_place_is_sent_once(monkeypatch):
    sent = []

    async def fake(self, chat_id, text, *a, **k):
        sent.append(text)
        return object()

    monkeypatch.setattr(ExtBot, "send_message", fake)
    monkeypatch.setattr(duplicate_guard, "_installed", False)
    duplicate_guard._recent.clear()
    duplicate_guard.install_duplicate_guard()
    bot = ExtBot("123:abc")
    text = "הגיע/ה לרצף של 7 ימים! כל הכבוד"

    async def go():
        a = await bot.send_message(1, text, message_thread_id=2)
        b = await bot.send_message(1, text, message_thread_id=2)
        c = await bot.send_message(1, text, message_thread_id=3)
        d = await bot.send_message(1, "קצר")
        e = await bot.send_message(1, "קצר")
        f = await bot.send_message(1, text, message_thread_id=2, reply_markup=object())
        return a, b, c
    a, b, c = asyncio.run(go())
    assert a is b and a is not c
    assert len(sent) == 5  # one dropped repeat; short texts are never dropped
