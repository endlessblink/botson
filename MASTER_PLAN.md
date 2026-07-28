# MASTER PLAN — אלהוריים וזה Telegram Bot (robotnik)

> Telegram bot for the "אלהוריים וזה" childfree community (81 members, forum-style group with topic channels). All user-facing messages in Hebrew.

## Summary

| ID | Phase | Task | Status | Priority | Deps |
|----|-------|------|--------|----------|------|
| T-111 | 18 | Auto-start trivia/emoji scheduled game rows | DONE | P1 | T-023, T-026 |
| T-112 | 18 | Pre-flight admin DM 5min before trivia/emoji round announcements ("round at HH:MM in botson's corner — /trivia to start") | TODO | P1 | — |
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
| T-045 | 11 | Top navbar — move brand to horizontal header | DONE | P0 | T-041 |
| T-046 | 11 | Day-of-week selectors for scheduled items | DONE | P0 | T-044 |
| T-047 | 11 | Auto-detect forum topics from messages | DONE | P0 | T-004 |
| T-048 | 11 | Topic dropdown in schedule (replace ID input) | DONE | P0 | T-047 |
| T-049 | 11 | Clickable dots — instant visual toggle on/off | DONE | P1 | T-044 |
| T-050 | 11 | Schedule tab — show loaded question per slot | DONE | P1 | T-044 |
| T-051 | — | End-to-end dashboard QA pass | DONE | P1 | T-045..T-050 |
| T-052 | — | Single-instance bot guard (PID lock file) | DONE | P1 | T-004 |
| T-053 | — | Hot-reload config via SIGHUP (no restart needed) | DONE | P0 | T-004 |
| T-054 | — | Pyrogram one-time topic seeder | TODO | P2 | T-047 |
| T-055 | — | SQLite WAL mode on all connections | DONE | P1 | T-002 |
| T-056 | — | Add AIORateLimiter to bot | DONE | P1 | T-004 |
| T-057 | — | Switch to webhooks for VPS deployment | TODO | P2 | T-030 |
| T-058 | 12 | Auto-restart bot on code changes (file watcher) | DONE | P0 | T-052 |
| T-059 | 12 | Bot process supervisor script (run_bot.sh) | DONE | P0 | T-052 |
| T-060 | 12 | Dashboard "restart bot" button | DONE | P1 | T-059 |
| T-061 | 12 | Bot version check — dashboard shows if bot needs restart | DONE | P1 | T-058 |
| T-062 | 12 | Persistent bot logging (log to file, not temp) | DONE | P0 | T-004 |
| T-063 | 12 | Dashboard log viewer (tail bot logs in real-time) | TODO | P2 | T-062 |
| T-064 | 13 | Dashboard: send test message to any topic | TODO | P1 | T-047 |
| T-065 | 13 | Dashboard: trigger morning/evening prompt manually | TODO | P1 | T-044 |
| T-066 | 13 | Dashboard: trigger discussion prompt manually | TODO | P1 | T-044 |
| T-067 | 13 | Dashboard: start trivia question from dashboard | TODO | P1 | T-023 |
| T-068 | 13 | Dashboard: view/manage member levels + reset | TODO | P1 | T-031 |
| T-069 | 13 | Dashboard: view/manage streaks | TODO | P1 | T-011 |
| T-070 | 13 | Dashboard: whitelist management (add/remove patterns) | TODO | P1 | T-008 |
| T-071 | 13 | Dashboard: view group stats (same as /stats) | TODO | P1 | T-004 |
| ~~T-072~~ | 13 | ✅ Dashboard: create event from dashboard (cover + pin + topic + from-poll picker) | ✅ DONE (2026-04-19) | P1 | T-020 |
| T-073 | 13 | Dashboard: send weekly roundup manually | TODO | P2 | T-015 |
| T-074 | 13 | Dashboard: activity analytics (charts/graphs) | DONE | P2 | T-034 |
| T-075 | — | Investigate: levels points given to only some users | TODO | P1 | T-031 |
| T-076 | — | Rotating admin titles for top members (weekly) | TODO | P1 | T-031 |
| T-077 | — | Configurable validation-based scoring system | TODO | P1 | T-031, T-003 |
| T-078 | 14 | Dashboard: create & send interactive polls | DONE | P1 | T-027, T-047 |
| T-079 | — | Trivia redesign: speed scoring + themed channels | TODO | P1 | T-023 |
| ~~T-080~~ | 14 | ~~Planner page redesign~~ → Content calendar (FullCalendar) | IN PROGRESS | P0 | T-078 |
| T-081 | 15 | Calendar Session 1: FullCalendar render + dark theme + RTL | DONE | P0 | T-080 |
| T-082 | 15 | Calendar Session 2: Type-first creation drawer wizard | TODO | P0 | T-081 |
| T-083 | 15 | Calendar Session 3: Status lifecycle + queued lock | TODO | P1 | T-081 |
| T-084 | 15 | Calendar Session 4: Recurring rules with rrule | TODO | P1 | T-083 |
| T-085 | 15 | Calendar Session 5: AI draft button in wizard | TODO | P1 | T-082 |
| T-086 | 15 | Calendar Session 6: Post-send metrics badges | TODO | P2 | T-083 |
| T-087 | — | Content calendar auto-sender (calendar_checker job) | DONE | P0 | T-080 |
| T-088 | — | Calendar CRUD API endpoints | DONE | P0 | T-080 |
| T-089 | — | Atomic schedule reload (no job loss) | DONE | P1 | T-053 |
| T-090 | — | Validation-based scoring (no points for raw messages) | DONE | P1 | T-077 |
| T-091 | — | Inline poll vote tracking (poll_ handler) | DONE | P1 | T-078 |
| T-092 | — | Join request notifications to Den | TODO | P3 | T-007 |
| ~~T-093~~ | — | ✅ Anti-spam live mode + dashboard ban/unban | ✅ DONE (2026-04-11) | P1 | — |
| T-094 | — | Onboarding video: side panel topic switching | TODO | P3 | — |
| T-095 | — | Planner calendar: show recurring schedule as dots | TODO | P2 | T-080 |
| T-096 | — | Materializer migration: scheduled_messages as single source | DONE | P0 | T-005, T-044 |
| T-097 | 16 | Free Games: daily GG.deals feed with LLM reranker | IN PROGRESS | P2 | T-005 |
| T-098 | 16 | Free Games: add Epic Games API as second source | TODO | P2 | T-097 |
| T-099 | 16 | Free Games: activate in main group (topic 1517) | TODO | P2 | T-097 |
| T-100 | — | Configure git remote and push WIP commits | TODO | P1 | — |
| T-101 | — | Handle media assets (gitignore / LFS / commit) | TODO | P3 | — |
| T-111 | 18 | Trivia: end-to-end real Telegram launch verification | TODO | P1 | — |
| T-112 | 18 | Trivia: verify drawer stays open on launch after hard-refresh | TODO | P2 | T-111 |
| T-115 | 18 | Trivia flow: audit + remove hardcoded strings in generation/teaser | DONE | P1 | — |
| T-116 | 20 | Events: stop overwriting user content with date/time line | DONE | P1 | — |
| T-117 | 21 | Bulk-cancel future auto-scheduled rows endpoint + dashboard button (purge AI-generated content created before quality-rules wiring) | DONE | P1 | — |
| T-118 | 21 | Audit + rewrite 7 flagged questions in config/discussions.yaml (English jargon: ironic/autocorrect/overrated/underrated/red flag/green flag; 1 stacked question in cute) | DONE | P2 | — |
| T-123 | 23 | Planner AI Populate mixed suggestions | DONE | P0 | T-080 |
| T-124 | 23 | Fix emoji subject bias: remove hardcoded fallback, pool filter, sort key | DONE | P0 | — |
| ~~T-125~~ | 23 | ~~Extend RSVP buttons to remaining activity types + global toggle~~ → RSVP stays opt-in per-feature; no global toggle | WONT-FIX | P3 | — |
| T-126 | 23 | Second warm-up reminder: schedule a reminder row, skip dispatch if threshold already met (trivia + emoji) | DONE | P1 | T-128 |
| T-127 | 23 | Cancel trivia/emoji game at dispatch time if min_ready_players not reached (no point running if no one signed up) | DONE | P1 | T-126 |
| T-128 | 23 | Emoji Night announcement uses trivia_warmup_rsvp type so it gets the RSVP button at dispatch | DONE | P0 | T-124 |
| T-129 | 23 | Fix LLM prompt for warm-up copy: say button is on THIS message (was telling users to wait for game opening) | DONE | P0 | — |
| T-130 | 23 | Generic activity_label in trivia_interest poll_options so confirmation text works for any activity (not only trivia) | DONE | P1 | T-128 |
| T-131 | 23 | Bump warm-up lead time from 35 → 60 min in settings; add warmup_reminder_offset_min: 20 placeholder | DONE | P1 | — |
| T-132 | 23 | Hardcoded content audit: remove "ישראל" trivia form default, drop "movie/tv" fallbacks in emoji schedule, drop pool-size primary sort key in trivia category selector | DONE | P0 | T-124 |
| T-133 | 24 | Grow content pools 🌱 — facts.yaml spooky/tidbit ≥40 each, discussions.yaml every category ≥25 (use Hermes botson-question-pool + hebrew-content-qa) | DONE | P1 | — |
| T-134 | 24 | Wire question_quality.md 🧪 into every prompt path (build_generation_prompt, _generate_activity_copy, _gen_text, _generate_fresh_text); surface ✅/❌ examples in each | DONE | P1 | — |
| T-135 | 24 | Quality gate 🔧 — generate 3 candidates, pick first that passes freshness; if all fail raise SkippedActivity (rule-based first; LLM-judge as T-135b if needed) | DONE | P2 | T-134 |
| T-103 | 17 | Emoji Night: DB schema + helpers (puzzles, rounds) | DONE | P1 | T-002 |
| T-104 | 17 | Emoji Night: YAML pool seed + init loader | DONE | P1 | T-103 |
| T-105 | 17 | Emoji Night: settings + feature flag + per-group toggle | DONE | P1 | T-103 |
| T-106 | 17 | Emoji Night: session layer + rounds.session_id migration | DONE | P1 | T-103 |
| T-107 | 17 | Emoji Night: handler (send/watch/judge/reveal) | DONE | P1 | T-103, T-104, T-106 |
| T-108 | 17 | Emoji Night: dashboard-driven scheduler (cron from settings) | DONE | P1 | T-105, T-107 |
| T-109 | 17 | Emoji Night: dashboard /puzzles page + sidebar both blocks | DONE | P1 | T-107 |
| T-110 | 17 | Emoji Night 🔧: cleanup + dry-run in Den | TODO | P2 | T-108, T-109 |
| T-113 | 19 | Cleanup stale scheduled_messages rows 🔧 with target_channel="general" | TODO | P2 | — |
| T-114 | 19 | Handler Routing UI 🔧: teaser_topic_ids multi-select on Settings | TODO | P2 | — |
| T-136 | 25 | Phase B reaction tracking 🔧 — MessageReactionUpdated handler + message_engagement table + dashboard badge on /weekplan + /api/engagement/recent | DONE | P1 | — |
| T-137 | 25 | Slow-chat cadence 🧪 — drop morning/evening/discussion to 3 prompts/week (Sun morning, Mon discussion, Thu evening) | DONE | P1 | — |
| T-138 | 25 | Welcome DM lurker exit-ramp 🌱 — appended "להיות שקט/ה כאן זו השתתפות" line to single + multi-join templates | DONE | P2 | — |
| T-139 | 25 | Day-anchor prompt fix 🔧 (Layer 1) — translate ISO date to Hebrew weekday in materializer prompt; shared bot/utils/time_context.py | DONE | P0 | — |
| T-140 | 25 | Day-anchor output validation 🔧 (Layer 2) — extend freshness_rejection to reject text naming the wrong Hebrew day | DONE | P0 | T-139 |
| T-141 | 25 | Facts caption preface 🧪 — pool-specific bot-voice line ("🕯️ סיפור מסתורי מבוטסון" / "🔎 ידעתם?") loaded from settings.yaml:copy.facts.preface_* | DONE | P2 | — |
| T-142 | 25 | Facts preview-image button 🔧 — POST /api/facts/{pool}/{id}/generate-image, kie.ai call, writes image_url back into facts.yaml | DONE | P2 | — |
| T-143 | 25 | Calendar checker delete-race 🔧 — re-SELECT status before send so dashboard cancels in the dispatch window stop the post | TODO | P1 | — |
| T-144 | 25 | Phase A onboarding follow-up cadence 🔧 — +24h topic-mention + +72h DM + +7d lurker tag, opt-out per user, dashboard /onboarding admin page | TODO | P2 | T-136 |
| T-145 | 25 | Layer 3 nightly smoke test 🔧 — periodic real-LLM call against next-week slots, asserts day-anchor + freshness pass | TODO | P3 | T-139, T-140 |
| T-146 | 25 | Discussion seeds for חדר מוסיקה 🌱 — add `music` category to discussions.yaml (≥25 Hebrew questions). Topic id 4502 verified + settings wired 2026-05-10; only the content half remains. | DONE | P2 | — |
| T-147 | 26 | Planner Populate flex slots 🔧 — config-driven non-game suggestions after fixed slots fail | DONE | P0 | T-123 |
| T-148 | 26 | Populate skip diagnostics 🔧 — show exact reasons instead of generic empty modal | DONE | P1 | T-147 |
| T-149 | 26 | Populate flex routing config 🔧 — operator-controlled custom/discussion topic strategy | TODO | P1 | T-147 |
| T-150 | 26 | Populate flex regression suite 🧪 — evening fallback, custom commit, no hardcoding | TODO | P1 | T-147, T-148, T-149 |
| T-151 | 26 | Long-form facts pass 🌱 — expand fact previews into richer 5–8 line mini-stories, starting with spooky | DONE | P2 | T-133 |
| T-152 | 27 | E2E lane: control-surface action inventory | DONE | P0 | — |
| T-153 | 27 | E2E lane: scheduled-card persistence invariant | DONE | P0 | T-152 |
| T-154 | 27 | E2E lane: create drawer state isolation | DONE | P0 | T-152 |
| T-155 | 27 | E2E lane: review-draft lifecycle proof | DONE | P0 | T-152 |
| T-156 | 27 | E2E lane: scheduler due-row lifecycle proof | DONE | P0 | T-152 |
| T-157 | 27 | E2E lane: send-now vs scheduler parity | DONE | P1 | T-156 |
| T-158 | 27 | E2E lane: failure visibility diagnostics | DONE | P1 | T-153, T-156 |
| T-159 | 27 | E2E lane: Sherlocks Den smoke harness | DONE | P1 | T-153..T-158 |
| T-160 | 27 | E2E lane: one-command local/CI gate | DONE | P1 | T-153..T-159 |
| T-161 | 27 | E2E lane: audit remaining dashboards | TODO | P2 | T-152 |
| T-162 | 28 | Question quality review queue | IN PROGRESS | P0 | T-134, T-135 |
| T-163 | 28 | Draft review quality approval loop | IN PROGRESS | P0 | T-162 |
| T-164 | 28 | Real-output regression corpus | TODO | P1 | T-162 |
| ~~T-165~~ | 28 | ✅ Channel-specific question rubrics | ✅ DONE (2026-06-08) | P1 | T-162 |
| T-166 | 29 | Reaction-informed bot cadence loop | TODO | P1 | T-136 |
| T-167 | 29 | Trivia emoji facts review flows | TODO | P2 | T-163 |
| T-168 | 29 | Community health metrics loop | TODO | P2 | T-136 |
| T-169 | 30 | Materializer dedup + source-echo fix 🩸 — bulk auto-refill quality leak | TODO | P0 | T-134, T-135 |
| T-170 | 30 | Planner Populate hardening — loud failures + retry budget + near-dup | TODO | P0 | T-169 |
| T-171 | 30 | Discussion pool validator + CLI + guardian test | TODO | P0 | T-169 |
| T-172 | 30 | Operator feedback capture — content_feedback schema + dashboard controls | TODO | P0 | T-163 |
| T-173 | 30 | Regression corpus + per-channel rubrics injected into prompts | TODO | P1 | T-172 |
| T-174 | 30 | Style-profile learning loop — operator-approved generation guidance updates | TODO | P1 | T-172, T-173 |
| T-175 | 23 | Trivia 'never fired' diagnosis + pre-roll gate logging + vps-admin diagnostics | DONE | P1 | T-127 |
| ~~T-176~~ | 30 | ✅ Learned rules survive deploys — durable prefs store + baseline reconcile | ✅ **DONE** (2026-07-27) | P0 | T-174 |
| ~~T-177~~ | 30 | ✅ Learned rules injected into the bulk-fill path (materializer) | ✅ **DONE** (2026-07-27) | P0 | T-176 |
| ~~T-178~~ | 30 | ✅ Rule-list consolidation under an operator-set cap | ✅ **DONE** (2026-07-27) | P1 | T-177 |
| ~~T-179~~ | 23 | ✅ CLI call timing instrumentation + configurable budgets + llm-doctor | ✅ **DONE** (2026-07-28) | P1 | T-175 |
| ~~T-180~~ | 23 | ✅ Watchdog classifies CLI health and alerts; blind-monitor guard | ✅ **DONE** (2026-07-28) | P1 | T-179 |
| ~~T-181~~ | 30 | ✅ Rephrase-with-anchors — keep the idea, redo the wording (planner cards, prompt drawer, review page); one-off edit that never writes a learned rule | ✅ **DONE** (2026-07-28) | P1 | T-172 |

