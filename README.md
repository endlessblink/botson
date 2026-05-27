# Botson – Elhoriim Community Bot

A Telegram bot for the "אלהוריים וזה" (Elhoriim and This) childfree community. The bot fosters engagement through welcome messages, daily goal tracking, peer recognition, spam protection, and weekly activity summaries. All user-facing messages are in Hebrew.

**Community:** 81 members | **Group type:** Forum-style with topics | **Status:** Production-ready

---

## Features

### Welcome Bot
Greets new members when they join the group, batches multiple joins into a single message, and skips bot accounts. Sends a friendly introduction with channel recommendations to the "מצטרפים חדשים + עדכונים" (New Members + Updates) topic.

### Daily Goals & Achievements
Morning and evening prompts in Hebrew sent to the "הישגים ומטרות 🌟" (Achievements & Goals) topic. Prompts rotate through 30+ variations per type to keep engagement fresh. Members track their participation streaks and get celebrated at milestones (7 days, 30 days).

### Karma & Recognition
Members can appreciate each other by replying to a message with `+1`, `תודה` (thanks), or `👏` (clap). Points are tracked, and a weekly leaderboard posts every Friday showing the top 10 contributors. Anti-abuse rules prevent self-karma, limit karma given per day, and block karma to bots.

### Anti-Spam
Silent background moderation protecting the group from:
- Forwarded messages from unknown channels
- Excessive links from new members
- Crypto/betting/adult content (regex patterns)
- Duplicate message spam
- Suspicious new member activity

All actions are logged for admin review. Admins can whitelist patterns as needed.

### Weekly Roundup
Every Friday at 18:00 (Israel time), a digest posts to the general channel showing:
- Most active topic channels this week
- Top 3 karma earners
- Number of new members
- Quiet channels that could use engagement
- Achievement streak highlights

---

## Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| **Language** | Python 3.11+ | Rich Telegram ecosystem, excellent async support |
| **Framework** | python-telegram-bot v20+ | Mature, async-native, full forum/topics support |
| **Database** | SQLite with aiosqlite | Zero setup, file-based, perfect for small groups |
| **Scheduler** | APScheduler | Reliable cron-like scheduling for daily/weekly posts |
| **Config** | YAML files | Easy prompt/settings editing without code changes |
| **Hosting** | Railway or Render | Always-on deployment, free tier available |

---

## Quick Start

### Prerequisites
- Python 3.11 or later
- A Telegram bot token (from @BotFather)
- The bot added to your Telegram group with admin privileges

### Installation

Clone the repository and set up:

```bash
git clone https://github.com/your-username/elhoriim-bot.git
cd elhoriim-bot

python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### Configuration

Copy the example environment file and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your bot token and group details (see [Environment Variables](#environment-variables) below).

### Run Locally

```bash
python -m bot.main
```

You should see output like:
```
2026-04-04 10:30:45,123 - telegram.ext.Application - INFO - Application started
```

The bot will connect to Telegram and start handling messages. Press Ctrl+C to stop gracefully.

---

## BotFather Setup

### Step 1: Create the Bot

1. Open Telegram and message @BotFather
2. Send `/newbot`
3. Choose a display name (e.g., "Botson")
4. Choose a username (must be unique, e.g., `elhoriim_community_bot`)
5. Copy the API token (looks like `123456:ABC-DEF1234567890`)
6. Save this token to your `.env` file as `BOT_TOKEN`

### Step 2: Configure Bot Permissions

In your @BotFather conversation:

```
/setjoingroups
→ Select your bot
→ Enable (so it can join and receive new member events)

