"""Level system backed by operator-editable settings."""

from .config import get_settings
from .copy import load_copy


def _levels() -> list[dict]:
    configured = (get_settings().get("levels") or {}).get("tiers") or []
    levels: list[dict] = []
    for idx, item in enumerate(configured, start=1):
        if not isinstance(item, dict):
            continue
        try:
            level = int(item.get("level") or idx)
            min_points = int(item.get("min_points") or 0)
        except (TypeError, ValueError):
            continue
        tag = str(item.get("tag") or "").strip()
        if not tag:
            tag = load_copy("levels", "fallback_tag", default="Level {level}", level=level)
        levels.append({
            "level": level,
            "min_points": min_points,
            "tag": tag,
            "emoji": str(item.get("emoji") or ""),
        })
    if levels:
        return sorted(levels, key=lambda lvl: (lvl["min_points"], lvl["level"]))
    return [{"level": 1, "min_points": 0, "tag": load_copy("levels", "fallback_tag", default="Level {level}", level=1), "emoji": ""}]


def get_level(points: int) -> dict:
    """Get the current level info for a given point count."""
    levels = _levels()
    result = levels[0]
    for lvl in levels:
        if points >= lvl["min_points"]:
            result = lvl
        else:
            break
    return result


def get_progress(points: int) -> dict:
    """Get current level + progress toward next level."""
    levels = _levels()
    current = get_level(points)
    current_idx = next((i for i, lvl in enumerate(levels) if lvl["level"] == current["level"]), 0)

    if current_idx >= len(levels) - 1:
        # Max level
        return {
            "current": current,
            "next": None,
            "progress": 100,
            "points_current": points,
            "points_needed": 0,
        }

    next_lvl = levels[current_idx + 1]
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
