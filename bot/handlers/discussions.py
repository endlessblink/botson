"""Discussion prompts metadata.

Sending is now fully owned by the materializer + calendar_checker pipeline
(bot/scheduler/materializer.py + bot/handlers/calendar.py). This module only
exports CATEGORY_NAMES for human-readable labeling in the dashboard.
"""

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


def register(app):
    """No handlers to register — sending is driven by scheduled_messages."""
    pass
