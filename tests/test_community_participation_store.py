"""Durable offline review bookkeeping; synthetic identities and no dispatch."""
import asyncio
import json
from copy import deepcopy
from datetime import timedelta

import aiosqlite
import pytest

from bot.utils.community_participation_store import ParticipationReviewStore, payload_digest
from test_community_participation_policy import NOW, news, reply


async def create_store(path):
    store = ParticipationReviewStore(path)
    await store.initialize()
    return store


async def rows(path, table='participation_review_items'):
    assert table in {'participation_review_items', 'participation_optouts'}
    async with aiosqlite.connect(str(path)) as db:
        async with db.execute('SELECT * FROM '+table) as cursor:
            return await cursor.fetchall()


async def reserve(store, kind, policy, candidate, key='fixture', at=NOW, chat=1):
    result = await store.preview(kind, candidate, policy, now=at, chat_id=chat, verified_topics={99,100})
    assert result['accepted'], result
    return await store.reserve_approved(kind, candidate, policy,
        approved_digest=payload_digest(result['preview']), idempotency_key=key,
        now=at, chat_id=chat, verified_topics={99,100})


def story(item, identity, topic=99):
    return {**item,'topic_id':topic,'context_topic_id':topic,'source_url':f'https://example.test/{identity}',
            'event_id':identity}


def test_preview_never_claims_or_writes(news, tmp_path):
    policy,item = news
    path = tmp_path/'readonly.db'
    async def scenario():
        store = await create_store(path)
        for _ in range(3):
            result = await store.preview('topic_news',item,policy,now=NOW,chat_id=1,verified_topics={99})
            assert result['accepted'] and not result['can_publish']
        assert not await rows(path)
        assert not await rows(path,'participation_optouts')
    asyncio.run(scenario())


def test_missing_bookkeeping_is_visible_and_never_creates_a_file(news, tmp_path):
    policy,item=news
    path=tmp_path/'missing.db'
    async def scenario():
        result=await ParticipationReviewStore(path).preview('topic_news',item,policy,now=NOW,chat_id=1,verified_topics={99})
        assert result['reason']=='bookkeeping_unavailable' and not result['can_publish']
        assert not path.exists()
    asyncio.run(scenario())


@pytest.mark.parametrize('kind,fixture_name', [('topic_news','news'),('occasional_replies','reply')])
def test_preview_and_snapshot_exclude_raw_context(request,tmp_path,kind,fixture_name):
    policy,item=request.getfixturevalue(fixture_name)
    item['raw_context']='Synthetic unapproved conversation body'
    item['raw_feed']='Synthetic raw article body'
    path=tmp_path/'minimal.db'
    async def scenario():
        store=await create_store(path)
        preview=await store.preview(kind,item,policy,now=NOW,chat_id=1,verified_topics={99})
        assert preview['accepted'] and 'raw_context' not in preview['preview'] and 'raw_feed' not in preview['preview']
        result=await reserve(store,kind,policy,item)
        saved=json.loads((await rows(path))[0][8])
        assert result['reserved'] and 'raw_context' not in saved and 'raw_feed' not in saved
    asyncio.run(scenario())


def test_approval_snapshot_and_idempotency_bind_route_and_text(news, tmp_path):
    policy,item = news
    path=tmp_path/'approval.db'
    async def scenario():
        store=await create_store(path)
        first=await reserve(store,'topic_news',policy,item)
        preview=await store.preview('topic_news',item,policy,now=NOW,chat_id=1,verified_topics={99})
        assert preview['reason']=='duplicate_story'
        digest=payload_digest({**item,'source_url':'https://example.test/article'})
        replay=await store.reserve_approved('topic_news',item,policy,approved_digest=digest,
            idempotency_key='fixture',now=NOW,chat_id=1,verified_topics={99})
        changed=await store.reserve_approved('topic_news',{**item,'title':'Changed synthetic headline'},policy,
            approved_digest=digest,idempotency_key='another',now=NOW,chat_id=1,verified_topics={99})
        cross_chat=await store.reserve_approved('topic_news',item,policy,approved_digest=digest,
            idempotency_key='fixture',now=NOW,chat_id=2,verified_topics={99})
        assert first['reserved'] and replay['replay'] and not replay['can_publish']
        assert changed['reason']=='approved_payload_changed'
        assert cross_chat['reason']=='idempotency_conflict'
        assert len(await rows(path))==1
    asyncio.run(scenario())


