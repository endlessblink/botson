# MASTER PLAN — אלהוריים וזה Telegram Bot (robotnik)

> Telegram bot for the "אלהוריים וזה" childfree community (81 members, forum-style group with topic channels). All user-facing messages in Hebrew.

## Current execution order — reconciled 2026-10-06 11:02 UTC

Owner approval for continued build/deploy and the conversational/arcade scope is recorded from Oct6 07:26 UTC. Heavy work remains paused for system load; this checkpoint used only lightweight source/config/service reads. Exact per-feature evidence: `/home/endlessblink/Documents/Codex/2026-10-05/task-10/evidence/oct6/status-all-upgrades-1102.md`.

| Order | Work | Current status / checkpoint | Dependencies / decision |
|---|---|---|---|
| 1 | T-191/T-192/T-149/T-150: deploy coherent integration | Exact tested local candidate **6f690ea** supersedes c22e33f as the complete requested release; production/public main still **fa5252b**, services running, no intervening restart observed | Earlier automatic-review public-main rejection remains unresolved; no retry/bypass. Owner feature/deploy approval is already recorded |
| 2 | **T-193**: topic-relevant news | Verified-source/review/dedupe/caps runtime and dashboard/agent controls implemented/tested in6f690ea; live OFF/preview_only | Selected existing topics/free RSS only; publication/deploy/preset activation and real semantic/delivery observation remain |
| 3 | **T-194**: natural addressed conversation | Confirmed same-person/topic follow-ups, turn/window limits, durable opt-out/caps and shared controls implemented/tested in6f690ea; live OFF | Proposed3 answers,10-minute follow-up window,6 global/3 topic/thread per day; initial120min cooldown, quiet hours and no-unsolicited boundary retained |
| 4 | **T-197**: weekly AI invitation and self-service | DEPLOYED/ENABLED revision1 infa5252b; question/progress/no-time acceptance and one recipient confirmed; registered due **Oct10 19:00 Asia/Jerusalem** | Shared question/tag/day/time controls and own-ID reply opt-in/out code deployed; authenticated production visual and first real delivery/pin/self-service remain unobserved |
| 5 | General own-pin replacement | Reconciled general pin ledger/serialization implemented/tested in6f690ea; NOT DEPLOYED. Separate owned-weekly pin/unpin code is already live/enabled | Publish/deploy integration; never discover/unpin unknown/member/weekly pins from the general ledger |
| 6 | **T-198**: separate opt-in arcade + shared scores | Independent memory-pattern service and requested Botson entry implemented/tested in6f690ea; real local demo8 rounds/800 points, mobile390px/no overflow; production code/config ABSENT | Confirm existing authorized HTTPS origin/hosting route; actual Mini App/member admission/shared scoring after accepted deployment authorization; no new costs/credentials/audience/security changes |
| 7 | Thursday/Friday schedule coverage | Last recorded Oct5 05:30UTC shortage: ThuOct8 needs3, FriOct9 needs4; no fresh count/context or seven drafts is claimed | Prior private-context drafting rejection not retried; exact drafts and per-item review required. No filler or unapproved calendar writes |

**Execution boundaries:** latest owner decisions authorize routine implementation and normal tested deployment within the existing audience. The earlier exact public-main publication denial is an execution-review barrier still unresolved in this delegated environment. No push, deployment, restart, activation, model call, private-chat read or new suite occurred in this status reconciliation. The arcade is now explicitly within implementation scope; older statements that it was merely exploratory retain their historical dates. It remains a separate opt-in service, with disabled public defaults and no selected hosting origin. The canonical source remains6d17a89 with unrelated WIP preserved; do not reset or wholesale-stage it. The earlier c22e33f prompt/handoff is stale for the full requested feature set.

**ID reconciliation:** T-111 remains scheduled-game auto-start (its detailed section). The other former T-111 (real Telegram launch verification) is **T-195**. T-112 remains drawer/hard-refresh verification (its detailed section); the former duplicate T-112 pre-flight admin DM is **T-196**. T-182–T-190 already exist in code and are reserved; T-181 also appears as an older prefs-store alias in code and is not a new task. See the evidence ledger for mappings.

## Summary

