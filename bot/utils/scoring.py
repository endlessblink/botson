"""Centralized scoring configuration -- reads point values from settings.yaml."""

from .config import get_settings


def get_points(action: str) -> int:
    """Get point value for an action from config.

    Actions: prompt_reply, discussion_reply, goals_post, trivia_correct,
             trivia_winner, emoji_puzzle_winner, event_rsvp, streak_daily, streak_7_bonus,
             streak_14_bonus, streak_30_bonus
    """
    settings = get_settings()
    gamification = settings.get("gamification", {})

    defaults = {
        "prompt_reply": 3,
        "discussion_reply": 3,
        "goals_post": 2,
        "trivia_correct": 12,
        "trivia_winner": 20,
        "emoji_puzzle_winner": 5,
        "event_rsvp": 5,
        "streak_daily": 3,
        "streak_7_bonus": 10,
        "streak_14_bonus": 15,
        "streak_30_bonus": 25,
    }

    return gamification.get(action, defaults.get(action, 1))
