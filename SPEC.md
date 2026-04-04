# Telegram Bot Spec — אלהוריים וזה

## 1. Group Overview
The bot serves "אלהוריים וזה", a childfree community with 81 members in a forum-style Telegram group with topic channels. All user-facing messages should be in Hebrew.

**Existing topic channels:** כל מה שאין לו ערוץ (General), מצטרפים חדשים + עדכונים (New members + updates), אנימה / קומיקס וכל הדברים הגיקיים, גיימינג + משחקי לוח, כל מה שחמוד, סרטים סדרות וכו, אל הוריים/יות פנויים פנויות, מצחיק / מגניב, אל הוריים טבעונים וצמחוניים, ערוץ אומנות ויצירה, פוליטיקה / גיאו-פוליטיקה וכל היתר.

**New channel to create:** הישגים ומטרות 🌟

## 2. Bot Features (by priority)

### 2.1 Welcome Bot — P0 (Must Have)
**Target channel:** מצטרפים חדשים + עדכונים
**Trigger:** new_chat_members event

**Welcome message template:**
```
היי {name}! ברוך/ה הבא/ה לאלהוריים וזה! 🌟
אנחנו קהילה של אנשים חופשיים שבחרו לחיות בלי ילדים.
הנה כמה ערוצים שאפשר להציץ בהם:
🎨 אומנות ויצירה | 🎮 גיימינג | 📺 סרטים וסדרות | 😂 מצחיק
💚 טבעוניות | 💌 פנויים/ות | 🌟 הישגים ומטרות | ועוד...
ספר/י לנו קצת על עצמך בערוץ הכללי! 👋
```

**Behavior rules:**
- Send within 5 seconds of join
- Use the member's Telegram display name
- Batch multiple joins within 30 seconds into one message
- Skip bots (check is_bot flag)

### 2.2 Daily Goals & Achievements — P0 (Must Have)
**New channel:** הישגים ומטרות 🌟
**Backed by research:** 77%+ adoption rate in communities with gratitude sharing. Small wins tracking builds momentum and connection.

**Morning prompt — 08:00 Israel time:**
```
☀️ בוקר טוב! מה המטרה שלכם להיום?
```

**Evening prompt — 21:00 Israel time:**
```
🌙 ערב טוב! מה הדבר הטוב שקרה לכם היום?
```

**Prompt rotation:** Store 30+ variations per type (morning/evening) in a YAML config file. The bot picks a random unused prompt, marks it used, and resets the pool when exhausted. Examples of variations: "מה דבר אחד שאתם רוצים להספיק היום?", "על מה אתם גאים מהיום?", "מה גרם לכם לחייך היום?"

**Optional enhancements:**
- Weekly streak tracker — celebrate consistent participants
- Monthly highlights — compile best achievements into a summary
- Reaction-based kudos — members react to boost someone's achievement

### 2.3 Karma / Recognition — P1 (High)
Members can appreciate each other with points. Research shows recognized members are significantly more likely to stay engaged.

**How it works:**
- A member replies to someone's message with +1, תודה, or 👏
- Bot detects this and adds a karma point to the original poster
- Bot reacts with a confirmation emoji (silent, no spam)
- Weekly leaderboard posted to general channel every Friday

**Commands:**

| Command | Description | Access |
|---------|-------------|--------|
| /karma | Show your karma points | Everyone |
| /leaderboard | Top 10 by karma | Everyone |
| /karma @user | Check someone's karma | Everyone |
| /resetkarma | Reset for new season | Admin only |

**Anti-abuse rules:** Can't give karma to yourself, max 5 karma given per user per day, can't give karma to bots.

### 2.4 Anti-Spam — P1 (High)
Silent background moderation. Protection without disrupting the group's flow.

**Detection rules:**

| Rule | Action | Sensitivity |
|------|--------|-------------|
| Forwarded messages from unknown channels | Delete + warn user via DM | Medium |
| 3+ links from new members (<7 days old) | Hold for admin review | High |
| Crypto/betting/adult spam patterns (regex) | Auto-delete + log | High |
| Repeated identical messages (>3 in 60 sec) | Delete duplicates + mute 10 min | Medium |
| New member sends message in first 30 sec | Flag for review | Low |

**Admin logging:** All actions logged to a private admin channel or DM to group owner. Each log includes: deleted message content, user, rule triggered, timestamp. Admins can whitelist users/patterns with /whitelist.

### 2.5 Weekly Roundup — P2 (Medium)
**Schedule:** Every Friday at 18:00 Israel time
**Target:** כל מה שאין לו ערוץ (General)

