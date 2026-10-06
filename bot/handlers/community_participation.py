"""Bounded publisher news and explicit contextual replies in the existing group."""
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler, MessageHandler, filters

from ..scheduler.materializer import _generate_with_claude
from ..utils import participation_config as configuration
from ..utils.community_participation import _quiet, _local_now
from ..utils.community_participation_store import ParticipationReviewStore, payload_digest
from ..utils.config import CONFIG_DIR, GROUP_ID, load_yaml
from ..utils.copy import load_copy
from ..utils.freshness import freshness_rejection
from ..utils.participation_feeds import fetch_articles
from ..utils.redaction import redact_sensitive
from ..utils.topic_guard import safe_send

logger = logging.getLogger(__name__)


def read_config():
    return configuration.read_config(CONFIG_DIR / 'community_participation.yaml')


def store():
    return ParticipationReviewStore(CONFIG_DIR.parent / 'data' / 'participation-review.db')


async def generate(prompt):
    from ..utils.operator_anchors import render_learned_rules_block
    from ..utils.quality_rules import load_quality_rules_short
    guidance = render_learned_rules_block() + '\n' + load_quality_rules_short()
    return await _generate_with_claude(prompt+'\n\n'+guidance, allow_paid_fallback=False)


def _json(raw):
    try:
        value = json.loads(raw or '')
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def private_context_text(value):
    text = redact_sensitive(value)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[private email]', text)
    text = re.sub(r'(?<!\w)(?:\+?\d[ .-]?){8,15}(?!\w)', '[private number]', text)
    return re.sub(r'(?<!\w)@[A-Za-z0-9_]{4,32}', '[member]', text)


async def context_for(db, topic, config, *, kind, now):
    runtime = config['runtime']
    policy = config[kind]
    rows = await db.get_recent_community_messages(GROUP_ID, thread_id=topic,
        since=now-timedelta(minutes=policy['context_max_age_minutes']),
        limit=runtime['context_messages'], prune_expired=False)
    rows = [row for row in rows if row.get('thread_id') == topic]
    # No names, account identities or raw-context copies in preview/storage.
    texts, remaining = [], runtime['context_max_chars']
    for row in reversed(rows):
        if remaining <= 0:
            break
        text = private_context_text(str(row.get('text') or ''))[:remaining]
        remaining -= len(text)
        texts.append(text)
    joined = json.dumps(list(reversed(texts)), ensure_ascii=False)
    marker = hashlib.sha256(json.dumps([(r.get('message_id'), r.get('occurred_at')) for r in rows]).encode()).hexdigest()
    return joined, marker, rows


def preview_policy(config, kind):
    policy = dict(config[kind])
    policy['mode'] = 'preview_only'
    return policy


