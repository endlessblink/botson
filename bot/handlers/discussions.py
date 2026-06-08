# All user-facing Hebrew strings in this file must be loaded from config
# (settings.yaml or a sibling YAML). Inline literals are allowed only as
# explicit `# noqa: hardcoded-content` fallbacks — see CLAUDE.md.
"""Discussion prompts metadata.

Sending is now fully owned by the materializer + calendar_checker pipeline
(bot/scheduler/materializer.py + bot/handlers/calendar.py). This module only
exports CATEGORY_NAMES for human-readable labeling in the dashboard.
"""

# Channel category to human-readable name mapping
CATEGORY_NAMES = {
    "general": "UNVERIFIED — do not use until user reconfirms the real topic",
    "gaming": "גיימינג + משחקי לוח",
    "movies": "סרטים סדרות וכו",
    "cute": "כל מה שחמוד",
    "singles": "אל הוריים/יות פנויים פנויות",
    "funny": "מצחיק / מגניב",
    "vegan": "אל הוריים טבעונים וצמחוניים",
    "art": "ערוץ אומנות ויצירה",
    "support": "מרימים אחד לשני/ה!",
    "fitness": "כושר",
    "politics": "פוליטיקה / גיאו-פוליטיקה וכל היתר",
}


def register(app):
    """No handlers to register — sending is driven by scheduled_messages."""
    pass
