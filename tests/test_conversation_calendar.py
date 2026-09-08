import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers import calendar
from test_calendar_scheduled_games import FakeScheduledDb, _base_row


class ConversationCalendarTests(unittest.IsolatedAsyncioTestCase):
    async def dispatch(self, kind, verdict, text='concrete question', freshness=None):
        row = _base_row(kind)
        row['text'] = text
        db = FakeScheduledDb(row)
        context = SimpleNamespace(bot_data={'db': db}, bot=AsyncMock())
        send = AsyncMock(return_value=SimpleNamespace(message_id=456))
        raw = json.dumps({'pass': verdict is True, 'reason': 'semantic verdict', **dict.fromkeys(
            ('specificity', 'naturalness', 'novelty', 'channel_fit', 'answerability', 'payoff'), 4)})
        generate = AsyncMock(return_value=raw)
        if isinstance(verdict, Exception):
            generate.side_effect = verdict
        with patch.dict('os.environ', {'TEST_GROUP_ID': '123', 'BOT_TOKEN': '123:test'}), \
             patch('bot.utils.conversation_quality.load_yaml', return_value={
                 'reviewer_prompt': 'Review conversation quality.',
                 'candidate_prompt': '{category}\n{recent_texts}\n{text}',
                 'minimum_score': 4, 'score_min': 1, 'score_max': 5,
             }), \
             patch('bot.scheduler.materializer._used_texts_for_type', new=AsyncMock(return_value=['previous question'])) as history, \
             patch('bot.scheduler.materializer._generate_with_claude', new=generate), \
             patch('bot.utils.freshness.freshness_rejection', return_value=freshness), \
             patch.object(calendar, 'send_message_with_optional_cover', new=send), \
             patch('bot.handlers.dm_menu.notify_opted_in_users', new=AsyncMock()):
            await calendar.check_and_send_due_messages(context)
        return db, send, generate, history

    async def test_rejected_queued_conversations_skip_without_send(self):
        for kind in ('morning', 'evening', 'discussion'):
            with self.subTest(kind=kind):
                db, send, _, _ = await self.dispatch(kind, False)
                send.assert_not_awaited()
                self.assertFalse(db.sent)
                self.assertTrue(db.skipped)
                self.assertIn('conversation_semantic', db.skipped[0][1])

    async def test_accepted_text_is_sent_unchanged_with_sent_only_history(self):
        for kind in ('morning', 'evening', 'discussion'):
            db, send, _, history = await self.dispatch(kind, True, 'specific approved text')
            self.assertEqual(send.call_args.kwargs['text'], 'specific approved text')
            self.assertEqual(db.sent, [(123, 456)])
            self.assertTrue(history.call_args.kwargs['sent_only'])

    async def test_provider_unavailable_skips_without_send(self):
        db, send, _, _ = await self.dispatch('morning', RuntimeError('offline'))
        send.assert_not_awaited()
        self.assertTrue(db.skipped)

    async def test_freshness_rejection_prevents_review_and_send(self):
        db, send, generate, _ = await self.dispatch('evening', True, freshness='duplicate')
        send.assert_not_awaited()
        generate.assert_not_awaited()
        self.assertIn('conversation_freshness', db.skipped[0][1])

    async def test_custom_messages_are_outside_conversation_gate(self):
        db, send, generate, history = await self.dispatch('custom', False)
        send.assert_awaited_once()
        generate.assert_not_awaited()
        history.assert_not_awaited()
