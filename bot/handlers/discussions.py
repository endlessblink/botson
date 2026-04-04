"""Discussion prompts handler — sparks conversation in topic channels."""

import logging
import random

from telegram.ext import ContextTypes

from ..utils.config import GROUP_ID, get_settings

logger = logging.getLogger(__name__)

# Channel category to human-readable name mapping
CATEGORY_NAMES = {
    "general": "כל מה שאין לו ערוץ",
    "geek": "אנימה / קומיקס וכל הדברים הגיקיים",
    "gaming": "גיימינג + משחקי לוח",
    "movies": "סרטים סדרות וכו",
    "cute": "כל מה שחמוד",
    "singles": "אל הוריים/יות פנויים פנויות",
    "funny": "מצחיק / מגניב",
    "vegan": "אל הוריים טבעונים וצמחוניים",
    "art": "ערוץ אומנות ויצירה",
    "politics": "פוליטיקה / גיאו-פוליטיקה וכל היתר",
}


def _load_discussions() -> dict[str, list[str]]:
    """Load discussion prompts from YAML."""
    from ..utils.config import load_yaml
    try:
        return load_yaml("discussions.yaml")
    except Exception as e:
        logger.error("Failed to load discussions.yaml: %s", e)
        return {}


# In-memory tracking of used prompts per category
_used_prompts: dict[str, set[int]] = {}


def _pick_prompt(category: str, prompts: list[str]) -> str:
    """Pick a random unused prompt, reset when all used."""
    if category not in _used_prompts:
        _used_prompts[category] = set()

    available = [i for i in range(len(prompts)) if i not in _used_prompts[category]]
    if not available:
        _used_prompts[category] = set()
        available = list(range(len(prompts)))

    idx = random.choice(available)
    _used_prompts[category].add(idx)
    return prompts[idx]


async def send_discussion_prompt(context: ContextTypes.DEFAULT_TYPE):
    """Scheduled job: send a discussion prompt to a random topic channel.

    Picks one random category that has a configured topic ID,
    then sends a random prompt from that category.
    """
    settings = get_settings()
    topic_ids = settings.get("topics", {}).get("discussions", {})

    if not topic_ids:
        logger.warning("No discussion topic IDs configured in settings.yaml")
        return

    discussions = _load_discussions()
    if not discussions:
        return

    # Pick a random category that has both a topic ID and prompts
    available_categories = [
        cat for cat in discussions
        if cat in topic_ids and topic_ids[cat] and discussions[cat]
    ]

    if not available_categories:
        logger.warning("No categories with both topic IDs and prompts available")
        return

    category = random.choice(available_categories)
    prompt = _pick_prompt(category, discussions[category])
    topic_id = topic_ids[category]

    try:
        await context.bot.send_message(
            chat_id=GROUP_ID,
            text=f"💬 {prompt}",
            message_thread_id=topic_id,
        )
        logger.info("Sent discussion prompt to %s: %s", category, prompt[:50])
    except Exception as e:
        logger.error("Failed to send discussion prompt to %s: %s", category, e)


def register(app):
    """Register discussion handlers (nothing to register — jobs only)."""
    pass