async def prepare_news(db, config, *, topic_id, source_id, now):
    policy = config['topic_news']
    if not policy['enabled'] or source_id not in policy['topic_sources'].get(topic_id, []):
        return None, 'topic_or_source_not_selected'
    verified = await db.get_verified_forum_topics()
    topics = {row['topic_id'] for row in verified}
    if topic_id not in topics:
        return None, 'unverified_topic'
    if not await store().available('topic_news', {'topic_id': topic_id}, policy, now=now, chat_id=GROUP_ID):
        return None, 'quiet_hours_rate_cap_or_opt_out'
    context, marker, rows = await context_for(db, topic_id, config, kind='topic_news', now=now)
    if not rows:
        return None, 'current_context_unavailable'
    source = policy['sources'][source_id]
    articles = await fetch_articles(source_id, source, config['runtime'], now=now)
    prompts = load_yaml('participation_prompts.yaml')
    prior = await store().recent_news(GROUP_ID, limit=config['runtime']['context_messages'])
    for article in articles[:config['runtime']['max_candidates_per_cycle']]:
        key = f'news:{GROUP_ID}:{article["event_id"]}'
        if await store().status(key):
            continue
        # Drop stale publisher items before any generation cost.
        published = datetime.fromisoformat(article['published_at'])
        if now-published > timedelta(hours=policy['freshness_hours']):
            continue
        result = _json(await generate(prompts['news'].format(topic=source['topic_label'],
            article=json.dumps(article, ensure_ascii=False), context=context,
            max_chars=config['runtime']['max_news_chars'])))
        summary = result.get('summary')
        if (result.get('sensitive') is not False or not isinstance(summary, str) or
                not summary.strip() or len(summary) > config['runtime']['max_news_chars'] or
                freshness_rejection(summary) or not any('\u0590' <= c <= '\u05ff' for c in summary)):
            continue
        # The separate reviewer checks the exact summary against publisher text.
        review = _json(await generate(prompts['news_review'].format(
            article=json.dumps(article, ensure_ascii=False), summary=summary, context=context,
            prior_updates=json.dumps(prior, ensure_ascii=False))))
        event_key = review.get('event_key')
        if (review.get('pass') is not True or review.get('factually_supported') is not True or
                review.get('sensitive') is not False or review.get('duplicate_event') is not False or
                not isinstance(event_key, str) or not re.fullmatch(r'[a-z0-9-]{8,120}', event_key)):
            continue
        candidate = {k:v for k,v in article.items() if k != 'source_excerpt'}
        candidate.update(event_id='event:'+event_key, topic_id=topic_id, summary=summary.strip(), relevance=review.get('relevance'),
            context_at=now.isoformat(), context_same_topic=True, context_topic_id=topic_id,
            content_review_passed=True, reviewed_summary_digest=hashlib.sha256(summary.strip().encode()).hexdigest())
        outcome = await store().preview('topic_news', candidate, preview_policy(config, 'topic_news'),
            now=now, chat_id=GROUP_ID, verified_topics=topics)
        if outcome['accepted']:
            return {'candidate': outcome['preview'], 'key': key, 'context_marker': marker}, None
    return None, 'no_fresh_relevant_reviewed_news'


async def deliver(bot, db, config, kind, prepared, *, reply_to=None):
    candidate, key = prepared['candidate'], prepared['key']
    policy = preview_policy(config, kind)
    ledger = store()
    now = datetime.now(timezone.utc)
    topics = {row['topic_id'] for row in await db.get_verified_forum_topics()}
    reservation = await ledger.reserve_approved(kind, candidate, policy, approved_digest=payload_digest(candidate),
        idempotency_key=key, now=now, chat_id=GROUP_ID, verified_topics=topics)
    if reservation['replay'] or reservation['status'] != 'reserved':
        return reservation['reason']
    await ledger.transition(key, expected_status='reserved', status='scheduled', at=now)
    current = read_config()
    _, marker, _ = await context_for(db, candidate['topic_id'], current, kind=kind, now=now)
    outcome = await ledger.revalidate(key, preview_policy(current, kind), now=now,
                                     chat_id=GROUP_ID, verified_topics=topics)
    if (configuration.config_digest(current) != configuration.config_digest(config) or
            not current[kind]['enabled'] or current[kind]['mode'] != 'auto_send' or
            marker != prepared['context_marker'] or not outcome['accepted']):
        await ledger.transition(key, expected_status='scheduled', status='cancelled', at=now)
        return 'changed_context_or_policy'
    text = (load_copy('participation', 'news_post', summary=candidate['summary'],
                      source_url=candidate['source_url']) if kind == 'topic_news' else candidate['text'])
    kwargs = {'chat_id': GROUP_ID, 'message_thread_id': candidate['topic_id'], 'text': text,
              'disable_notification': True}
    if reply_to:
        kwargs['reply_to_message_id'] = reply_to
        kwargs['reply_markup'] = InlineKeyboardMarkup([[InlineKeyboardButton(
            load_copy('participation', 'opt_out'), callback_data='participation_opt_out'),
            InlineKeyboardButton(load_copy('participation', 'opt_in'),
                                 callback_data='participation_opt_in')]])
    try:
        sent = await safe_send(bot, db, 'send_message', **kwargs)
        message_id = getattr(sent, 'message_id', None)
        if type(message_id) is not int or message_id < 1:
            raise RuntimeError('delivery outcome not confirmed')
        await ledger.transition(key, expected_status='scheduled', status='sent', at=now,
                                external_message_id=message_id)
        return 'sent'
    except Exception:
        await ledger.transition(key, expected_status='scheduled', status='uncertain', at=now)
        logger.warning('Participation delivery uncertain; no automatic retry')
        return 'uncertain'