/setprivacy
→ Select your bot
→ Disable (so the bot can read all messages for karma detection and spam moderation)
```

### Step 3: Add Bot to Group

1. Open your "אלהוריים וזה" group in Telegram
2. Go to **Members** → **Add Member**
3. Search for your bot username and add it
4. **Promote to Admin** with these permissions:
   - Delete messages
   - Ban users
   - Pin messages
   - Manage topics/channels

---

## Group Setup

### Create the Goals Topic

1. In the "אלהוריים וזה" group, create a new topic called **"הישגים ומטרות 🌟"** (Achievements & Goals)
2. After creation, the topic will have an ID. Get it by:
   - Having a bot admin send a message to that topic
   - Check the bot's logs or use `message.message_thread_id` from the message object
   - Or ask @BotFather for the topic ID
3. Add this ID to `.env` as `GOALS_TOPIC_ID`

### Verify Topic Setup

The bot will automatically post daily prompts to this topic. Test with:

```bash
python -m bot.main
# Wait 5 seconds, then Ctrl+C
```

Check the group — you should see a test message in the console logs.

---

## Environment Variables

Copy `.env.example` to `.env` and fill in the following:

| Variable | Description | Example | Required |
|----------|-----------|---------|----------|
| `BOT_TOKEN` | Bot API token from @BotFather | `123456:ABC-DEF1234567890` | Yes |
| `GROUP_ID` | Telegram group ID (negative number) | `-1003873409631` | Yes |
| `ADMIN_IDS` | Comma-separated admin user IDs | `602196268,987654321` | Yes |
| `TIMEZONE` | Timezone for scheduled posts | `Asia/Jerusalem` | Yes |
| `DB_PATH` | Path to SQLite database | `./data/bot.db` | Optional (default: `./data/bot.db`) |
| `GOALS_TOPIC_ID` | Forum topic ID for goals channel | `123` | Yes |
| `TELEGRAM_API_ID` | Telegram API ID for automatic MTProto forum-topic sync | `123456` | Required for full topic sync |
| `TELEGRAM_API_HASH` | Telegram API hash for automatic MTProto forum-topic sync | `abcdef...` | Required for full topic sync |
| `TELEGRAM_SESSION_STRING` | Authorized user MTProto session string for forum-topic sync | `1A...` | Required for full topic sync |
| `BOTSON_TOPIC_SYNC_INTERVAL_SECONDS` | Forum-topic reconciliation interval | `21600` | Optional |
| `BOTSON_TOPIC_SYNC_FIRST_SECONDS` | First reconciliation delay after startup | `10` | Optional |

### Finding Your Group ID

Run the bot once, then have someone post in the group. Check the logs for a line like:
```
Chat ID: -1003873409631
```

Add this to `.env` as `GROUP_ID`.

### Finding Admin IDs

Ask admins to send `/start` to the bot in DM and check the logs for their user ID.

---

## Docker Deployment

### Build the Image

```bash
docker build -t elhoriim-bot .
```

### Run Locally with Docker

```bash
docker run --env-file .env elhoriim-bot
```

### Deploy to Railway

1. Create a free Railway account at https://railway.app
2. Push your code to GitHub
3. Connect Railway to your GitHub repo
4. Railway auto-detects the Dockerfile
5. Add environment variables in Railway dashboard (BOT_TOKEN, GROUP_ID, etc.)
6. Deploy — Railway will build and run the container

---

## Bot Commands

All commands are in Hebrew and work in the group or DM with the bot.

| Command | Description | Access |
|---------|-----------|--------|
| `/start` | Initialize bot connection | DM only |
| `/help` | Show available commands | Everyone |
| `/karma` | View your karma points | Group |
| `/karma @user` | Check someone's karma | Group |
| `/leaderboard` | Show top 10 karma earners | Group |
| `/streak` | View your goals participation streak | Group |
| `/stats` | Group activity statistics | Admin only |
| `/whitelist <pattern>` | Whitelist a spam detection pattern | Admin only |
| `/resetkarma` | Reset karma for a new season | Admin only |

### Example Usage

```
User: /karma
Bot: אתה יש 15 נקודות קארמה! 🌟

User: /leaderboard
Bot: 🥇 Alice — 42 points
     🥈 Bob — 38 points
     🥉 Carol — 35 points
     ... (7 more)

