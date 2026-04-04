"""SQLite schema definitions for the bot database."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    display_name TEXT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    karma_points INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS karma_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    giver_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_id INTEGER,
    FOREIGN KEY (giver_id) REFERENCES members(user_id),
    FOREIGN KEY (receiver_id) REFERENCES members(user_id)
);

CREATE TABLE IF NOT EXISTS daily_prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type TEXT NOT NULL CHECK(type IN ('morning', 'evening')),
    text TEXT NOT NULL,
    last_used_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS spam_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    message_text TEXT,
    rule_triggered TEXT NOT NULL,
    action TEXT NOT NULL,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS streaks (
    user_id INTEGER PRIMARY KEY,
    current_streak INTEGER DEFAULT 0,
    longest_streak INTEGER DEFAULT 0,
    last_post_date DATE,
    FOREIGN KEY (user_id) REFERENCES members(user_id)
);
"""