| ID | Phase | Task | Status | Priority | Deps |
|----|-------|------|--------|----------|------|
| T-111 | 18 | Auto-start trivia/emoji scheduled game rows | DONE | P1 | T-023, T-026 |
| T-196 | 18 | Legacy duplicate T-112: pre-flight admin DM before round announcements — reconcile with current personal reminders | NEEDS DECISION | P2 | T-175 |
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
| T-064 | 13 | Dashboard: send test message to any topic | DONE (verified present in dashboard 2026-10-05) | P1 | T-047 |
| T-065 | 13 | Dashboard: trigger morning/evening prompt manually | DONE (verified present in dashboard 2026-10-05) | P1 | T-044 |
| T-066 | 13 | Dashboard: trigger discussion prompt manually | DONE (verified present in dashboard 2026-10-05) | P1 | T-044 |
| T-067 | 13 | Dashboard: start trivia question from dashboard | DONE (verified present in dashboard 2026-10-05) | P1 | T-023 |
| T-068 | 13 | Dashboard: view/manage member levels + reset | DONE (verified present in dashboard 2026-10-05) | P1 | T-031 |
| T-069 | 13 | Dashboard: view/manage streaks | DONE (verified present in dashboard 2026-10-05) | P1 | T-011 |
| T-070 | 13 | Dashboard: whitelist management (add/remove patterns) | DONE (verified present in dashboard 2026-10-05) | P1 | T-008 |
| T-071 | 13 | Dashboard: view group stats (same as /stats) | DONE (verified present in dashboard 2026-10-05) | P1 | T-004 |
| ~~T-072~~ | 13 | ✅ Dashboard: create event from dashboard (cover + pin + topic + from-poll picker) | ✅ DONE (2026-04-19) | P1 | T-020 |
| T-073 | 13 | Dashboard: send weekly roundup manually | DONE (verified present in dashboard 2026-10-05) | P2 | T-015 |
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
| T-100 | — | Configure git remote and push WIP commits | DONE (historical; remote and deployed git revision observed 2026-10-04) | P1 | — |
| T-101 | — | Handle media assets (gitignore / LFS / commit) | DONE (historical detail 2026-04-17; tracked assets present) | P3 | — |
| T-195 | 18 | Legacy duplicate T-111: real Telegram launch verification | NEEDS EXPLICIT APPROVAL | P1 | T-111, T-191 |
| T-112 | 18 | Trivia: verify drawer stays open on launch after hard-refresh | TODO | P2 | T-195 |
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
| T-143 | 25 | Calendar checker delete-race 🔧 — re-read + atomic claim already present | CODE DEPLOYED; BEHAVIOR UNVERIFIED | P1 | T-191 |
| T-144 | 25 | Phase A onboarding follow-up cadence 🔧 — +24h topic-mention + +72h DM + +7d lurker tag, opt-out per user, dashboard /onboarding admin page | TODO | P2 | T-136 |
| T-145 | 25 | Layer 3 nightly smoke test 🔧 — periodic real-LLM call against next-week slots, asserts day-anchor + freshness pass | TODO | P3 | T-139, T-140 |
| T-146 | 25 | Discussion seeds for חדר מוסיקה 🌱 — add `music` category to discussions.yaml (≥25 Hebrew questions). Topic id 4502 verified + settings wired 2026-05-10; only the content half remains. | DONE | P2 | — |
| T-147 | 26 | Planner Populate flex slots 🔧 — config-driven non-game suggestions after fixed slots fail | DONE | P0 | T-123 |
| T-148 | 26 | Populate skip diagnostics 🔧 — show exact reasons instead of generic empty modal | DONE | P1 | T-147 |
| T-149 | 26 | Populate flex routing config 🔧 — operator-controlled custom/discussion topic strategy | PREPARED; VERIFIED OFFLINE; NOT DEPLOYED | P1 | T-147 |
| T-150 | 26 | Populate flex regression suite 🧪 — evening fallback, custom commit, no hardcoding | OFFLINE REGRESSIONS VERIFIED; ROUTING DEPLOY/REAL UI PENDING | P1 | T-147, T-148, T-149 |
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
| T-162 | 28 | Question quality review queue | IN PROGRESS; REAL QUEUE REVIEW BLOCKED | P0 | T-134, T-135 |
| T-163 | 28 | Draft review quality approval loop | IN PROGRESS; OFFLINE FLOW VERIFIED; VISUAL PENDING | P0 | T-162 |
| T-164 | 28 | Real-output regression corpus | TODO | P1 | T-162 |
| ~~T-165~~ | 28 | ✅ Channel-specific question rubrics | ✅ DONE (2026-06-08) | P1 | T-162 |
| T-166 | 29 | Reaction-informed bot cadence loop | TODO | P1 | T-136 |
| T-167 | 29 | Trivia emoji facts review flows | TODO | P2 | T-163 |
| T-168 | 29 | Community health metrics loop | TODO | P2 | T-136 |
| T-169 | 30 | Materializer dedup + source-echo fix 🩸 — bulk auto-refill quality leak | PARTIAL CODE DEPLOYED; NEW HARDENING PREPARED | P0 | T-134, T-135 |
| T-170 | 30 | Planner Populate hardening — loud failures + retry budget + near-dup | PARTIAL CODE DEPLOYED; NEW HARDENING PREPARED | P0 | T-169 |
| T-171 | 30 | Discussion pool validator + CLI + guardian test | CODE ALREADY DEPLOYED; CLI/GUARDIAN VERIFIED OFFLINE | P0 | T-169 |
| T-172 | 30 | Operator feedback capture — content_feedback schema + dashboard controls | CAPTURE CODE DEPLOYED; OFFLINE HTTP VERIFIED; LIVE LEARNING UNVERIFIED | P0 | T-163 |
| T-173 | 30 | Regression corpus + per-channel rubrics injected into prompts | TODO | P1 | T-172 |
| T-174 | 30 | Style-profile learning loop — operator-approved generation guidance updates | TODO | P1 | T-172, T-173 |
| T-175 | 23 | Trivia 'never fired' diagnosis + pre-roll gate logging + vps-admin diagnostics | DONE | P1 | T-127 |
| ~~T-176~~ | 30 | ✅ Learned rules survive deploys — durable prefs store + baseline reconcile | ✅ **DONE** (2026-07-27) | P0 | T-174 |
| ~~T-177~~ | 30 | ✅ Learned rules injected into the bulk-fill path (materializer) | ✅ **DONE** (2026-07-27) | P0 | T-176 |
| ~~T-178~~ | 30 | ✅ Rule-list consolidation under an operator-set cap | ✅ **DONE** (2026-07-27) | P1 | T-177 |
| ~~T-179~~ | 23 | ✅ CLI call timing instrumentation + configurable budgets + llm-doctor | ✅ **DONE** (2026-07-28) | P1 | T-175 |
| ~~T-180~~ | 23 | ✅ Watchdog classifies CLI health and alerts; blind-monitor guard | ✅ **DONE** (2026-07-28) | P1 | T-179 |
| ~~T-181~~ | 30 | ✅ Rephrase-with-anchors — keep the idea, redo the wording (planner cards, prompt drawer, review page); one-off edit that never writes a learned rule | ✅ **DONE** (2026-07-28) | P1 | T-172 |

