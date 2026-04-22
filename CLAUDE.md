# Robotnik (Botson) — Project Directives

## Dashboard Parity Rule

**Every bot capability that exists as a Telegram slash command MUST have a dashboard equivalent.**

The dashboard is the primary control interface. Users should never need to use Telegram commands to manage the bot. When adding any new bot feature:

1. Add the Telegram command handler (for in-group use)
2. Add the dashboard UI for the same action (for admin management)
3. Both must use the same underlying logic/database

### Command → Dashboard Mapping

| Command | Dashboard Location | Status |
|---------|-------------------|--------|
| `/level` | Levels page — leaderboard table | Done |
| `/leaderboard` | Levels page — full leaderboard | Done |
| `/resetlevels` | Levels page — reset button | Done |
| `/streak` | Levels/Members page — streak column | TODO (T-069) |
| `/stats` | Overview/Health page — stats cards | TODO (T-071) |
| `/whitelist` | Spam page — whitelist textarea | Partial (T-070) |
| `/event` | Events page — create form | Partial (T-072) |
| `/events` | Events page — events table | Done |
| `/trivia` | Trivia page — start button | TODO (T-067) |
| `/triviascore` | Trivia page — leaderboard | Done |
| `/triviatop` | Trivia page — leaderboard | Done |
| Send message | Health page — send test | TODO (T-064) |
| Morning prompt | Prompts page — send now | TODO (T-065) |
| Discussion prompt | Prompts page — send now | TODO (T-066) |
| Weekly roundup | Prompts page — send now | TODO (T-073) |
| Topic routing | Moderation page — observation stats + rules | Phase 0 (observe-only) |

### Dashboard-Only Features (no Telegram equivalent needed)

- Schedule configuration (times, days, topics)
- Feature toggles (on/off per feature)
- Prompt pool management (AI generate, preview)
- Spam pattern management
- Activity log viewer
- Bot health monitoring
- Config reload / bot restart

## Schedule & Content Rules

- When updating the bot's schedule or weekly plan, **always update `pages/week-plan.html`** to reflect the changes.
- **NEVER present Hebrew text options in the terminal** — always render on the dashboard `/review` page (route: `dashboard/app.py:review_page`, template: `dashboard/templates/review.html`) for approval. Add drafts by appending dicts to the `pending` list in `dashboard/app.py` with fields: `title`, `channel`, `when`, `preview` (or `options` for a choice), `note`. This includes message drafts, discussion questions, poll text, event announcements — anything Hebrew.
- **Project layout:** `docs/` contains markdown documentation only. Hand-built HTML snapshots/visualizations (week plan, activity reports, message previews) live in `pages/`.
- Anti-spam runs in `dry_run` mode by default — detect and log only, no deletions.
- **Topic routing** (off-topic detection) runs in Phase 0 `observe` mode — classifies messages against `config/topic_rules.yaml` and logs to `topic_observations` table, no user-visible action. Controlled via `topic_routing: {enabled, mode}` in `settings.yaml` and the `/moderation` dashboard page.

## Deploy

**VPS is a deploy target, not a workspace.** Never edit `/opt/robotnik/*` files directly on the VPS. Every change goes through: commit on local → push → run `scripts/deploy.sh` on VPS.

VPS at `/opt/robotnik` is a git checkout tracking `origin/main`, owned by `botson:botson`. Standard workflow:

```bash
git push origin main
ssh -i ~/.ssh/id_ed25519 root@84.46.253.137 '/opt/robotnik/scripts/deploy.sh'
```

`deploy.sh` runs `git fetch + reset --hard origin/main` as `botson`, reinstalls pinned deps, and restarts both services. Migrations self-run on bot startup via `Database._migrate()` (idempotent `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE`).

Before starting a session or merging a risky PR, verify the VPS matches origin:

```bash
scripts/verify-sync.sh       # exits 0 if clean and in sync, 1 on drift
```

`media/covers/`, `data/`, `.env`, `.doppler/`, `backups/` are in `.gitignore` and never touched by deploys.

**Never use rsync from local working tree** — it captures unstaged changes and overwrites VPS file ownership (`botson` → `endlessblink`), breaking the systemd services. Always deploy from a committed git state via `deploy.sh`.

VPS service users:
- `botson.service` runs as `botson` (calls `run_bot.sh` → `python -m bot.main`)
- `botson-dashboard.service` runs as `botson` (calls `python -m dashboard.server` on port 8080)
- `WorkingDirectory=/opt/robotnik` for both — must be readable by `botson`

After any file change on VPS (manual edit, deploy, etc.), always: `chown -R botson:botson /opt/robotnik`.

## Tech Stack

- **Bot**: Python 3.12 + python-telegram-bot v20+
- **Dashboard**: FastAPI + Jinja2 + Tailwind CSS (CDN)
- **Database**: SQLite (shared between bot and dashboard, WAL mode)
- **Design**: shadcn/ui dark theme, Hebrew RTL
- **Config**: YAML files in `config/`, editable from dashboard

## Key Patterns