## Lanes (active queues)

#### T-170: Planner Populate hardening — loud failures + retry budget + near-dup
**Priority:** P0 | **Status:** TODO
Planner Populate should fail loudly when generation quality fails, retry with useful guidance, avoid near-duplicates/source echoes, and reduce samey suggestions across a Populate run.

**Progress (2026-05-18):** Gap 1 implemented locally: Populate now rotates configured generation patterns per retry, rejects repeated openers from recent history/current run, and passes configurable temperature to the Anthropic API fallback. Focused planner and guardian tests pass.

**Remaining gaps:** Continue tracking unresolved Populate hardening gaps here as they are picked up, so restart context is visible from this task section.

Active queues should reflect what to work on next, not historical shipped phases. Tasks in different lanes don't block each other; tasks within a lane run sequentially.

### 🌱 Question quality lane (curation + review)
- **T-162** — Question quality review queue (IN PROGRESS, P0) — review generated/scheduled/pending discussion prompts against `config/question_quality.md`, rewrite or reject weak rows, and capture every new failure pattern as a rule/test.
- ~~**T-165**~~ — ✅ Channel-specific question rubrics (DONE 2026-06-08, P1) — channel-aware guidance now lives in `config/channel_rubrics.yaml`; support/fitness category mappings and regression coverage prevent stale topic/category prompt leakage.
- **T-164** — Real-output regression corpus (TODO, P1) — turn bad and good real generated outputs into fixtures that guard `_validate_draft_text`, quality prompts, and pool false positives.
- ✅ **T-133** — Grow content pools (DONE) — facts and discussions pool sizes are sufficient; future work is quality review, not raw volume.

### 🔧 Bot enhancement lane (runtime/product behavior)
- **T-166** — Reaction-informed bot cadence loop (TODO, P1) — use `message_engagement` to tune which slots/content types continue, pause, or move.
- **T-149** — Populate flex routing config (TODO, P1) — custom/discussion topic choice must be operator-controlled, not inferred from hardcoded defaults.
- **T-143** — Calendar checker delete-race fix (TODO, P1) — re-check row status in dispatch window so dashboard cancels stop the send.
- **T-110** — Emoji Night cleanup + dry-run in Den (TODO, P2).
- **T-144** — Phase A onboarding follow-up cadence (TODO, P2) — depends on T-136 telemetry + opt-out wiring before deploy.
- **T-168** — Community health metrics loop (TODO, P2) — expose lightweight health signals so bot changes are based on participation, not vibes.

### 🧪 Quality systems lane (code + content together)
- **T-163** — Draft review quality approval loop (IN PROGRESS, P0) — make dashboard draft review enforce quality decisions before scheduling/sending.
- **T-150** — Populate flex regression suite (TODO, P1) — tests must prove today-evening non-game fallback, custom commit support, config-driven windows, and guardian compatibility.
- **T-145** — Layer 3 nightly smoke test (TODO, P3) — periodic real-LLM call asserting day-anchor + freshness; now belongs after the review/corpus loop, not before it.
- **T-167** — Trivia emoji facts review flows (TODO, P2) — apply the same preview/approve/reject discipline beyond discussion prompts.
- ✅ **T-134** — Wire `config/question_quality.md` into every prompt path (DONE).
- ✅ **T-135** — Quality gate: generate-3-pick-best (DONE, materializer only); future LLM-judge work should be justified by real corpus failures.

### 🧪 E2E trust lane (control-surface reliability)
- **T-152** — Control-surface action inventory (DONE, P0) — map every dashboard action to API, DB state, worker owner, external side effect, visible result, and unknowns.
- **T-153** — Scheduled-card persistence invariant (DONE, P0) — prove every `יישלח` calendar card has one persisted `scheduled_messages.status='scheduled'` row and preview/draft/scheduled are visually distinct.
- **T-154** — Create drawer state isolation (DONE, P0) — browser E2E for type switches, generated text, cover/poll/event fields, and preview rows.
- **T-155** — Review-draft lifecycle proof (DONE, P0) — draft -> edit -> schedule -> send-now -> delete paths with DB assertions and visible diagnostics.
- **T-156** — Scheduler due-row lifecycle proof (DONE, P0) — due scheduled row -> mocked Telegram send -> sent/failed/skipped terminal state -> dashboard visibility.
- **T-157** — Send-now vs scheduler parity (DONE, P1) — contract tests for shared behavior and documented differences across event/poll/trivia/emoji/facts/weekly rows.
- **T-158** — Failure visibility diagnostics (DONE, P1) — failed/skipped/stale/missing-topic cases must be visible in calendar and diagnostics endpoints without log reading.
- **T-159** — Sherlocks Den smoke harness (DONE, P1) — production-safe dry-run that sends only to Den, records row ID/status/message ID, and never touches main chat.
- **T-160** — One-command local/CI gate (DONE, P1) — `scripts/run_control_surface_e2e.py` bundles the trust-lane pytest files, supporting planner/calendar contracts, the hardcoded-content guardian, and the Den smoke dry-run. JSON output via `--json` for CI parsing. — stable command that runs the trust E2E suite, browser smoke, guardian, and scheduler contracts.
- **T-161** — Audit remaining dashboards (TODO, P2) — repeat `control-surface-audit` for puzzles/trivia, settings/config, moderation, content pools, and onboarding.

## Detailed Tasks

---

#### T-152: E2E lane: control-surface action inventory
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P0 | **Status:** DONE | **Deps:** —

Create the authoritative action inventory for Botson's dashboard/control surfaces. This is the root map for discovering unknown holes before adding more tests.

Scope:
- Inventory planner, weekplan, prompt modal, review-drafts modal, puzzles, trivia, settings/routing, moderation, facts/pools, and send-now controls.
- For each action, map: UI entry -> JS handler -> API endpoint -> DB mutation -> worker/scheduler owner -> external side effect -> terminal state -> visible feedback.
- Mark every unknown explicitly. Do not assume a flow works because a function exists.
- Classify each action's lifecycle states: preview, draft, scheduled, running, sent/succeeded, failed, skipped, cancelled, unknown.

Deliverable:
- `docs/control-surface-action-inventory.md` with one table per dashboard area.

Verification:
- Table covers every `/api/calendar`, `/api/weekplan`, `/api/trivia`, `/api/pool`, `/api/bot`, and `/api/diagnostics` route used by dashboard JS.
- Every row has either a known test reference or an explicit missing-test note.

---

#### T-153: E2E lane: scheduled-card persistence invariant
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P0 | **Status:** DONE | **Deps:** T-152

Prove the planner calendar cannot show a sendable card unless the backend has durable scheduled state.

Scope:
- Add a DB-backed test for `/api/calendar` asserting every event with `extendedProps.willSend=true` maps to exactly one `scheduled_messages` row with `status='scheduled'`.
- Add a browser/HTML assertion that preview, draft, scheduled, sent, failed, and skipped cards have distinct labels/tooltips.
- Ensure preview rows, if reintroduced later, always carry `willSend=false` and `diagnosticLabel='תצוגה בלבד'`.

Regression target:
- A visible card must never imply it will send when no persisted scheduled row exists.

Verification:
- `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py -q`
- `PYTHONPATH=. uv run pytest tests/test_planner_visual.py -q`

---

#### T-154: E2E lane: create drawer state isolation
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P0 | **Status:** DONE | **Deps:** T-152

Convert the create-drawer trust fixes into real browser E2E coverage.

Scope:
- Make `tests/test_planner_edit_browser.py` active or replace it with a stable Playwright/pytest browser spec.
- Test type switches: morning -> event, event -> poll, poll -> morning, custom -> event.
- Assert generated text, poll options, event fields, cover state, and preview UI do not leak across incompatible types.
- Assert fake RSVP buttons never render in draft preview; real event RSVP is only tested in dispatch-layer tests.

Regression target:
- A morning/daily prompt cannot appear as an event preview with RSVP controls.

Verification:
- Browser test runs headless without unconditional skip.
- DB bootstrap is isolated with `DB_PATH` temp file.

---

#### T-155: E2E lane: review-draft lifecycle proof
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P0 | **Status:** DONE | **Deps:** T-152

Prove the review-drafts modal has a reliable lifecycle from generated draft to durable terminal state.

Scope:
- Seed AI drafts in a temp DB.
- Browser or route-level E2E: edit text/time/topic -> save -> schedule -> send now -> delete/dismiss.
- Assert each action mutates the expected DB row and updates visible UI state.
- Assert dirty-state warnings do not accidentally send unsaved edits.
- Assert schedule refuses past/too-soon slots unless `force=true` is explicitly confirmed.

Regression target:
- Draft cards cannot disappear from the modal without a durable state change.

Verification:
- New focused test file, e.g. `tests/test_planner_review_drafts_e2e.py`.
- Include at least one negative path: failed schedule/send remains visible with error.

---

#### T-156: E2E lane: scheduler due-row lifecycle proof
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P0 | **Status:** DONE | **Deps:** T-152

Prove scheduled rows move through the scheduler lifecycle exactly once and always reach a visible terminal state.

Scope:
- For plain message, poll, event, trivia_round, emoji_puzzle, facts_tidbit, facts_spooky, weekly_roundup, weekly_leaderboard: create due rows in temp DB, run `check_and_send_due_messages` with mocked Telegram/handlers, assert status and message IDs.
- Add stale-row and missing-routing/topic cases.
- Assert `sent`, `failed`, and `skipped` appear in `/api/calendar` and `/api/diagnostics/planner-day` with useful details.

Regression target:
- A due row cannot remain silently `scheduled` after a send attempt unless the worker never ran, which diagnostics must reveal.

Verification:
- Extend `tests/test_calendar_scheduled_games.py` or create `tests/test_scheduler_lifecycle_e2e.py`.

---

#### T-157: E2E lane: send-now vs scheduler parity
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P1 | **Status:** DONE | **Deps:** T-156

Make the two dispatch paths intentionally consistent, and document the differences that must remain.

Scope:
- Compare `/api/calendar/{id}/send-now` via `_send_scheduled_row` against `check_and_send_due_messages` for each executable type.
- Decide whether event RSVP, emoji media filters, fact pinned IDs, trivia warm-up gate, failed/skipped distinctions, and test-target behavior should be shared or explicitly different.
- Add tests for every documented difference.

Regression target:
- A row should not behave differently just because the operator clicked Send Now instead of waiting for the scheduler, unless that difference is documented and tested.

Verification:
- Contract tests pass and reference the documented behavior table.

---

#### T-158: E2E lane: failure visibility diagnostics
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P1 | **Status:** DONE | **Deps:** T-153, T-156

Make every failure mode visible from the dashboard without reading logs.