| T-191 | 31 | Reliability reconciliation: reported planner failures, prompt budget and faithful offline dispatch fixtures | CODE DEPLOYED; VERIFIED OFFLINE; LIVE FLOW UNVERIFIED | P0 | T-152..T-160 |
| T-192 | 31 | Constructive alternatives for rejected drafts, preview before moderator decision | CODE DEPLOYED; VISUAL/SEMANTIC UNVERIFIED | P1 | T-163, T-191 |
| T-193 | 32 | Topic-relevant current news with verified sources, bounded cadence and moderator preview | POLICY CODE DEPLOYED; INTEGRATION TODO | P2 | T-149, T-163, T-192 |
| T-194 | 32 | Tentative occasional contextual replies with privacy/value/rate gates | NEEDS DECISION; DISABLED | P3 | T-168, T-191 |
| T-197 | 33 | Weekly AI-topic invitation: selected members share what they built | DEPLOYED AND ENABLED; VISUAL/FIRST DELIVERY PENDING | P1 | T-163, T-191 |

## Lanes (active queues)

Historical queue notes are retained below; the reconciled execution order above takes precedence. Starter pools were retired in September (see `docs/conversation-starter-cleanup-plan.md`); historical pool-growth tasks do not authorize restoring them.

#### T-170: Planner Populate hardening — loud failures + retry budget + near-dup
**Priority:** P0 | **Status:** PARTIAL CODE DEPLOYED; NEW HARDENING PREPARED
Planner Populate should fail loudly when generation quality fails, retry with useful guidance, avoid near-duplicates/source echoes, and reduce samey suggestions across a Populate run.

**Progress (2026-05-18):** Gap 1 implemented locally: Populate now rotates configured generation patterns per retry, rejects repeated openers from recent history/current run, and passes configurable temperature to the Anthropic API fallback. Focused planner and guardian tests pass.

**Remaining gaps:** Continue tracking unresolved Populate hardening gaps here as they are picked up, so restart context is visible from this task section.

Active queues should reflect what to work on next, not historical shipped phases. Tasks in different lanes don't block each other; tasks within a lane run sequentially.

### 🌱 Question quality lane (curation + review)
- **T-162** — Question quality review queue (IN PROGRESS; REAL QUEUE REVIEW BLOCKED, P0) — review generated/scheduled/pending discussion prompts against `config/question_quality.md`, rewrite or reject weak rows, and capture every new failure pattern as a rule/test.
- ~~**T-165**~~ — ✅ Channel-specific question rubrics (DONE 2026-06-08, P1) — channel-aware guidance now lives in `config/channel_rubrics.yaml`; support/fitness category mappings and regression coverage prevent stale topic/category prompt leakage.
- **T-164** — Real-output regression corpus (TODO, P1) — turn bad and good real generated outputs into fixtures that guard `_validate_draft_text`, quality prompts, and pool false positives.
- ✅ **T-133** — Grow content pools (DONE) — historical pool-growth result; curated facts remain, reusable discussion starters were retired in September and must not be restored.

### 🔧 Bot enhancement lane (runtime/product behavior)
- **T-166** — Reaction-informed bot cadence loop (TODO, P1) — use `message_engagement` to tune which slots/content types continue, pause, or move.
- **T-149** — Populate flex routing config (PREPARED; VERIFIED OFFLINE; NOT DEPLOYED, P1) — isolated patch reads the selected strategy, requires a configured verified destination, and reports missing routing. Current live routes/schedules are unchanged.
- **T-143** — Calendar checker delete-race (CODE DEPLOYED; BEHAVIOR UNVERIFIED, P1) — re-read and atomic claim exist in local/deployed revision; preserve them and verify offline under T-191. No fresh Telegram send proof.
- **T-110** — Emoji Night cleanup + dry-run in Den (TODO, P2).
- **T-144** — Phase A onboarding follow-up cadence (TODO, P2) — depends on T-136 telemetry + opt-out wiring before deploy.
- **T-168** — Community health metrics loop (TODO, P2) — expose lightweight health signals so bot changes are based on participation, not vibes.

### 🧪 Quality systems lane (code + content together)
- **T-163** — Draft review quality approval loop (IN PROGRESS; OFFLINE VERIFIED; VISUAL PENDING, P0) — make dashboard draft review enforce quality decisions before scheduling/sending.
- **T-150** — Populate flex regression suite (OFFLINE VERIFIED; ROUTING DEPLOY/REAL UI PENDING, P1) — 297 tests/191 subtests cover configured day/week routes, write-free preview, checked commit, evening fallback, windows, occupied/past skips and guardians. Real operator UI remains unverified.
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

