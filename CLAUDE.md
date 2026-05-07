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

## Dashboard UI Change Guardrails

- **Disambiguate repeated UI names before editing.** In this dashboard, terms like "sidebar", "drawer", "panel", "planner", "review", and "schedule" can refer to multiple visible surfaces. Before changing UI for any ambiguous term, inspect the relevant templates/routes and confirm the exact target in one sentence, e.g. "I am changing the global dashboard sidebar in `base.html`, not the planner create drawer." If the target is still unclear, ask one short question before editing.
- **Global navigation lives in `dashboard/templates/base.html`.** The planner create side panel lives in `dashboard/templates/planner.html` and should be called the "planner drawer" or "create drawer", not the sidebar. Do not modify the planner drawer when the request is about the dashboard sidebar/navigation unless explicitly asked.
- **Preserve compactness unless the user asks for expansion.** The dashboard navigation should stay dense and scannable. Prefer grouping, ordering, and label clarity over larger cards, long descriptions, or extra vertical spacing.
- **Visually validate navigation changes before deploy.** For dashboard/sidebar/nav changes, run a headless Playwright CLI check or screenshot against the local dashboard before committing/deploying. Validate the intended labels are visible and the active route is highlighted.

## Trivia Round Scheduling

When scheduling a trivia game, follow these rules — they exist because the calendar listing must be honest about what will fire.

- **Every trivia game gets its own row** with `message_type='trivia_round'` and a `poll_options` payload like `{"pre_roll_s":30, "theme_label":"<תיוג>", "categories":["<קטגוריה>"], "question_count":5}`. Never hide a game inside a `discussion`-typed announcement and rely on the text-coercion fallback in `bot/handlers/calendar.py:_coerce_due_game_row` / `dashboard/app.py:_coerce_game_message_fields` — coercion is a safety net, not the convention.
- **Warm-up announcement runs at least 30 minutes before kickoff** (35 min is the default). Anything closer is too late. Warm-up text should contain `בעוד` or `מתחממים` so it stays a `discussion` and is not mistaken for a launcher.
- **No theme channel? Both rows live in `botson_corner` (topic 4037).** Only set a teaser in a different topic when a theme channel actually matches (e.g., movies trivia → teaser in topic 54). For music there is no dedicated channel today, so warm-up + game both stay in botson_corner.
- **Edit prod via the dashboard, never via SSH+SQL.** Calendar API is `GET/POST/PUT/DELETE /api/calendar`. To check the live schedule without SSH, fetch `GET /api/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD` (currently requires session auth; a read-only token is on the TODO list).

## Schedule & Content Rules

- When updating the bot's schedule or weekly plan, **always update `pages/week-plan.html`** to reflect the changes.
- **NEVER present Hebrew text options in the terminal** — always render on the dashboard `/review` page (route: `dashboard/app.py:review_page`, template: `dashboard/templates/review.html`) for approval. Add drafts by appending dicts to the `pending` list in `dashboard/app.py` with fields: `title`, `channel`, `when`, `preview` (or `options` for a choice), `note`. This includes message drafts, discussion questions, poll text, event announcements — anything Hebrew.
- **Project layout:** `docs/` contains markdown documentation only. Hand-built HTML snapshots/visualizations (week plan, activity reports, message previews) live in `pages/`.
- Anti-spam runs in `dry_run` mode by default — detect and log only, no deletions.
- **Topic routing** (off-topic detection) runs in Phase 0 `observe` mode — classifies messages against `config/topic_rules.yaml` and logs to `topic_observations` table, no user-visible action. Controlled via `topic_routing: {enabled, mode}` in `settings.yaml` and the `/moderation` dashboard page.

## Planner AI Populate Rules

- Populate must always use the suggest+confirm modal flow: `/api/weekplan/ai-suggest` returns candidate rows and writes nothing; `/api/weekplan/ai-suggest-commit` inserts only the approved checked rows as scheduled rows.
- Populate must suggest a balanced content mix, not a flood of discussion questions. The budgets live in `config/settings.yaml:ai_populate.caps`; adjust those values before changing Python.
- Day-level Populate is intentionally free: it uses schedule-configured times but does **not** restrict candidates by `schedule.*.days`. A Friday click may suggest morning, evening, discussion, trivia, emoji, facts, and weekly rows if those types are configured, enabled, routable, and free.
- Populate must never suggest slots before the current server time. For the current day/current week, skip past dates and past times; approval should only create future scheduled rows.
- Emoji Night suggestions must include a separate announcement row before the executable `emoji_puzzle` row. The lead time and subject are admin-configurable in `schedule.emoji_puzzle.{announcement_lead_minutes,theme_label,media_types}`; the runtime must use the same payload so the actual puzzle pool matches the modal subject. **The Emoji Night announcement row uses `message_type="trivia_warmup_rsvp"` (not `discussion`) so it gets the RSVP button.**
- No hardcoded slot config in code. Times come from `schedule.*`, discussion topics from `topics.discussions`, executable bot content topics from `bot_message_routing`, and trivia poll/warm-up defaults from `trivia.populate_defaults`.
- **Defaults that steer outcomes count as hardcoded.** `value="ישראל"` on a form, sort-by-pool-size when the largest pool correlates with one topic, `media_types=["movie","tv"]` fallback in emoji schedule — all rejected. Defaults must be blank, random, or operator-configured. Use random tiebreaks (`random.random()`).
- **Verify before claiming "all X cleaned".** Grep the full codebase before reporting a category-of-fix done. If scope uncertain, say "fixed the instances I found" with an explicit list. Premature completion claims are treated as overclaim.
- A `discussion` suggestion with a category must commit only to `settings.yaml.topics.discussions[category]`; reject mismatches instead of silently inserting into the wrong topic.
- Keep regression coverage for the production failure class: the suggest engine must return mixed types and must not write to `scheduled_messages` before approval.

