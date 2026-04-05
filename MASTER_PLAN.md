# MASTER PLAN — אלהוריים וזה Telegram Bot (robotnik)

> Telegram bot for the "אלהוריים וזה" childfree community (81 members, forum-style group with topic channels). All user-facing messages in Hebrew.

## Summary

| ID | Phase | Task | Status | Priority | Deps |
|----|-------|------|--------|----------|------|
| T-001 | 0 | Project scaffolding & git init | DONE | P0 | — |
| T-002 | 0 | Database layer (SQLite + aiosqlite) | DONE | P0 | T-001 |
| T-003 | 0 | Config system (YAML loader + settings) | DONE | P0 | T-001 |
| T-004 | 0 | Bot entry point & handler registration | DONE | P0 | T-002, T-003 |
| T-005 | 0 | Scheduler setup (JobQueue) | DONE | P0 | T-004 |
| T-006 | 0 | Docker + deployment config | DONE | P0 | T-001 |
| T-007 | 1 | Welcome bot handler | DONE | P0 | T-004 |
| T-008 | 1 | Anti-spam handler | DONE | P0 | T-004, T-002 |
| T-009 | 2 | Prompts YAML (31 morning + 31 evening) | DONE | P0 | T-003 |
| T-010 | 2 | Daily goals handler + prompt rotation | DONE | P0 | T-005, T-009 |
| T-011 | 2 | Streak tracking system | DONE | P0 | T-010, T-002 |
| ~~T-012~~ | 3 | ~~Karma handler~~ → Levels system | DONE | P1 | T-004, T-002 |
| ~~T-013~~ | 3 | ~~Karma anti-abuse~~ → Activity-based points | DONE | P1 | T-012 |
| T-014 | 3 | Weekly levels leaderboard (scheduled) | DONE | P1 | T-012, T-005 |
| T-015 | 4 | Weekly roundup handler (scheduled) | DONE | P2 | T-005, T-012, T-002 |
| T-016 | — | README.md with setup guide | DONE | P1 | T-006 |
| T-017 | — | End-to-end testing & verification | TODO | P1 | All |
| T-018 | 5 | Discussion prompts in topic channels | DONE | P1 | T-005, T-003 |
| T-019 | 5 | Discussion prompts YAML (per-channel questions) | DONE | P1 | T-003 |
| T-020 | 6 | Events/activities system — create & announce | DONE | P1 | T-004, T-002 |
| T-021 | 6 | Events DB table + RSVP tracking | DONE | P1 | T-020 |
| T-022 | 6 | Event reminders (scheduled) | DONE | P2 | T-020, T-005 |
| T-023 | 7 | Trivia game engine | DONE | P1 | T-004, T-002 |
| T-024 | 7 | Trivia questions YAML (Hebrew) | DONE | P1 | T-003 |
| T-025 | 7 | Trivia scoring + leaderboard | DONE | P1 | T-023 |
| T-026 | 7 | Scheduled trivia sessions | DONE | P2 | T-023, T-005 |
| T-027 | 8 | Dashboard — FastAPI backend + API routes | DONE | P1 | T-002 |
| T-028 | 8 | Dashboard — HTML templates (9 pages) | DONE | P1 | T-027 |
| T-029 | 8 | Dashboard — auth (password login) | DONE | P1 | T-027 |
| T-030 | — | VPS deployment config (Docker Compose) | TODO | P1 | T-006 |
| T-031 | 9 | Levels system (replaced karma/stars) | DONE | P1 | T-012 |
| T-032 | 9 | Feature toggles (all off by default) | DONE | P1 | T-004 |
| T-033 | 9 | Welcome via DM (not in-channel) | DONE | P1 | T-007 |
| T-034 | 9 | Activity log DB + dashboard page | DONE | P1 | T-027 |
| T-035 | 9 | Anti-spam dry_run mode | DONE | P1 | T-008 |
| T-036 | 9 | Hebrew spam patterns | DONE | P1 | T-008 |
| T-037 | 9 | Dashboard feature toggles UI | DONE | P1 | T-032, T-027 |
| T-038 | 9 | Messages reference doc (RTL HTML) | DONE | P2 | — |
| T-039 | — | Auto-detect topic IDs from group | TODO | P1 | T-004 |
| T-040 | — | Auto-create goals channel | TODO | P1 | T-039 |
| T-041 | 10 | Dashboard redesign — shadcn/ui dark theme | DONE | P1 | T-028 |
| T-042 | 10 | Per-tool settings + Telegram previews | DONE | P1 | T-041 |
| T-043 | 10 | Prompts page — pool preview + AI gen | DONE | P1 | T-041 |
| T-044 | 10 | Schedule timeline + editable config | DONE | P1 | T-043 |
| T-045 | 11 | Top navbar — move brand to horizontal header | TODO | P0 | T-041 |
| T-046 | 11 | Day-of-week selectors for scheduled items | TODO | P0 | T-044 |
| T-047 | 11 | Auto-detect forum topics from messages | TODO | P0 | T-004 |
| T-048 | 11 | Topic dropdown in schedule (replace ID input) | TODO | P0 | T-047 |
| T-049 | 11 | Clickable dots — instant visual toggle on/off | TODO | P1 | T-044 |
| T-050 | 11 | Schedule tab — show loaded question per slot | TODO | P1 | T-044 |
| T-051 | — | End-to-end dashboard QA pass | TODO | P1 | T-045..T-050 |
| T-052 | — | Single-instance bot guard (PID lock file) | TODO | P1 | T-004 |