- Features can be bool or dict `{enabled, groups}` — always check both
- Hebrew week: 0=Sunday, Python weekday: 0=Monday — use `_hebrew_to_python_days()`
- Bot logs to `data/bot.log` (RotatingFileHandler)
- PID lock at `data/bot.pid`
- Version tracking at `data/bot.version`
- Hot reload via `data/reload` flag file (bot checks every 5s), triggered by dashboard on schedule save
- **Never use `drop_pending_updates=True`** — it causes the bot to miss all queued messages on restart
- **Always verify before assuming** — check logs/DB with full timestamps before claiming something did or didn't happen. Never guess from memory.
- **Always check Israel time before any date/time work** — run `date +"%Y-%m-%d %H:%M %A"` before writing schedules, checking logs, or mentioning times. System clock is IDT. Never guess.
- **Do not act on uncertain topic/channel assumptions.** If a target topic/channel is not 100% verified from the live source of truth, stop and ask. Never infer from stale notes, guessed labels, or partial memory.

## Telegram Group Info

- **Main group**: אלהוריים וזה (childfree community, 81 members)
- **Group ID**: `-1003873409631`
- **Test group**: Sherlocks Den
- **Test Group ID**: `-1003747545764`
- **Bot username**: `@thebotstonbot`
- **Bot name**: Botson

### Forum Topic IDs (main group)

**Source of truth is the `verified_forum_topics` table in `data/bot.db`**, populated by the dashboard's dot-test workflow (Settings page → "send dot"). The `forum_topics.name` column is NOT trustworthy — it can be stale, overwritten, or polluted by user message text.

#### Dot-verified (safe to use)

| Topic ID | Channel Name | Category Key | Verified |
|----------|-------------|-------------|----------|
| 7 | אל הוריים טבעונים וצמחונים | vegan | 2026-04-22 via dot |
| 54 | סרטים סדרות וכו | movies | 2026-04-22 via dot |
| 59 | אל הוריים/יות פנויים פנויות | singles | 2026-04-22 via dot |
| 153 | מצחיק / מגניב | funny | 2026-04-22 via dot |
| 335 | כל מה שחמוד | cute | 2026-04-22 via dot |
| 341 | מצטרפים חדשים + עדכונים | welcome | 2026-04-22 via dot |
| 347 | ערוץ אומנות ויצירה | art | 2026-04-22 via dot |
| 442 | אנימה / קומיקס וכל הדברים הגיקיים | geek | 2026-04-22 via dot |
| 1431 | פוליטיקה / גיאו-פוליטיקה וכל היתר | politics | 2026-04-22 via dot |
| 1517 | גיימינג + משחקי לוח | gaming | 2026-04-22 via dot |
| 2184 | יום יום | goals | 2026-04-22 via dot |
| 3113 | Ai וטכנולוגיה | ai_en | 2026-04-22 via dot (renamed from "AI & Tech") |
| 4037 | הפינה של בוטסון | botson_corner | 2026-04-22 via dot (new — central home for bot-generated content) |

All 13 main-group topics were dot-verified on 2026-04-22 in a single session. The `כל מה שאין לו ערוץ` (Telegram "General") topic exists but is deliberately **not** used by the bot — there is no trusted way to target it via Bot API, and trying to use it was the root of the incident. No handler writes to General.

#### Routing table

Which handler posts to which topic is owned by the `bot_message_routing` table (one row per handler), editable live via the dashboard Settings page → "ניתוב פיצ'רים לערוצים". The DB, not code, decides where each feature lands. Seeded defaults:

| Handler | Default play_topic_id |
|---|---|
| `trivia_round` / `trivia_scheduled` | `4037` botson_corner |
| `emoji_puzzle` / `free_games` | `4037` botson_corner |
| `weekly_roundup` / `weekly_leaderboard` | `4037` botson_corner |
| `events_publish` / `events_reminder` | `341` welcome |

`trivia_round` also supports `teaser_topic_ids` — optional short announcements in theme-matched topics (e.g., movie trivia → teaser in `movies` / 54) set per-launch from the dashboard.

#### Send guard (bot/utils/topic_guard.py)

Every outbound Telegram send goes through `safe_send`. Rules:
- DM (positive `chat_id`) → pass through.
- Test group (`TEST_GROUP_ID`) → pass through.
- Main group with `message_thread_id=None` → `UnverifiedTopicError` (no root sends).
- Main group with id not in `verified_forum_topics` → `UnverifiedTopicError`.
- `bypass_verification=True` skips the checks; only the dot-test workflow's `/api/bot/send-message?is_topic_discovery=true` path sets it.

Historical note: topic `7` was wrongly treated as the `general` / `כל מה שאין לו ערוץ` thread in earlier sessions — a dot test on 2026-04-22 revealed it is actually the vegan thread. See `docs/2026-04-22-trivia-topic-incident.md`.

### Level System

| Level | Points | Tag | Emoji |
|-------|--------|-----|-------|
| 1 | 0 | חדש/ה | 🌱 |
| 2 | 20 | פעיל/ה | ⭐ |
| 3 | 50 | כוכב/ת | 🌟 |
| 4 | 100 | סופרסטאר | 💫 |
| 5 | 250 | אגדה | 🔥 |
| 6 | 500 | אלוף/ה | 👑 |

### Point Values

| Action | Points |
|--------|--------|
| Message in group | 1 (max 10/day) |
| Goals channel post | 2 |
| Reply to bot prompt | 3 |
| Event RSVP | 3 |
| Correct trivia answer | 5 |
