"""YAML config loader and settings."""

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

load_dotenv()

CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


def load_yaml(filename: str) -> dict:
    """Load a YAML config file."""
    path = CONFIG_DIR / filename
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_prompts() -> dict[str, list[str]]:
    """Load morning/evening prompts."""
    return load_yaml("prompts.yaml")


def get_settings() -> dict:
    """Load bot settings."""
    return load_yaml("settings.yaml")


def get_spam_patterns() -> list[str]:
    """Load spam regex patterns."""
    data = load_yaml("spam_patterns.yaml")
    return data.get("patterns", [])


def get_topic_rules() -> list[dict]:
    """Load per-topic routing rules (Phase 0: observation only).

    Returns list of dicts: {topic_id, category_key, name_he, description_he,
    keywords_on, keywords_off, siblings}.
    """
    try:
        data = load_yaml("topic_rules.yaml")
    except FileNotFoundError:
        return []
    return data.get("topics", []) or []


# Environment helpers
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_ID = int(os.getenv("GROUP_ID", "0"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
TIMEZONE = os.getenv("TIMEZONE", "Asia/Jerusalem")
DB_PATH = os.getenv("DB_PATH", "./data/bot.db")
_goals_raw = os.getenv("GOALS_TOPIC_ID", "").strip()
GOALS_TOPIC_ID = int(_goals_raw) if _goals_raw else None
TEST_GROUP_ID = int(os.getenv("TEST_GROUP_ID", "0")) or None
ALL_GROUP_IDS = [gid for gid in [GROUP_ID, TEST_GROUP_ID] if gid]


def is_feature_enabled(feature: str, group_id: int | None = None) -> bool:
    """Check if a feature is enabled for a specific group.

    Features config in settings.yaml looks like:
    features:
      welcome:
        enabled: true
        groups: [main, test]

    If groups list is missing or empty, feature is enabled for all groups when enabled=true.
    """
    settings = get_settings()
    feat_config = settings.get("features", {}).get(feature, {})

    # Support old format (just true/false) for backwards compat
    if isinstance(feat_config, bool):
        return feat_config

    if not feat_config.get("enabled", False):
        return False

    # If no group_id provided or no groups restriction, return enabled status
    groups = feat_config.get("groups", [])
    if not groups or not group_id:
        return True

    # Map group names to IDs
    group_map = {"main": GROUP_ID, "test": TEST_GROUP_ID}
    allowed_ids = [group_map.get(g) for g in groups if group_map.get(g)]

    return group_id in allowed_ids