### Integrated release checkpoint — 2026-10-05 21:47 UTC

- **Exact local release:** `c22e33f69c5f816a369d3cb92156b6acf9ec2a22`, branch `codex/botson-integration-20261005`, isolated checkout `/home/endlessblink/Documents/Codex/2026-10-05/task-10/botson-release`. Includes prior combined quality/routing/store baseline `71544ef` plus the reconciled pin and active-runtime implementation. Relative to deployed `fa5252b`: 33 files. Public main was freshly verified still fa5252b immediately before the rejected push.
- **Verification:** 27 relevant test files: **513 passed, 1 skipped, 197 subtests passed**, four existing lifecycle deprecation warnings. Final template render/JavaScript check: **1 passed**. Remaining discussion deploy guardian: **7 passed**. These overlapping suites are not an added test total. Python AST (71 files), deploy shell syntax and diff checks passed. All provider/Telegram calls used mocks or offline blocking; no paid evaluation or claim of live model quality.
- **Selected activation preset, still inactive:** news times **11:15/17:15 Asia/Jerusalem**, global **2/day**, topic **1/day**, one send/cycle; publisher age **24h**, context **3h**, minimum relevance **0.85**. OpenAI RSS → AI 3113; Steam RSS → gaming 1517; BBC entertainment RSS filtered to movies/series → 54; BBC Middle East RSS filtered to relevant politics → 1431. All four public feed reads returned HTTP 200 with publisher-dated entries; this does not verify a particular generated story. No dating/animals destination guessed.
- **Selected replies preset, still inactive:** explicit mention/reply only in **3113/1517/54**, global **3/day**, topic **1/day**, thread **1/day**, topic cooldown **120min**, value **0.90**, context **3h**. Both features use quiet hours **22:00–09:00**, silent delivery and fail closed without strong fresh context/source/review. Existing subscription CLI only, no API fallback or new provider.
- **Weekly/pins:** general scheduled/send-now posts replace only recorded general bot pins after the replacement pins successfully; failed old-pin removal remains recorded. Weekly retains its separate ledger and silent weekly-only replacement. Original source HEAD `6d17a89` and all source WIP code/config remain untouched.
- **Content:** last recorded schedule checkpoint (Oct5 05:30 UTC) needs **3 Thursday Oct8 + 4 Friday Oct9** items. No fresh production count/text read or seven draft proposals is claimed. Prepared bounded VPS-local generation/review script was rejected before execution; no fillers were substituted or scheduled. Exact new drafts require per-item review through the normal question tool.
- **Artifacts:** private task directory `/home/endlessblink/Documents/Codex/2026-10-05/task-10/evidence`: `botson-release.patch`, `botson-release.bundle`, `release-manifest.json`, `activation-settings.yaml`, `release-final-tests.txt`, `dashboard-final-test.txt`, `discussion-guardian-final.txt`, `public-feed-verification.json`, `prepare-content-proposals.py`. Runtime identities, private plan and activation preset are excluded from the public release.
- **Unverified/blockers:** trusted direct owner authorization for exact main publication/deploy/activation and bounded VPS-local current-context proposal generation; authenticated production dashboard visual access; actual news/reply semantic quality/delivery; weekly first delivery/pin/self-service after Oct10. Production remains fa5252b, news/replies OFF. Do not count service/API/synthetic HTML as authenticated production visual acceptance.

### Content quality and routing checkpoint — 2026-10-04 22:44 UTC

This checkpoint distinguishes deployed code from the further prepared changes. Production and public `origin/main` are still **fa5252ba6a36ecd4789432a3a8a670b8e70d75c1**. Further quality commit **855bec0f5fc166d19655457701ac37e0fac0b7bc** exists only on the isolated release branch; its public push was rejected before execution by automatic approval review and explicit owner approval is pending. No second deployment occurred.