## Warm-up RSVP system

Trivia and Emoji Night announcements use **`message_type="trivia_warmup_rsvp"`**. Calendar dispatch attaches an inline `🙋 אני בפנים!` button (callback `trivint_<scheduled_msg_id>`). Clicks are written to `trivia_interest_responses(scheduled_msg_id, user_id)`; when the count reaches `poll_options.min_ready_players`, a confirmation message fires in the warm-up topic.

- **Topic**: warm-up topic comes from the `trivia_warmup` row in `bot_message_routing` (seeded as topic 341 — מצטרפים חדשים + עדכונים). Never hardcode 341.
- **Default lead time**: `trivia.populate_defaults.warmup_offset_min: 60` (was 35 before 2026-05-07). `warmup_reminder_offset_min: 20` is wired (T-126 ✅ 2026-05-07): a paired `warmup_reminder` row fires 20 min before kickoff, `reply_to`-anchored to the announcement; if the RSVP count already meets `min_ready_players`, dispatch marks the reminder `skipped` and sends nothing.
- **Generic confirmation copy**: `poll_options.activity_label` makes the confirmation text type-agnostic (`"הטריוויה על {theme}"`, `"Emoji Night על {theme}"`, etc.). Falls back to `"הטריוויה על {theme}"` if missing.
- **LLM prompt**: `_generate_activity_copy` instructs the model to tell users to click the button on THIS warm-up message. If you change the prompt, keep that instruction.
- **Distinct from in-game ready gate**: the warm-up RSVP fires ~60 min before the game. The pre-roll ready button on the trivia_round announcement (callback `trivready`) is a separate mechanism. Don't conflate them.
- **Code paths**: `dashboard/app.py:_ensure_trivia_announcement_scheduled` + `_ensure_warmup_reminder_scheduled` (manual schedule), `dashboard/app.py:_ai_suggest_calendar` + `_maybe_add_warmup_reminder_suggestion` (Populate), `bot/handlers/calendar.py` (dispatch branches for `trivia_warmup_rsvp` and `warmup_reminder`), `bot/handlers/trivia_interest.py` (callback handler).
- **Reminder pairing**: announcement and reminder share `poll_options.warmup_marker`. Dispatch resolves the marker → announcement row in Python (no `json_extract`) so it works on any SQLite. Reminder is sent as `reply_to_message_id` of the announcement, no extra button.
- **Dispatch-time RSVP gate (T-127 ✅ 2026-05-07)**: trivia/emoji games stamp the same `warmup_marker` on their poll_options. At dispatch, `_enforce_warmup_rsvp_gate` (`bot/handlers/calendar.py`) counts responses; if below `min_ready_players` the game row is marked `skipped` and a Hebrew cancel notice posts in the warm-up topic as a reply to the announcement. Legacy rows (no marker / threshold 0) bypass the gate.
- **RSVP is opt-in per-feature, not a default applied to every activity type.** Today it's wired for `trivia_round` and `emoji_puzzle` — synchronous, time-boxed activities where a minimum attendance materially changes whether the activity is worth running. New activity types do NOT get RSVP automatically. Add it only when (a) the activity is synchronous, (b) running it cold is meaningfully worse than skipping it, and (c) you can name the threshold. Re-use the existing primitives (`poll_options.warmup_marker`, `min_ready_players`, the dispatch gate `_enforce_warmup_rsvp_gate`) — there is no global toggle and one is not needed. T-125 was closed as won't-fix (2026-05-07) on this exact reasoning.
- **No open follow-ups in Phase 23** — see the "RSVP is opt-in per-feature" rule above.

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

## Hermes Operator Alias

Hermes can be used as a local Botson operator assistant with the project-specific playbook preloaded.

Run from anywhere:

```bash
botson-hermes
```

One-off prompt example:

```bash
botson-hermes chat -q "Check Botson status. Do not deploy."
```

Alias behavior:
- Executes from the Botson repo: `/media/endlessblink/data/my-projects/ai-development/bots+automation/botson`
- Preloads Hermes skill: `botson-operator-playbook`
- Wrapper path: `~/.local/bin/botson-hermes`
- Skill path: `~/.hermes/skills/software-development/botson-operator-playbook/SKILL.md`

Operating plan:
- Use Hermes for local repo inspection, local tests, read-only diagnostics, draft code changes, and suggested verification steps.
- Require explicit approval before deploys, pushes, Telegram sends, production game starts, prod row cancellation/deletion, prod SQLite edits, secrets changes, or broad content-validator changes.
- Prefer production read-only diagnostics over SSH+SQL for verification.
- Never run `botson-hermes --yolo` for Botson production work.
- Hermes must report status precisely: local-only, tested locally, committed, pushed, deployed, verified by production diagnostics, or verified in runtime logs.
- For planner Populate work, Hermes must preserve the suggest+confirm modal invariant and run the focused Populate tests before claiming the flow works.

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
