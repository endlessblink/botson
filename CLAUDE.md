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

### Dashboard-Only Features (no Telegram equivalent needed)

- Schedule configuration (times, days, topics)
- Feature toggles (on/off per feature)
- Prompt pool management (AI generate, preview)
- Spam pattern management
- Activity log viewer
- Bot health monitoring
- Config reload / bot restart

## Schedule & Content Rules

- When updating the bot's schedule or weekly plan, **always update `docs/week-plan.html`** to reflect the changes.
- When suggesting Hebrew text for review, render it in `docs/review.html` (RTL page) so the user can read it properly.
- Anti-spam runs in `dry_run` mode by default — detect and log only, no deletions.

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
- SIGHUP reloads schedule config without restart
