"""Durable participation review, caps and opt-outs; no direct external sends.

Initialization and approved reservations are explicit separate operations.
Preview reads alone never record a draft, consume a cap, or change opt-outs.
Source/context/relevance evidence must come from trusted server adapters.
"""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import aiosqlite

from .community_participation import _local_now, normalized_preview, preview_news, preview_reply


KINDS = {'topic_news', 'occasional_replies'}
HELD = ('reserved', 'scheduled', 'sent', 'uncertain')
SCHEMA = """
CREATE TABLE IF NOT EXISTS participation_review_items (
    idempotency_key TEXT PRIMARY KEY,
    payload_digest TEXT NOT NULL,
    kind TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    topic_id INTEGER NOT NULL,
    conversation_key TEXT,
    source_url TEXT,
    event_id TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('reserved','scheduled','sent','uncertain','cancelled')),
    intended_at TEXT NOT NULL,
    day_key TEXT NOT NULL,
    timezone TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    external_message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_participation_counts
ON participation_review_items(kind,chat_id,day_key,topic_id,status);
CREATE INDEX IF NOT EXISTS idx_participation_identity
ON participation_review_items(kind,chat_id,event_id,source_url,status);
CREATE TABLE IF NOT EXISTS participation_optouts (
    kind TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    subject_kind TEXT NOT NULL CHECK(subject_kind IN ('topic','member')),
    subject_id INTEGER NOT NULL,
    opted_out INTEGER NOT NULL CHECK(opted_out IN (0,1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY(kind,chat_id,subject_kind,subject_id)
);
"""


def payload_digest(payload):
    """Bind approval to the complete normalized preview, including destination."""
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _scope(kind, chat_id, topic_id):
    if kind not in KINDS or type(chat_id) is not int or chat_id == 0:
        raise ValueError('invalid participation scope')
    if type(topic_id) is not int or topic_id < 1:
        raise ValueError('invalid topic identity')