**Contents:** Most active topic channels this week, top 3 karma earners, number of new members, quiet channels that could use some love, achievement streak highlights.

## 3. Technical Architecture

**Tech stack:**

| Component | Technology | Why |
|-----------|------------|-----|
| Language | Python 3.11+ | Rich Telegram library ecosystem, easy async |
| Framework | python-telegram-bot v20+ | Mature, async-native, forum/topics support |
| Database | SQLite (via aiosqlite) | Zero setup, file-based, perfect for 81 members |
| Scheduler | APScheduler | Cron-like scheduling for daily/weekly posts |
| Config | YAML files | Easy to edit prompts/settings without code changes |
| Hosting | Railway or Render (free tier) | Always-on, free for small bots |

**Project structure:**
```
elhoriim-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py              # Entry point, bot setup
│   ├── handlers/
│   │   ├── welcome.py       # New member welcome
│   │   ├── goals.py         # Daily goals & achievements
│   │   ├── karma.py         # Karma system
│   │   ├── antispam.py      # Spam detection
│   │   └── roundup.py       # Weekly roundup
│   ├── database/
│   │   ├── models.py        # SQLite schema
│   │   └── db.py            # DB connection & queries
│   ├── scheduler/
│   │   └── jobs.py          # Scheduled tasks config
│   └── utils/
│       ├── config.py        # Load YAML configs
│       └── helpers.py       # Shared utilities
├── config/
│   ├── prompts.yaml         # Morning/evening prompt pool
│   ├── settings.yaml        # Bot settings, thresholds
│   └── spam_patterns.yaml   # Regex patterns for spam
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

**Database schema:**

| Table | Columns | Purpose |
|-------|---------|---------|
| members | user_id, username, display_name, joined_at, karma_points | Track all members |
| karma_log | id, giver_id, receiver_id, timestamp, message_id | Audit trail |
| daily_prompts | id, type (morning/evening), text, last_used_at | Prompt rotation |
| spam_log | id, user_id, message_text, rule_triggered, action, timestamp | Moderation log |
| streaks | user_id, current_streak, longest_streak, last_post_date | Goals participation |

**Environment variables:**

| Variable | Description | Example |
|----------|-------------|---------|
| BOT_TOKEN | Bot API token from BotFather | 123456:ABC-DEF... |
| GROUP_ID | Telegram group ID | -1003873409631 |
| ADMIN_IDS | Comma-separated admin user IDs | 602196268 |
| TIMEZONE | For scheduled posts | Asia/Jerusalem |
| DB_PATH | SQLite database path | ./data/bot.db |
| GOALS_TOPIC_ID | Forum topic ID for goals channel | TBD after creation |

## 4. Setup Guide

**Step 1 — Create bot via BotFather:**
1. Open @BotFather in Telegram
2. Send /newbot
3. Pick a display name (e.g., "אלהוריים בוט")
4. Pick a username (e.g., elhoriim_community_bot)
5. Save the API token for the .env file
6. Send /setjoingroups → enable
7. Send /setprivacy → disable (so bot can read messages for karma detection)

**Step 2 — Add bot to group:**
1. Open אלהוריים וזה group settings
2. Members → Add Member → search the bot username
3. Promote to Admin with permissions: Delete messages, Ban users, Pin messages, Manage topics
4. Create the new topic channel הישגים ומטרות 🌟

**Step 3 — Deploy (Railway):**
1. Push code to a GitHub repo
2. Connect Railway to the repo
3. Set BOT_TOKEN and GROUP_ID as environment variables
4. Deploy — Railway auto-detects the Dockerfile

## 5. Rollout Plan

| Phase | Features | Timeline | Success Criteria |
|-------|----------|----------|-----------------|
| Phase 1 | Welcome bot + Anti-spam | Week 1 | New members get greeted, zero spam |
| Phase 2 | Daily Goals & Achievements | Week 2 | 5+ members posting per day |
| Phase 3 | Karma / Recognition | Week 3-4 | 20+ karma interactions per week |
| Phase 4 | Weekly Roundup | Week 5 | Members engage with the post |

Gather feedback after each phase. If a feature gets negative reception, adjust or disable it.

## 6. Bot Commands Reference

| Command | Description | Where |
|---------|-------------|-------|
| /start | Initialize the bot | DM only |
| /help | Show available commands | Group / DM |
| /karma | Show your karma points | Group |
| /karma @user | Check someone's karma | Group |
| /leaderboard | Top 10 karma earners | Group |
| /streak | Show your goals streak | Group |
| /stats | Group activity stats | Admin only |
| /whitelist <pattern> | Whitelist a spam pattern | Admin only |
| /resetkarma | Reset karma for new season | Admin only |
