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

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    event_date TEXT NOT NULL,
    event_time TEXT,
    location TEXT,
    created_by INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_id INTEGER,
    rsvp_yes TEXT DEFAULT '[]',
    rsvp_maybe TEXT DEFAULT '[]',
    active INTEGER DEFAULT 1,
    FOREIGN KEY (created_by) REFERENCES members(user_id)
);

CREATE TABLE IF NOT EXISTS trivia_scores (
    user_id INTEGER PRIMARY KEY,
    total_score INTEGER DEFAULT 0,
    correct_answers INTEGER DEFAULT 0,
    total_answers INTEGER DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES members(user_id)
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    description TEXT NOT NULL,
    target_user_id INTEGER,
    target_channel TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS forum_topics (
    topic_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""