async def run_news_cycle(context):
    now = datetime.now(timezone.utc)
    config = read_config()
    policy = config['topic_news']
    if not policy['enabled'] or policy['mode'] != 'auto_send':
        return
    report = {'observed_at': now.isoformat(), 'revision': config.get('revision', 0), 'sent': 0, 'outcomes': []}
    if _quiet(_local_now(now, policy), policy['quiet_hours']):
        report['outcomes'].append('quiet_hours')
    else:
        topics = list(policy['topic_sources'])
        # Rotate the first eligible topic, avoiding permanent source-order bias.
        offset = now.astimezone(ZoneInfo(policy['timezone'])).date().toordinal() % len(topics)
        topics = topics[offset:]+topics[:offset]
        for topic in topics:
            if report['sent'] >= config['runtime']['max_sends_per_cycle']:
                break
            for source in policy['topic_sources'][topic]:
                try:
                    prepared, reason = await prepare_news(context.bot_data['db'], config,
                        topic_id=topic, source_id=source, now=now)
                    result = await deliver(context.bot, context.bot_data['db'], config, 'topic_news', prepared) if prepared else reason
                    report['outcomes'].append({'topic_id': topic, 'source_id': source, 'result': result})
                    if result == 'sent':
                        report['sent'] += 1
                        break
                except Exception:
                    report['outcomes'].append({'topic_id': topic, 'source_id': source, 'result': 'unavailable'})
                    logger.warning('News source/context unavailable; skipping without filler')
    path = CONFIG_DIR.parent / 'data' / 'participation-cycle-status.json'
    path.write_text(json.dumps(report))


async def handle_reply(update, context):
    message, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not message or not user or user.is_bot or not chat or chat.id != GROUP_ID:
        return
    config = read_config()
    policy = config['occasional_replies']
    topic = getattr(message, 'message_thread_id', None)
    if not policy['enabled'] or policy['mode'] != 'auto_send' or topic not in policy['allowed_topics']:
        return
    # The existing original-worktree mentions-only handler is not registered;
    # this one owns addressed replies, with durable caps and opt-outs.
    replied = getattr(message, 'reply_to_message', None)
    bot_id = context.bot.id
    addressed = bool(replied and getattr(getattr(replied, 'from_user', None), 'id', None) == bot_id)
    username = getattr(context.bot, 'username', None)
    addressed = addressed or bool(username and re.search(rf'(?<![\w@])@{re.escape(username)}(?!\w)', message.text or '', re.I))
    if not addressed:
        return
    now = datetime.now(timezone.utc)
    incoming_at = getattr(message, 'date', None)
    if not incoming_at or getattr(incoming_at, 'tzinfo', None) is None or not timedelta(0) <= now-incoming_at <= timedelta(minutes=policy['context_max_age_minutes']):
        return
    if _quiet(_local_now(now, policy), policy['quiet_hours']):
        return
    # Weekly membership commands keep their own existing handler and ledger.
    if (message.text or '').strip() in {load_copy('weekly_state_review', 'opt_in_command'),
                                       load_copy('weekly_state_review', 'opt_out_command')}:
        return
    ledger = store()
    key = f'reply:{GROUP_ID}:{message.message_id}'
    if await ledger.status(key):
        return
    db = context.bot_data['db']
    topics = {row['topic_id'] for row in await db.get_verified_forum_topics()}
    if topic not in topics:
        return
    identity = {'topic_id': topic, 'sender_user_id': user.id,
                'conversation_key': str(getattr(replied, 'message_id', None) or message.message_id)}
    parent = await ledger.reply_parent(GROUP_ID, topic, user.id, getattr(replied, 'message_id', None),
                                      now=now, policy=policy) if replied else None
    if parent:
        identity.update(conversation_key=parent['conversation_key'], parent_message_id=replied.message_id,
                        turn_number=parent.get('turn_number', 1) + 1)
    if not await ledger.available('occasional_replies', identity, policy, now=now, chat_id=GROUP_ID):
        return
    context_text, marker, _ = await context_for(db, topic, config, kind='occasional_replies', now=now)
    incoming = private_context_text((message.text or '')[:config['runtime']['context_max_chars']])
    prompts = load_yaml('participation_prompts.yaml')
    result = _json(await generate(prompts['reply'].format(context=context_text, incoming=incoming,
        previous_reply=parent['text'] if parent else '',
                                                         max_chars=config['runtime']['max_reply_chars'])))
    text = result.get('text')
    if result.get('decision') != 'reply' or result.get('sensitive') is not False or not isinstance(text, str) or not text.strip() or len(text) > config['runtime']['max_reply_chars'] or not any('\u0590' <= c <= '\u05ff' for c in text):
        return
    review = _json(await generate(prompts['reply_review'].format(context=context_text, incoming=incoming, text=text)))
    if review.get('pass') is not True or review.get('sensitive') is not False:
        return
    candidate = {**identity, 'trigger_message_id': message.message_id, 'text': text.strip(),
        'value': review.get('value'), 'context_at': now.isoformat(), 'context_same_topic': True,
        'context_topic_id': topic, 'sender_is_bot': False, 'conversation_active': True,
        'privacy_permits_reply': True, 'moderation_allows_reply': True, 'opted_out': False,
        'sensitive': False, 'addressed_to_bot': True}
    preview = await ledger.preview('occasional_replies', candidate, preview_policy(config, 'occasional_replies'),
        now=now, chat_id=GROUP_ID, verified_topics=topics)
    if preview['accepted']:
        await deliver(context.bot, db, config, 'occasional_replies',
            {'candidate': preview['preview'], 'key': key, 'context_marker': marker}, reply_to=message.message_id)


