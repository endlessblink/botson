"""Only an explicit eligible request can expose the configured game entry."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from bot.handlers import arcade


@pytest.mark.parametrize('case',['disabled','bad_url','bot','other_chat','other_topic','unverified'])
def test_arcade_entry_does_not_post_outside_selected_request(monkeypatch,case):
    cfg={'enabled':True,'public_url':'https://arcade.example.test','allowed_topics':[99]}
    message=SimpleNamespace(message_id=5,message_thread_id=99,reply_text=AsyncMock())
    chat=SimpleNamespace(id=-10099,type='supergroup');user=SimpleNamespace(id=123,is_bot=False)
    db=SimpleNamespace(get_verified_forum_topics=AsyncMock(return_value=[{'topic_id':99}]))
    if case=='disabled':cfg['enabled']=False
    if case=='bad_url':cfg['public_url']='http://arcade.example.test'
    if case=='bot':user.is_bot=True
    if case=='other_chat':chat.id=-10088
    if case=='other_topic':message.message_thread_id=100
    if case=='unverified':db.get_verified_forum_topics.return_value=[]
    send=AsyncMock();monkeypatch.setattr(arcade,'GROUP_ID',-10099)
    monkeypatch.setattr(arcade,'load_yaml',lambda _:cfg);monkeypatch.setattr(arcade,'safe_send',send)
    ctx=SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock()),bot_data={'db':db})
    asyncio.run(arcade.open_arcade(SimpleNamespace(effective_chat=chat,effective_user=user,effective_message=message),ctx))
    send.assert_not_called();message.reply_text.assert_not_called()


@pytest.mark.parametrize('status',['left','kicked','unknown'])
def test_private_entry_requires_existing_group_membership(monkeypatch,status):
    monkeypatch.setattr(arcade,'load_yaml',lambda _:{'enabled':True,'public_url':'https://arcade.example.test'})
    message=SimpleNamespace(reply_text=AsyncMock())
    ctx=SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status))))
    update=SimpleNamespace(effective_chat=SimpleNamespace(type='private'),effective_user=SimpleNamespace(id=123,is_bot=False),effective_message=message)
    asyncio.run(arcade.open_arcade(update,ctx));message.reply_text.assert_not_called()


@pytest.mark.parametrize('url',['https://arcade.example.test','https://arcade.example.test/arcade/'])
def test_requested_private_member_entry_uses_mini_app_and_no_extra_post(monkeypatch,url):
    monkeypatch.setattr(arcade,'load_yaml',lambda _:{'enabled':True,'public_url':url})
    monkeypatch.setattr(arcade,'load_copy',lambda *_:'[configured copy]')
    message=SimpleNamespace(reply_text=AsyncMock())
    ctx=SimpleNamespace(bot=SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status='member'))))
    update=SimpleNamespace(effective_chat=SimpleNamespace(type='private'),effective_user=SimpleNamespace(id=123,is_bot=False),effective_message=message)
    asyncio.run(arcade.open_arcade(update,ctx));message.reply_text.assert_awaited_once()
    args=message.reply_text.call_args.kwargs
    assert args['disable_notification'] is True
    assert args['reply_markup'].inline_keyboard[0][0].web_app.url==url
