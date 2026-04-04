"""Level system — maps accumulated points to community levels."""

LEVELS = [
    {"level": 1, "min_points": 0,   "tag": "חדש/ה",     "emoji": "🌱"},
    {"level": 2, "min_points": 20,  "tag": "פעיל/ה",    "emoji": "⭐"},
    {"level": 3, "min_points": 50,  "tag": "כוכב/ת",    "emoji": "🌟"},
    {"level": 4, "min_points": 100, "tag": "סופרסטאר",  "emoji": "💫"},
    {"level": 5, "min_points": 250, "tag": "אגדה",      "emoji": "🔥"},
    {"level": 6, "min_points": 500, "tag": "אלוף/ה",    "emoji": "👑"},
]


def get_level(points: int) -> dict:
    """Get the current level info for a given point count."""
    result = LEVELS[0]
    for lvl in LEVELS:
        if points >= lvl["min_points"]:
            result = lvl
        else:
            break
    return result


def get_progress(points: int) -> dict:
    """Get current level + progress toward next level."""
    current = get_level(points)
    current_idx = current["level"] - 1

    if current_idx >= len(LEVELS) - 1:
        # Max level
        return {
            "current": current,
            "next": None,
            "progress": 100,
            "points_current": points,
            "points_needed": 0,
        }

    next_lvl = LEVELS[current_idx + 1]
    points_in_level = points - current["min_points"]
    points_for_level = next_lvl["min_points"] - current["min_points"]
    progress = int(points_in_level / points_for_level * 100) if points_for_level > 0 else 100

    return {
        "current": current,
        "next": next_lvl,
        "progress": progress,
        "points_current": points_in_level,
        "points_needed": points_for_level,
    }


def check_level_up(old_points: int, new_points: int) -> dict | None:
    """Check if a level threshold was crossed. Returns new level info or None."""
    old_level = get_level(old_points)
    new_level = get_level(new_points)
    if new_level["level"] > old_level["level"]:
        return new_level
    return None


def make_progress_bar(progress: int, width: int = 10) -> str:
    """Create a text progress bar."""
    filled = int(width * progress / 100)
    empty = width - filled
    return "█" * filled + "░" * empty
