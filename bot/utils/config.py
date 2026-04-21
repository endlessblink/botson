"""YAML config loader and settings."""

import os
from datetime import date
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


def get_holiday_blackouts() -> list[dict]:
    """Load manually configured holiday blackout rows from settings.yaml."""
    settings = get_settings() or {}
    raw_items = settings.get("holiday_blackouts", []) or []
    items: list[dict] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        date_iso = str(raw.get("date") or "").strip()
        if not date_iso:
            continue
        items.append({
            "date": date_iso,
            "name": str(raw.get("name") or "").strip(),
            "note": str(raw.get("note") or "").strip(),
            "block_auto": bool(raw.get("block_auto", True)),
        })
    items.sort(key=lambda item: item["date"])
    return items


def get_holiday_blackout(date_iso: str | date) -> dict | None:
    """Return the blackout row for a date, if one exists."""
    if isinstance(date_iso, date):
        date_iso = date_iso.isoformat()
    for item in get_holiday_blackouts():
        if item.get("date") == date_iso:
            return item
    return None


def is_auto_blocked_on(date_iso: str | date) -> bool:
    """Whether automatic bot content should be suppressed on a date."""
    item = get_holiday_blackout(date_iso)
    return bool(item and item.get("block_auto", True))


def should_skip_scheduled_message(date_iso: str | date, created_by: str | None) -> bool:
    """Block only bot-generated scheduled rows on blackout dates.

    Admin-created planner/dashboard rows remain allowed.
    """
    if not is_auto_blocked_on(date_iso):
        return False
    return str(created_by or "").strip() in {"auto", "ai-fill"}


def get_emoji_puzzles() -> list[dict]:
    """Load the seed emoji-puzzle pool."""
    try:
        data = load_yaml("emoji_puzzles.yaml")
    except FileNotFoundError:
        return []
    return data.get("puzzles", []) or []


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
PUBLIC_DASHBOARD_URL = os.getenv("PUBLIC_DASHBOARD_URL", "").rstrip("/")
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