Scope:
- Calendar cards expose `diagnosticLabel`, `diagnosticDetail`, `errorMessage`, `willSend`, and terminal status.
- `/api/diagnostics/planner-day` reports due eligibility, stale/past reasons, topic verification, routing, payload shape, and last error.
- Add dashboard affordance or tooltip/modal for "why won't/wouldn't this send?".
- Cover failed, skipped, stale-drop, draft, missing group ID, missing topic, invalid poll options, and executable handler failure.

Regression target:
- Operators must not need SSH/log access to understand why a visible row did or did not send.

Verification:
- API tests for diagnostics payload.
- Browser/HTML test for visible diagnostic entry point.

---

#### T-159: E2E lane: Sherlocks Den smoke harness
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P1 | **Status:** DONE | **Deps:** T-153, T-154, T-155, T-156, T-157, T-158

Create a production-safe dry-run harness that proves the full lane against Sherlocks Den only.

Scope:
- Use `target_group='test'` or test-only env routing. Never send to main chat.
- Create one row per safe content type with unique marker text.
- Exercise schedule and send-now paths where safe.
- Record scheduled row ID, Telegram message ID/session ID, terminal status, topic, and cleanup status.
- Include a dry-run mode that prints planned actions without sending.

Regression target:
- A real-world smoke can prove the dashboard + API + DB + bot integration without risking the main group.

Verification:
- `PYTHONPATH=. uv run python scripts/e2e_den_smoke.py --dry-run`
- Explicit approval required before non-dry-run send.

---

#### T-160: E2E lane: one-command local/CI gate
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P1 | **Status:** DONE | **Deps:** T-153, T-154, T-155, T-156, T-157, T-158, T-159

Bundle the trust suite into one reliable command that can run locally and in CI-like checks.

Scope:
- Add a script or Make target that runs: scheduler lifecycle tests, planner API tests, planner browser tests, hardcoded-content guardian, and optional Den dry-run.
- Ensure browser tests skip only when browsers are genuinely unavailable, not because fixtures are WIP.
- Emit a concise pass/fail summary and point to artifacts/logs on failure.

Regression target:
- Future dashboard changes cannot bypass the E2E trust lane by only running narrow unit tests.

Verification:
- Example command: `PYTHONPATH=. uv run python scripts/run_control_surface_e2e.py` or documented equivalent.

---

#### T-161: E2E lane: audit remaining dashboards
**Phase:** 27 — Control-surface E2E reliability | **Priority:** P2 | **Status:** TODO | **Deps:** T-152

Repeat the control-surface audit beyond the planner/scheduler.

Scope:
- Run `control-surface-audit` separately for puzzles/Emoji Night, trivia live + trivia pool, settings/routing, moderation/spam, content pools/facts, onboarding/welcome, and levels/engagement.
- For each area, add or link follow-up tasks only when a trust gap has concrete evidence.
- Do not turn this into generic cleanup. Every new task must name the broken promise and the test/diagnostic needed.

Deliverable:
- Add sections to `docs/control-surface-action-inventory.md` or split into `docs/control-surface-*.md` if it gets too large.

Verification:
- Each audited area has an action inventory, lifecycle map, trust gaps, invariants, and test matrix.

---

#### T-162: Question quality review queue
**Phase:** 28 — Question quality review | **Priority:** P0 | **Status:** IN PROGRESS | **Deps:** T-134, T-135

The next work is not adding more raw questions. It is reviewing real generated and scheduled prompts against `config/question_quality.md`, removing weak sendable content, and feeding concrete failures back into the validator and prompt rules.

Scope:
- Inspect upcoming `scheduled_messages`, pending review drafts, and any recent generated text available locally.
- Classify each questionable prompt as keep, rewrite, reject, or needs-channel-context.
- Promote only concrete recurring failures into `config/question_quality.md` and `_validate_draft_text` tests.
- Avoid broad subjective rules. Every new rule needs at least one real bad output and one false-positive check against curated pools.
- Rewrite weak `config/discussions.yaml` entries only when they are genuinely likely to underperform or violate the rules.

Deliverable:
- A short review log in the task notes or a dedicated doc, plus targeted config/test changes.

Verification:
- `PYTHONPATH=. uv run pytest tests/test_digest_quality_consolidation.py tests/test_quality_rules_wiring.py tests/test_quality_gate.py -q`
- If `config/discussions.yaml` changes: `PYTHONPATH=. uv run pytest tests/test_facts_pool.py -q` only if fact/content validators are touched.

Progress:
- 2026-05-14: First concrete review batch inspected local `data/bot.db` scheduled/draft rows, `data/pending_reviews.json`, `config/prompts.yaml`, and `config/discussions.yaml`. The existing validator had zero failures on live rows/pools, but manual review found weak legacy text patterns: English `Done` in Hebrew prompt copy, generic day/evening check-ins (`איך היה היום`, `הרגע הכי שווה מהיום`, `מה עשיתם היום בשביל עצמכם`), broad fandom fantasy (`לחיות בעולם של סדרה/משחק/ספר`), and generic movie rewatch (`סרט שראיתם יותר מ-3 פעמים`).
- Rewrote the tiny morning/evening pool in `config/prompts.yaml` to use more concrete micro-scenes and no English jargon.
- Added the concrete failures to `config/question_quality.md`, `config/freshness.yaml`, `_validate_draft_text`, and `tests/test_digest_quality_consolidation.py` so the same weak drafts are rejected before scheduling.
- Verified with `PYTHONPATH=. uv run pytest tests/test_digest_quality_consolidation.py tests/test_quality_rules_wiring.py tests/test_quality_gate.py -q` — 29 passed, 55 subtests passed.

---

#### T-163: Draft review quality approval loop
**Phase:** 28 — Question quality review | **Priority:** P0 | **Status:** IN PROGRESS | **Deps:** T-162

Dashboard review should make weak AI drafts hard to accidentally schedule. The operator needs explicit quality status, visible rejection reasons, and a durable approve/rewrite/reject path before content becomes sendable.

Scope:
- Add quality status to review-draft rows: unreviewed, accepted, rejected, needs rewrite.
- Surface validator failures and quality-rule excerpts near the draft text.
- Ensure schedule/send-now refuses rejected or failing drafts unless the operator explicitly overrides with a visible reason.
- Persist edits and review decisions so drafts cannot disappear without a durable state change.
- Keep `/api/weekplan/ai-suggest` preview-only; only the commit/review path may write scheduled rows.

Regression target:
- A draft with known-bad wording cannot silently become a scheduled message.

Verification:
- Add/extend focused review-draft tests, likely alongside T-155.
- `PYTHONPATH=. uv run pytest tests/test_digest_quality_consolidation.py tests/test_planner_coercion_and_chips.py -q`

Progress:
- 2026-05-14: Closed the first bypass: saving edited draft text was already validated, but approving/scheduling an existing draft with no text payload, scheduling through `/api/calendar/{id}/schedule`, and send-now paths could reuse bad stored text. Added a shared row-level quality gate for `morning`, `evening`, and `discussion` rows.
- `/api/calendar` now exposes `qualityStatus` and `qualityFailures` for draft rows so the review modal can show a visible rejection panel before the operator clicks schedule/send.
- The review modal now formats `quality_rejected` API errors instead of showing `[object Object]`, keeps failed send-now cards in place, and shows quality warnings next to failing drafts.
- Added regressions proving low-quality drafts remain `draft` when scheduled via the review endpoint or promoted through `PUT /api/calendar/{id}` without a replacement text payload.
- Verified with `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py tests/test_digest_quality_consolidation.py -q` — 108 passed, 172 subtests passed; and `PYTHONPATH=. uv run pytest tests/test_quality_rules_wiring.py tests/test_quality_gate.py tests/test_no_hardcoded_content.py -q` — 22 passed, 13 subtests passed.

---

#### T-164: Real-output regression corpus
**Phase:** 28 — Question quality review | **Priority:** P1 | **Status:** TODO | **Deps:** T-162

The quality gate should learn from real misses, not imagined generic examples. Create a small corpus of actual accepted/rejected outputs that can be reused by tests and future prompt work.

Scope:
- Store bad outputs, corrected rewrites, channel/category, failure reason, and expected validator result in a versioned fixture.
- Include good borderline examples to protect against over-aggressive validator rules.
- Wire the corpus into `tests/test_digest_quality_consolidation.py` or a focused quality-corpus suite.
- Keep private/user-identifying text out of fixtures unless already public and safe.

Verification:
- Corpus tests fail when a known-bad output passes or a known-good curated output is rejected.
- `PYTHONPATH=. uv run pytest tests/test_digest_quality_consolidation.py -q`

---

#### T-165: Channel-specific question rubrics
**Phase:** 28 — Question quality review | **Priority:** P1 | **Status:** DONE (2026-06-08) | **Deps:** T-162

The global rules are useful but still too generic. Each active channel should have a compact rubric with local examples: what a good prompt sounds like there, what fails, and which participation pattern it is trying to trigger.

Scope:
- Add channel-specific examples for active discussion categories in `config/question_quality.md` or a linked config file loaded by generation prompts.
- Cover at least: general, movies, gaming, vegan, art, politics, singles, cute, music.
- For each channel, include positive examples, reject examples, and 2-3 preferred prompt patterns.
- Keep the prompt payload short enough for current CLI timeout constraints.

Verification:
- `PYTHONPATH=. uv run pytest tests/test_quality_rules_wiring.py tests/test_digest_quality_consolidation.py -q`
- Manual review of generated prompts for at least three channels.

**Shipped 2026-06-08:** `config/channel_rubrics.yaml` is loaded by discussion generation, and the active discussion set now includes dedicated `support` (`מרימים אחד לשני/ה!`, topic 347) and `fitness` (`כושר`, topic 5438) categories with starter pools and rubrics. The stale `art:347` mapping was replaced so support/uplift generation no longer receives art/יצירה context. Regression coverage pins renamed-topic prompt behavior, category/topic mismatch handling, planner topic payloads, and support/fitness Populate mappings.

**Verification:** `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py tests/test_planner_generation_pipeline.py -q`; `PYTHONPATH=. uv run python scripts/validate_discussions.py`; `npx playwright test tests/playwright/planner-discussion-topic.spec.js --reporter=line`. Production was also verified after restart: `support` and `fitness` are active, have pools/rubrics, and services were healthy.

---

#### T-166: Reaction-informed bot cadence loop
**Phase:** 29 — Bot enhancement | **Priority:** P1 | **Status:** TODO | **Deps:** T-136

Now that reaction tracking exists, cadence decisions should be based on actual participation. Add a lightweight loop that shows which content types and slots are working and lets the operator adjust the schedule deliberately.

Scope:
- Aggregate recent engagement by content type, channel/topic, weekday, and time slot.
- Flag slots with repeated low engagement or high replies/reactions.
- Show recommendations as diagnostics only; do not auto-change schedule.
- Keep copy and thresholds config-driven.

Verification:
- API tests for aggregation and empty-data behavior.
- Dashboard smoke or HTML test for visible cadence diagnostics.

---

#### T-167: Trivia emoji facts review flows
**Phase:** 29 — Bot enhancement | **Priority:** P2 | **Status:** TODO | **Deps:** T-163

The review discipline should not stop at discussion prompts. Trivia launches, Emoji Night announcements, and facts captions also need preview/approve/reject flows before they become scheduled content.

Scope:
- Inventory current preview/review behavior for trivia, emoji, and facts suggestions.
- Add missing quality status and rejection reasons where generated text can become sendable.
- Ensure pinned executable payloads match previewed content.
- Avoid duplicating question-quality rules where a content type needs its own rubric.

Verification:
- Focused tests for preview payload matching committed/sent payload.
- `PYTHONPATH=. uv run pytest tests/test_calendar_scheduled_games.py tests/test_digest_quality_consolidation.py -q`

---

#### T-168: Community health metrics loop
**Phase:** 29 — Bot enhancement | **Priority:** P2 | **Status:** TODO | **Deps:** T-136

Bot enhancements should preserve the community's feel: useful prompts, low spam, enough quiet space, and no forced participation. Add small health signals that help decide what to change next.

Scope:
- Track simple signals: active responders, unique reactors, unanswered bot prompts, muted/disabled content types, and manual sends.
- Show trends without gamifying members or shaming quiet users.
- Use metrics to inform future queue ordering, not automatic interventions.

Verification:
- API tests for aggregation with empty and populated DBs.
- Manual dashboard check that labels are neutral and not member-shaming.

---

#### T-151: Long-form facts pass 🌱
**Phase:** 26 — Content polish | **Priority:** P2 | **Status:** DONE (2026-05-11) | **Deps:** T-133

**Shipped 2026-05-11:** Expanded every `spooky` entry to a 5–8 line mini-story and every `tidbit` entry to at least 4 lines. Added `test_fact_previews_have_enough_story_depth` so future entries cannot regress into short captions.

The new fact pools are large enough, but some previews still read like short captions. Future curation should expand every `config/facts.yaml` entry, starting with `spooky`, into a richer 5–8 line mini-story that gives setup, concrete details, source/caveat, and a satisfying final beat.

Reference bar: `spooky/borley_rectory` after the 2026-05-11 expansion. It includes location, time period, reported phenomena, investigator/source context, skepticism, and a final framing line.

Scope:
- Audit all `spooky` entries first; then decide whether `tidbit` should use the same length or a slightly tighter 4–6 line format.
- Preserve every existing `source`, `source_url`, and `image_prompt`/`image_url`.
- Do not add unsourced claims. If a richer version needs extra detail, either verify from the existing source or replace the source.
- Keep Hebrew natural and Telegram-readable; no walls of text.

Verification:
- `PYTHONPATH=. uv run pytest tests/test_facts_pool.py -q`
- Spot-check several dashboard previews for truncation/readability.

Files: `config/facts.yaml`; optionally `tests/test_facts_pool.py` if a safe line-count/readability assertion is useful.

---

#### T-147: Planner Populate flex slots
**Phase:** 26 — Planner Populate flexibility | **Priority:** P0 | **Status:** DONE (2026-05-10) | **Deps:** T-123