async def member_opt_out(update, context):
    query, user = update.callback_query, update.effective_user
    message = getattr(query, 'message', None)
    if not query or not user or user.is_bot or not message or message.chat.id != GROUP_ID:
        return
    await store().set_member_opt_out(GROUP_ID, user.id, actor_user_id=user.id,
                                    opted_out=query.data == 'participation_opt_out', at=datetime.now(timezone.utc))
    await query.answer(load_copy('participation', 'opted_out' if query.data == 'participation_opt_out' else 'opted_in'))
    # This keyboard is visible to every member. Both actions remain available;
    # only the clicker's preference and private callback acknowledgement change.


def register(app):
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reply, block=False), group=97)
    app.add_handler(CallbackQueryHandler(member_opt_out, pattern='^participation_opt_(out|in)$'), group=97)


def register_news_jobs(app, config=None):
    for job in app.job_queue.get_jobs_by_name('topic_news'):
        job.schedule_removal()
    config = config or read_config()
    policy = config['topic_news']
    if policy['enabled'] and policy['mode'] == 'auto_send':
        for at in policy['times']:
            hour, minute = map(int, at.split(':'))
            app.job_queue.run_daily(run_news_cycle, time(hour, minute, tzinfo=ZoneInfo(policy['timezone'])), name='topic_news')


async def configure_jobs(app):
    config = read_config()
    active = any(config[k]['enabled'] for k in ('topic_news', 'occasional_replies'))
    if active:
        topics = {r['topic_id'] for r in await app.bot_data['db'].get_verified_forum_topics()}
        configuration.validate_config(config, topics)
        await store().initialize()
    policy = config['topic_news']
    register_news_jobs(app, config)
    status = {'revision': config.get('revision', 0), 'news_jobs': len(app.job_queue.get_jobs_by_name('topic_news')),
              'news_enabled': policy['enabled'], 'replies_enabled': config['occasional_replies']['enabled'],
              'observed_at': datetime.now(timezone.utc).isoformat()}
    path = CONFIG_DIR.parent / 'data' / 'participation-runtime-status.json'
    path.write_text(json.dumps(status))
    return status


async def reload_job(context):
    flag = CONFIG_DIR.parent / 'data' / 'reload_participation.flag'
    if flag.exists():
        await configure_jobs(context.application)
        flag.unlink(missing_ok=True)
