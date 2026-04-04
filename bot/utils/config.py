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


# Environment helpers
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
GROUP_ID = int(os.getenv("GROUP_ID", "0"))
ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
TIMEZONE = os.getenv("TIMEZONE", "Asia/Jerusalem")
DB_PATH = os.getenv("DB_PATH", "./data/bot.db")
_goals_raw = os.getenv("GOALS_TOPIC_ID", "").strip()
GOALS_TOPIC_ID = int(_goals_raw) if _goals_raw else None
