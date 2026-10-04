"""Versioned AI check-in: selected ID-bound members, drafts and self-service."""

import hashlib
import json
import logging
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import MessageEntity, User
from telegram.ext import ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.config import CONFIG_DIR, GROUP_ID, get_settings, is_auto_blocked_on
from ..utils.copy import load_copy, load_copy_block
from ..utils.freshness import freshness_rejection
from ..utils.topic_guard import safe_send
from ..utils.weekly_checkin_config import configuration_digest, write_weekly_config

logger = logging.getLogger(__name__)


def _review_config():
    return get_settings().get('weekly_state_review') or {}


def selected_usernames(values):
    if isinstance(values, str):
        values = values.replace('\n', ',').split(',')
    if not isinstance(values, list):
        raise ValueError('tag_usernames must be a list')
    result = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError('selected usernames must be strings')
        name = value.strip()
        if name.startswith('@'):
            name = name[1:]
        if not name:
            continue
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', name):
            raise ValueError('invalid selected username')
        if name.lower() not in {item.lower() for item in result}:
            result.append(name)
    return result


def resolve_selected_users(usernames, roster):
    selected = []
    for username in usernames:
        matches = [member for member in roster if str(member.get('username') or '').lower() == username.lower()]
        if len(matches) != 1:
            raise ValueError('selected username is unknown or ambiguous')
        selected.append(matches[0])
    return selected


def weekly_review_key(now, topic_id):
    week_start = now.date() - timedelta(days=(now.weekday() + 1) % 7)
    return f'weekly-state-review:{GROUP_ID}:{topic_id}:{week_start.isoformat()}'


def render_weekly_state_review(config):
    text = str(config.get('question') or '').strip()
    if not text:
        return None, []
    entities = []
    members = config.get('selected_members') or []
    if members:
        text += '\n\n'
        for index, member in enumerate(members):
            if index:
                text += ' '
            username = member.get('username')
            if username:
                selected_usernames([username])
            label = f'@{username}' if username else load_copy('weekly_state_review', 'member_label')
            offset = len(text.encode('utf-16-le')) // 2
            text += label
            entities.append(MessageEntity('text_mention', offset, len(label.encode('utf-16-le')) // 2,
                                          user=User(int(member['user_id']), label, False)))
    else:
        # Legacy formatter support only; dispatch requires canonical member IDs.
        tags = selected_usernames(config.get('tag_usernames') or [])
        if tags:
            text += '\n\n' + ' '.join(f'@{name}' for name in tags)
    return text, entities


def build_weekly_state_review(config=None):
    return render_weekly_state_review(config or _review_config())[0]


def _active_member(live, expected_id):
    return (getattr(live, 'status', '') in {'creator', 'administrator', 'member', 'restricted'} and
            (live.status != 'restricted' or getattr(live, 'is_member', False)) and
            getattr(getattr(live, 'user', None), 'id', None) == expected_id and
            not getattr(getattr(live, 'user', None), 'is_bot', True))


async def current_weekly_audience(bot, db, config):
    """Recheck the ID-bound audience immediately before either mode delivers."""
    members = config.get('selected_members') or []
    if (not members or any(type(member.get('user_id')) is not int or member['user_id'] <= 0 for member in members)
            or len({member['user_id'] for member in members}) != len(members)):
        return False
    verified = await db.get_verified_forum_topics()
    if not any(item.get('topic_id') == config.get('topic_id') and
               item.get('category_key') == config.get('initial_topic_category') for item in verified):
        return False
    excluded = set(config.get('excluded_user_ids') or [])
    for member in members:
        if member['user_id'] in excluded:
            return False
        live = await bot.get_chat_member(GROUP_ID, member['user_id'])
        if not _active_member(live, member['user_id']):
            return False
        if member.get('username') and str(live.user.username or '').lower() != member['username'].lower():
            return False
    return configuration_digest(_review_config()) == configuration_digest(config)


async def record_delivered_checkin(bot, db, *, topic_id, message_id, week_key, pin_enabled):
    await db.record_weekly_checkin_post(GROUP_ID, topic_id, message_id, week_key)
    if not pin_enabled:
        return
    try:
        previous = await db.weekly_checkin_pins(GROUP_ID, topic_id)
        await bot.pin_chat_message(chat_id=GROUP_ID, message_id=message_id, disable_notification=True)
        await db.set_weekly_checkin_pin(GROUP_ID, topic_id, message_id, True)
        for old_id in previous:
            if old_id != message_id:
                await bot.unpin_chat_message(chat_id=GROUP_ID, message_id=old_id)
                await db.set_weekly_checkin_pin(GROUP_ID, topic_id, old_id, False)
    except Exception as error:
        logger.warning('weekly check-in delivered but pin operation failed: %s', error)


