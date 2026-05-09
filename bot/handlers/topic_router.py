# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Off-topic routing — Phase 0: observation only.

Listens to every group message, classifies its fit against per-topic keyword
rules from config/topic_rules.yaml, and records the classification in the
topic_observations table. No user-visible action is taken in Phase 0.

Priority: group=5 (after antispam at group=0, before feature handlers).
"""

import json
import logging

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters

from ..database.db import Database
from ..utils.config import get_settings, get_topic_rules
from ..utils.helpers import is_admin, is_bot_user

logger = logging.getLogger(__name__)

# Loaded rules cached per bot run. Reloaded lazily when empty.
_rules_by_topic: dict[int, dict] = {}


def _load_rules() -> None:
    """Populate the rules cache from config/topic_rules.yaml."""
    global _rules_by_topic
    try:
        rules = get_topic_rules()
        _rules_by_topic = {int(r["topic_id"]): r for r in rules if r.get("topic_id")}
        logger.info("topic_router: loaded %d topic rules", len(_rules_by_topic))
    except Exception as e:  # noqa: BLE001
        logger.error("topic_router: failed to load topic rules: %s", e)
        _rules_by_topic = {}


def _match_keywords(text: str, keywords: list) -> list:
    """Return the keywords that appear as substrings of text (case-insensitive)."""
    if not text or not keywords:
        return []
    low = text.lower()
    return [kw for kw in keywords if isinstance(kw, str) and kw.lower() in low]


def _classify(text: str, rule: dict, all_rules: dict[int, dict]) -> tuple[str, int | None, dict]:
    """Classify a message against its topic's rule.

    Returns (fit_label, suggested_topic_id, hits_dict).
    Labels: on | off | unknown | no_rule
    """
    on_hits = _match_keywords(text, rule.get("keywords_on") or [])
    off_hits = _match_keywords(text, rule.get("keywords_off") or [])
    hits: dict = {"on": on_hits, "off": off_hits, "sibling": None}

    # keywords_off is a strong signal — belongs elsewhere
    if off_hits:
        # Find which sibling topic the off-keywords point to
        siblings = rule.get("siblings") or []
        best_sibling = None
        best_sibling_hits: list = []
        for sib_id in siblings:
            sib_rule = all_rules.get(int(sib_id))
            if not sib_rule:
                continue
            sib_hits = _match_keywords(text, sib_rule.get("keywords_on") or [])
            if len(sib_hits) > len(best_sibling_hits):
                best_sibling = int(sib_id)
                best_sibling_hits = sib_hits
        hits["sibling"] = {"topic_id": best_sibling, "matches": best_sibling_hits} if best_sibling else None
        return "off", best_sibling, hits

    if on_hits:
        return "on", None, hits

    return "unknown", None, hits


async def observe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Classify every message's topic fit and log it. No user action."""
    msg = update.message
    if not msg or not update.effective_user or not update.effective_chat:
        return

    # Feature gate — top-level flag in settings.yaml (not under features.*)
    settings = get_settings()
    tr_config = settings.get("topic_routing") or {}
    if not tr_config.get("enabled", False):
        return
    if tr_config.get("mode") != "observe":
        # Phases 1/2 (soft/strict) would branch here — Phase 0 only implements observe.
        return

    user = update.effective_user

    # Skip admins and bots (same as antispam pattern)
    if is_admin(user.id) or is_bot_user(user):
        return

    # Only act on messages that originated in a forum topic
    thread_id = msg.message_thread_id
    if not thread_id:
        return

    # Lazy-load rules if cache is empty (bot just started or config reloaded)
    if not _rules_by_topic:
        _load_rules()

    rule = _rules_by_topic.get(int(thread_id))
    text = (msg.text or msg.caption or "").strip()

    if not rule:
        fit_label = "no_rule"
        suggested = None
        hits: dict = {"on": [], "off": [], "sibling": None}
    elif not text:
        fit_label = "unknown"
        suggested = None
        hits = {"on": [], "off": [], "sibling": None, "reason": "no_text"}
    else:
        fit_label, suggested, hits = _classify(text, rule, _rules_by_topic)

    try:
        db: Database = context.bot_data["db"]
        await db.log_topic_observation(
            user_id=user.id,
            from_topic_id=int(thread_id),
            message_id=msg.message_id,
            keyword_hits=json.dumps(hits, ensure_ascii=False),
            fit_label=fit_label,
            suggested_topic_id=suggested,
        )
    except Exception as e:  # noqa: BLE001
        logger.error("topic_router: failed to log observation: %s", e)


def register(app):
    """Register the topic-router handler at priority 5."""
    _load_rules()
    app.add_handler(
        MessageHandler(
            filters.ALL & ~filters.COMMAND & ~filters.StatusUpdate.ALL,
            observe,
        ),
        group=5,
    )
