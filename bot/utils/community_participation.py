"""Offline preview policies; no fetching, LLM, scheduler registration or send API.

News input must come from a separately verified source adapter. This module
checks that adapter's evidence; it does not itself verify a live article.
Reply policy is for eventual integration with the existing mentions/replies
handler, not a second autonomous conversational agent.
"""

import hashlib
from datetime import datetime, timedelta
from math import isfinite
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


_NEWS_FIELDS = {'topic_id','source_id','source_url','published_at','verified_at','source_verified',
                'title','summary','event_id','relevance','context_at','context_same_topic','context_topic_id',
                'content_review_passed','reviewed_summary_digest'}
_REPLY_FIELDS = {'topic_id','sender_user_id','trigger_message_id','conversation_key','text','value',
                 'context_at','context_same_topic','context_topic_id','sender_is_bot','conversation_active',
                 'privacy_permits_reply','moderation_allows_reply','opted_out','sensitive','addressed_to_bot'}


def normalized_preview(kind, candidate):
    """Keep approved Botson text/metadata; never copy raw chat/feed bodies."""
    fields = _NEWS_FIELDS if kind == 'topic_news' else _REPLY_FIELDS
    result = {key:value for key,value in candidate.items() if key in fields}
    if kind == 'topic_news':
        result['source_url'] = _canonical_url(candidate.get('source_url'))
    return result


def _result(reason, candidate=None):
    return {'accepted': candidate is not None, 'reason': reason,
            'preview': candidate, 'can_publish': False}


def _positive(value):
    return type(value) in (int, float) and isfinite(value) and value > 0


def _score(value):
    return type(value) in (int, float) and isfinite(value) and 0 <= value <= 1


def _time(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result if result.tzinfo is not None else None
    except ValueError:
        return None


def _quiet(now, windows):
    if not isinstance(windows, list):
        raise ValueError('quiet hours not configured')
    minute = now.hour * 60 + now.minute
    for window in windows:
        start, end = [datetime.strptime(part, '%H:%M') for part in window]
        lo, hi = start.hour * 60 + start.minute, end.hour * 60 + end.minute
        if (lo <= minute < hi) if lo < hi else (minute >= lo or minute < hi):
            return True
    return False


def _local_now(now, policy):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError('current time needs a timezone')
    name = policy.get('timezone')
    if not isinstance(name, str) or not name:
        raise ValueError('policy timezone not configured')
    return now.astimezone(ZoneInfo(name))


def _current_context(candidate, policy, now, topic):
    at = _time(candidate.get('context_at'))
    age = policy.get('context_max_age_minutes')
    return (_positive(age) and at is not None and
            timedelta(0) <= now-at <= timedelta(minutes=age) and
            candidate.get('context_same_topic') is True and
            type(candidate.get('context_topic_id')) is int and candidate['context_topic_id'] == topic)


def _at_cap(policy, counts, names):
    for name in names:
        cap = policy.get(f'{name}_daily_cap')
        count = counts.get(name)
        if type(cap) is not int or cap < 1 or type(count) is not int or count < 0:
            raise ValueError('rate limits or counts not configured')
        if count >= cap:
            return True
    return False


def _canonical_url(url):
    parts = urlsplit(str(url))
    if parts.scheme not in {'https', 'http'} or not parts.hostname or parts.username or parts.password:
        raise ValueError('invalid source URL')
    port = parts.port  # Invalid/out-of-range ports are not source identities.
    host = parts.hostname.lower().rstrip('.')
    host = '[' + host + ']' if ':' in host else host
    netloc = host if port is None or (parts.scheme, port) in {('https', 443), ('http', 80)} else f'{host}:{port}'
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                             if not k.lower().startswith('utm_')))
    return urlunsplit((parts.scheme, netloc, parts.path or '/', query, ''))


