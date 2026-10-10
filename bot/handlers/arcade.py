"""Requested game entry only; no broadcasts, scheduler or service startup."""
from urllib.parse import urlsplit

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import CommandHandler

from ..utils.config import GROUP_ID, deep_link, load_yaml
from ..utils.copy import load_copy
from ..utils.topic_guard import safe_send


async def open_arcade(update, context):
    cfg=load_yaml('arcade.yaml')
    chat,user,message=update.effective_chat,update.effective_user,update.effective_message
    if not cfg.get('enabled') or not chat or not user or user.is_bot or not message:
        return
    parts=urlsplit(cfg.get('public_url') or '')
    if parts.scheme!='https' or not parts.hostname or parts.username or parts.password:
        return
    if chat.type=='private':
        try:
            member=await context.bot.get_chat_member(GROUP_ID,user.id)
            if member.status not in {'creator','administrator','member'} and not (
                    member.status=='restricted' and getattr(member,'is_member',False)):
                return
        except Exception:
            return
        markup=InlineKeyboardMarkup([[InlineKeyboardButton(load_copy('arcade','bot_open'),
                                                          web_app=WebAppInfo(cfg['public_url']))]])
        await message.reply_text(load_copy('arcade','bot_entry'),reply_markup=markup,
                                 disable_notification=True)
    elif chat.id==GROUP_ID and message.message_thread_id in cfg.get('allowed_topics',[]):
        topics={row['topic_id'] for row in await context.bot_data['db'].get_verified_forum_topics()}
        if message.message_thread_id not in topics:
            return
        link=deep_link('arcade') or f"https://t.me/{context.bot.username}?start=arcade"
        markup=InlineKeyboardMarkup([[InlineKeyboardButton(load_copy('arcade','bot_private'),
                                                          url=link)]])
        await safe_send(context.bot,context.bot_data['db'],'send_message',chat_id=GROUP_ID,
            message_thread_id=message.message_thread_id,text=load_copy('arcade','bot_entry'),
            reply_to_message_id=message.message_id,reply_markup=markup,disable_notification=True)


def register(app):
    app.add_handler(CommandHandler('arcade',open_arcade),group=98)
