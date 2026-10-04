"""Synthetic preview inputs; no live articles, messages, providers or schedulers."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from bot.utils.config import load_yaml
from bot.utils.community_participation import preview_news, preview_reply

NOW = datetime(2026, 1, 5, 12, tzinfo=timezone.utc)


@pytest.fixture
def news():
    policy = {
        'enabled': False, 'mode': 'preview_only',
        'sources': {'fixture': {'allowed_domains': ['example.test']}},
        'topic_sources': {99: ['fixture']}, 'freshness_hours': 24,
        'verification_max_age_hours': 2, 'minimum_relevance': 0.8,
        'global_daily_cap': 4, 'topic_daily_cap': 2, 'quiet_hours': [['23:00', '07:00']],
    }
    item = {'topic_id': 99, 'source_id': 'fixture', 'source_url': 'https://example.test/article?utm_source=fixture',
            'published_at': (NOW-timedelta(hours=3)).isoformat(), 'verified_at': NOW.isoformat(),
            'source_verified': True, 'title': 'Synthetic fixture', 'summary': 'Synthetic summary',
            'event_id': 'fixture-story', 'relevance': 0.9}
    return policy, item


def news_result(policy, item, **overrides):
    return preview_news(item, policy, **{
        'now': NOW, 'verified_topics': {99}, 'seen': set(), 'counts': {'global': 0, 'topic': 0}, **overrides,
    })


def test_news_preview_keeps_source_date_link_and_cannot_publish(news):
    policy, item = news
    original = deepcopy(item)
    result = news_result(policy, item)
    assert result['accepted'] is True and result['can_publish'] is False
    assert result['preview']['published_at'] == item['published_at']
    assert result['preview']['source_url'] == 'https://example.test/article'
    assert item == original


@pytest.mark.parametrize('field,value,reason', [
    ('source_verified', False, 'source_evidence_missing'),
    ('published_at', '2020-01-01T00:00:00Z', 'no_fresh_news'),
    ('published_at', '2027-01-01T00:00:00Z', 'no_fresh_news'),
    ('published_at', '2026-01-05T10:00:00', 'source_evidence_missing'),
    ('verified_at', '2020-01-01T00:00:00Z', 'source_verification_stale'),
    ('relevance', 0.1, 'not_relevant_enough'),
    ('source_url', 'https://other.test/article', 'source_domain_mismatch'),
    ('source_id', 'not-selected', 'topic_or_source_not_selected'),
    ('topic_id', 100, 'topic_or_source_not_selected'),
])
def test_news_rejects_unverified_stale_irrelevant_or_unroutable_candidates(news, field, value, reason):
    policy, item = news
    item[field] = value
    result = news_result(policy, item)
    assert result['accepted'] is False and result['reason'] == reason
    assert result['can_publish'] is False


@pytest.mark.parametrize('seen', [{'fixture-story'}, {'https://example.test/article'}])
def test_news_deduplicates_event_and_canonical_source_link(news, seen):
    assert news_result(*news, seen=seen)['reason'] == 'duplicate_story'


def test_news_caps_and_quiet_hours_are_enforced(news):
    assert news_result(*news, counts={'global': 4, 'topic': 0})['accepted'] is False
    assert news_result(*news, now=NOW.replace(hour=2))['accepted'] is False


def test_default_news_and_reply_policies_cannot_enable_communications(news):
    config = load_yaml('community_participation.yaml')
    assert config['topic_news']['enabled'] is False
    assert config['occasional_replies']['enabled'] is False
    assert news_result(config['topic_news'], news[1])['accepted'] is False
    result = preview_reply({}, config['occasional_replies'], now=NOW, verified_topics={99}, counts={})
    assert result['reason'] == 'needs_decision' and result['can_publish'] is False


@pytest.fixture
def reply():
    policy = {'enabled': True, 'mode': 'preview_only', 'allowed_topics': [99],
              'allow_unsolicited': False, 'minimum_value': 0.8,
              'global_daily_cap': 4, 'topic_daily_cap': 2, 'thread_daily_cap': 1,
              'cooldown_minutes': 30, 'quiet_hours': [['23:00', '07:00']]}
    candidate = {'topic_id': 99, 'sender_is_bot': False, 'context_same_topic': True,
                 'conversation_active': True, 'privacy_permits_reply': True,
                 'moderation_allows_reply': True, 'opted_out': False, 'sensitive': False,
                 'addressed_to_bot': True, 'value': 0.9, 'text': 'Synthetic response'}
    return policy, candidate


def reply_result(policy, item, **overrides):
    return preview_reply(item, policy, **{
        'now': NOW, 'verified_topics': {99}, 'counts': {'global': 0, 'topic': 0, 'thread': 0}, **overrides,
    })


def test_even_eligible_reply_is_only_a_moderator_preview(reply):
    result = reply_result(*reply)
    assert result['accepted'] is True and result['can_publish'] is False


@pytest.mark.parametrize('field,value', [
    ('sender_is_bot', True), ('context_same_topic', False), ('conversation_active', False),
    ('privacy_permits_reply', False), ('moderation_allows_reply', False),
    ('opted_out', True), ('sensitive', True), ('addressed_to_bot', False), ('value', 0.1),
])
def test_reply_privacy_moderation_value_and_loop_gates(reply, field, value):
    policy, item = reply
    item[field] = value
    assert reply_result(policy, item)['accepted'] is False


def test_reply_stops_at_thread_cap_cooldown_and_quiet_hours(reply):
    assert reply_result(*reply, counts={'global': 0, 'topic': 0, 'thread': 1})['accepted'] is False
    assert reply_result(*reply, last_reply=NOW-timedelta(minutes=5))['accepted'] is False
    assert reply_result(*reply, now=NOW.replace(hour=2))['accepted'] is False