- **T-162:** a VPS-local deterministic scan covered 8 upcoming Botson-owned scheduled conversation rows and returned no hard-rule flags, counts only. No message text was exported and no semantic review or rewrite was performed. A separate attempted bounded scheduled-text read was rejected; explicit approval for 8 rows, at most 500 characters each, is pending. This does not prove those ideas are interesting or relevant.
- **T-163/T-169/T-170/T-192:** 855bec0 adds same-topic exact/paraphrase dedupe across conversation types, existing sent/scheduled history and checked siblings; missing history refuses approval. It supplies the configured channel rubric, fetches alternative context inside the exact topic before LIMIT, rejects unsuitable weekly/test-group draft flows, and refuses unavailable-generator results/rejected paraphrases. Auth, context receipts, content/routing/timing/claim safeguards remain. These additions are verified offline, not deployed or semantically evaluated with a live model.
- **T-171:** existing CLI/guardian implementation was found in deployed code. Pure-local validator output: 4 historical category-only baseline findings and **0 new failures**. Retired reusable discussion starters remain retired; no materializer refill or pool growth was restored.
- **T-172:** existing feedback API/schema/cache recovery is present in deployed code. Synthetic authenticated HTTP tests prove rejection of unauthorized requests, capture persistence/filters and hydration after a cache reset. The learning queue is mocked; no actual learned rule/provider result or live learning is claimed.
- **T-149/T-150:** a separate five-file candidate based on 855bec0 now consumes `topic_strategy` (`discussion_category`, `topics.welcome`, `topics.goals`, or explicit `handler_routing`), requires verified destinations and a configured category for discussion rows, and exposes `missing_flex_routing`. It also fixes missing fixed-slot arrays crashing flex-only Populate. Five old routing fixtures now explicitly supply a synthetic verified catalog; negative verification cases remain covered. This candidate is uncommitted/unpublished/undeployed and is not part of 855bec0.
- **Verification:** quality increment **521 passed, 1 skipped, 160 subtests passed**; routing increment **297 passed, 191 subtests passed**. Counts are separate overlapping suites, not a combined total. Providers/network/Telegram remained blocked or mocked. Logs, exact patch/manifests and limits are in the reconciliation ledger.
- **T-197 preservation:** authenticated read-back plus service checks confirm deployed fa5252b, both services active/running with ExecMainStatus 0, and every canonical weekly field unchanged (including revision 1, audience, wording, schedule, audit and quiet pin). Next registered due remains **2026-10-10T19:00:00+03:00**. No test/catch-up post or second activation.
- **Remaining gates:** real authenticated visual dashboard verification is mandatory under AGENTS and still unavailable at the normal sign-in screen. T-166 cadence needs participation policy/threshold decisions. T-193 news stays disabled pending verified sources/topics/caps/quiet hours and integration; T-194 replies stay disabled pending product decision and reconciliation of original WIP ownership. No paid evaluation, model/provider change or new live communication occurred.

### T-191: Reliability and approval workflow reconciliation
**Priority:** P0 | **Status:** CODE DEPLOYED; VERIFIED OFFLINE; LIVE FLOW UNVERIFIED | **Deps:** T-152..T-160

- Reproduce the reported planner failures with synthetic fixtures and no external provider calls. Preserve middleware/session/agent-token semantics in fake requests, Hebrew content validation, explicit test targets, and the retired-starter invariant.
- Keep CLI digest input within the existing tested budget by removing duplicated schema descriptions, not learned operator rules or JSON validation constraints.
- Prove that the operator-quality header does not bypass authorization, fresh-chat receipt, content bans, verified topic routing, timing or durable dispatch claims. Quality preference override is distinct from these safeguards.
- Keep local tests, deployed code presence, and independently observed runtime behavior separate. Acceptance: focused planner/approval/dispatch guardians pass; no false DONE or live test post.
- Evidence: final release suite passed 435 tests and 771 subtests, with one skipped test and four existing deprecation warnings. Deployment guardians also passed; no live test post. Logs and constraints are recorded in the linked reconciliation ledger.

### T-192: Constructive alternatives for rejected drafts
**Priority:** P1 | **Status:** CODE DEPLOYED; VISUAL/SEMANTIC UNVERIFIED | **Deps:** T-163, T-191

- Moderator-only `POST /api/calendar/{id}/suggest-alternative` requires a draft snapshot, rejection reason, verified destination and captured current context from the same topic.
- Return a genuinely different candidate addressing the rejection; validate content/freshness/dedupe and semantic quality. Provider outage or insufficient context returns no usable alternative, never filler.
- Preview only: no DB write, approval, scheduling, feedback learning or Telegram send. Refuse a changed draft snapshot before/after generation. The operator still selects/reviews the exact replacement through the established review workflow.
- Acceptance: offline tests prove auth/snapshot rejection, no unreviewed alternative and write-free preview. Review-card controls now show a write-free candidate and only transfer it to unsaved editing; saving/approving stays a separate action. Next checkpoint is authenticated visual verification, followed by separately authorized real-model evaluation. Do not install new models/providers.

### T-193: Topic-relevant news updates
**Priority:** P2 | **Status:** LOCAL RUNTIME IMPLEMENTED/TESTED 6f690ea; PRODUCTION DISABLED | **Deps:** T-149, T-163, T-192

- Existing selected topics only, trusted HTTPS publisher/domain allowlists, actual publication date and canonical link. Undated/future/stale/private-network/redirected/oversized/unverified items fail closed. Current bounded same-topic context is required before generation. Configured news uses the exact conservative preset in the integrated checkpoint above.
- Separate semantic review binds the exact Hebrew summary to publisher evidence and topic context, rejects sensitive/unsubstantiated/duplicate events, and assigns a stable cross-source event identity. Persistent ledger owns duplicate claims, daily caps, quiet hours and delivery receipts; changed configuration/context refuses a reserved send. Uncertain delivery holds identity/cap and never blindly retries. No worthwhile fresh story means no filler.
- Shared authenticated dashboard `/participation` and scoped agent API validate the same versioned configuration. Preview is write-free and cannot publish; exact expiring preview receipt, activation approval, CAS revision and agent context/idempotency safeguards remain. Public defaults are disabled with no topics/sources/audience. Future normal deploys preserve activated participation configuration independently of weekly settings.
- Offline acceptance passes. Real provider quality, authenticated production dashboard visual, ordinary job registration and actual sends remain unverified. Automatic review rejected the exact public push before execution; no deployment/activation occurred.

### T-194: Addressed contextual replies
**Priority:** P3 | **Status:** LOCAL RUNTIME IMPLEMENTED/TESTED 6f690ea; PRODUCTION DISABLED | **Deps:** T-168, T-191