AI Populate currently only previews rows at fixed `schedule.*.time(s)` values, so an evening with several hours left can still return zero suggestions if games need past warm-up slots or exact game/facts slots are occupied. Add config-driven flex windows so Populate can propose non-game content during remaining free time without hardcoded hours or hardcoded Hebrew themes.

**Scope:**
- Add `config/settings.yaml:ai_populate.flex` windows and caps.
- Expand `/api/weekplan/ai-suggest` to generate non-game flex candidates after fixed candidates are exhausted or under-filled.
- Keep preview-before-commit invariant: suggest writes nothing; approve inserts only selected rows.
- Preserve trivia/emoji RSVP safety. A missed warm-up skips the game, but must not block non-game flex posts.

**Pass criteria:** day-level Populate after fixed slots fail can still return a future discussion/custom row if a configured flex window is available.

**Shipped 2026-05-10:** Added `settings.yaml:ai_populate.flex` day windows, flex candidate expansion, `custom` suggestion commit support, and modal icon rendering. Day-level Populate can now return non-game flex suggestions in configured future windows while keeping trivia/emoji RSVP safety intact.

#### T-148: Populate skip diagnostics
**Phase:** 26 — Planner Populate flexibility | **Priority:** P1 | **Status:** DONE (2026-05-10) | **Deps:** T-147

Replace the generic empty-state explanation with concrete skip diagnostics from the backend. The response should surface reasons such as past fixed time, occupied exact slot, warm-up RSVP time already passed, disabled feature, missing routing/topic, empty pool, or exhausted flex window.

**Pass criteria:** when `suggestions=[]`, the modal shows actionable reasons rather than only "all slots occupied".

**Shipped 2026-05-10:** `/api/weekplan/ai-suggest` now returns `skip_reasons` with code/date/time/type metadata, and the modal renders the top reasons when no suggestions are returned. Current codes cover past slots, occupied exact slots, occupied flex times, and flex windows that are too soon.

#### T-149: Populate flex routing config
**Phase:** 26 — Planner Populate flexibility | **Priority:** P1 | **Status:** TODO | **Deps:** T-147

Make flex suggestion routing operator-controlled. Do not infer a default topic in code. Supported strategies should be config-backed, e.g. active discussion category, `topics.welcome`, `topics.goals`, or a handler-routing row for a future flex handler.

**Pass criteria:** custom/discussion flex rows always use verified/configured topics, and missing routing produces a visible skip reason.

#### T-150: Populate flex regression suite
**Phase:** 26 — Planner Populate flexibility | **Priority:** P1 | **Status:** TODO | **Deps:** T-147, T-148, T-149

Add focused tests for the flexible Populate behavior: evening fallback, config-defined windows, occupied/past skips, `custom` commit support, diagnostic reasons, and hardcoded-content guardian compatibility.

**Verification:**
- `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py -q`
- `PYTHONPATH=. uv run pytest tests/test_calendar_scheduled_games.py -q`
- `PYTHONPATH=. uv run pytest tests/test_no_hardcoded_content.py -q`

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

#### T-058: Auto-restart bot on code changes (file watcher)
**Phase:** 12 — Reliability | **Priority:** P0 | **Status:** TODO | **Deps:** T-052

Watch `bot/` and `config/` directories for file changes. When a change is detected, gracefully restart the bot process. Prevents stale code from running after edits.

Options:
- `watchdog` Python library to monitor filesystem changes
- Simple shell loop with `inotifywait`
- Integrate into `run_bot.sh` wrapper

Files: new `run_bot.sh` or `bot/watcher.py`

---

#### T-059: Bot process supervisor script (run_bot.sh)
**Phase:** 12 — Reliability | **Priority:** P0 | **Status:** TODO | **Deps:** T-052

A wrapper script that:
- Ensures only one instance runs (via PID lock)
- Redirects logs to a persistent file (`data/bot.log`)
- Auto-restarts on crash
- Handles SIGTERM gracefully
- Can be used with systemd or run standalone

Files: `run_bot.sh`

---

#### T-060: Dashboard "restart bot" button
**Phase:** 12 — Reliability | **Priority:** P1 | **Status:** TODO | **Deps:** T-059

Add a button to the health page that sends SIGHUP or restarts the bot process. Dashboard reads the PID from `data/bot.pid` and sends a signal.

Files: `dashboard/app.py`, `dashboard/templates/health.html`

---

#### T-061: Bot version check — dashboard shows if bot needs restart
**Phase:** 12 — Reliability | **Priority:** P1 | **Status:** TODO | **Deps:** T-058

Track a version hash (git commit or file mtime) at bot startup. Dashboard compares the running version against current code on disk. Shows a warning banner if they differ: "Bot is running old code — restart required."

Files: `bot/main.py`, `dashboard/templates/health.html`

---

#### T-062: Persistent bot logging (log to file, not temp)
**Phase:** 12 — Reliability | **Priority:** P0 | **Status:** TODO | **Deps:** T-004

Bot should always log to `data/bot.log` with rotation (10MB max, 3 backups). Currently logs go to whatever stdout/stderr the process was started with (often temp files that get deleted).

Files: `bot/main.py` (logging config)

---

#### T-063: Dashboard log viewer (tail bot logs in real-time)
**Phase:** 12 — Reliability | **Priority:** P2 | **Status:** TODO | **Deps:** T-062

Add a section to the health page that shows the last 50 lines of `data/bot.log`. Auto-refreshes every 10 seconds via JS polling. Lets you monitor the bot from the dashboard without SSH.

Files: `dashboard/app.py`, `dashboard/templates/health.html`

---

#### T-064: Dashboard: send test message to any topic
**Phase:** 13 — Dashboard Parity | **Priority:** P1 | **Status:** TODO

API endpoint `POST /api/bot/send` that sends a message to a selected topic. Dashboard UI: text input + topic dropdown + send button. Useful for testing and announcements.

---

#### T-065: Dashboard: trigger morning/evening prompt manually
**Phase:** 13 | **Priority:** P1 | **Status:** TODO

"Send now" button on the prompts schedule tab. Calls bot to pick a random prompt from the pool and send it to the goals topic immediately. Same as what the scheduler does, but on demand.

---

#### T-066: Dashboard: trigger discussion prompt manually
**Phase:** 13 | **Priority:** P1 | **Status:** TODO

"Send now" button for discussion prompts. Picks a random category with a configured topic ID, selects a prompt, and sends it.

---

#### T-067: Dashboard: start trivia question from dashboard
**Phase:** 13 | **Priority:** P1 | **Status:** TODO

Button on the trivia page to start a trivia question in the group. Equivalent to `/trivia` command. Shows the question in Telegram with answer buttons.

---

#### T-068: Dashboard: view/manage member levels + reset
**Phase:** 13 | **Priority:** P1 | **Status:** TODO

Levels page already shows leaderboard + reset. Enhance with: individual member level edit, point adjustment, search. Currently partially done.

---

#### T-069: Dashboard: view/manage streaks
**Phase:** 13 | **Priority:** P1 | **Status:** TODO

Add streak data to the members or levels page. Show current streak, longest streak, last post date. Equivalent to `/streak` for all users.

---

#### T-070: Dashboard: whitelist management
**Phase:** 13 | **Priority:** P1 | **Status:** TODO

Already partially done on spam page (textarea). Enhance: individual add/remove, test pattern against sample text, import/export.

---

#### T-071: Dashboard: view group stats
**Phase:** 13 | **Priority:** P1 | **Status:** TODO

Equivalent to `/stats` command. Show on overview or health page: top karma earners, top streaks, member count, active topics, messages per day.

---

#### T-072: Dashboard: create event from dashboard
**Phase:** 13 | **Priority:** P1 | **Status:** ✅ DONE (2026-04-19)

Dashboard `/events` page rebuilt as a two-tab form:
- **Blank tab**: title, description, date, time, location, topic dropdown, target group, cover image (upload / AI-generate via kie.ai / URL-scrape), auto-pin checkbox.
- **From-Poll tab**: dropdown of recent polls (via `GET /api/polls`); picking a poll auto-fills title (cleaned), cover, topic, target group; clicking an option button adds date/time parsed from the option label.

`POST /api/events/create` now actually posts to Telegram via `bot.handlers.calendar.send_message_with_optional_cover`, attaches RSVP inline buttons (`rsvp_yes_{event_id}` / `rsvp_maybe_{event_id}`), pins if requested, persists `message_id` + provenance (`source_poll_message_id`, `source_poll_option_key`).

New "שמור בלי לפרסם" button creates the DB row without sending to Telegram (draft mode).

Cache-Control: no-store middleware on HTML responses prevents stale-JS issues.

Bugs fixed along the way: title was option-label not poll-text; date rolled to next year for slightly-past polls; cover preview rendered above viewport (added scrollIntoView); RSVP `callback_data` lacked event_id; literal `*` markdown asterisks rendered in caption; `edit_message_text` failed on photo+caption messages (switched to `edit_message_reply_markup`).

**Files:** `bot/database/{db,models}.py` (events columns: cover_path, auto_pin, topic_id, source_poll_*), `bot/handlers/{events,polls}.py`, `dashboard/app.py` (events routes, /api/polls, no-cache middleware), `dashboard/templates/events.html` (full rewrite of form + JS), `scripts/{demo_event_to_den,repair_event_rsvp_buttons}.py`.

---

#### T-073: Dashboard: send weekly roundup manually
**Phase:** 13 | **Priority:** P2 | **Status:** TODO

"Send now" button for the weekly roundup. Generates and posts the roundup to the general channel immediately.

---

#### T-074: Dashboard: activity analytics (charts/graphs)
**Phase:** 13 | **Priority:** P2 | **Status:** TODO

Add simple charts to the overview page: activity over time (line chart), messages per topic (bar chart), member growth. Use Chart.js via CDN.

---

#### T-075: Investigate: levels points given to only some users
**Priority:** P1 | **Status:** TODO

**Finding:** Not a bug. The `goals` feature is enabled, so the `track_goals_participation` handler gives 2-3 points to anyone who posts in the יום יום topic (thread 2184). The `levels` feature (1 point per message in ANY topic) is NOT enabled. Only goals-topic posters got points. This is by design.

**Action needed:** Decide whether to enable the `levels` feature for all-topic points, or keep it goals-only. Update dashboard to make this clearer.

---

#### T-076: Rotating admin titles for top members (weekly)
**Priority:** P1 | **Status:** TODO | **Deps:** T-031

Use `setChatAdministratorCustomTitle` to give top-performing members visible titles in the group.

**Design:**
- Weekly scheduled job (e.g., during roundup): check top 3-5 members by weekly activity/points
- Promote them to admin with **zero permissions** (all flags false)
- Set custom title matching their level: "🔥 Fire Starter", "⭐ Rising Star", etc.
- **Expire after 1 week**: next week's job demotes previous winners and promotes new ones
- Track promoted users in a `title_holders` DB table to manage demotion

**Constraints:**
- Max 50 admins per group (Telegram limit) — keep to 3-5 title holders max
- Bot must have "Add admins" permission
- Users WILL see they're admin (unavoidable) — frame it as an achievement/reward
- Zero-permission admin can't do anything harmful

**Flow:**
1. Weekly job runs → get top N members by points this week
2. Demote previous title holders (remove admin)
3. Promote new winners (add admin, zero permissions)
4. Set custom title via `setChatAdministratorCustomTitle`
5. Announce in group: "השבוע הזוכים בתואר..."
6. Dashboard shows current title holders on health/levels page

Files: `bot/handlers/titles.py` (new), `bot/database/models.py`, `bot/scheduler/jobs.py`

---

#### T-077: Configurable validation-based scoring system
**Priority:** P1 | **Status:** TODO | **Deps:** T-031, T-003

Replace hardcoded point values with a validation-based scoring model loaded from YAML config. No points for raw message sending — only for actions validated by the bot or community.

**Scoring model:**
- Reply to bot prompt (morning/evening/discussion): 3-5 pts
- Reaction received on your message: 1 pt
- Reply received on your message: 2 pts
- Correct trivia answer: 12-15 pts
- Trivia round winner: 20-25 pts
- Event participation: 6-8 pts
- Daily streak bonus: 3 pts (ramps at day 7/14/30)