def test_distinct_events_compete_atomically_for_global_budget(news, tmp_path):
    policy,item=news
    policy['global_daily_cap']=2
    policy['topic_daily_cap']=2
    path=tmp_path/'concurrent.db'
    async def scenario():
        await create_store(path)
        candidates=[story(item,f'concurrent-{index}') for index in range(6)]
        # All six moderator previews occur before any approval reservation.
        previews=[await ParticipationReviewStore(path).preview('topic_news',candidate,policy,
                  now=NOW,chat_id=1,verified_topics={99}) for candidate in candidates]
        assert all(result['accepted'] for result in previews)
        results=await asyncio.gather(*[
            ParticipationReviewStore(path).reserve_approved('topic_news',candidate,policy,
                approved_digest=payload_digest(preview['preview']),idempotency_key=f'key-{index}',
                now=NOW,chat_id=1,verified_topics={99})
            for index,(candidate,preview) in enumerate(zip(candidates,previews))])
        assert sum(result['reserved'] for result in results)==2
        assert len(await rows(path))==2
        assert all(not result['can_publish'] for result in results)
    asyncio.run(scenario())


def test_event_and_canonical_url_dedupe_survive_reopening(news, tmp_path):
    policy,item=news
    policy['sources']['second']={'enabled':True,'allowed_domains':['second.test']}
    policy['topic_sources'][99].append('second')
    path=tmp_path/'durable.db'
    async def scenario():
        store=await create_store(path)
        await reserve(store,'topic_news',policy,item)
        reopened=ParticipationReviewStore(path)
        event_result=await reopened.preview('topic_news',
            {**item,'source_id':'second','source_url':'https://second.test/article'},policy,
            now=NOW,chat_id=1,verified_topics={99})
        url_result=await reopened.preview('topic_news',
            {**item,'event_id':'another-event','source_url':'https://EXAMPLE.test:443/article#section'},policy,
            now=NOW,chat_id=1,verified_topics={99})
        assert event_result['reason']==url_result['reason']=='duplicate_story'
    asyncio.run(scenario())


def test_explicit_dedupe_scope_and_topic_limits(news, tmp_path):
    policy,item=news
    policy['topic_sources'][100]=['fixture']
    policy['topic_daily_cap']=1
    path=tmp_path/'scope.db'
    async def scenario():
        store=await create_store(path)
        await reserve(store,'topic_news',policy,item)
        same=await store.preview('topic_news',story(item,'different'),policy,now=NOW,chat_id=1,verified_topics={99})
        across={**item,'topic_id':100,'context_topic_id':100}
        global_duplicate=await store.preview('topic_news',across,policy,now=NOW,chat_id=1,verified_topics={100})
        per_topic=await store.preview('topic_news',across,{**policy,'dedupe_scope':'topic'},now=NOW,chat_id=1,verified_topics={100})
        assert same['reason']=='quiet_hours_or_rate_cap'
        assert global_duplicate['reason']=='duplicate_story'
        assert per_topic['accepted']
    asyncio.run(scenario())


def test_uncertain_delivery_holds_budget_and_cannot_be_cancelled(news, tmp_path):
    policy,item=news
    policy['global_daily_cap']=1
    path=tmp_path/'uncertain.db'
    async def scenario():
        store=await create_store(path)
        await reserve(store,'topic_news',policy,item)
        await store.transition('fixture',expected_status='reserved',status='scheduled',at=NOW)
        uncertain=await store.transition('fixture',expected_status='scheduled',status='uncertain',at=NOW)
        cancelled=await store.transition('fixture',expected_status='uncertain',status='cancelled',at=NOW)
        result=await ParticipationReviewStore(path).preview('topic_news',story(item,'next'),policy,
            now=NOW,chat_id=1,verified_topics={99})
        assert uncertain['status']=='uncertain' and cancelled['reason']=='state_conflict'
        assert result['reason']=='quiet_hours_or_rate_cap'
        assert (await rows(path))[0][9]=='uncertain'
    asyncio.run(scenario())