def preview_news(candidate, policy, *, now, verified_topics, seen, counts):
    """Evaluate supplied source evidence and relevance without collecting news."""
    if not isinstance(candidate, dict) or not isinstance(policy, dict):
        return _result('configuration_or_evidence_invalid')
    if policy.get('mode') != 'preview_only':
        return _result('preview_mode_required')
    try:
        local = _local_now(now, policy)
        if policy.get('dedupe_scope') not in {'chat', 'topic'}:
            raise ValueError('dedupe scope not configured')
        if _quiet(local, policy.get('quiet_hours')) or _at_cap(policy, counts, ('global', 'topic')):
            return _result('quiet_hours_or_rate_cap')
        topic = candidate.get('topic_id')
        source_id = candidate.get('source_id')
        if type(topic) is not int or topic < 1 or topic not in verified_topics or not isinstance(source_id, str) or source_id not in (policy.get('topic_sources') or {}).get(topic, []):
            return _result('topic_or_source_not_selected')
        source = (policy.get('sources') or {}).get(source_id)
        if not isinstance(source, dict) or source.get('enabled') is not True:
            return _result('source_not_configured')
        url = _canonical_url(candidate.get('source_url'))
        domains = [domain.lower().rstrip('.') for domain in source.get('allowed_domains', []) if isinstance(domain, str)]
        if urlsplit(url).hostname not in domains:
            return _result('source_domain_mismatch')
        published, checked = _time(candidate.get('published_at')), _time(candidate.get('verified_at'))
        if not published or not checked or candidate.get('source_verified') is not True:
            return _result('source_evidence_missing')
        age, verification_age = policy.get('freshness_hours'), policy.get('verification_max_age_hours')
        if not _positive(age) or not _positive(verification_age):
            raise ValueError('freshness limits not configured')
        if not (timedelta(0) <= now - published <= timedelta(hours=age)):
            return _result('no_fresh_news')
        if not (timedelta(0) <= now - checked <= timedelta(hours=verification_age)):
            return _result('source_verification_stale')
        if checked < published:
            return _result('source_evidence_missing')
        minimum, relevance = policy.get('minimum_relevance'), candidate.get('relevance')
        if not _score(minimum) or not _score(relevance):
            raise ValueError('relevance threshold or verdict missing')
        if relevance < minimum:
            return _result('not_relevant_enough')
        if not _current_context(candidate, policy, now, topic):
            return _result('current_context_evidence_missing')
        event = candidate.get('event_id')
        summary = candidate.get('summary')
        title = candidate.get('title')
        if not isinstance(event, str) or not event.strip() or not isinstance(title, str) or not title.strip() or not isinstance(summary, str) or not summary.strip():
            return _result('article_or_event_identity_missing')
        if (candidate.get('content_review_passed') is not True or
                candidate.get('reviewed_summary_digest') != hashlib.sha256(summary.encode()).hexdigest()):
            return _result('content_review_missing_or_changed')
        if url in seen or event in seen:
            return _result('duplicate_story')
        return _result('moderator_review_required', normalized_preview('topic_news', candidate))
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return _result('configuration_or_evidence_invalid')


def preview_reply(candidate, policy, *, now, verified_topics, counts, last_reply=None):
    """Tentative replies require a value gate, privacy consent and bounded caps."""
    if not isinstance(candidate, dict) or not isinstance(policy, dict):
        return _result('configuration_or_evidence_invalid')
    if policy.get('enabled') is not True or policy.get('mode') != 'preview_only':
        return _result('needs_decision')
    try:
        local = _local_now(now, policy)
        if _quiet(local, policy.get('quiet_hours')):
            return _result('quiet_hours_or_invalid_time')
        if _at_cap(policy, counts, ('global', 'topic', 'thread')):
            return _result('rate_cap')
        cooldown = policy.get('cooldown_minutes')
        if not _positive(cooldown) or policy.get('cooldown_scope') not in {'topic', 'conversation'}:
            raise ValueError('cooldown not configured')
        if last_reply is not None and now - last_reply < timedelta(minutes=cooldown):
            return _result('cooldown')
        topic = candidate.get('topic_id')
        if type(topic) is not int or topic < 1 or topic not in verified_topics or topic not in policy.get('allowed_topics', []):
            return _result('topic_not_selected')
        if (not _current_context(candidate, policy, now, topic) or
                type(candidate.get('sender_user_id')) is not int or candidate['sender_user_id'] < 1 or
                type(candidate.get('trigger_message_id')) is not int or candidate['trigger_message_id'] < 1 or
                not isinstance(candidate.get('conversation_key'), str) or not candidate['conversation_key'].strip()):
            return _result('current_context_identity_missing')
        if (candidate.get('sender_is_bot') is not False or
                candidate.get('context_same_topic') is not True or
                candidate.get('conversation_active') is not True or
                candidate.get('privacy_permits_reply') is not True or
                candidate.get('moderation_allows_reply') is not True or
                candidate.get('opted_out') is not False or
                candidate.get('sensitive') is not False):
            return _result('context_privacy_or_moderation_gate')
        if candidate.get('addressed_to_bot') is not True and policy.get('allow_unsolicited') is not True:
            return _result('unsolicited_reply_not_allowed')
        minimum, value = policy.get('minimum_value'), candidate.get('value')
        if not _score(minimum) or not _score(value):
            raise ValueError('value threshold or verdict missing')
        text = candidate.get('text')
        if value < minimum or not isinstance(text, str) or not text.strip():
            return _result('not_valuable_enough')
        return _result('moderator_review_required', normalized_preview('occasional_replies', candidate))
    except (ValueError, TypeError, OverflowError, ZoneInfoNotFoundError):
        return _result('configuration_or_evidence_invalid')