User: (in reply to someone's message) +1
Bot: (reacts with ✨ emoji)
```

---

## Database Schema

The bot uses SQLite with the following tables:

### members
Tracks all group members and their karma balance.

```
- user_id (PRIMARY KEY)
- username
- display_name
- joined_at (timestamp)
- karma_points (default: 0)
```

### karma_log
Audit trail of all karma transactions.

```
- id (PRIMARY KEY, auto-increment)
- giver_id
- receiver_id
- timestamp
- message_id
```

### daily_prompts
Stores prompts and tracks their usage to enable rotation.

```
- id (PRIMARY KEY, auto-increment)
- type (morning or evening)
- text (Hebrew prompt)
- last_used_at (timestamp, null if unused)
```

### spam_log
Records all moderation actions for admin review.

```
- id (PRIMARY KEY, auto-increment)
- user_id
- message_text
- rule_triggered (rule name)
- action (delete, warn, mute, etc.)
- timestamp
```

### streaks
Tracks goal participation streaks.

```
- user_id (PRIMARY KEY)
- current_streak (default: 0)
- longest_streak (default: 0)
- last_post_date (date)
```

---

## Project Structure

```
elhoriim-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py                      # Bot entry point and command handlers
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── welcome.py               # New member welcome messages
│   │   ├── goals.py                 # Daily prompts and streak tracking
│   │   ├── karma.py                 # Karma give/check/leaderboard
│   │   ├── antispam.py              # Spam detection and moderation
│   │   └── roundup.py               # Weekly activity digest
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py                # SQL schema definitions
│   │   └── db.py                    # Async database operations
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── jobs.py                  # Scheduled tasks (daily prompts, weekly roundup)
│   └── utils/
│       ├── __init__.py
│       ├── config.py                # YAML config and env var loading
│       └── helpers.py               # Shared utilities (admin check, rate limiting)
├── config/
│   ├── prompts.yaml                 # 30+ morning and evening prompts
│   ├── settings.yaml                # Bot settings and thresholds
│   └── spam_patterns.yaml           # Regex patterns for spam detection
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Container image definition
├── .env.example                     # Environment variable template
├── .gitignore                       # Git ignore patterns
└── README.md                        # This file
```

---

## Configuration Files

### config/prompts.yaml

Stores morning and evening prompts in Hebrew. The bot randomly selects unused prompts and resets the pool when all are exhausted.

```yaml
morning:
  - "☀️ בוקר טוב! מה המטרה שלכם להיום?"
  - "מה דבר אחד שאתם רוצים להספיק היום?"
  # ... (30+ variations)

evening:
  - "🌙 ערב טוב! מה הדבר הטוב שקרה לכם היום?"
  - "על מה אתם גאים מהיום?"
  # ... (30+ variations)
```

### config/settings.yaml

Bot-wide configuration like group IDs, topic IDs, and moderation thresholds.

```yaml
group_id: -1003873409631
topics:
  new_members: 123
  goals: 456
  general: 789

scheduling:
  morning_time: "08:00"
  evening_time: "21:00"
  roundup_day: "friday"
  roundup_time: "18:00"

karma:
  max_daily: 5
  min_interval_seconds: 0

antispam:
  new_member_grace_period_days: 7
  link_threshold: 3
  duplicate_threshold: 3
```

### config/spam_patterns.yaml

Regex patterns for detecting crypto scams, betting spam, adult content, etc.

```yaml
patterns:
  crypto:
    - "bitcoin|ethereum|crypto"
    - "investment opportunity|guaranteed returns"
  
  betting:
    - "casino|slot machine|bet now"
  
  adult:
    - # ... patterns
```

---

## Troubleshooting

### Bot doesn't start
- **Check Python version:** `python --version` (must be 3.11+)
- **Check dependencies:** `pip list | grep python-telegram-bot`
- **Check logs:** Look for error messages in console output
- **Verify token:** Ensure BOT_TOKEN in `.env` is correct and from @BotFather

### Bot joins group but doesn't respond
- **Check privacy settings:** `/setprivacy` with @BotFather should be DISABLED
- **Check admin permissions:** Bot needs Delete messages, Ban users, Pin messages, Manage topics
- **Check ADMIN_IDS:** Make sure you're listed in the ADMIN_IDS variable
- **Check logs:** Run locally first to see detailed errors

### Daily prompts don't appear
- **Check GOALS_TOPIC_ID:** Verify it's set in `.env`
- **Check timezone:** Ensure TIMEZONE is set to `Asia/Jerusalem`
- **Check scheduler logs:** Look for APScheduler warnings in console
- **Test manually:** Run `python -m bot.main` and wait 5 seconds for a test message

### Karma reactions don't appear
- **Check privacy:** Bot must have privacy disabled
- **Check message format:** Replies must contain exactly `+1`, `תודה`, or `👏`
- **Check self-karma:** Can't give karma to yourself or to bots

### Docker build fails
- **Check Python version:** Dockerfile uses `python:3.11-slim`
- **Check internet:** Docker needs to download base image and dependencies
- **Check syntax:** Ensure Dockerfile has no typos

---

## Development & Testing

### Run Tests

The project includes basic e2e tests. Run with:

```bash
pytest tests/
```

### Debug Mode

For verbose logging:

```bash
export LOG_LEVEL=DEBUG
python -m bot.main
```

### Database Inspection

Inspect the SQLite database:

```bash
sqlite3 ./data/bot.db

# List tables
.tables

# View karma points
SELECT username, karma_points FROM members ORDER BY karma_points DESC;

# View recent karma transactions
SELECT giver_id, receiver_id, timestamp FROM karma_log ORDER BY timestamp DESC LIMIT 10;
```

---

## Contributing

This bot is tailored for the "אלהוריים וזה" community. If you want to adapt it for your own group:

1. Fork the repository
2. Update `config/prompts.yaml` with your group's language and tone
3. Modify `config/spam_patterns.yaml` for your needs
4. Update welcome messages in `bot/handlers/welcome.py`
5. Create a new environment and test locally
6. Deploy using Docker or Railway

---

## License

This project is private to the אלהוריים וזה community. Use, modification, and distribution require permission from the group admins.

---

## Support

For issues or questions:
- Open an issue in the repository
- DM an admin in the אלהוריים וזה group
- Check bot logs: `tail -f /app/bot.log` (if deployed)

---

## Deployment Checklist

Before going live with a new instance:

- [ ] Bot token obtained from @BotFather
- [ ] `/setjoingroups` enabled
- [ ] `/setprivacy` disabled
- [ ] Bot added to group with admin permissions
- [ ] `הישגים ומטרות 🌟` topic created
- [ ] All 6 environment variables set in `.env`
- [ ] Database initialized: `python -m bot.main` (exit after 5 seconds)
- [ ] Daily prompts appear at 08:00 and 21:00 (test locally first)
- [ ] Test karma: Reply `+1` to a message
- [ ] Test spam detection: Send a forwarded message
- [ ] Deploy to Railway/Render with environment variables
- [ ] Verify bot still works after deployment
- [ ] Monitor logs for 24 hours after launch

---

**Built for the אלהוריים וזה community with care. Happy engaging!**
