"""Pre-flight verification for every outbound Telegram send.

Rules, in order:
    1. DMs (positive chat_id) pass through untouched.
    2. Test group (TEST_GROUP_ID) passes through untouched — it is the scratch-pad.
    3. Main group (GROUP_ID):
         a. Sending with message_thread_id=None is REFUSED
            (the Telegram "General" topic is deliberately off-limits —
             see docs/2026-04-22-trivia-topic-incident.md).
         b. message_thread_id must exist in verified_forum_topics
            or the send is REFUSED.
    4. bypass_verification=True opts out of rules 3a/3b. The only legitimate
       caller is the dashboard dot-test endpoint when it is sending the
       single-char dot used to discover a brand-new thread id.

Raises UnverifiedTopicError on refusal. Callers must decide whether to
log+skip or propagate; the guard itself never sends and never silently
downgrades a refusal into a successful send.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from .config import GROUP_ID, TEST_GROUP_ID

logger = logging.getLogger(__name__)


class _VerifiedTopicsLookup(Protocol):
    async def is_verified_topic_id(self, topic_id: int) -> bool: ...
    async def delete_topic(self, topic_id: int) -> None: ...


class UnverifiedTopicError(RuntimeError):
    """Raised when a send targets an unverified topic in the main group."""


# Telegram error strings that signal the target topic no longer exists.
# On these, safe_send auto-removes the topic from both forum_topics and
# verified_forum_topics so the picker stops offering a dead target.
_DELETED_TOPIC_MARKERS = (
    "message thread not found",
    "topic_deleted",
    "chat not found",
)


async def verify_send_target(
    db: _VerifiedTopicsLookup,
    *,
    chat_id: int,
    message_thread_id: int | None,
    bypass_verification: bool = False,
) -> None:
    if bypass_verification:
        return
    if chat_id >= 0:
        return
    if chat_id == TEST_GROUP_ID:
        return
    if chat_id == GROUP_ID:
        if message_thread_id is None:
            raise UnverifiedTopicError(
                "main-group send blocked: no message_thread_id. Telegram 'General' topic is off-limits; pick a verified category."
            )
        if not await db.is_verified_topic_id(message_thread_id):
            raise UnverifiedTopicError(
                f"main-group send blocked: topic_id={message_thread_id} is not in verified_forum_topics. Run the Settings page dot-test workflow first."
            )
        return
    logger.warning(
        "topic_guard: send to unexpected chat_id=%s (thread=%s); allowing",
        chat_id,
        message_thread_id,
    )


async def safe_send(
    bot: Any,
    db: _VerifiedTopicsLookup,
    method_name: str,
    *,
    chat_id: int,
    message_thread_id: int | None = None,
    bypass_verification: bool = False,
    **kwargs: Any,
) -> Any:
    await verify_send_target(
        db,
        chat_id=chat_id,
        message_thread_id=message_thread_id,
        bypass_verification=bypass_verification,
    )
    method = getattr(bot, method_name)
    call_kwargs = dict(kwargs)
    call_kwargs["chat_id"] = chat_id
    if message_thread_id is not None:
        call_kwargs["message_thread_id"] = message_thread_id
    try:
        return await method(**call_kwargs)
    except Exception as e:
        err = str(e).lower()
        if message_thread_id is not None and any(m in err for m in _DELETED_TOPIC_MARKERS):
            try:
                await db.delete_topic(message_thread_id)
                logger.warning(
                    "topic_guard: auto-removed deleted topic_id=%s after send failed (%s)",
                    message_thread_id, e,
                )
            except Exception:
                logger.exception(
                    "topic_guard: failed to auto-remove topic_id=%s", message_thread_id,
                )
        raise