def test_cancelled_review_releases_budget_and_sent_is_terminal(news, tmp_path):
    policy,item=news
    policy['global_daily_cap']=1
    path=tmp_path/'lifecycle.db'
    async def scenario():
        store=await create_store(path)
        await reserve(store,'topic_news',policy,item)
        await store.transition('fixture',expected_status='reserved',status='cancelled',at=NOW)
        await reserve(store,'topic_news',policy,item,key='fresh-approval')
        await store.transition('fresh-approval',expected_status='reserved',status='scheduled',at=NOW)
        missing=await store.transition('fresh-approval',expected_status='scheduled',status='sent',at=NOW)
        sent=await store.transition('fresh-approval',expected_status='scheduled',status='sent',at=NOW,external_message_id=777)
        undo=await store.transition('fresh-approval',expected_status='sent',status='cancelled',at=NOW)
        assert missing['reason']=='delivery_evidence_missing'
        assert sent['status']=='sent' and undo['reason']=='state_conflict'
        assert len(await rows(path))==2
    asyncio.run(scenario())


def test_day_counters_follow_explicit_timezone_even_after_change(news, tmp_path):
    policy,item=news
    policy.update(timezone='Asia/Jerusalem',quiet_hours=[],global_daily_cap=1)
    at=NOW.replace(hour=22,minute=30)
    item.update(verified_at=at.isoformat(),context_at=at.isoformat(),published_at=(at-timedelta(hours=1)).isoformat())
    path=tmp_path/'timezone.db'
    async def scenario():
        store=await create_store(path)
        await reserve(store,'topic_news',policy,item,at=at)
        new=story(item,'timezone-new')
        same_local_day=await store.preview('topic_news',new,policy,now=at+timedelta(minutes=30),chat_id=1,verified_topics={99})
        changed_zone=await store.preview('topic_news',new,{**policy,'timezone':'UTC'},now=at+timedelta(minutes=30),chat_id=1,verified_topics={99})
        assert same_local_day['reason']==changed_zone['reason']=='quiet_hours_or_rate_cap'
        assert (await rows(path))[0][11]=='2026-01-06'
    asyncio.run(scenario())


def test_persisted_own_id_and_topic_optouts_cannot_be_overridden_by_candidate(reply, tmp_path):
    policy,item=reply
    path=tmp_path/'optouts.db'
    async def scenario():
        store=await create_store(path)
        with pytest.raises(ValueError,match='own-ID'):
            await store.set_member_opt_out(1,101,actor_user_id=102,opted_out=True,at=NOW)
        assert not await rows(path,'participation_optouts')
        await store.set_member_opt_out(1,101,actor_user_id=101,opted_out=True,at=NOW)
        result=await ParticipationReviewStore(path).preview('occasional_replies',item,policy,now=NOW,chat_id=1,verified_topics={99})
        assert result['reason']=='persisted_opt_out'
        await store.set_member_opt_out(1,101,actor_user_id=101,opted_out=False,at=NOW)
        await store.set_topic_opt_out('occasional_replies',1,99,opted_out=True,at=NOW)
        result=await store.preview('occasional_replies',item,policy,now=NOW,chat_id=1,verified_topics={99})
        assert result['reason']=='persisted_opt_out' and not await rows(path)
    asyncio.run(scenario())


def test_reply_duplicate_and_cooldown_survive_day_boundary(reply, tmp_path):
    policy,item=reply
    policy.update(quiet_hours=[],global_daily_cap=8,topic_daily_cap=8,thread_daily_cap=8)
    at=NOW.replace(hour=23,minute=55)
    item['context_at']=at.isoformat()
    path=tmp_path/'reply-boundary.db'
    async def scenario():
        store=await create_store(path)
        await reserve(store,'occasional_replies',policy,item,at=at)
        reopened=ParticipationReviewStore(path)
        duplicate=await reopened.preview('occasional_replies',item,policy,now=at,chat_id=1,verified_topics={99})
        later={**item,'trigger_message_id':203}
        cooldown=await reopened.preview('occasional_replies',later,policy,now=at+timedelta(minutes=10),chat_id=1,verified_topics={99})
        other={**later,'conversation_key':'another-thread'}
        separate=await reopened.preview('occasional_replies',other,policy,now=at+timedelta(minutes=10),chat_id=1,verified_topics={99})
        topic_cooldown=await reopened.preview('occasional_replies',other,{**policy,'cooldown_scope':'topic'},now=at+timedelta(minutes=10),chat_id=1,verified_topics={99})
        assert duplicate['reason']=='duplicate_reply' and cooldown['reason']=='cooldown'
        assert separate['accepted'] and topic_cooldown['reason']=='cooldown'
    asyncio.run(scenario())