**Implementation:**
- Add `gamification` section to `config/settings.yaml` with all point values
- Create `bot/utils/scoring.py` — central scoring logic reading from config
- Update `bot/handlers/levels.py`, `trivia.py`, `events.py`, `goals.py` to use config values
- Track bot prompt replies (detect replies to bot's scheduled messages)
- Track reactions received (new handler)
- Dashboard page to view/edit scoring config
- Display scoring breakdown on levels dashboard page

**Key principle:** System is self-limiting by design — bot sends finite prompts/day, trivia is scheduled, reactions require others. No artificial cooldowns needed.

---

#### T-097: Free Games — daily GG.deals feed with LLM reranker
**Priority:** P2 | **Status:** IN PROGRESS (2026-04-13) — shipped disabled, awaits activation
**Commit:** `37f12e9 wip: free games daily feed with LLM reranker`

Daily scheduler fetches GG.deals RSS, filters `/freebie/` entries (skipping aggregator/roundup posts), dedups via `free_games_posted` SQLite table, then asks Claude Haiku (via `claude -p` CLI) to pick the most interesting candidate for a gaming community. Posts one curated drop per day to topic 1517 (gaming) or to the test group without a topic.

**Current config** (`config/settings.yaml`): `features.free_games.enabled=false, groups=[test]` — safe default. Dashboard at `/free-games` has toggle, schedule, test/main send-now buttons, unpost button.

**Files:** `bot/handlers/free_games.py` (new), `bot/database/{models,db}.py`, `bot/scheduler/jobs.py`, `dashboard/app.py` (5 new routes), `dashboard/templates/{base,free-games}.html`, `requirements.txt` (+feedparser, +httpx), `config/settings.yaml`.

**Key decisions made:**
- Source is GG.deals RSS (`https://gg.deals/eu/news/feed/`), filtered by `"/freebie/" in entry.link`. Reddit r/FreeGameFindings is config-swappable fallback.
- Aggregator tokens skipped: `roundup, weekend, weekly, this week, best deals, best free, best freebies, top deals, deals of`. Catches trial/free-play-days entries too (lower quality than permanent "keep" drops).
- Reranker uses `claude -p "..." --model haiku` subprocess — ~37s runtime but once/day so fine. No ANTHROPIC_API_KEY needed (uses user's CLI login session). Falls back to "newest" if CLI missing/errors.
- Dedup GUID = `entry.id` (gg.deals URL). When adding Epic, prefix with `epic:<slug>` to avoid collision.

**Verified:** 24 feed entries → 3 freebie candidates → LLM correctly picks "Graveyard Keeper" (known indie, permanent keep, 3 platforms) over "LivingForest" (obscure) and "Burning Skies DLC" (DLC-only for F2P).

See T-099 for activation.

#### T-098: Free Games — add Epic Games API as second source
**Priority:** P2 | **Status:** TODO
Add Epic's weekly free-games drops (every Thursday, the most popular freebies) alongside GG.deals, since GG.deals rarely surfaces Epic's weekly drops as `/freebie/` entries.

**Endpoint (public, no auth):** `https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions`

**Not yet verified** — user interrupted before I fetched the endpoint. First step: `curl` + `jq` to confirm JSON shape. Expected path approximately: `data.Catalog.searchStore.elements[]` with `title`, `productSlug`, `promotions.promotionalOffers[]` (current free) vs `promotions.upcomingPromotionalOffers[]` (future).

**Implementation:**
1. New helper `_fetch_epic_candidates()` in `bot/handlers/free_games.py`, returning `[{"guid": "epic:<slug>", "title": ..., "link": "https://store.epicgames.com/en-US/p/<slug>"}]`. Filter to entries with ACTIVE promotional offer (startDate ≤ now < endDate) AND `discountPercentage == 100`.
2. In `fetch_and_post_once()`: extend GG.deals candidate list with Epic ones, cap at `_MAX_CANDIDATES=5`.
3. Handle Epic fetch failures gracefully — log warning, continue with GG.deals-only. Don't let Epic outage break the flow.

**Files:** `bot/handlers/free_games.py` only. No schema changes (store column already supports "epic" value). No dashboard changes.

#### T-099: Free Games — activate in main group (topic 1517)
**Priority:** P2 | **Status:** TODO | **Depends on:** T-097

Flip free-games from test-only → main group production.

**Steps:**
1. Open `http://localhost:8000/free-games`
2. Toggle "פעיל" (enabled) on
3. Add `main` to groups (remove `test` or keep both)
4. Confirm `topics.gaming: 1517` is set in `config/settings.yaml` (already is — handler auto-uses it when posting to main)
5. Click "שלח לקבוצה ראשית" once manually to verify format in topic 1517
6. Save triggers `_signal_bot_reload()` → SIGHUP → `reload_jobs()` picks up new schedule
7. First scheduled run fires at next 10:00 IDT

**Optional:** Reset dedup for a fresh LLM-ranked demo post: `sqlite3 data/bot.db "DELETE FROM free_games_posted;"`

**Note:** Sherlocks Den (test group) forum-topic status is UNVERIFIED. Handler already skips `message_thread_id` for test group. Main group IS forum-enabled (topic 1517 used regularly).

#### T-100: Configure git remote and push WIP commits (✅ DONE 2026-04-17)
**Priority:** P1 | **Status:** ✅ DONE

Remote added: `https://github.com/endlessblink/botson.git`. Default branch renamed `master` → `main`. First push required `git filter-branch` to purge accidentally-committed `media/showcase/node_modules/` (159 MB chrome-headless-shell + webpack caches) from history — repo size dropped from 136M to 35M. All commits rewritten; hashes changed.

#### T-101: Handle media assets (✅ DONE 2026-04-17)
**Priority:** P3 | **Status:** ✅ DONE — committed directly

All files small enough (1-4 MB each): `botson-showcase_Resolve.mov` 2.9M, `telegram-onboarding.gif` 2.8M, `telegram-onboarding.mp4` 1.3M. No LFS needed. Existing siblings (`botson-showcase.gif/mp4`) were already tracked — consistency wins. Committed directly.

#### T-102: Cover-image support in planner wizard (✅ DONE 2026-04-17)
**Priority:** P2 | **Status:** ✅ DONE

Added optional cover images to scheduled messages and polls. Three sources from the planner wizard Step 2:
- **📁 Upload** — POST `/api/covers/upload` (multipart, validates mime, 8MB cap)
- **✨ AI** — POST `/api/covers/generate` via kie.ai Flux 2 Pro text-to-image (16:9)
- **🔗 Scrape URL** — POST `/api/covers/scrape` (og:image / twitter:image / JSON-LD / img srcset; handles direct-image URLs)

**Schema (non-destructive ADD COLUMN):**
- `scheduled_messages.cover_path TEXT` — relative to MEDIA_DIR, e.g. `covers/1776369279_scrape_4e4c4b6c.jpg`
- `scheduled_messages.poll_options TEXT` (JSON array), `poll_duration INTEGER` — promoted from wizard-only to persisted

**Sending:** `bot/handlers/calendar.py::send_message_with_optional_cover()` sends `send_photo` + caption when cover set; `send_poll_message()` uses inline-button polls (`🗓️ {label}` with `callback_data=poll_{idx}`) compatible with the existing `bot/handlers/polls.py` vote tracker. Native Telegram `send_poll` was swapped out because it can't carry images and hid voter names.

**Infrastructure:**
- Doppler project `botson` created (description: "Telegram bot for the children group with a web dashboard"). All secrets migrated from `/opt/robotnik/.env` with no prefix (per-app convention).
- `/opt/robotnik/scripts/sync-env.sh` regenerates `.env` from Doppler on every systemd restart (ExecStartPre on both `botson.service` and `botson-dashboard.service`).
- `KIE_API_KEY` stored in Doppler `botson/prd` (imported from `~/.watchpost/.env`).
- UFW port 8080 open to user IP only.
- New dashboard password rotated and stored in Doppler; fetch with `doppler secrets get DASHBOARD_PASSWORD --project botson --config prd --plain`.

**Files:** `bot/database/{models,db}.py`, `bot/handlers/calendar.py`, `bot/utils/kie_client.py` (new), `dashboard/app.py` (3 new routes, `/media` mount), `dashboard/templates/planner.html` (cover section in wizard, FullCalendar `defaultTimedEventDuration: '00:01'` to fix late-evening events spanning next day).

**Backup before deploy:** `/opt/robotnik/backups/bot.db.backup-20260416-210946` (pre-migration).

---

## Sprint: Emoji Night 🎬 (Phase 17)

> Packaged 2026-04-18 · Tasks T-103 → T-110 · Status: 7/8 done (T-103 → T-109)

### Goal

Ship a weekly emoji-puzzle **quiz-show event**: dashboard-configurable, per-group toggleable, auto-judging, auto-scoring. Activates the dormant `movies` topic (~0 activity in 30d) and builds a ritual members plan around.

### Scope (in)

- Weekly event: intro → 5 puzzles paced ~6 min apart → wrap-up (~35 min total)
- First-correct-reply wins = **+5 pts**, anti-dominance cap 1 win/user/week
- Curated pool of ~30 seed puzzles (Hebrew + English + aliases), expandable from dashboard
- Dashboard page `/puzzles` — schedule knobs, pool CRUD, history, admin "Run now"
- Works in any enabled group (main / test), no hardcoded chat_id or time

### Scope (out)

- Main-group launch — gated on successful dry-run in Sherlocks Den (sprint ends at dry-run; promote in a follow-up)
- Themes / difficulty curves / streak bonuses — defer to v2 if sprint v1 lands
- AI-generated puzzles from the dashboard — defer to v2

### Definition of Done

- [x] All schedule + pool + feature-flag values editable from `/puzzles` dashboard
- [ ] "Run now" admin button fires a full event flow in Sherlocks Den
- [ ] Replies auto-judged, winner auto-announced, points awarded in real DB
- [x] 24h reveal job posts answer for any unsolved puzzle
- [x] `scripts/emoji_puzzle_test.py` deleted
- [x] `/puzzles` appears in BOTH sidebar blocks of `base.html`
- [ ] One successful end-to-end dry-run observed in Sherlocks Den

### Timeline

**Estimated:** ~10h focused work. **Deadline:** none — "launch when ready" (confirmed 2026-04-18).

### Tasks

| Phase | Task | Est | Status | Deps |
|---|---|---|---|---|
| A | T-103: DB schema + helpers | 2h | ✅ DONE | — |
| B | T-104: YAML pool seed + loader | 1h | ✅ DONE | T-103 |
| C | T-105: Settings + feature flag | 30m | ✅ DONE | T-103 |
| D | T-106: Session layer + migration | 1h | ✅ DONE | T-103 |
| E | T-107: Handler (send/watch/judge/reveal) | 3h | ✅ DONE | T-103, T-104, T-106 |
| F | T-108: Scheduler (cron from settings) | 1h | ✅ DONE | T-105, T-107 |
| G | T-109: Dashboard `/puzzles` + sidebar | 3h | ✅ DONE | T-107 |
| H | T-110: Cleanup + dry-run | 1h | TODO | T-108, T-109 |

### Design decisions locked in 2026-04-18

- Format: quiz-show (A) — 5 puzzles paced ~6 min over 35 min, intro + wrap-up
- Scoring: flat 5 pts per first-correct (matches `event_rsvp`)
- Frequency: weekly, day configurable from dashboard
- Anti-dominance: 1 win per user per 7-day window; if same user solves again, round stays active silently until someone else solves or 24h reveal
- Runs in any group where feature enabled (main / test toggleable per group)
- Built on existing `scheduled_messages` + `calendar_checker` pipeline for message pacing (no parallel scheduler)
- Matching: permissive — Hebrew + English + curated aliases per puzzle. Strips "ה" prefix, punctuation, whitespace, lowercase. Transliteration NOT accepted (must be added as alias).

### Risks & mitigations

| Risk | Mitigation |
|---|---|
| Noam/Lotem sweep all 5 puzzles, entrench dominance | 1-win-per-7-days cap; round stays open silently if same user wins again |
| Pool thins out after N weeks | 30 seeds × 5/week = 6 weeks base; dashboard pool CRUD + add-new workflow (T-109) |
| Hebrew matching false-negatives ("מלך האריות" vs "ממלכת האריות") | Curated aliases per puzzle; disputed titles add an alias rather than weakening normalization |
| Polling conflict with live bot | Handler runs inside live bot process, reuses single getUpdates loop — no conflict |
| Event fires during a quiet hour, nobody participates | Dry-run in Den first; dashboard "Run now" button for testing; hot-reload so schedule can be tuned mid-week |
| Reply watcher misses replies (e.g. after bot restart mid-event) | Round state in DB, not memory; handler is stateless and looks up active rounds per reply |

### Dependencies outside sprint

- T-002 (DB layer) — ✅ done
- Existing `scheduled_messages` + `calendar_checker` pipeline — ✅ done
- `bot/utils/scoring.py` `get_points()` pattern — ✅ done

### Detailed task specs follow

#### T-103: Emoji Night — DB schema + helpers (✅ DONE 2026-04-18)
**Priority:** P1 | **Status:** ✅ DONE

Added two tables to `bot/database/models.py` SCHEMA:
- `emoji_puzzles` — pool: id, emoji_prompt, answer_he, answer_en, aliases (JSON), difficulty (1/2/3), media_type (movie/series), enabled, times_used, created_at
- `emoji_puzzle_rounds` — round state: id, puzzle_id, chat_id, message_thread_id, message_id, sent_at, winner_user_id, winner_message_id, solved_at, revealed_at, status (active/solved/revealed), award_points
- Indexes: `idx_emoji_rounds_active` (status, message_id) for reply-watcher lookup; `idx_emoji_rounds_sent` (sent_at) for 24h reveal job

Added 13 helper methods to `bot/database/db.py`:
- CRUD: `create_emoji_puzzle`, `list_emoji_puzzles`, `get_emoji_puzzle`, `update_emoji_puzzle`, `delete_emoji_puzzle`
- Selection: `pick_next_emoji_puzzle` (least-used, deterministic)
- Round lifecycle: `start_emoji_round`, `get_active_emoji_round_for_message`, `get_all_active_emoji_rounds`
- Atomic mark: `mark_emoji_round_solved` (conditional UPDATE — second concurrent correct returns False), `mark_emoji_round_revealed`
- Background: `get_emoji_rounds_to_reveal` (24h+ active), `user_has_emoji_win_since` (anti-dominance)
- Stats: `get_emoji_round_stats`

**Verified end-to-end:** created puzzle → picked → started round → solved (True) → second solve attempt (False, race-safe) → win-since-7d check (True) → stats aggregation.

#### T-104: Emoji Night — YAML pool seed + init loader (✅ DONE 2026-04-18)
**Priority:** P1 | **Status:** DONE | **Deps:** T-103

Create `config/emoji_puzzles.yaml` with ~30 curated seed puzzles (Hebrew + English + 3–4 aliases each, mixed difficulty 1/2/3, mix of movies + series). Initial picks discussed in session: 🦁👑 (Lion King), 🧙‍♂️💍🌋 (LOTR), ⚗️💊🏜️ (Breaking Bad), 🕷️👦 (Spider-Man), 👨‍🍳🐀🇫🇷 (Ratatouille), 🐠🔍🌊 (Finding Nemo), 👽📞🏠 (E.T.), ❄️👭⛄ (Frozen), 🦖🏝️ (Jurassic Park), 🚢🧊💔 (Titanic), 🤖🟥💊🟦 (Matrix), 🎓🧙‍♂️⚡ (Harry Potter).

On bot init: loader reads YAML, checks if `emoji_puzzles` table is empty, bulk-inserts if so. Idempotent — re-runs don't duplicate. User can add/edit/remove from dashboard (T-109) afterward, YAML is just the seed.

**Approval flow:** before YAML ships, put the 30 puzzles on dashboard `/review` as individual items so user can approve Hebrew copy + alias lists per puzzle (per feedback_hebrew_review_location memory). Batch as a pool-review item once curated.

**Implemented:** `config/emoji_puzzles.yaml` now seeds 30 movie/series puzzles; `bot.main.post_init()` calls `db.seed_emoji_puzzles()` on startup; `/review` one-time injects 30 per-puzzle approval cards generated from the same YAML so copy + aliases can be reviewed without duplicating source data.

#### T-105: Emoji Night — settings + feature flag + per-group toggle (✅ DONE 2026-04-18)
**Priority:** P1 | **Status:** DONE | **Deps:** T-103

Add to `config/settings.yaml`:

```yaml
features:
  emoji_puzzle:
    enabled: false
    groups: []             # "main" / "test"
schedule:
  emoji_puzzle:
    days: []               # Hebrew 0=Sun..6=Sat
    time: "22:00"
    puzzle_count: 5        # 3 / 5 / 7
    interval_minutes: 6    # pacing between puzzles
    intro_offset_seconds: 60
    wrap_offset_seconds: 420  # 7 min after last puzzle
gamification:
  emoji_puzzle_winner: 5
```

Hot-reload compatible (picked up on `data/reload` touch). All values editable from dashboard (T-109) — this task just defines the schema and defaults.

**Implemented:** added `features.emoji_puzzle`, `schedule.emoji_puzzle`, and `gamification.emoji_puzzle_winner` defaults to `config/settings.yaml`. Added scoring fallback support for `emoji_puzzle_winner` in `bot/utils/scoring.py`, so upcoming handler work can rely on the shared points config immediately.

#### T-106: Emoji Night — session layer + rounds.session_id migration (✅ DONE 2026-04-18)
**Priority:** P1 | **Status:** DONE | **Deps:** T-103

Add a session concept to group N rounds within a single Emoji Night event:
- New table `emoji_puzzle_sessions`: id, chat_id, message_thread_id, started_at, ended_at, puzzle_count, winner_summary (JSON), status (active/completed)
- Migration: `ALTER TABLE emoji_puzzle_rounds ADD COLUMN session_id INTEGER` (idempotent via try/except, matches existing migration pattern in `_migrate()`)
- Helpers: `create_emoji_session`, `get_active_session`, `complete_emoji_session`, `get_session_leaderboard` (aggregates winners within a session for the wrap-up message)

**Implemented:** added `emoji_puzzle_sessions` to the base schema plus indexes, added idempotent migration for `emoji_puzzle_rounds.session_id`, updated `start_emoji_round()` to attach rounds to a session, and added the four session lifecycle helpers in `bot/database/db.py`.

Enables: wrap-up message can show "winners of the night"; dashboard can show event history per-session instead of per-round.

#### T-107: Emoji Night — handler (send/watch/judge/reveal) (✅ DONE 2026-04-18)
**Priority:** P1 | **Status:** DONE | **Deps:** T-103, T-104, T-106

Create `bot/handlers/emoji_puzzle.py`:

- `start_emoji_night(context, chat_id, thread_id)` — picks N puzzles (random-by-difficulty: 2 easy + 2 med + 1 hard from enabled pool), creates session, schedules the N+2 messages (intro + puzzles + wrap-up) via `db.create_scheduled_message()` rows timed by `interval_minutes` config. Each puzzle send creates a round row via `db.start_emoji_round()` so the watcher has the answer.
- `MessageHandler` — watches replies where `update.message.reply_to_message.message_id` matches an active round. Normalizes user text (strip ה prefix, unicode NFC, lowercase, strip punct/whitespace), compares to answer_he + answer_en + aliases. On match + no prior 7-day win: atomic `mark_emoji_round_solved` → `db.add_points(5)` → `log_activity` → `check_level_up` → bot reply "🎉 {name} פתר/ה!"
- `reveal_unsolved_rounds_job` — APScheduler every hour, calls `get_emoji_rounds_to_reveal(24)`, posts answer, marks revealed
- Startup recovery: on bot start, `get_all_active_emoji_rounds()` warms the reply-watcher (handler is stateless, lookup is DB-driven, so nothing special needed — just confirm lookup index is fast)

**Implemented:** added `bot/handlers/emoji_puzzle.py` with session scheduling, reply judging, normalization, anti-dominance win suppression, scoring, level-up support, and hourly reveal job. `bot/handlers/calendar.py` now routes `emoji_puzzle_*` scheduled rows through the new handler so puzzle sends create round rows and wrap-up messages close sessions cleanly. Registered the watcher + reveal job in `bot/main.py`.

`register()` function at bottom for `bot/main.py` to call.

#### T-108: Emoji Night — dashboard-driven scheduler (cron from settings) (✅ DONE 2026-04-18)
**Priority:** P1 | **Status:** DONE | **Deps:** T-105, T-107

Register APScheduler cron job in `bot/scheduler/jobs.py` (or equivalent) that reads `schedule.emoji_puzzle` from settings on each tick and fires `start_emoji_night` for each enabled group. Pattern to mirror: `send_scheduled_trivia` in `bot/handlers/trivia.py:79`.

Hot-reload: on `data/reload`, re-register cron with new days/time. No parallel scheduler — must go through the existing reload pipeline (per project_botson memory: "Don't add parallel schedulers").

**Implemented:** added `send_scheduled_emoji_night()` in `bot/handlers/emoji_puzzle.py`, resolving enabled `main`/`test` targets from shared settings and registering one JobQueue cron per configured weekday in `bot/scheduler/jobs.py`. Reload-safe because the schedule is recreated through `setup_jobs()`.

#### T-109: Emoji Night — dashboard /puzzles page + sidebar both blocks (✅ DONE 2026-04-18)
**Priority:** P1 | **Status:** DONE | **Deps:** T-107

New dashboard page `/puzzles` (per Dashboard Parity Rule). Cards:
1. **Schedule** — day chips (per feedback_dashboard_ux), time picker, puzzle_count (3/5/7), interval_minutes, group toggle (main/test)
2. **Pool** — table of puzzles (emoji_prompt, answer_he, answer_en, aliases, difficulty, media_type, enabled toggle, times_used). Add / edit / delete. Per dashboard_ux memory: NO long list — show next 10 with a pool count + "show all" expand.
3. **History** — recent sessions (session_id, date, group, puzzle_count, winners)
4. **Admin** — "Run now" button for testing in Sherlocks Den

Routes in `dashboard/app.py`: GET `/puzzles`, POST `/api/puzzles/create`, PATCH `/api/puzzles/{id}`, DELETE `/api/puzzles/{id}`, POST `/api/puzzles/schedule`, POST `/api/puzzles/run-now`.

Template `dashboard/templates/puzzles.html`. Style-mirror `trivia.html`.

**CRITICAL:** Add `/puzzles` to BOTH sidebar blocks in `dashboard/templates/base.html` (mobile overlay + desktop sidebar) per feedback_sidebar_accessibility memory. Currently the two blocks start at lines 80 and 162.

**Implemented:** added `/puzzles` page, pool CRUD APIs, schedule-save API, run-now API, history view, and both sidebar links. The page exposes schedule controls, group toggles, winner points, collapsed pool editing, recent sessions, and manual run-now for Sherlocks Den or the main group.

#### T-110: Emoji Night — cleanup + dry-run in Den
**Priority:** P2 | **Status:** TODO | **Deps:** T-108, T-109

- Delete `scripts/emoji_puzzle_test.py` (obsolete once handler exists)
- Delete the 3 `emoji-puzzle-r1-*` items from VPS `data/pending_reviews.json` (single-puzzle templates superseded by pool)
- Dry-run: enable feature for `test` group only from dashboard, click "Run now" → watch Sherlocks Den for full 35-min event flow, verify pacing + judging + winner announcement + wrap-up + 24h reveal (or confirm winners-before-24h ends cleanly)
- Iterate on copy/pacing based on how it lands
- Only after a successful dry-run: enable for `main` group, schedule first real event

**Implemented so far:** deleted `scripts/emoji_puzzle_test.py` and replaced it with the real dashboard run-now flow. Local workspace has no `data/pending_reviews.json`, so there were no local `emoji-puzzle-r1-*` review items to remove here. Remaining work is the live Sherlocks Den dry-run and any copy/pacing iteration based on that observation.

---

#### T-111: Auto-start trivia/emoji scheduled game rows
**Phase:** 18 — Trivia polish | **Priority:** P1 | **Status:** DONE

Scheduled game rows now start gameplay instead of posting only announcement text:

- `scheduled_messages.message_type='trivia_round'` launches `bot.handlers.trivia_round.start_scheduled_trivia_round()` from the calendar dispatcher.
- `scheduled_messages.message_type='emoji_puzzle'` launches `start_emoji_night()` from the calendar dispatcher.
- Existing natural-language rows like `discussion` text containing "סיבוב טריוויה" are coerced at send time into trivia launch rows, so already-saved calendar items don't need manual DB surgery.
- Existing `emoji_puzzle_*` internal rows still use `send_scheduled_emoji_message()`.
- Calendar rows are marked consumed with `sent_message_id=0` because the launch flow sends multiple Telegram messages rather than one canonical scheduled message.

**Files:** `bot/handlers/calendar.py`, `bot/handlers/trivia_round.py`, `dashboard/app.py`, `tests/test_calendar_scheduled_games.py`.

**Verified:** `./.venv/bin/python -m pytest tests`.

---

#### T-112: Trivia — verify drawer stays open on launch after hard-refresh
**Phase:** 18 | **Priority:** P2 | **Status:** TODO | **Deps:** T-111

The 2026-04-23 commit `fb8e058` removed `closeDrawer()` from `startTriviaRound` in `dashboard/templates/planner.html` so iterative test-launches don't force re-navigation. User reported drawer still closing on launch — suspected browser cache. Task: hard-refresh (Ctrl/Cmd+Shift+R) then launch; confirm drawer stays open. If it still closes, grep for other `closeDrawer` calls fired during the launch path.

**Files:** `dashboard/templates/planner.html` (search for `closeDrawer`).

---

#### T-113: Cleanup stale scheduled_messages rows with target_channel="general"
**Phase:** 19 — Cleanup | **Priority:** P2 | **Status:** TODO

After the 2026-04-22 routing refactor, `"general"` is no longer a resolvable target. Old rows in the `scheduled_messages` table (VPS `/opt/robotnik/data/bot.db`) with `target_channel='general'` would now fail the `topic_guard` at send time. Probably zero live impact (the guard refuses, bot.log shows the refusal), but the rows are dead weight.

**How to execute:**
1. On VPS: `sqlite3 data/bot.db "SELECT id, scheduled_date, text FROM scheduled_messages WHERE target_channel='general' AND sent_at IS NULL;"`
2. Review the list with the user.
3. After approval: `DELETE FROM scheduled_messages WHERE target_channel='general' AND sent_at IS NULL;` or UPDATE them to a valid channel.

**Files:** `bot/database/db.py` (scheduled_messages schema, for reference).

---

#### T-114: Handler Routing UI — teaser_topic_ids multi-select on Settings
**Phase:** 19 | **Priority:** P2 | **Status:** TODO

`bot_message_routing.teaser_topic_ids` is a JSON array — the API and DB already support per-handler multi-topic teasers. The dashboard Settings page currently only renders a single-select dropdown for `play_topic_id` (see `dashboard/templates/settings.html` "ניתוב פיצ'רים לערוצים" section). Build the teaser multi-select so operators can configure e.g. trivia → teaser in `movies` + `gaming` + `politics`.

**Files:**
- `dashboard/templates/settings.html` — add multi-select widget per row
- `dashboard/app.py` `/api/handler-routing/save` (already accepts the list — no backend change)
- `dashboard/templates/settings.html::saveHandlerRouting` JS — collect multiple selected option values

**Reuse:** the `verified_topics` context list that already feeds the play dropdown.

---

#### T-115: Trivia flow — audit + remove hardcoded strings in generation/teaser
**Phase:** 18 | **Priority:** P1 | **Status:** DONE (2026-05-10)

**Shipped 2026-05-10:** Audited the four sites listed below. Two had real leaks, fixed:
- `dashboard/templates/planner.html:_buildDefaultTeaser` — removed the `'הפינה של בוטסון'` fallback. When the play-channel label is missing, the teaser now drops the "ב{channel}" clause entirely instead of naming the wrong channel.
- `dashboard/app.py:build_generation_prompt` (field=trivia) — removed the `"נושאים מגוונים: תרבות, מדע, היסטוריה, בידור, גאוגרפיה, אוכל"` topic-list fallback. When the operator provides neither a theme nor categories, the prompt now says "ללא נושא מרכזי — מגוון חופשי…" without naming specific subjects (CLAUDE.md: defaults must be blank/random/operator-configured).

The other two sites (`bot/handlers/trivia_round.py:_run_round` teaser, `COMMUNITY_CONTEXT`) had already been cleaned up in earlier phases — the teaser fallback now uses `theme_label` (which defaults to `GENERAL_THEME_LABEL = "כללי"`) and the generic line "הודעת הפתיחה תופיע בערוץ המשחק" with no channel-name leak.

The `bot/handlers/trivia_round.py` numeric constants (`POINTS_CORRECT`, `QUESTION_COUNT`, etc.) are gameplay rules, not LLM-prompt thresholds, and are out of scope for the hardcoded-content rule.

Coverage: `tests/test_trivia_no_hardcoded_strings.py` (5 tests) pins the absence of both leaked strings + that explicit categories/themes still flow through correctly.

Blocker for broad category use: the pipeline still has several hardcoded strings that break when the user picks a non-Israel theme. Audit and make every user-facing string derived from the user's inputs (category, theme, channel names) or configurable.

**Known hardcoded bits to inspect/fix:**
- `dashboard/app.py::build_generation_prompt` field=trivia — default fallback `"נושאים מגוונים: תרבות, מדע, היסטוריה, בידור, גאוגרפיה, אוכל"` when user passes no category/theme. Should be configurable or removed.
- `dashboard/app.py::build_generation_prompt` — `COMMUNITY_CONTEXT` is a shared constant; verify it doesn't lock the AI to a specific demographic.
- `bot/handlers/trivia_round.py::_run_round` default teaser fallback `"🧠 עוד רגע מתחיל סיבוב טריוויה ({theme_label}) בפינה של בוטסון"` — hardcodes "בפינה של בוטסון" even when play channel is something else. User-provided `teaser_text` overrides, but when absent the fallback should derive the channel name from `verified_forum_topics` using `thread_id`.
- `bot/handlers/trivia_round.py` constants `THEME_LABEL`, `PREFERRED_CATEGORIES`, `QUESTION_COUNT`, `QUESTION_TIMEOUT_S`, `POINTS_CORRECT`, `POINTS_FIRST_PLACE_BONUS` — check which are truly immutable vs which should be editable from the dashboard.
- `dashboard/templates/planner.html::_buildDefaultTeaser` — includes "הפינה של בוטסון" fallback string; should always come from the selected play channel's name.
- Hardcoded ה-category filter fallback pool `PREFERRED_CATEGORIES` in `trivia_round.py` — used only when caller passes `None`; confirm no dashboard path still triggers that fallback.

**Pass criteria:** launching a round with a completely unrelated theme (e.g. categories=`["קולינריה"]`, play=an arbitrary verified channel) produces an AI-generated batch, a teaser that names *that* channel, and an announcement/final message with no lingering "ישראל" / "בוטסון corner" / Israel-specific copy.

---

#### T-116: Events — stop overwriting user content with date/time line
**Phase:** 20 — Events polish | **Priority:** P1 | **Status:** DONE (2026-05-10)

**Shipped 2026-05-10:** Removed the auto-title machinery in `dashboard/templates/events.html` entirely (`_autoTitleFor`, `_maybeRetitleFromDateTime`, `_autoTitlePrev`, `HE_DAYS`). The form already has dedicated `event-date` and `event-time` inputs — a title that is *also* a date string was redundant, and the `_autoTitlePrev` guard was unreliable when a poll-source filled the title (it set `_autoTitlePrev = null`, which then let date-string overwrites slip through on the next field change). Title is now strictly user-controlled or filled by `_applyPollMeta` from a poll question. Coverage: `tests/test_events_no_title_overwrite.py` pins the absence of the removed functions and confirms `_onAnyFieldChange` no longer calls retitle.

On `/events`, the title field gets auto-filled with a date/time summary like `"יום שישי | 24.4 | 18:30"`, wiping whatever the user typed (or what was pulled in from a source poll like `"משחקי קופסה אונליין: Splendor או משהו אחר..."`). The broken title then flows straight into the Telegram announcement when the event is published — the group sees a date string as the event name. The form already has explicit separate date (`event-date`) and time (`event-time`) inputs, so a title that is *also* date+time is pure redundancy.

**Root cause — pinned:**
- `dashboard/templates/events.html:645-655` defines `_autoTitleFor(dateIso, time)` that builds `"יום {day} | {D.M} | {HH:MM}"`.
- `dashboard/templates/events.html:657-669` `_maybeRetitleFromDateTime()` runs it and writes into `#event-title`. The `_autoTitlePrev` guard is supposed to preserve user edits but misfires when the source is a poll (line 378 sets `_autoTitlePrev = null` after `_applyPollMeta` fills the title, then the next date/time change re-overwrites).
- `seedDefaultsIfEmpty()` (around line 671) invokes the retitle on page load, producing the bad default even before the user interacts.

**Proposed fix (recommended):**
1. Remove the auto-title feature entirely. Delete `_autoTitleFor`, `_maybeRetitleFromDateTime`, `_autoTitlePrev`, and the calls that trigger them. Leave `#event-title` empty by default; user types a real title or it inherits from the poll source.
2. Publish-side: confirm `bot/handlers/events.py` uses the title as-is and never fabricates one.

**Alternative (if auto-title has fans):** only fill the title when both (a) it's empty AND (b) no poll source is active AND (c) no draft was loaded. Never run `_maybeRetitleFromDateTime` after any non-seed event (date/time changes must not rewrite title after page load).

**Files to modify:**
- `dashboard/templates/events.html` — kill the auto-title block and its callers (search `_autoTitle` / `_maybeRetitle`)
- `dashboard/templates/events.html:377` — `_applyPollMeta` already writes the poll text to title; keep that, just drop the `_autoTitlePrev = null` reset if auto-title is removed
- `bot/handlers/events.py` — verify the published message doesn't reconstruct a date-summary title

**Pass criteria:**
- Opening `/events` with empty form: `#event-title` stays empty (placeholder visible), date/time defaults appear separately below.
- Creating an event from a poll: title = cleaned poll question (already works via `_applyPollMeta`), never overwritten when user picks a date/time.
- Publishing the event to Telegram: group sees the user's title, not `"יום שישי | 24.4 | 18:30"`.
- The events list (bottom of `/events`) shows the original titles per row, not date-summary strings.

---

#### T-119: Scheduled activity reliability hardening (✅ DONE 2026-05-01)
**Phase:** 22 — Scheduler reliability | **Priority:** P1 | **Status:** DONE | **Deps:** T-111

Hardened scheduled executable activities so the calendar never reports false success:

- `trivia_round` still requires a real Telegram announcement `message_id` before the row is marked `sent`.
- Approval/send paths preserve visible HTTP errors instead of silently showing success.
- Successful trivia top-up is covered by tests: missing category questions are generated, deterministically reviewed, persisted to `config/trivia.yaml`, and reloaded as eligible questions.
- Added regression coverage for failed top-up, successful top-up, and scheduler proof checks.

**Files:** `dashboard/app.py`, `dashboard/templates/planner.html`, `tests/test_planner_coercion_and_chips.py`, `tests/test_calendar_scheduled_games.py`.

---

#### T-120: Runtime fallback for underfilled trivia rounds (✅ DONE 2026-05-01)
**Phase:** 22 — Scheduler reliability | **Priority:** P1 | **Status:** DONE | **Deps:** T-119

Added a bot-side fallback for already-approved trivia rows or post-approval pool edits: if `_pick_questions()` finds too few matching questions at fire time, the scheduled launcher attempts the same reviewed top-up path, reloads `trivia.yaml`, and only fails the row if the pool is still underfilled.

**Files:** `bot/handlers/trivia_round.py`, `tests/test_calendar_scheduled_games.py`.

---

#### T-121: Skipped status for legitimate scheduled no-ops (✅ DONE 2026-05-01)
**Phase:** 22 — Scheduler reliability | **Priority:** P2 | **Status:** DONE | **Deps:** T-119

Added `status='skipped'` for legitimate no-op activities so the calendar distinguishes "nothing to send" from real failures:

- Free-games rows with no new candidates are skipped, not failed.
- Weekly leaderboard rows with no leaders are skipped, not failed.
- `scheduled_messages.error_message` stores the skip reason.
- Planner status dots now include a skipped color.

**Files:** `bot/database/db.py`, `bot/handlers/calendar.py`, `bot/handlers/free_games.py`, `bot/handlers/levels.py`, `dashboard/templates/planner.html`, `tests/test_calendar_scheduled_games.py`.

---

#### T-122: Trivia top-up concurrency guard (✅ DONE 2026-05-01)
**Phase:** 22 — Scheduler reliability | **Priority:** P2 | **Status:** DONE | **Deps:** T-119

Added a per-row async lock around trivia top-up generation so concurrent approval/retry requests for the same scheduled game cannot duplicate generated questions or race on `config/trivia.yaml` writes.

**Files:** `dashboard/app.py`.

---

#### T-125: RSVP extension — closed as won't-fix (2026-05-07)
**Phase:** 23 — RSVP system | **Priority:** P3 | **Status:** WONT-FIX | **Deps:** —

Originally proposed extending RSVP buttons to morning/evening/discussion/facts/free_games/weekly_* with a global toggle. That framing was the inverse of how the feature should grow: it assumed RSVP was a default-on capability for every activity type that operators would opt out of via toggle. The actual rule is the opposite — RSVP is opt-in per-feature, only for activities that genuinely need an attendance commitment to work (synchronous, time-boxed). Today that's `trivia_round` and `emoji_puzzle`, both shipped in T-126/T-127. The other types listed (passive prompts, share-and-react content, scheduled summaries) don't have RSVP semantics, so adding a button there would be a feature in search of a problem. Future activity types that genuinely need RSVP can plug in by re-using `poll_options.warmup_marker` + `min_ready_players` + the dispatch gate at `bot/handlers/calendar.py:_enforce_warmup_rsvp_gate` — no master switch needed. The forward-looking rule lives in `CLAUDE.md` under "Warm-up RSVP system" → "RSVP is opt-in per-feature".

---

#### T-127: Cancel underfilled trivia/emoji games at dispatch (✅ DONE 2026-05-07)
**Phase:** 23 — RSVP system | **Priority:** P1 | **Status:** DONE | **Deps:** T-126

Added a dispatch-time RSVP gate so trivia/Emoji Night games don't burn a full launch into an empty topic. Before `start_scheduled_trivia_round` / `start_emoji_night` runs, the gate counts `trivia_interest_responses` against the paired warm-up announcement (looked up by `poll_options.warmup_marker`). If the count is below `min_ready_players`, the game row is marked `skipped` and a Hebrew "lo yotse laderech ha'pa'am — only N/M signed up" notice posts in the warm-up topic as a `reply_to` of the original announcement.

- New `_enforce_warmup_rsvp_gate(db, msg, bot, group_id)` in `bot/handlers/calendar.py`. Raises `SkippedActivity` to short-circuit; the existing exception handler marks the row `skipped` (T-121's status pattern).
- Marker stamped on the game row only when an announcement is actually emitted: manual path updates the `trivia_round` poll_options after `_ensure_trivia_announcement_scheduled`; populate path adds it to the trivia poll_payload and the emoji_puzzle poll_options. Emoji_puzzle's poll_options also gains `min_ready_players` so the gate has a threshold to compare against.
- Legacy rows (no `warmup_marker` or `min_ready_players=0`) bypass the gate entirely — the existing in-game pre-roll ready gate (`trivready` callback) is still the safety net for those.
- Cancel notice send is wrapped in try/except + UnverifiedTopicError handling so a guard rejection or network blip can't let the launch through.

Verification: 4 new regression tests in `tests/test_planner_coercion_and_chips.py::TestSchedulerTypeExposure` covering: cancel-when-under-threshold (1/3 RSVPs), proceed-when-met (2/2 RSVPs), legacy-row-no-marker pass-through, and emoji-cancel-when-under (0/2). Full suite: 91 pass, 115 subtests.

**Files:** `dashboard/app.py`, `bot/handlers/calendar.py`, `tests/test_planner_coercion_and_chips.py`.

**Refinement 2026-05-29:** Warm-up RSVP announcements are now one-shot public teasers. Populate/manual trivia can place the first warm-up in the relevant topic (for example movies, gaming, or music), while the executable game still launches in Botson's corner. `config/settings.yaml:game_warmup_topic_routes.enabled` is the operator escape hatch: set it false to keep warm-ups in the game play topic if relevant-topic teasers feel noisy. Public warm-ups are still cleaned up after the configured window, and no public reminder rows are reintroduced.

---

#### T-126: Second warm-up reminder (✅ DONE 2026-05-07)
**Phase:** 23 — RSVP system | **Priority:** P1 | **Status:** DONE | **Deps:** T-128

Wired the second warm-up reminder so under-RSVP'd trivia/Emoji Night announcements get a follow-up nudge `warmup_reminder_offset_min` minutes before kickoff (default 20). When the RSVP count already meets `min_ready_players` at dispatch, the reminder short-circuits to `status='skipped'` and no message is sent.

- New `message_type='warmup_reminder'`. Reminder rows are paired to their announcement via a shared `warmup_marker` in `poll_options` ('warmup-rsvp:<game_id>' for the manual trivia turn-live path, 'warmup-rsvp:<kind>:<date>:<time>' for Populate). Dispatch resolves the marker → announcement row → counts `trivia_interest_responses` for the *announcement* id, not the reminder's.
- Reminder button is omitted by design — when threshold is unmet, the reminder posts as a `reply_to_message_id` of the original announcement so users see the existing 🙋 button right above. Avoids the live-count drift between two messages with the same callback.
- Manual path: `_ensure_trivia_announcement_scheduled` now also creates/updates a paired reminder row via `_ensure_warmup_reminder_scheduled`. Marker lives on both rows.
- Populate path: `_ai_suggest_calendar` adds a `warmup_reminder` suggestion right after each `trivia_warmup_rsvp` announcement (trivia + emoji blocks). Slot-free guard, math validation (`reminder_offset < lead`), and LLM copy generation match the announcement flow.
- LLM prompt: `_generate_activity_copy` detects `is_reminder` and switches rules — copy explains the button is on the original announcement, not on this reminder.
- Dispatch: `bot/handlers/calendar.py` adds a `warmup_reminder` branch that filters announcement rows by marker in Python (no `json_extract` dependency) for VPS-version-agnostic behavior.
- Calendar UI: `_CAL_TYPE_STYLE` and `planner.html` typeEmoji map both gain `warmup_reminder` (⏰ "תזכורת RSVP") plus the previously-missing `trivia_warmup_rsvp` (🙋 "הכרזת RSVP") entries.

Verification: `tests/test_planner_coercion_and_chips.py::TestSchedulerTypeExposure::{test_turning_trivia_live_creates_warmup_reminder_with_shared_marker,test_warmup_reminder_skipped_when_threshold_met,test_warmup_reminder_sends_when_under_threshold}` (3 new tests, all green; full file 67 → 70 tests pass; `test_calendar_scheduled_games.py` 19 tests still pass).

**Files:** `dashboard/app.py`, `dashboard/templates/planner.html`, `bot/handlers/calendar.py`, `tests/test_planner_coercion_and_chips.py`.

---

#### T-123: Planner AI Populate mixed suggestions (✅ DONE 2026-05-04)
**Phase:** 23 — Planner AI reliability | **Priority:** P0 | **Status:** DONE | **Deps:** T-080

Fixed the production AI Populate flow so it opens the suggest+confirm modal first and returns a balanced mixed slate instead of discussion-only drafts. The suggest endpoint still writes nothing to `scheduled_messages`; only `/api/weekplan/ai-suggest-commit` inserts the rows explicitly approved in the modal.

- Week Populate now targets a configurable mixed budget from `config/settings.yaml:ai_populate.caps.week`.
- Day Populate ignores `schedule.*.days` and can suggest all configured activity types for the clicked date.
- Supported suggestion types now include morning, evening, discussion, trivia round + warm-up, emoji puzzle, facts tidbit, facts spooky, free games, weekly roundup, and weekly leaderboard when enabled/routed.
- Slot times come from `config/settings.yaml.schedule.*`; executable bot content topics come from `bot_message_routing`; discussion topic/category matching is validated again at commit time.
- Trivia populate defaults live in `config/settings.yaml:trivia.populate_defaults`, including warm-up offset and poll payload defaults.
- Removed the pre-modal browser confirm so every Populate click shows the modal immediately.
- Suggestions now skip slots before the current server datetime, including earlier dates in the current week and earlier times today.
- Emoji Night suggestions now include a separate announcement row before the game row; the game row shows the subject and carries `theme_label`/`media_types` in `poll_options` so the runtime filters to the same subject.
- The Emoji Night announcement lead is admin-configurable via `schedule.emoji_puzzle.announcement_lead_minutes` (default 90 minutes, intended 1–2 hour range), alongside `theme_label` and `media_types`.
- Added regression coverage that the suggest engine returns mixed types and writes zero rows before approval.

Verification: `python3 -m py_compile dashboard/app.py`, `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py -q`, and `/tmp/e2e_suggest.py` stubbed flow. Deployed to prod as `28ce1b4`; user confirmed the production behavior looked much better.

**Files:** `dashboard/app.py`, `dashboard/templates/planner.html`, `config/settings.yaml`, `tests/test_planner_coercion_and_chips.py`.

---

#### T-133: Grow content pools 🌱
**Phase:** 24 — Content quality & freshness | **Priority:** P1 | **Status:** DONE (2026-05-11) | **Deps:** —

**Shipped 2026-05-11:** Expanded `config/facts.yaml` to `tidbit=46` and `spooky=48`, rebuilt `config/discussions.yaml` so every category has at least 25 Hebrew prompts, and added `music=25` for חדר מוסיקה. `tests/test_facts_pool.py` now enforces minimum pool sizes for facts and discussions.

The mechanical repetition problem (broken dedup) was fixed in Phase A.1 / A.1.4 — facts won't repeat for 60 days, emoji for 30. But pools are too small for dedup to help: 8 spooky + 8 tidbit facts means full cycling within 8 weeks no matter what. Discussion seeds are similarly thin (~10 per category). This task grows the pools so dedup has room to work.

Targets:
- `config/facts.yaml:spooky` — 8 → 40 items.
- `config/facts.yaml:tidbit` — 8 → 40 items.
- `config/discussions.yaml` — every category ≥ 25 items (currently ~10).

Tooling: use the Hermes `botson-question-pool` skill for discussion generation, then run each batch through the `hebrew-content-qa` skill before commit. Every fact must carry a real `source` and `source_url` per the existing schema in `config/facts.yaml`.

This is a **content task** — no Python changes. The bot doesn't change.

**Verification:**
- `pytest tests/test_facts_pool.py -q` — existing curation tests pass.
- Add a count assertion: every category in `discussions.yaml` has ≥ 25 items; `facts.yaml:spooky` and `:tidbit` each have ≥ 40 items. Test fails loudly if pool shrinks below threshold.

**Files:** `config/facts.yaml`, `config/discussions.yaml`, `tests/test_facts_pool.py` (extend with count assertion).

---

#### T-134: Wire question_quality.md into every prompt path 🧪
**Phase:** 24 — Content quality & freshness | **Priority:** P1 | **Status:** DONE (2026-05-09) | **Deps:** —

**Shipped 2026-05-09:** Audit found two of the four builders already wired (`build_generation_prompt` line 2908, `_gen_text` transitively via `build_generation_prompt`). The two missing sites — `_generate_activity_copy` (warm-up announcements + reminders) and `bot/scheduler/materializer.py:_generate_fresh_text` (morning/evening/discussion refill) — now append `load_quality_rules_short()` to their inline rules. Loader extracted to `bot/utils/quality_rules.py` so dashboard and bot share one source. Regression coverage in `tests/test_quality_rules_wiring.py` (5 tests) asserts the canonical-rules markers appear in every wired prompt and that both modules read identical text.

`config/question_quality.md` already has 120 lines of curated rules including ✅ good and ❌ bad examples (lines 26–27 + the "Concrete failures to refuse" block at lines 44–60). It IS authoritative. But it's only consumed by `_build_digest_cli_prompt()` via `_load_quality_rules_short()`. Other LLM-generation paths build their own prompts without these rules, which is why discussion / morning / evening output quality is inconsistent.

Audit (read-only, do first):
- `dashboard/app.py:build_generation_prompt` — does it include the rules?
- `dashboard/app.py:_generate_activity_copy` — used by warm-up announcements + reminders; rules absent today.
- `dashboard/app.py:_gen_text` — populate single-row helper; rules absent.
- `bot/scheduler/materializer.py:_generate_fresh_text` — batch refill; rules absent.
- Any other `build_*_prompt` builder.

Fix per path:
- Add the `_load_quality_rules_short()` block (or full version where the CLI timeout budget allows).
- Surface ✅ / ❌ examples explicitly in the prompt so the LLM has anchors, not abstract rules.

Content side (the **content** half of the hybrid lane): `config/question_quality.md` may need 2–3 more positive ✅ examples per discussion category. We currently have one ❌/✅ pair at the abstract level and no category-level positive examples. Hermes `hebrew-content-qa` output can seed these.

**Verification:**
- Regression test: for each prompt builder, the output prompt string must contain "❌" and "✅" markers (proves the examples flowed through).
- Smoke test: calling `_generate_activity_copy` with the rules block stripped fails — catches future regressions where someone bypasses the rules.

**Files:** `dashboard/app.py` (multiple prompt-builder sites), `bot/scheduler/materializer.py`, `config/question_quality.md` (more positive examples), `tests/` (new prompt-content assertions).

---

#### T-135: Quality gate — generate-3-pick-best 🔧
**Phase:** 24 — Content quality & freshness | **Priority:** P2 | **Status:** DONE (2026-05-09) | **Deps:** T-134

**Shipped 2026-05-09:** v1 ships in `bot/scheduler/materializer.py:_generate_fresh_text` only — the worst quality offender, where bad text actually goes to the group. Calls `_generate_with_claude` up to `_QUALITY_GATE_CANDIDATES=3` times, returns the first candidate that passes `freshness_rejection`. If all 3 fail returns None (preserves the existing skip-and-retry-tomorrow contract — no `SkippedActivity` raise needed because the materializer runs at materialize-time, not dispatch-time). All-fail emits a single warning naming the slot + each rejection reason for debugging. `_generate_activity_copy` and `_gen_text` deliberately left single-shot for v1: warm-up copy is parameterized (lower risk) and `_gen_text` already has retry-once + curated-pool fallback. Coverage in `tests/test_quality_gate.py` (4 tests) pins all-pass / 1-of-3 / 0-of-3 / empty-response behavior.

Every discussion / morning / evening generation today is single-shot: LLM produces text → it goes out. One bad LLM moment per N calls becomes one bad message in production. This task wraps generation in a 3-candidate loop, runs each through a judge, picks the best. Reduces the bad-output rate from 1/N to (1/N)³.

Two judge approaches:
- **Rule-based (recommended start):** apply `freshness_rejection()` from `bot/utils/freshness.py` to all 3 candidates; pick the first that passes; if all fail, raise `SkippedActivity` (reuses the Phase A.1.4 skipped-vs-failed pattern). Free, fast, builds on existing machinery.
- **LLM-judge (T-135b if quality still weak after T-133+T-134):** second LLM call rates each candidate 1–10 against `question_quality.md` rules, pick the highest. ~4× LLM calls per slot, better quality, more cost.

Boundary cases (per the boundary checklist applied to A.1.4):
- All 3 candidates rejected → `SkippedActivity("all 3 candidates failed quality gate")`. Calendar dispatch marks the row `skipped` with the reason visible in the activity log.
- LLM-judge times out (T-135b) → fall back to rule-based.
- One candidate passes, two fail → first-pass behavior; no LLM-judge needed.

The boundary guardian (`test_calendar_dispatch_distinguishes_skipped_from_failed`, shipped in A.1.4) catches any regression where we accidentally raise `RuntimeError` on the all-fail case instead of `SkippedActivity`.

**Verification:**
- New tests: simulate "all 3 reject", "1/3 pass", "3/3 pass". Each verifies the right outcome (skipped / sent / sent).
- Confirm the marker passes through to `mark_message_skipped` with a clear reason for the all-fail case.

**Files:** `dashboard/app.py:_gen_text`, `dashboard/app.py:_generate_activity_copy`, `bot/scheduler/materializer.py:_generate_fresh_text`, new `tests/test_quality_gate.py`.

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

#### T-094: Onboarding video: side panel topic switching
**Priority:** P3 | **Status:** TODO
Replace the full-screen TopicsScene with animated side panel topic switching. Instead of a dedicated topic list scene, show topic navigation happening in the side panel during chat scenes — highlight different topics, animate switching between them. The side panel IS the topic navigation, not a separate screen.
- Files: `media/showcase/src/scenes/TopicsScene.tsx`, `media/showcase/src/scenes/BotFeaturesScene.tsx`, `media/showcase/src/components/telegram/TopicSidePanel.tsx`
- Consider merging TopicsScene into BotFeaturesScene with the side panel animating topic switches

#### T-096: Materializer migration — scheduled_messages as single source of truth
**Priority:** P0 | **Status:** DONE (2026-05-09)
Make `scheduled_messages` the one source for every text-content send (morning, evening, discussion). The bot runtime no longer has parallel APScheduler cron jobs for those types; a shared `compute_week_previews()` in `bot/scheduler/materializer.py` is called by both dashboard preview rendering and a bootstrap/reload/daily materializer that writes rows the `calendar_checker` sends from. Kills a whole class of dashboard↔bot drift bugs.

**Progress (2026-04-13):**
- New `bot/scheduler/materializer.py` with `compute_week_previews`, `materialize_forward`, `purge_future_auto_rows`. Date-seeded content selection — same date always yields same content, consecutive days can't collide.
- Removed `send_morning_prompt` / `send_evening_prompt` / `send_discussion_prompt` APScheduler cron jobs (`bot/scheduler/jobs.py`), removed dead handler code from `goals.py` / `discussions.py`, deleted `bot/utils/commitment.py`.
- Fixed Hebrew→Python day-of-week bug (`_hebrew_to_python_days` was shifting Sun/Tue/Thu → Sat/Mon/Wed).
- Wired materializer into `post_init`, `_reload_config`, and a daily 00:05 refill job.
- Dashboard imports `compute_week_previews` from the shared module — one function object shared between dashboard and bot. `_is_feature_enabled` also unified.
- `_signal_bot_reload()` now fires from every config-write endpoint (topics, antispam, prompts, spam patterns, gamification, features) so dashboard edits propagate without manual reload.
- Fixed ghost-row duplication on weekplan/calendar rendering (cancelled rows were leaking into the calendar-events render path).
- `scripts/test_sync.py` — 27-test end-to-end verification suite. All pass.

**Closed (2026-05-09):** Materializer + freshness path is shipped and stable. `compute_week_previews` was rewritten to return `[]` under strict freshness — the static-preview model the original `/prompts` lineup plan assumed no longer applies. The `/prompts` page lineup display is filed as a follow-up cosmetic task (file: `~/.claude/plans/splendid-puzzling-stallman.md`); the live scheduling surface is `/planner`/`/weekplan`, which already reads from `scheduled_messages`.

Critical files: `bot/scheduler/materializer.py` (new), `bot/scheduler/jobs.py`, `bot/main.py`, `bot/handlers/goals.py`, `bot/handlers/discussions.py`, `bot/database/db.py`, `dashboard/app.py`, `scripts/test_sync.py`

---

## Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Framework | python-telegram-bot v20+ |
| Database | SQLite via aiosqlite |
| Scheduler | APScheduler |
| Config | YAML files |
| Hosting | Railway or Render |


### BUG-1: Emoji game serves questions from a different category than announced

**Priority**: P0
**Status**: Backlog

---

#### T-175: Trivia "never fired" diagnosis + pre-roll gate logging + vps-admin diagnostics ✅ (2026-05-23)

**Symptom (2026-05-22):** scheduled trivia in the main group "never fired" despite enough warm-up RSVPs; worked in Sherlock's Den.

**Root cause (HIGH, log-confirmed):** the game was NOT skipped and there were NO duplicate marker rows. Announcement 493 had 3 RSVPs → dispatch gate `_enforce_warmup_rsvp_gate` passed → game 495 launched at 19:00:13 (`status=sent`). It then **aborted at the in-game pre-roll ready gate** (`bot/handlers/trivia_round.py:_continue_round_after_announcement`): warm-up RSVPs were not carried into the round, so the gate saw 1/2 and cancelled at 19:02:09. activity_log row: `סיבוב טריוויה בוטל — 1/2 מוכנים`. The actual fix — `7d5938b`, which seeds the pre-roll gate from warm-up RSVPs — deployed at 19:40, 40 min after the game died.

**Shipped this session:**
- `46762db` — `Database.get_warmup_rsvp_user_map()` aggregates RSVPs across all rows sharing a marker + dispatch-gate decision logging. (Hardening; the duplicate-marker scenario it guards never occurred — confirmed via `vps-admin.sh schedule`. Part-2 commit-boundary dedup was considered and dropped.)
- `f941ae7` — pre-roll ready-gate now logs the seed count + decision (ready/min, user_ids, start|cancel) at INFO and a WARNING on cancel, so a recurrence is greppable in `bot.log`.
- `vps-admin.sh` read-only diagnostics (`8cf24fa`, `0ff262d`, `f941ae7`): `schedule [days]`, `applog [n] [pat]` (searches rotated logs), `activity [n] [filter]`. These are the sanctioned prod-read path (SSH+SQL is classifier-blocked).

**Verify:** `vps-admin.sh schedule 4`, `vps-admin.sh applog 100000 'pre-roll ready gate|CANCELLED at pre-roll'`, `vps-admin.sh activity 400 trivia_round`. Local: `pytest tests/test_scheduler_e2e_trivia_launch.py` (incl. the duplicate-marker regression).