- Latest owner instruction chooses occasional useful replies; selected implementation supports explicit mentions/replies only in the three configured existing topics. Sensitive unsolicited participation and bot-originated loops are refused. Original source's independent mentions-only WIP is preserved; only the new reconciled runtime handler is registered in the isolated release.
- Recent real Telegram trigger, verified topic/current context, separate quality/privacy review, per-group/topic/thread caps, topic cooldown, quiet hours and immutable sender-ID opt-out are enforced. The button changes only the authenticated clicker's future response preference. Weekly self-join/leave commands remain with the existing exact-post weekly handler.
- Missing CLI/context/review or changed context/configuration refuses sending. No paid API fallback, provider/credential addition or extra audience. Offline acceptance passes; actual Telegram delivery/model quality and authenticated production visual remain unverified. Publication/deploy/activation require the direct trusted authorization identified above.

### T-195: Real Telegram launch verification (former duplicate T-111)
**Priority:** P1 | **Status:** NEEDS EXPLICIT APPROVAL | **Deps:** T-111, T-191

Keep the old real-launch verification outcome, distinct from scheduled-game auto-start T-111. Current offline tests do not prove a real Telegram game or authenticated dashboard launch. No live test post is authorized by this batch.

### T-196: Pre-flight admin DM (former duplicate T-112)
**Priority:** P2 | **Status:** NEEDS DECISION | **Deps:** T-175

Preserve the former admin pre-flight reminder request without duplicating the existing warm-up/personal reminder mechanisms. Reconfirm its purpose/recipient/cadence before implementation; never infer consent to new DMs from this historical TODO.

### T-197: Weekly AI-topic invitation, selected mentions and own-ID subscription
**Priority:** P1 | **Status:** DEPLOYED AND ENABLED; VISUAL/FIRST DELIVERY PENDING | **Deps:** T-163, T-191
**Owner decisions recorded:** 2026-10-04 20:52 UTC. Replaces the earlier pending day/time/automatic-vs-draft decisions.

- Approved schedule: **Saturday 19:00, Asia/Jerusalem, AI topic only**, automatic delivery through the existing Botson scheduler. First eligible date **2026-10-10**; no catch-up or test post now. No extra weekly questions or other-topic automation.
- Approved editable invitation asks what members built/tried with AI or progressed on, accepts small/unfinished/boring steps and “no time this week”, and explains how to join or leave future mentions by replying “תייג אותי” / “הסר אותי מהתיוג”. This is an invitation, never a summary or disclosure of member-history/activity.
- Initial named member **@Bubashvaba** was resolved against the existing group roster and independently checked with Telegram getChatMember. The second person's handle/immutable ID is unknown; exclude them and do not publish/store/search a supplied phone number. Starting with only the verified first member is authorized.
- Shared dashboard and restricted agent API: editable question, selected handles/verified IDs, weekday/time/IANA timezone, disabled/draft/automatic mode and silent pin switch. Both use the same validated settings, exact preview receipt, explicit activation approval, revision/CAS concurrency and audit trail. Agent authentication, fresh-context receipt and idempotency rules remain active; no arbitrary code interface.
- Self-service accepts only the requester's authenticated Telegram ID, in the correct group/topic and as a reply to a recorded check-in post. Own-ID opt-in/out merges atomically with admin changes, is idempotent, affects future posts/previews and never edits already delivered text. Confirmation stays brief in that context; no member-history report.
- Canonical recipients bind immutable IDs; future sends recheck current membership and username. Empty/unknown/ambiguous/bot/opted-out audiences cannot be expanded to all members. Members without public handles can opt in to an ID-bound mention.
- One durable group/topic/local-week claim; successful retries reuse receipts, uncertain delivery never retries automatically. Explicit IANA timezone; skip nonexistent DST times, first fold only; stale configuration/draft snapshots refuse sending.
- Pin silently using existing bot permissions. Replace only the previous recorded **weekly check-in** pin after the new one is pinned; never unpin other people's or other activities' posts. No invented 24-hour expiry or wider notification.
- Production evidence: release **fa5252ba6a36ecd4789432a3a8a670b8e70d75c1** pushed and normally deployed after a private VPS-local backup and passing guardians. Canonical weekly revision **1**, enabled **true**, mode **auto_send**, pin **true**. At **2026-10-04 21:40:42 UTC**, the actual scheduler registered the job with next due **2026-10-10T19:00:00+03:00**. The exact authenticated API preview/read-back matched the approved text and sole verified initial member. No weekly test/catch-up post was requested. Final offline suite: **435 tests and 771 subtests passed**, one skipped. Original unrelated WIP and unpublished pin-manager commit were excluded from the release; runtime participant identities were not pushed publicly.
- Remaining verification: the browser is at the login screen and blocks the temporary localhost read-only relay; authenticated dashboard visual verification is **UNVERIFIED**, not replaced by service/API evidence. Await normal browser login. Actual Telegram delivery, silent pin replacement and real member self-service remain unobserved until the first approved post. The second handle is still unknown. News/replies remain disabled; no extra automation was created.


### T-198: Separate opt-in arcade with shared leaderboard
**Priority:** P2 | **Status:** PROTOTYPE IMPLEMENTED/TESTED 6f690ea; PRODUCTION ABSENT | **Deps:** T-191, existing authorized hosting/admission