## Detailed Tasks

---

#### T-001: Project scaffolding & git init
**Phase:** 0 — Setup | **Priority:** P0 | **Status:** TODO

Create the full directory structure:
```
elhoriim-bot/
├── bot/
│   ├── __init__.py
│   ├── main.py
│   ├── handlers/
│   │   ├── __init__.py
│   │   ├── welcome.py
│   │   ├── goals.py
│   │   ├── karma.py
│   │   ├── antispam.py
│   │   └── roundup.py
│   ├── database/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   └── db.py
│   ├── scheduler/
│   │   ├── __init__.py
│   │   └── jobs.py
│   └── utils/
│       ├── __init__.py
│       ├── config.py
│       └── helpers.py
├── config/
│   ├── prompts.yaml
│   ├── settings.yaml
│   └── spam_patterns.yaml
├── requirements.txt
├── Dockerfile
├── .env.example
└── .gitignore
```

Files: requirements.txt, .gitignore, .env.example, all `__init__.py` stubs

---

#### T-002: Database layer (SQLite + aiosqlite)
**Phase:** 0 — Setup | **Priority:** P0 | **Status:** TODO | **Deps:** T-001

- `bot/database/models.py` — SQL schema definitions for all 5 tables:
  - `members` (user_id PK, username, display_name, joined_at, karma_points DEFAULT 0)
  - `karma_log` (id PK autoincrement, giver_id, receiver_id, timestamp, message_id)
  - `daily_prompts` (id PK autoincrement, type TEXT, text TEXT, last_used_at TIMESTAMP NULL)
  - `spam_log` (id PK autoincrement, user_id, message_text, rule_triggered, action, timestamp)
  - `streaks` (user_id PK, current_streak DEFAULT 0, longest_streak DEFAULT 0, last_post_date DATE NULL)
- `bot/database/db.py` — Async DB class: init/migrate, CRUD helpers for each table
- DB path from `DB_PATH` env var, default `./data/bot.db`
- Auto-create `data/` directory if missing

---

#### T-003: Config system (YAML loader + settings)
**Phase:** 0 — Setup | **Priority:** P0 | **Status:** TODO | **Deps:** T-001

- `bot/utils/config.py` — Load YAML files from `config/` directory
- `config/settings.yaml` — Bot settings: group_id, topic IDs, thresholds, schedule times
- `config/spam_patterns.yaml` — Regex patterns for spam detection
- `config/prompts.yaml` — Placeholder (filled in T-009)
- Environment variables loaded via `python-dotenv`

---

#### T-004: Bot entry point & handler registration
**Phase:** 0 — Setup | **Priority:** P0 | **Status:** TODO | **Deps:** T-002, T-003

- `bot/main.py` — Application entry point:
  - Initialize `python-telegram-bot` Application with bot token
  - Initialize database on startup
  - Register all handlers (welcome, goals, karma, antispam, roundup)
  - Register command handlers (/start, /help, /karma, /leaderboard, /streak, /stats, /whitelist, /resetkarma)
  - Setup APScheduler
  - Graceful shutdown handling
  - Logging configuration
- `bot/utils/helpers.py` — Shared utilities: admin check, bot check, rate limiting

---

#### T-005: Scheduler setup (APScheduler)
**Phase:** 0 — Setup | **Priority:** P0 | **Status:** TODO | **Deps:** T-004

- `bot/scheduler/jobs.py` — Define all scheduled jobs:
  - Morning prompt: 08:00 Asia/Jerusalem daily
  - Evening prompt: 21:00 Asia/Jerusalem daily
  - Weekly karma leaderboard: Friday 18:00
  - Weekly roundup: Friday 18:00
- Use `AsyncIOScheduler` with `CronTrigger`
- Jobs call handler functions from respective modules

---

#### T-006: Docker + deployment config
**Phase:** 0 — Setup | **Priority:** P0 | **Status:** TODO | **Deps:** T-001

- `Dockerfile` — Python 3.11-slim, copy code, install deps, run bot
- `requirements.txt`:
  - python-telegram-bot[ext]>=20.0
  - aiosqlite>=0.19.0
  - APScheduler>=3.10.0
  - PyYAML>=6.0
  - python-dotenv>=1.0.0