def _utc(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError('aware timestamp required')
    return value.astimezone(timezone.utc).isoformat()


def _receipt(reason, *, key=None, status=None, replay=False):
    return {'reserved': status in HELD, 'reason': reason, 'idempotency_key': key,
            'status': status, 'replay': replay, 'can_publish': False}


class ParticipationReviewStore:
    """Independent explicitly initialized store, never the community database."""

    def __init__(self, db_path):
        if str(db_path) == ':memory:':
            raise ValueError('a file is required for independent durable connections')
        self.path = Path(db_path).resolve()

    async def initialize(self):
        """Explicit setup after validated activation; previews never initialize."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(str(self.path)) as connection:
            await connection.executescript(SCHEMA)
            await connection.commit()

    def _connect(self, *, write=False):
        # mode=ro/rw prevents an accidental preview against a missing path
        # from creating an empty file or implicitly initializing a live DB.
        mode = 'rw' if write else 'ro'
        return aiosqlite.connect('file:'+quote(str(self.path), safe='/')+'?mode='+mode, uri=True)

    async def _state(self, connection, kind, candidate, policy, now, chat_id, exclude_key=None):
        topic = candidate.get('topic_id')
        _scope(kind, chat_id, topic)
        local = _local_now(now, policy)
        day = local.date().isoformat()
        conversation = candidate.get('conversation_key')
        async with connection.execute(
            "SELECT topic_id,conversation_key,source_url,event_id,intended_at,day_key "
            "FROM participation_review_items WHERE kind=? AND chat_id=? "
            "AND status IN ('reserved','scheduled','sent','uncertain') AND idempotency_key<>?", (kind, chat_id, exclude_key or ''),
        ) as cursor:
            rows = await cursor.fetchall()
        # Stored day_key is the approval-time audit. Counters follow the current
        # explicit timezone, including later moderator timezone changes.
        today = [row for row in rows if datetime.fromisoformat(row[4]).astimezone(local.tzinfo).date().isoformat() == day]
        counts = {'global': len(today), 'topic': sum(row[0] == topic for row in today),
                  'thread': sum(row[0] == topic and row[1] == conversation for row in today)}
        seen_rows = rows if policy.get('dedupe_scope') == 'chat' else [row for row in rows if row[0] == topic]
        seen = {identity for row in seen_rows for identity in (row[2], row[3]) if identity}
        recent = [datetime.fromisoformat(row[4]) for row in rows if row[0] == topic and (
            policy.get('cooldown_scope') == 'topic' or row[1] == conversation)]
        sender = candidate.get('sender_user_id')
        async with connection.execute(
            "SELECT 1 FROM participation_optouts WHERE kind=? AND chat_id=? AND opted_out=1 "
            "AND ((subject_kind='topic' AND subject_id=?) OR (subject_kind='member' AND subject_id=?))",
            (kind, chat_id, topic, sender),
        ) as cursor:
            opted_out = await cursor.fetchone() is not None
        return counts, seen, max(recent) if recent else None, opted_out

    async def _evaluate(self, connection, kind, candidate, policy, now, chat_id, verified_topics, exclude_key=None):
        initial = (preview_news(candidate, policy, now=now, verified_topics=verified_topics,
                                counts={'global':0,'topic':0}, seen=set()) if kind == 'topic_news' else
                   preview_reply(candidate, policy, now=now, verified_topics=verified_topics,
                                 counts={'global':0,'topic':0,'thread':0}))
        if not initial['accepted']:
            return initial
        counts, seen, last, opted_out = await self._state(connection, kind, candidate, policy, now, chat_id, exclude_key)
        if opted_out:
            return {'accepted': False, 'reason': 'persisted_opt_out', 'preview': None, 'can_publish': False}
        if kind == 'topic_news':
            return preview_news(candidate, policy, now=now, verified_topics=verified_topics,
                                counts=counts, seen=seen)
        # An inbound message is a durable identity even when its wording changes.
        event = 'reply:'+str(candidate.get('trigger_message_id'))
        if event in seen:
            return {'accepted': False, 'reason': 'duplicate_reply', 'preview': None, 'can_publish': False}
        return preview_reply(candidate, policy, now=now, verified_topics=verified_topics,
                             counts=counts, last_reply=last)

    async def preview(self, kind, candidate, policy, *, now, chat_id, verified_topics):
        """Read persisted counters/identities; no claim, source fetch or write."""
        try:
            _scope(kind, chat_id, candidate.get('topic_id'))
            async with self._connect() as connection:
                return await self._evaluate(connection, kind, candidate, policy, now, chat_id, verified_topics)
        except (sqlite3.Error, ValueError, TypeError):
            return {'accepted':False,'reason':'bookkeeping_unavailable','preview':None,'can_publish':False}

    async def reserve_approved(self, kind, candidate, policy, *, approved_digest,
                               idempotency_key, now, chat_id, verified_topics):
        """Reserve the exact reviewed snapshot under the owner's active policy.

        Callers must authenticate/approve configuration and enforce the exact
        reviewed payload separately. This function does not insert calendar
        rows or perform an external delivery.
        """
        _scope(kind, chat_id, candidate.get('topic_id'))
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return _receipt('idempotency_key_missing')
        normalized = normalized_preview(kind, candidate)
        digest = payload_digest(normalized)
        if digest != approved_digest:
            return _receipt('approved_payload_changed')
        async with self._connect(write=True) as connection:
            await connection.execute('BEGIN IMMEDIATE')
            async with connection.execute(
                'SELECT payload_digest,status FROM participation_review_items WHERE idempotency_key=?',
                (idempotency_key,),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing:
                # Same key in another chat/topic is a conflict, not a replay.
                async with connection.execute(
                    'SELECT kind,chat_id,topic_id FROM participation_review_items WHERE idempotency_key=?',
                    (idempotency_key,),
                ) as cursor:
                    scope = await cursor.fetchone()
                if existing[0] != digest or tuple(scope) != (kind, chat_id, candidate['topic_id']):
                    return _receipt('idempotency_conflict')
                return _receipt('already_recorded', key=idempotency_key, status=existing[1], replay=True)
            result = await self._evaluate(connection, kind, normalized, policy, now, chat_id, verified_topics)
            if not result['accepted']:
                return _receipt(result['reason'])
            if payload_digest(result['preview']) != approved_digest:
                return _receipt('approved_payload_changed')
            event = normalized['event_id'] if kind == 'topic_news' else 'reply:'+str(normalized['trigger_message_id'])
            await connection.execute(
                'INSERT INTO participation_review_items '
                '(idempotency_key,payload_digest,kind,chat_id,topic_id,conversation_key,source_url,event_id,'
                'payload_json,status,intended_at,day_key,timezone,updated_at) '
                "VALUES (?,?,?,?,?,?,?,?,?,'reserved',?,?,?,?)",
                (idempotency_key,digest,kind,chat_id,normalized['topic_id'],normalized.get('conversation_key'),
                 normalized.get('source_url') if kind == 'topic_news' else None,event,
                 json.dumps(normalized,ensure_ascii=False,allow_nan=False),_utc(now),
                 _local_now(now,policy).date().isoformat(),policy['timezone'],_utc(now)),
            )
            await connection.commit()
            return _receipt('approved_review_reserved', key=idempotency_key, status='reserved')

    async def transition(self, key, *, expected_status, status, at, external_message_id=None):
        """Bookkeeping only; uncertainty retains its identity and budget."""
        allowed = {'reserved': {'scheduled','cancelled'}, 'scheduled': {'sent','uncertain','cancelled'},
                   'uncertain': {'sent'}, 'sent': set(), 'cancelled': set()}
        timestamp = _utc(at)
        if status == 'sent' and (type(external_message_id) is not int or external_message_id < 1):
            return _receipt('delivery_evidence_missing')
        async with self._connect(write=True) as connection:
            await connection.execute('BEGIN IMMEDIATE')
            async with connection.execute('SELECT status,external_message_id FROM participation_review_items WHERE idempotency_key=?', (key,)) as cursor:
                row = await cursor.fetchone()
            if not row:
                return _receipt('reservation_missing')
            if row[0] == status and row[1] == external_message_id:
                return _receipt('already_recorded', key=key, status=status, replay=True)
            if row[0] != expected_status or status not in allowed.get(row[0], set()):
                return _receipt('state_conflict')
            await connection.execute('UPDATE participation_review_items SET status=?,external_message_id=?,updated_at=? WHERE idempotency_key=?',
                             (status,external_message_id,timestamp,key))
            await connection.commit()
            return _receipt('state_recorded', key=key, status=status)

    async def status(self, key):
        async with self._connect() as connection:
            async with connection.execute('SELECT status FROM participation_review_items WHERE idempotency_key=?', (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def available(self, kind, candidate, policy, *, now, chat_id):
        """Check durable opt-outs/caps before source/model work costs anything."""
        from .community_participation import _at_cap, _quiet
        async with self._connect() as connection:
            counts, _, last, opted_out = await self._state(connection, kind, candidate, policy, now, chat_id)
        names = ('global', 'topic') if kind == 'topic_news' else ('global', 'topic', 'thread')
        if opted_out or _quiet(_local_now(now, policy), policy['quiet_hours']) or _at_cap(policy, counts, names):
            return False
        if kind == 'occasional_replies' and last:
            from datetime import timedelta
            return now-last >= timedelta(minutes=policy['cooldown_minutes'])
        return True

    async def recent_news(self, chat_id, *, limit):
        async with self._connect() as connection:
            async with connection.execute(
                "SELECT payload_json FROM participation_review_items WHERE kind='topic_news' AND chat_id=? "
                "AND status IN ('reserved','scheduled','sent','uncertain') ORDER BY updated_at DESC LIMIT ?", (chat_id, limit),
            ) as cursor:
                return [json.loads(row[0]) for row in await cursor.fetchall()]

    async def revalidate(self, key, policy, *, now, chat_id, verified_topics):
        """Recheck persisted exact approved payload, caps and opt-outs before send."""
        async with self._connect() as connection:
            async with connection.execute('SELECT kind,payload_json,chat_id,status FROM participation_review_items WHERE idempotency_key=?', (key,)) as cursor:
                row = await cursor.fetchone()
            if not row or row[2] != chat_id or row[3] != 'scheduled':
                return {'accepted': False, 'reason': 'reservation_unavailable', 'can_publish': False}
            return await self._evaluate(connection, row[0], json.loads(row[1]), policy, now, chat_id, verified_topics, key)

    async def _opt_out(self, kind, chat_id, subject_kind, subject_id, opted_out, at):
        _scope(kind, chat_id, subject_id)
        if type(opted_out) is not bool:
            raise ValueError('explicit opt-out state required')
        async with self._connect(write=True) as connection:
            await connection.execute(
                'INSERT INTO participation_optouts VALUES (?,?,?,?,?,?) '
                'ON CONFLICT(kind,chat_id,subject_kind,subject_id) DO UPDATE SET opted_out=excluded.opted_out,updated_at=excluded.updated_at',
                (kind,chat_id,subject_kind,subject_id,int(opted_out),_utc(at)),
            )
            await connection.commit()

    async def set_member_opt_out(self, chat_id, user_id, *, actor_user_id, opted_out, at):
        if type(actor_user_id) is not int or actor_user_id != user_id:
            raise ValueError('own-ID opt-out only')
        await self._opt_out('occasional_replies',chat_id,'member',user_id,opted_out,at)

    async def set_topic_opt_out(self, kind, chat_id, topic_id, *, opted_out, at):
        """Future moderator control; callers must enforce moderator auth."""
        await self._opt_out(kind,chat_id,'topic',topic_id,opted_out,at)
