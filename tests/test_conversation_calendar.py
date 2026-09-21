import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import calendar
from test_calendar_scheduled_games import FakeScheduledDb, _base_row


class ConversationCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, kind, verdict, text='concrete question', freshness=None, replacement=None, created_by='dashboard'):
        row = _base_row(kind)
        row['text'] = text
        row['created_by'] = created_by
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={'db': db}, bot=AsyncMock())
        send = AsyncMock(return_value=SimpleNamespace(message_id=456))
        raw = json.dumps({'pass': verdict is True, 'reason': 'semantic verdict', **dict.fromkeys(
            ('specificity', 'naturalness', 'novelty', 'channel_fit', 'answerability', 'payoff'), 4)})
        generate = AsyncMock(return_value=raw)
        if isinstance(verdict, Exception):
            generate.side_effect = verdict
        regenerate = AsyncMock(return_value=replacement)
        with patch.dict('os.environ', {'TEST_GROUP_ID': '123', 'BOT_TOKEN': '123:test'}), \
             patch('bot.utils.conversation_quality.load_yaml', return_value={
                 'reviewer_prompt': 'Review conversation quality.',
                 'candidate_prompt': '{category}\n{recent_texts}\n{text}',
                 'minimum_score': 4, 'score_min': 1, 'score_max': 5,
             }), \
             patch('bot.scheduler.materializer._used_texts_for_type', new=AsyncMock(return_value=['previous question'])) as history, \
             patch('bot.scheduler.materializer._generate_with_claude', new=generate), \
             patch('bot.scheduler.materializer.regenerate_slot_text', new=regenerate), \
             patch('bot.utils.freshness.freshness_rejection', return_value=freshness), \
             patch.object(calendar, 'send_message_with_optional_cover', new=send), \
             patch.object(calendar, 'notify_admins', new=AsyncMock(return_value=0)), \
             patch('bot.handlers.dm_menu.notify_opted_in_users', new=AsyncMock()):
            await calendar.check_and_send_due_messages(context)
        return db, send, generate, history, regenerate

    async def test_rejected_stored_slot_is_skipped_without_regeneration(self):
        for kind in ('morning', 'evening', 'discussion'):
            with self.subTest(kind=kind):
                db, send, _, _, regenerate = await self.dispatch(
                    kind, False, replacement='fresh replacement text',
                )
                send.assert_not_awaited()
                self.assertFalse(db.sent)
                self.assertTrue(db.skipped)
                self.assertIn('conversation_regeneration_failed', db.skipped[0][1])
                regenerate.assert_not_awaited()

    async def test_regeneration_failure_skips_and_alerts(self):
        for kind in ('morning', 'evening', 'discussion'):
            with self.subTest(kind=kind):
                db, send, _, _, regenerate = await self.dispatch(kind, False, replacement=None)
                send.assert_not_awaited()
                self.assertFalse(db.sent)
                self.assertTrue(db.skipped)
                self.assertIn('conversation_regeneration_failed', db.skipped[0][1])
                self.assertIn('conversation_semantic', db.skipped[0][1])
                regenerate.assert_not_awaited()

    async def test_accepted_text_is_sent_unchanged_with_sent_only_history(self):
        for kind in ('morning', 'evening', 'discussion'):
            db, send, _, history, regenerate = await self.dispatch(kind, True, 'specific approved text')
            self.assertEqual(send.call_args.kwargs['text'], 'specific approved text')
            self.assertEqual(db.sent, [(123, 456)])
            self.assertTrue(history.call_args.kwargs['sent_only'])
            regenerate.assert_not_awaited()

    async def test_generated_conversation_row_is_never_sent(self):
        db, send, generate, history, regenerate = await self.dispatch(
            'morning', True, created_by='ai-fill-today',
        )
        send.assert_not_awaited()
        self.assertFalse(db.sent)
        self.assertTrue(db.skipped)
        self.assertIn('conversation_not_scheduler_authored', db.skipped[0][1])
        generate.assert_not_awaited()
        history.assert_not_awaited()
        regenerate.assert_not_awaited()

    async def test_provider_unavailable_skips_without_send(self):
        db, send, _, _, regenerate = await self.dispatch('morning', RuntimeError('offline'))
        send.assert_not_awaited()
        self.assertTrue(db.skipped)
        regenerate.assert_not_awaited()

    async def test_freshness_rejection_prevents_review_and_send(self):
        db, send, generate, _, regenerate = await self.dispatch(
            'evening', True, freshness='duplicate', replacement=None,
        )
        send.assert_not_awaited()
        generate.assert_not_awaited()
        self.assertIn('conversation_freshness', db.skipped[0][1])
        regenerate.assert_not_awaited()

    async def test_custom_messages_are_outside_conversation_gate(self):
        db, send, generate, history, regenerate = await self.dispatch('custom', False)
        send.assert_awaited_once()
        generate.assert_not_awaited()
        history.assert_not_awaited()
        regenerate.assert_not_awaited()