- `.env.example` — Template with all env vars documented

---

#### T-007: Welcome bot handler
**Phase:** 1 | **Priority:** P0 | **Status:** TODO | **Deps:** T-004

- `bot/handlers/welcome.py`
- Trigger: `ChatMemberHandler` or `MessageHandler` for `new_chat_members`
- Send to "מצטרפים חדשים + עדכונים" topic (via `message_thread_id`)
- Use member's Telegram display name (`first_name`)
- Skip if `is_bot` is True
- Batch: collect joins within 30 seconds, send single combined message
- Hebrew welcome template from spec
- Register/update member in database

---

#### T-008: Anti-spam handler
**Phase:** 1 | **Priority:** P0 | **Status:** TODO | **Deps:** T-004, T-002

- `bot/handlers/antispam.py`
- Run on every incoming message (high priority group handler)
- Detection rules:
  1. Forwarded from unknown channels → delete + warn via DM
  2. 3+ links from members < 7 days old → hold for admin review
  3. Crypto/betting/adult regex patterns (from `spam_patterns.yaml`) → auto-delete + log
  4. Repeated identical messages > 3 in 60s → delete dupes + mute 10 min
  5. New member message in first 30 seconds → flag for review
- Log all actions to admin (via DM or private admin channel)
- `/whitelist <pattern>` command for admins
- Store spam events in `spam_log` table

---

#### T-009: Prompts YAML (30+ morning + 30+ evening Hebrew prompts)
**Phase:** 2 | **Priority:** P0 | **Status:** TODO | **Deps:** T-003

- `config/prompts.yaml` — Structure:
  ```yaml
  morning:
    - "☀️ בוקר טוב! מה המטרה שלכם להיום?"
    - "מה דבר אחד שאתם רוצים להספיק היום?"
    ... (30+ variations)
  evening:
    - "🌙 ערב טוב! מה הדבר הטוב שקרה לכם היום?"
    - "על מה אתם גאים מהיום?"
    ... (30+ variations)
  ```
- All prompts in Hebrew, varied tone (motivational, reflective, fun, grateful)

---

#### T-010: Daily goals handler + prompt rotation
**Phase:** 2 | **Priority:** P0 | **Status:** TODO | **Deps:** T-005, T-009

- `bot/handlers/goals.py`
- Prompt rotation logic: pick random unused prompt, mark used in DB, reset pool when all used
- Morning job (08:00): send random morning prompt to "הישגים ומטרות 🌟" topic
- Evening job (21:00): send random evening prompt to same topic
- Track participation for streak system

---

#### T-011: Streak tracking system
**Phase:** 2 | **Priority:** P0 | **Status:** TODO | **Deps:** T-010, T-002

- Part of `bot/handlers/goals.py`
- When a member posts in the goals channel, update their streak in `streaks` table
- `/streak` command shows current and longest streak
- Celebrate milestones (7-day, 30-day streaks) with a congratulatory message

---

#### T-012: Karma handler (give/check/leaderboard)
**Phase:** 3 | **Priority:** P1 | **Status:** TODO | **Deps:** T-004, T-002

- `bot/handlers/karma.py`
- Detect karma triggers in reply messages: `+1`, `תודה`, `👏`
- Add karma point to original poster, log in `karma_log`
- React with confirmation emoji (silent)
- Commands:
  - `/karma` — show your points
  - `/karma @user` — check someone's points
  - `/leaderboard` — top 10

---

#### T-013: Karma anti-abuse rules
**Phase:** 3 | **Priority:** P1 | **Status:** TODO | **Deps:** T-012

- No self-karma (giver_id != receiver_id)
- Max 5 karma given per user per day (check karma_log)
- Can't give karma to bots
- Silent rejection (no error messages to avoid spam)

---

#### T-014: Weekly karma leaderboard (scheduled)
**Phase:** 3 | **Priority:** P1 | **Status:** TODO | **Deps:** T-012, T-005

- Scheduled job: every Friday at 18:00 Israel time
- Post top 10 karma earners to general channel
- Hebrew formatting with emoji medals (🥇🥈🥉)

---

#### T-015: Weekly roundup handler (scheduled)
**Phase:** 4 | **Priority:** P2 | **Status:** TODO | **Deps:** T-005, T-012, T-002

- `bot/handlers/roundup.py`
- Friday 18:00 Israel time → "כל מה שאין לו ערוץ" (General) topic
- Contents:
  - Most active topic channels this week (message count per topic)
  - Top 3 karma earners
  - Number of new members this week
  - Quiet channels that could use some love
  - Achievement streak highlights
- Hebrew formatting

---

#### T-016: README.md with setup guide
**Priority:** P1 | **Status:** TODO | **Deps:** T-006

