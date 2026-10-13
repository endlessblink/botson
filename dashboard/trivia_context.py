"""Validate discussion-derived trivia subjects before preparing a draft.

This module neither reads chat nor generates/sends content. Callers supply the
existing authenticated chat read-back and keep its receipt for normal approval.
"""

from datetime import datetime, timedelta, timezone


class TriviaContextUnavailable(ValueError):
    """The draft needs a fresh, evidenced discussion subject."""


def discussion_trivia_subject(
    messages: list[dict], selection: dict, *, chat_id: int,
    context_receipt: str, now: datetime, thread_id: int | None = None,
    max_age: timedelta = timedelta(hours=24),
) -> dict:
    """Return strict draft fields from a source-backed model/operator selection.

    ``selection`` contains theme_label, anchor and source_message_ids. The anchor
    is an exact subject phrase from member text, not a generated rationale. A
    theme must name the selected anchor, rather than an unrelated bank category.
    Chat read-back omits chat_id; its authenticated caller must stamp the known
    GROUP_ID on those rows before passing them here.
    Never substitute bank categories or Botson's own announcements as evidence.
    """
    if not context_receipt.strip():
        raise TriviaContextUnavailable("fresh_community_context_receipt_required")
    if now.tzinfo is None or max_age <= timedelta(0):
        raise ValueError("aware current time and positive context window required")
    now = now.astimezone(timezone.utc)
    eligible = {}
    for message in messages:
        if message.get("source") != "member" or message.get("chat_id") != chat_id:
            continue
        if thread_id is not None and message.get("thread_id") != thread_id:
            continue
        try:
            occurred = datetime.fromisoformat(str(message["occurred_at"]).replace("Z", "+00:00"))
            message_id = int(message["message_id"])
        except (KeyError, ValueError, TypeError):
            continue
        if occurred.tzinfo is None or not now - max_age <= occurred <= now:
            continue
        text = str(message.get("text") or "").strip()
        if text and not text.startswith("/"):
            eligible[message_id] = (text, occurred)
    if not eligible:
        raise TriviaContextUnavailable("fresh_member_discussion_required")
    theme = str(selection.get("theme_label") or "").strip()
    anchor = str(selection.get("anchor") or "").strip()
    ids = selection.get("source_message_ids")
    if not theme or len(theme) > 100 or len(anchor) < 3 or len(anchor) > 160:
        raise TriviaContextUnavailable("explicit_discussion_subject_required")
    if theme.casefold() != anchor.casefold():
        raise TriviaContextUnavailable("trivia_theme_must_match_discussion_anchor")
    if not isinstance(ids, list) or not ids or len(ids) > 10:
        raise TriviaContextUnavailable("discussion_source_ids_required")
    if any(type(value) is not int or value not in eligible for value in ids):
        raise TriviaContextUnavailable("discussion_source_ids_not_in_fresh_context")
    if not any(anchor.casefold() in eligible[value][0].casefold() for value in ids):
        raise TriviaContextUnavailable("discussion_anchor_not_in_member_text")
    return {
        "theme_label": theme,
        "categories": [theme],
        "discussion_context": {
            "context_receipt": context_receipt,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "anchor": anchor,
            "source_message_ids": list(dict.fromkeys(ids)),
            "source_timestamps": [eligible[value][1].isoformat() for value in dict.fromkeys(ids)],
        },
    }