- Owner explicitly requested the discussed game on Oct6. Independent service under `services/arcade`; Botson supplies only requested `/arcade` and private Mini App entry. No scheduled invitation, score broadcast, audience expansion, monetary reward or service startup inside Botson.
- Progressive memory game, same daily challenge, server-owned scoring/moves/timing/ownership/expiry/attempt limits and exact replay receipts. Shared leaderboard exposes generated aliases, score/rank; no group text, names, usernames or photos. Existing-token Telegram admission and current-group membership gating are prepared; no new persistent credential was created. Validation is not anti-automation proof against reading visible patterns.
- Full exact candidate regression:556 passed,1 skipped,197 subtests passed. Actual ephemeral browser demo completed8 rounds/800 verified points, shared demo board, zero browser exceptions and no390px horizontal overflow. Public entry/service remain disabled/unselected; live fa5252b has no arcade files/config.
- Remaining: verify an existing authorized HTTPS hosting origin/route and then real opt-in launch/member refusal/shared scoring. Existing publication denial is not bypassed. Heavy work awaits root's serial schedule. The staged code is in `/home/endlessblink/Documents/Codex/2026-10-05/task-10/botson-oct6`, exact SHA6f690ea856716c3d63573695a3c04f964bdca920; current source WIP remains separate.

### T-199: Botson content on WhatsApp via Botty
**Priority:** P2 | **Status:** PLANNED — unblocked 2026-10-09: Botty is admin in WhatsApp אלהוריים וזה (120363411214878038@g.us, 50 members; read-back via WAHA participants). Also joined community "ממלכת אל הוריים" (120363412910553673@g.us 54 members, 120363411360535416@g.us 2 members) as non-admin — not a target. | **Deps:** worlds-greatest-bot (Botty) WAHA session on VPS :3051

- Owner decision 2026-10-09: reuse Botson (not a new bot). Botson keeps generating, approving and scheduling; Botty (worlds-greatest-bot, +972505332864) only delivers to WhatsApp. Features toggle per group via the existing `{enabled, groups}` feature shape.
- First target group: אל הוריים וזה (WhatsApp). Read-only WAHA group list on 2026-10-09 shows Botty is in Custom Beer Node, נפגשים ומשחקים באזור ר״ג, הפרלמנט and two "טסטים לבוטי" groups — not אל הוריים וזה. Owner must add Botty's number (as admin if rate limiting/deletion is wanted).
- Phase 1 scope: discussion questions, morning/evening posts, riddles/polls (WhatsApp native polls). Out of scope until reworked: trivia rounds, RSVP/inline buttons, forum-topic routing.
- Build and verify in "טסטים לבוטי" first; no sends to a real group without approval.
- 2026-10-09 local implementation: calendar rows with target_group `whatsapp` / `whatsapp_test` dispatch via WAHA sendText/sendPoll (same freshness + conversation gates as Telegram; unsupported types skipped; covers sent as text only). Dashboard API accepts the targets, rejects unsupported types, clears topic id, and applies the agent publish review to `whatsapp`. Tests: `tests/test_whatsapp_dispatch.py`. Activation needs VPS env `WHATSAPP_WAHA_URL`, `WHATSAPP_WAHA_API_KEY`, `WHATSAPP_GROUP_ID=120363411214878038@g.us`, `WHATSAPP_TEST_GROUP_ID=120363401861710408@g.us` + deploy (not done). Dashboard UI target picker not built yet.
- 2026-10-09 15:51 DEPLOYED 94d99dd (rebased onto origin/main). Doppler has URL/key/session/TEST_GROUP_ID=120363400961082631@g.us (טסטים לבוטי 2 — Botty superadmin). WHATSAPP_GROUP_ID (real group) deliberately NOT set → real-group rows fail closed. Verified: row 927 poll delivered, WAHA ack 2 DEVICE.
- **WhatsApp summary (operator 2026-10-09, prioritized first):** built in Botty (worlds-greatest-bot 373bd40, e3cc68a): tag Botty → reply with summary of the last 400 messages, once per member per day. Not deployed / not enabled yet; awaiting operator go-ahead. Throttle enforcement shelved by operator; watch-only stays live.
- First real Botson post to the WhatsApp group: proposed 2026-10-09, operator deferred ("summary first").
- 2026-10-09 e2294db deployed: ack-confirmed delivery + admin alert on failure, planner Telegram/WhatsApp destination picker, WhatsApp rows tagged in calendar, Botty throttle report card on the Spam page. Botty be1a928 deployed (app container only): watch-only throttle for אלהוריים וזה, 8 msgs/60s, report endpoint keyed. Full suite on candidate vs deployed base: identical 17 pre-existing failures, +5 new passing.
- **E2E GOAL (operator 2026-10-09) — done only when all are true and verified live:**
  1. Noam approves a post (AskUserQuestion review flow) → it is scheduled for WhatsApp אלהוריים וזה without touching code or SSH.
  2. The dashboard shows and lets him pick WhatsApp as the destination, and lists WhatsApp rows with their status.
  3. At the scheduled minute Botty posts it in the real group (text and native poll), confirmed delivered (WAHA ack ≥ 1), not just "sent".
  4. A failed delivery is marked failed and the admin is alerted; nothing is silently recorded as sent.
  5. Same quality gates and learned rules as Telegram apply; unsupported types are refused up front.
  6. Per-member rate limiting runs in watch-only mode in the WhatsApp group and reports who it would have limited.
  7. Registry, plan and tests updated; full regression shows no new failures.