async def send_weekly_state_review(context: ContextTypes.DEFAULT_TYPE, *, force=False):
    config = _review_config()
    if config.get('enabled') is not True:
        return None
    try:
        now = datetime.now(ZoneInfo(config['timezone']))
        if config.get('not_before') and now.date().isoformat() < config['not_before']:
            return None
        if now.fold or is_auto_blocked_on(now.date()):
            return None
        if not force and (config.get('days') != [(now.weekday() + 1) % 7] or config.get('time') != now.strftime('%H:%M')):
            return None
        topic_id = int(config['topic_id'])
        text, entities = render_weekly_state_review(config)
        members = config.get('selected_members') or []
        if (not text or not members or config.get('mode') not in {'draft', 'auto_send'}
                or any(type(member.get('user_id')) is not int or member['user_id'] <= 0 for member in members)
                or len({member['user_id'] for member in members}) != len(members)
                or freshness_rejection(config['question'])):
            return None
        db: Database = context.bot_data['db']
        if not await current_weekly_audience(context.bot, db, config):
            return None
        key = weekly_review_key(now, topic_id)
        digest = hashlib.sha256((text + json.dumps(members, sort_keys=True)).encode()).hexdigest()
        state, cached = await db.begin_agent_api_action(key, digest)
        if state == 'complete':
            receipt = json.loads(cached or '{}')
            return receipt.get('message_id') or receipt.get('draft_id')
        if state != 'new':
            return None
        if config.get('mode') == 'draft':
            draft_id = await db.create_scheduled_message(
                text, 'custom', topic_id, 'main', now.date().isoformat(), config['time'], status='draft',
                created_by='weekly-checkin', poll_options=json.dumps({'weekly_checkin_key': key,
                    'weekly_member_entities': [entity.to_dict() for entity in entities],
                    'weekly_revision': config.get('revision', 0),
                    'weekly_pin_enabled': bool(config.get('pin_enabled'))}),
            )
            await db.complete_agent_api_action(key, digest, json.dumps({'draft_id': draft_id}))
            return draft_id
        if config.get('mode') != 'auto_send':
            return None
        sent = await safe_send(context.bot, db, 'send_message', chat_id=GROUP_ID,
                               text=text, entities=entities, message_thread_id=topic_id)
        message_id = getattr(sent, 'message_id', None)
        if not message_id:
            return None
        await record_delivered_checkin(context.bot, db, topic_id=topic_id, message_id=message_id,
                                       week_key=key, pin_enabled=config.get('pin_enabled', False))
        if not await db.complete_agent_api_action(key, digest, json.dumps({'message_id': int(message_id)})):
            return None
        await db.log_activity('weekly_state_review', now.date().isoformat(), target_channel=str(topic_id))
        return int(message_id)
    except Exception as error:
        logger.warning('weekly check-in refused or outcome uncertain; no automatic resend: %s', error)
        return None


async def checkin_membership_reply(update, context):
    message, user, chat = update.message, update.effective_user, update.effective_chat
    config = _review_config()
    copy = load_copy_block('weekly_state_review', default={}) or {}
    command = str(getattr(message, 'text', '') or '').strip()
    if command not in {copy.get('opt_in_command'), copy.get('opt_out_command')}:
        return
    if (not message or not user or user.is_bot or not chat or chat.id != GROUP_ID or
            getattr(message, 'message_thread_id', None) != config.get('topic_id') or
            not getattr(message, 'reply_to_message', None)):
        return
    db = context.bot_data['db']
    reply_id = message.reply_to_message.message_id
    if not await db.is_weekly_checkin_post(GROUP_ID, config['topic_id'], reply_id):
        return
    subscribe = command == copy.get('opt_in_command')
    if subscribe:
        live = await context.bot.get_chat_member(GROUP_ID, user.id)
        if not _active_member(live, user.id):
            return
    username = getattr(live.user if subscribe else user, 'username', None)
    if username:
        selected_usernames([username])
    if subscribe:
        await db.upsert_chat_member(GROUP_ID, user.id, username,
                                    username or load_copy('weekly_state_review', 'member_label'))
    _, applied = write_weekly_config(CONFIG_DIR / 'settings.yaml', expected_revision=None,
        actor=f'member:{user.id}', action='opt_in' if subscribe else 'opt_out',
        event_key=f'telegram:{GROUP_ID}:{message.message_id}',
        member_change=({'user_id': user.id, 'username': username}, subscribe))
    if applied:
        await safe_send(context.bot, db, 'send_message', chat_id=GROUP_ID, message_thread_id=config['topic_id'],
                        reply_to_message_id=message.message_id,
                        text=load_copy('weekly_state_review', 'opt_in_confirmation' if subscribe else 'opt_out_confirmation'))


def register(app):
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, checkin_membership_reply), group=98)
