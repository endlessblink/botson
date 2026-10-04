"""Offline preview policies; no fetching, LLM, scheduler registration or send API.

News input must come from a separately verified source adapter. This module
checks that adapter's evidence; it does not itself verify a live article.
Reply policy is for eventual integration with the existing mentions/replies
handler, not a second autonomous conversational agent.
"""

from datetime import datetime, timedelta
from math import isfinite
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


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
    query = urlencode(sorted((k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith('utm_')))
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, query, ''))


def preview_news(candidate, policy, *, now, verified_topics, seen, counts):
    """Evaluate supplied source evidence and relevance without collecting news."""
    try:
        if now.tzinfo is None:
            raise ValueError('current time needs a timezone')
        if _quiet(now, policy.get('quiet_hours')) or _at_cap(policy, counts, ('global', 'topic')):
            return _result('quiet_hours_or_rate_cap')
        topic = candidate.get('topic_id')
        source_id = candidate.get('source_id')
        if topic not in verified_topics or source_id not in (policy.get('topic_sources') or {}).get(topic, []):
            return _result('topic_or_source_not_selected')
        source = (policy.get('sources') or {}).get(source_id)
        if not source:
            return _result('source_not_configured')
        url = _canonical_url(candidate.get('source_url'))
        if urlsplit(url).hostname not in source.get('allowed_domains', []):
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
        minimum, relevance = policy.get('minimum_relevance'), candidate.get('relevance')
        if not _score(minimum) or not _score(relevance):
            raise ValueError('relevance threshold or verdict missing')
        if relevance < minimum:
            return _result('not_relevant_enough')
        event = candidate.get('event_id')
        if not event or not candidate.get('title') or not candidate.get('summary'):
            return _result('article_or_event_identity_missing')
        if url in seen or event in seen:
            return _result('duplicate_story')
        return _result('moderator_review_required', {**candidate, 'source_url': url})
    except (ValueError, TypeError, OverflowError):
        return _result('configuration_or_evidence_invalid')


def preview_reply(candidate, policy, *, now, verified_topics, counts, last_reply=None):
    """Tentative replies require a value gate, privacy consent and bounded caps."""
    if policy.get('enabled') is not True or policy.get('mode') != 'preview_only':
        return _result('needs_decision')
    try:
        if now.tzinfo is None or _quiet(now, policy.get('quiet_hours')):
            return _result('quiet_hours_or_invalid_time')
        if _at_cap(policy, counts, ('global', 'topic', 'thread')):
            return _result('rate_cap')
        cooldown = policy.get('cooldown_minutes')
        if not _positive(cooldown):
            raise ValueError('cooldown not configured')
        if last_reply is not None and now - last_reply < timedelta(minutes=cooldown):
            return _result('cooldown')
        topic = candidate.get('topic_id')
        if topic not in verified_topics or topic not in policy.get('allowed_topics', []):
            return _result('topic_not_selected')
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
        if value < minimum or not candidate.get('text'):
            return _result('not_valuable_enough')
        return _result('moderator_review_required', dict(candidate))
    except (ValueError, TypeError, OverflowError):
        return _result('configuration_or_evidence_invalid')