- Open: (1) WAHA returns 201 even when delivery fails (row 926 got ack -1 but was marked sent) — add post-send ack check → mark failed. (2) Dashboard target picker. (3) Set WHATSAPP_GROUP_ID after operator approves first real post via review flow. (4) Member rate limiting lives in Botty's repo (watch-only first).
- Related ask: per-member message rate limiting — natural home is Botty's existing WhatsApp moderation (watch-only first); for Telegram, Botson anti-spam (dry_run first).

## Historical detailed roadmap (original scope)

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
**Phase:** 18 | **Priority:** P2 | **Status:** TODO | **Deps:** T-195

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

## Bot identity (added 2026-10-02)

This project's bot: `botson` — Botson (robotnik), Elhoriim community bot; candidate identity `@thebotstonbot` (unverified), VPS systemd `botson.service` + `botson-dashboard.service`. See its entry in `devops/brainiac/BOTS.md` (single source of truth for every bot; Hermes = platform only, bots are referred to by handle). Update BOTS.md when this bot's topic, profile, host, token owner or status changes. No token values in plans.

## Status addendum — 2026-10-05 08:30 IDT (weekly-schedule session)

- **Deployed:** `fa5252b` (VPS and `origin/main`), including the operator-approval override `8114dc5`.
- **Un-deployed candidate:** `6d17a89` own-pin replacement (unpins only the bot's earlier pin in the same topic). Local `main` has diverged from `origin/main`; it needs a rebase/cherry-pick onto `fa5252b` and the owner's deploy approval. Reconcile with T-197's owned-weekly pin replacement first.
- **Schedule data (not code):** rows 907, 908, 909 scheduled on explicit per-item approval; Wednesday 07/10 has 4 slots; Thursday 08/10 and Friday 09/10 are still below the 4-per-day minimum.
- **T-193 input only:** the owner named news channels 3113, 54, 1517, 1431, plus dating and animals channels (IDs unconfirmed). News stays disabled until sources, caps and the approval flow are decided.
- Details: addendum in `docs/master-plan-reconciliation-2026-10-04.md`.


## 2026-10-05 — Registry check before normal deployment

Workstation `scripts/deploy-from-local.sh` checks workflow `botson`, source checkout and release host/path before SSH, then supplies a temporary non-secret registry/checker bundle and checks the actual VPS checkout before the installed deploy entry. Updated `scripts/deploy.sh` adds its own check before fetch/reset/restart for the next authorized release. `--preflight-only` in either entry avoids deployment; offline script/transport fixtures passed. No server script, service or bot communication was changed; direct SSH to the older installed script remains uncovered. Existing quality/dispatch guardians and their order remain. Fleet coverage and rollback: sibling bots-directory/ENFORCEMENT_COVERAGE.md.


## Final production read-back and preserved concurrent configuration — 2026-10-05 21:47 UTC

Read-only production check at 21:47:50 UTC reconfirmed fa5252b, both services active/running with ExecMainStatus 0, weekly revision 1 enabled with next registered due Oct10 19:00 Asia/Jerusalem, and news/replies OFF. Full settings-file SHA256 remains the previously observed ed9d3aa952def5198f468461eca87990fd30ecc6a5dccb051490f0cf6da0d351. Receipt: `/home/endlessblink/Documents/Codex/2026-10-05/task-10/evidence/production-final-readback.json`.

Canonical source HEAD remains 6d17a89. After the completed isolated tests, `config/settings.yaml` changed in the source checkout at 21:45:56 UTC (new SHA256 1836fea3335a11156096c702cdee30e2d5f0ac1c7855f380c6fabe17e12cadcd). This task did not write that configuration or overwrite the observed change. The other 18 tracked/untracked WIP file fingerprints remain as captured; the two plan-document updates are intentional. Recheck current source configuration and production revision before any later integration/deployment. Do not reset the source to the candidate. The candidate checkout is clean; bundle verification and patch apply-check against fa5252b passed.


## Status update — 2026-10-09 16:00 IDT (weekly-schedule session)

Production and origin/main are now **4a65f02** (deployed via deploy.sh; guardians passed, both services active): pin cleanup 5386472 (bot unpins only its own previous pin in the same topic) plus 4a65f02 (cover-image upload accepts the scoped agent token). Local `main` is still diverged and was not reset. Thursday and Friday were filled (rows 914–920); news posts 921 (movies) and 922 (politics) were sent 9 Oct, 923 (gaming) is scheduled 15 Oct. Operator judged the news posts weak (dry date announcements, no image): rows 924 (Moon cave, NASA photo, Sun 11 Oct 12:00) and 925 (comedian bots, generated image, Sat 10 Oct 12:00) are scheduled in the tech channel with images. T-193 stays disabled; news is a manual brief process (source opened and dated, image, twice a week). Arcade candidate 680b303 (based on fa5252b) is NOT deployed and conflicts with the deployed pin manager; it needs a local rebase before any publication; public route `botson.noamn.com/arcade` chosen in principle, TLS works via Cloudflare but no /arcade route exists yet.
