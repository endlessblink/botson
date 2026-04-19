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
    cover_path TEXT,
    auto_pin INTEGER DEFAULT 0,
    topic_id INTEGER,
    source_poll_message_id INTEGER,
    source_poll_option_key TEXT,
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

CREATE TABLE IF NOT EXISTS blocked_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE,
    blocked_by TEXT,
    reason TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scheduled_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    message_type TEXT NOT NULL DEFAULT 'custom',
    channel_topic_id INTEGER,
    target_group TEXT DEFAULT 'main',
    scheduled_date DATE NOT NULL,
    scheduled_time TIME NOT NULL,
    recurrence TEXT,
    recurrence_days TEXT,
    status TEXT DEFAULT 'scheduled',
    sent_at TIMESTAMP,
    sent_message_id INTEGER,
    error_message TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT DEFAULT 'dashboard',
    auto_pin BOOLEAN DEFAULT FALSE,
    draft_options TEXT,
    cover_path TEXT,
    poll_options TEXT,
    poll_duration INTEGER
);

CREATE TABLE IF NOT EXISTS free_games_posted (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guid TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    store TEXT,
    link TEXT,
    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    message_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_fg_posted_at ON free_games_posted(posted_at);

CREATE TABLE IF NOT EXISTS topic_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    from_topic_id INTEGER,
    message_id INTEGER,
    keyword_hits TEXT,
    fit_label TEXT NOT NULL,
    suggested_topic_id INTEGER,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_topic_obs_ts ON topic_observations(timestamp);
CREATE INDEX IF NOT EXISTS idx_topic_obs_from ON topic_observations(from_topic_id);

CREATE TABLE IF NOT EXISTS emoji_puzzles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    emoji_prompt TEXT NOT NULL,
    answer_he TEXT NOT NULL,
    answer_en TEXT NOT NULL,
    aliases TEXT DEFAULT '[]',
    difficulty INTEGER DEFAULT 2,
    media_type TEXT DEFAULT 'movie',
    enabled INTEGER DEFAULT 1,
    times_used INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS emoji_puzzle_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    message_thread_id INTEGER,
    started_at TIMESTAMP NOT NULL,
    ended_at TIMESTAMP,
    puzzle_count INTEGER NOT NULL,
    winner_summary TEXT DEFAULT '[]',
    status TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_emoji_sessions_active
    ON emoji_puzzle_sessions(status, chat_id, message_thread_id);

CREATE TABLE IF NOT EXISTS emoji_puzzle_rounds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER,
    puzzle_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    message_thread_id INTEGER,
    message_id INTEGER NOT NULL,
    sent_at TIMESTAMP NOT NULL,
    winner_user_id INTEGER,
    winner_message_id INTEGER,
    solved_at TIMESTAMP,
    revealed_at TIMESTAMP,
    status TEXT DEFAULT 'active',
    award_points INTEGER DEFAULT 0,
    FOREIGN KEY (session_id) REFERENCES emoji_puzzle_sessions(id),
    FOREIGN KEY (puzzle_id) REFERENCES emoji_puzzles(id)
);
CREATE INDEX IF NOT EXISTS idx_emoji_rounds_active ON emoji_puzzle_rounds(status, message_id);
CREATE INDEX IF NOT EXISTS idx_emoji_rounds_sent ON emoji_puzzle_rounds(sent_at);
CREATE INDEX IF NOT EXISTS idx_emoji_rounds_session ON emoji_puzzle_rounds(session_id, sent_at);

CREATE TABLE IF NOT EXISTS emoji_puzzle_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    answered_at TIMESTAMP NOT NULL,
    answer_rank INTEGER NOT NULL,
    points_awarded INTEGER NOT NULL,
    FOREIGN KEY (round_id) REFERENCES emoji_puzzle_rounds(id),
    UNIQUE(round_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_emoji_answers_round ON emoji_puzzle_answers(round_id, answer_rank);
CREATE INDEX IF NOT EXISTS idx_emoji_answers_user ON emoji_puzzle_answers(user_id, answered_at);

CREATE TABLE IF NOT EXISTS poll_votes (
    message_id INTEGER NOT NULL,
    option_key TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    display_name TEXT NOT NULL,
    voted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id, option_key, user_id)
);
CREATE INDEX IF NOT EXISTS idx_poll_votes_msg ON poll_votes(message_id);
"""