Full setup guide covering:
- BotFather setup steps
- Adding bot to group + admin permissions
- Creating the הישגים ומטרות topic
- Environment variables
- Local development
- Docker deployment
- Railway/Render deployment

---

#### T-017: End-to-end testing & verification
**Priority:** P1 | **Status:** TODO | **Deps:** All

- Verify bot starts without errors
- Test welcome message (add a test user)
- Test spam detection rules
- Test prompt rotation
- Test karma give/check/leaderboard
- Test scheduled jobs fire correctly
- Test graceful shutdown

---

#### T-045: Top navbar — move brand to horizontal header
**Phase:** 11 — Dashboard UX | **Priority:** P0 | **Status:** TODO | **Deps:** T-041

Move "Botson" from sidebar header to a horizontal top navbar spanning the full width (like Contractor app reference). Sidebar starts below the navbar. This gives a more professional app feel.

Files: `dashboard/templates/base.html`

---

#### T-046: Day-of-week selectors for scheduled items
**Phase:** 11 — Dashboard UX | **Priority:** P0 | **Status:** TODO | **Deps:** T-044

Each scheduled item (morning, evening, discussions, roundup) needs clickable day-of-week chips: א׳ ב׳ ג׳ ד׳ ה׳ ו׳ ש׳. Selected days are highlighted, unselected are gray. Saved to `settings.yaml` under each schedule item.

Requires:
- Update schedule config format to include days per item
- Update schedule API endpoint
- Update bot scheduler to check days
- Add day chips UI to prompts schedule tab

Files: `dashboard/templates/prompts.html`, `dashboard/app.py`, `config/settings.yaml`, `bot/scheduler/jobs.py`

---

#### T-047: Auto-detect forum topics from messages
**Phase:** 11 — Bot | **Priority:** P0 | **Status:** TODO | **Deps:** T-004

Telegram Bot API has no `getForumTopics` method. Workaround: track `message_thread_id` + topic name from every incoming message.

- Add `forum_topics` table to DB: `topic_id (PK), name, last_seen_at`
- Add a low-priority message handler that captures `message.message_thread_id` and `message.reply_to_message.forum_topic_created.name` (or from `message.forum_topic_created`)
- Expose via `/api/topics/forum` dashboard endpoint

Files: `bot/database/models.py`, `bot/database/db.py`, `bot/handlers/` (new tracker), `dashboard/app.py`

---

#### T-048: Topic dropdown in schedule (replace ID input)
**Phase:** 11 — Dashboard UX | **Priority:** P0 | **Status:** TODO | **Deps:** T-047

Replace all `<input type="number">` Topic ID fields with `<select>` dropdowns populated from the forum_topics table. Dropdown shows topic name + ID. Auto-refreshes when page loads.

Files: `dashboard/templates/prompts.html`, `dashboard/app.py`

---

#### T-049: Clickable dots — instant visual toggle on/off
**Phase:** 11 — Dashboard UX | **Priority:** P1 | **Status:** TODO | **Deps:** T-044

Schedule timeline dots must visually toggle instantly on click:
- Colored dot + full opacity = on
- Gray dot + 40% opacity = off
- JS swaps CSS classes immediately, then saves async via API
- Uses a color map per dot type (amber=morning, sky=discussion, indigo=evening, orange=roundup)

Files: `dashboard/templates/prompts.html`

---

#### T-050: Schedule tab — show loaded question per slot
**Phase:** 11 — Dashboard UX | **Priority:** P1 | **Status:** TODO | **Deps:** T-044

Each schedule row should show the actual next message/question that will be sent in that slot, not just "קטגוריה אקראית מהמאגר". Pull from the prompts pool to show a real preview.

Files: `dashboard/templates/prompts.html`

---

#### T-051: End-to-end dashboard QA pass
**Priority:** P1 | **Status:** TODO | **Deps:** T-045..T-050

- All pages load without 500 errors
- All toggles save correctly
- Schedule saves and reflects in timeline
- Topic dropdowns populate
- Day-of-week chips save
- Mobile responsive check
- RTL consistency check

---

## Environment Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `BOT_TOKEN` | Bot API token from BotFather | `123456:ABC-DEF...` |
| `GROUP_ID` | Telegram group ID | `-1003873409631` |
| `ADMIN_IDS` | Comma-separated admin user IDs | `602196268` |
| `TIMEZONE` | For scheduled posts | `Asia/Jerusalem` |
| `DB_PATH` | SQLite database path | `./data/bot.db` |
| `GOALS_TOPIC_ID` | Forum topic ID for goals channel | TBD after creation |

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Framework | python-telegram-bot v20+ |
| Database | SQLite via aiosqlite |
| Scheduler | APScheduler |
| Config | YAML files |
| Hosting | Railway or Render |
