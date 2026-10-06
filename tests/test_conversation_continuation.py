"""Verified short exchanges never relax unsolicited/cap/identity gates."""
import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_participation_runtime import config, integrated, NOW, runtime
from bot.utils.participation_config import validate_config


def addressed(db, bot, *, message_id=5, parent=None, user_id=123):
    message=SimpleNamespace(message_id=message_id,message_thread_id=99,
        text='@fixture_bot אפשר להסביר?',reply_to_message=parent,date=NOW)
    update=SimpleNamespace(effective_message=message,effective_user=SimpleNamespace(id=user_id,is_bot=False),
                           effective_chat=SimpleNamespace(id=-10099))
    return update,SimpleNamespace(bot=bot,bot_data={'db':db})


def responses(count):
    result=[]
    for _ in range(count):
        result.extend([json.dumps({'decision':'reply','text':'האפשרות הזאת נמצאת בתפריט ההגדרות.','sensitive':False}),
                       json.dumps({'pass':True,'sensitive':False,'value':0.95})])
    return result


def conversational(config):
    config['occasional_replies'].update(global_daily_cap=6,topic_daily_cap=3,thread_daily_cap=3,
        conversation_turn_limit=3,followup_max_age_minutes=10)


def test_short_same_person_exchange_uses_confirmed_parent_and_stable_thread(config,integrated):
    ledger,db,bot,_,send=integrated; conversational(config)
    runtime.generate.side_effect=responses(3)
    send.side_effect=[SimpleNamespace(message_id=n) for n in (101,102,103)]
    async def scenario():
        update,ctx=addressed(db,bot); await runtime.handle_reply(update,ctx)
        for incoming,parent_id in ((6,101),(7,102)):
            parent=SimpleNamespace(message_id=parent_id,from_user=SimpleNamespace(id=bot.id))
            update,ctx=addressed(db,bot,message_id=incoming,parent=parent)
            await runtime.handle_reply(update,ctx)
        assert send.await_count==3
        parent=SimpleNamespace(message_id=103,from_user=SimpleNamespace(id=bot.id))
        update,ctx=addressed(db,bot,message_id=8,parent=parent)
        await runtime.handle_reply(update,ctx)
        assert send.await_count==3
        async with ledger._connect() as connection:
            rows=await connection.execute_fetchall('SELECT payload_json FROM participation_review_items ORDER BY intended_at,idempotency_key')
        payloads=[json.loads(row[0]) for row in rows]
        assert {p['conversation_key'] for p in payloads}=={'5'}
        assert [p.get('turn_number',1) for p in payloads]==[1,2,3]
    asyncio.run(scenario())
    assert runtime.generate.await_count==6


@pytest.mark.parametrize('change',['other_person','stale','uncertain','opt_out'])
def test_followup_never_bypasses_changed_parent_or_opt_out(config,integrated,change):
    ledger,db,bot,_,send=integrated; conversational(config)
    runtime.generate.side_effect=responses(1)
    async def scenario():
        update,ctx=addressed(db,bot); await runtime.handle_reply(update,ctx)
        if change in {'stale','uncertain'}:
            async with ledger._connect(write=True) as connection:
                if change=='stale': await connection.execute('UPDATE participation_review_items SET updated_at=?',((NOW-timedelta(minutes=11)).isoformat(),))
                else: await connection.execute("UPDATE participation_review_items SET status='uncertain'")
                await connection.commit()
        if change=='opt_out': await ledger.set_member_opt_out(-10099,123,actor_user_id=123,opted_out=True,at=NOW)
        parent=SimpleNamespace(message_id=101,from_user=SimpleNamespace(id=bot.id))
        update,ctx=addressed(db,bot,message_id=6,parent=parent,user_id=124 if change=='other_person' else 123)
        runtime.generate.reset_mock();send.reset_mock()
        await runtime.handle_reply(update,ctx)
    asyncio.run(scenario());runtime.generate.assert_not_called();send.assert_not_called()


def test_manufactured_parent_cannot_suppress_cooldown(config,integrated):
    ledger,_,_,_,_=integrated;conversational(config)
    candidate={'topic_id':99,'sender_user_id':123,'conversation_key':'invented',
               'parent_message_id':999,'turn_number':2}
    assert asyncio.run(ledger.available('occasional_replies',candidate,config['occasional_replies'],now=NOW,chat_id=-10099)) is False


@pytest.mark.parametrize('field,value',[('conversation_turn_limit',0),('conversation_turn_limit',True),
                                      ('conversation_turn_limit',4),('followup_max_age_minutes',0)])
def test_conversation_configuration_has_explicit_bounds(config,field,value):
    conversational(config);config['occasional_replies'][field]=value
    with pytest.raises(ValueError):validate_config(config,{99})
