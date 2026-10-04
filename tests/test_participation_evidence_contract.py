"""Fail-closed configuration/evidence edges for the disabled policy modules."""
import hashlib
from datetime import timedelta

import pytest

from bot.utils.community_participation import _canonical_url
from test_community_participation_policy import NOW, news, reply, news_result, reply_result


@pytest.mark.parametrize('field,value', [('timezone',None),('timezone','invalid/timezone'),
    ('dedupe_scope',None),('mode','auto_send')])
def test_news_requires_explicit_supported_policy(news,field,value):
    policy,item=news
    policy[field]=value
    result=news_result(policy,item)
    assert not result['accepted'] and not result['can_publish']


def test_disabled_source_cannot_be_selected(news):
    policy,item=news
    policy['sources']['fixture']['enabled']=False
    assert news_result(policy,item)['reason']=='source_not_configured'


@pytest.mark.parametrize('field,value,reason', [
    ('context_at',(NOW-timedelta(hours=1)).isoformat(),'current_context_evidence_missing'),
    ('context_at',(NOW+timedelta(minutes=1)).isoformat(),'current_context_evidence_missing'),
    ('context_topic_id',100,'current_context_evidence_missing'),
    ('context_same_topic',False,'current_context_evidence_missing'),
    ('content_review_passed',False,'content_review_missing_or_changed'),
    ('summary','Changed after review','content_review_missing_or_changed'),
])
def test_news_context_and_review_bind_the_actual_summary(news,field,value,reason):
    policy,item=news
    item[field]=value
    assert news_result(policy,item)['reason']==reason


def test_quiet_hours_use_configured_wall_clock(news,reply):
    for policy,item,call in [(*news,news_result),(*reply,reply_result)]:
        policy.update(timezone='Asia/Jerusalem',quiet_hours=[['13:00','15:00']])
        assert not call(policy,item)['accepted']  # 12:00 UTC is 14:00 locally.


@pytest.mark.parametrize('field,value', [('context_at',(NOW-timedelta(hours=1)).isoformat()),
    ('context_at',(NOW+timedelta(minutes=1)).isoformat()),('context_topic_id',100),
    ('trigger_message_id',None),('sender_user_id',True),('conversation_key','')])
def test_reply_requires_current_same_topic_immutable_trigger(reply,field,value):
    policy,item=reply
    item[field]=value
    assert reply_result(policy,item)['reason']=='current_context_identity_missing'


def test_canonical_links_preserve_blank_query_and_normalize_default_ports():
    assert _canonical_url('https://EXAMPLE.test:443/story?a=&utm_source=x#one')=='https://example.test/story?a='
    assert _canonical_url('https://example.test/story')!='https://example.test/story?a='
    with pytest.raises(ValueError):
        _canonical_url('https://example.test:invalid/story')
