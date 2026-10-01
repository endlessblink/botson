# HANDOFF — Botson: riddle series (2026-10-01 12:25 IDT)

## Immediate task (Noam's latest words)
> "lets go with the riddle series"

That is option 1 from my last proposal:
- 2–3 **picture riddles a week** in topics people actually like (music room 4502, movies 54), each one a 4-option inline poll with a GPT Image 2 image, answered later by a reveal row that gives points to correct guessers and tags them in the group.
- A **Friday leaderboard post** that tags the week's top riddle guessers.
- First two riddles go to Noam for **approval before scheduling**: (a) the movie poll with his requested twist — *a famous film scene recreated with animals as the actors*, 4 options; (b) a **genuinely hard** dark-80s music riddle (see hard-riddle rule below).

He explicitly REJECTED "member-hosted riddles" ("won't work, I'm not sure people are entering that much anyway. stop using one occasion as a pattern"). Do not propose it again. Do not claim patterns from one busy night (memory: feedback_no_patterns_from_one_occasion). He also floated "measure first" (activity data over 4–8 weeks) — not chosen yet; mention it only if relevant.

## Definition of done for the riddle series
1. A repeatable way to create a riddle: image (GPT Image 2 via Codex only) → poll row (message_type `poll`, 4 options, `cover_path`) → reveal row (message_type `custom`, `poll_options: {"quiz_answer_for": <poll row id>, "correct_option": "<exact option>"}`) — this already works end-to-end (see "What exists").
2. A weekly cadence (2–3 riddles) scheduled with Noam's approval; times/days must come from config (`config/settings.yaml`), never hardcoded (hard project rule).
3. Friday leaderboard: weekly totals of quiz points, posted with tags. Does NOT exist yet — needs building: sum `activity_log` rows `action_type='points'` with description containing `quiz:` for the week (written by `award_quiz_points`), or a cleaner query; post via `safe_send` with HTML mentions (`tag_members` in bot/handlers/polls.py). Must be **one dispatcher only** (memory: weekly_roundup double-send incident — recurring computed content gets exactly one dispatcher; cron-owned types must not also be calendar rows). Copy text in settings `copy.*`, schedule in `schedule.*`, guardian tests must pass.
4. Tests + commit + push; deploy only after explicit "deploy" confirmation from Noam (memory: pause before deploy, every time).
5. Show Noam the first two riddles (images + exact Hebrew text + options + topic + times) for approval. Hebrew drafts: project rule says render on dashboard, not terminal — in practice I created **draft** calendar rows via the agent API and also showed text in chat; Noam has accepted that. Drafts never send.

## Hard rules for riddles (learned this session)
- **Hard means hard**: never illustrate the answer's title (Iris, a dark-80s fan, caught "pearls dripping like dew" = "Pearly-Dewdrops' Drops"). Clue must come from fan-only knowledge: video imagery, a lyric line other than the title, sleeve art, band history, lateral association; distractors must fit the picture equally. Canonical rule now in `config/operator_prefs.md` → `### Hebrew content rules` (committed 74b922e, live on VPS), in `~/.codex/skills/botson-game-creator/SKILL.md` (section "Image riddles and guess polls"), and memory `feedback_hard_riddles_oblique_clues`.
- Images ONLY GPT Image 2 or Seedream 5. Noam: "I don't want to use tokens, only through gpt image 2" → use Codex CLI image_generation (subscription, no paid credits):
  `cd <scratch dir> && codex exec --skip-git-repo-check -s workspace-write -C . "Use only your built-in image_generation tool (GPT Image 2)... Save it as X.png ..."` (~1–2 min). Never other models.
- 4 options. Not everything should be a poll ("the poll has no meaning if it's everything polls all the time").
- Winners must get points AND be tagged in the group (done automatically by reveal rows with the quiz marker).
- No notifying pin late at night; pins are group-wide.
- Cute topic: bot questions there are DISABLED (Noam, 2026-10-01). Don't put riddles/questions there.
- One-time posts are not features (memory feedback_one_time_posts_not_features).

## How to post a riddle (proven 2026-09-30, scripts in my scratchpad)
Scratchpad: `/media/endlessblink/data/.dev-tmp/endlessblink/claude-1000/-media-endlessblink-data-my-projects-ai-development-bots-automation-botson/aab09480-a275-4477-8830-8025b0afd61b/scratchpad/music/` — see `post2.py`, `post_hard.py` (poll create → send-now → reveal create). Pattern:
1. Upload image: `scp img root@84.46.253.137:/tmp/x.png` then `ssh root@84.46.253.137 'install -o botson -g botson -m 644 /tmp/x.png /opt/robotnik/media/covers/<epoch>_up_<name>.png && rm /tmp/x.png'` (dashboard upload endpoint needs a browser session; covers dir is gitignored runtime data). cover_path = `covers/<file>`.
2. Run the python script ON the VPS via `ssh root@84.46.253.137 'python3 -' < script.py` — it reads `BOTSON_AGENT_API_TOKEN` from `/opt/robotnik/.env` so the token never leaves the VPS, and calls `http://127.0.0.1:8080`.
3. Every agent mutation needs `Idempotency-Key` + `X-Community-Context-Receipt` (receipt = `context_receipt` from `GET /api/agent/community/messages?hours=24&limit=200`, ≤20 min old, invalid if a new chat message arrived — re-read right before each call).
4. For scheduled (not immediate) riddles: create poll row with `status: "scheduled"` at the slot time (or draft → `POST /api/calendar/{id}/schedule`). Image polls and reveal rows with the quiz marker are exempt from the AI discussion rubric (commit 02657ed). send-now only works for rows due within 10 min.
5. To credit/score an already-revealed poll: `POST /api/calendar/{poll_id}/award-quiz-points {"correct_option": ..., "announce_reply_to_row": <reveal row id>}`.

## What exists / was done this session (all committed & pushed to origin/main, VPS deployed at HEAD ffcafd8)
- 3bb02fb chat-read gate (agents must read real chat feed before content mutations; receipt) + notifying pin (`auto_pin: 2`).
- 609e35c reviewer JSON retry (`technical_attempts` in config/hot_take_review.yaml).
- 6aeb381 operator-approved posts never blocked by AI review at send time (hard rules only); new `conversation_precheck` job (advisory DM ~early).
- e99362e agent publishing guardrails (review all agent text posts regardless of type; rejected row can't be retried; `agent_guardrails.max_quality_rejections_per_day: 2`; send-now only if due ≤10 min; agent rows `created_by: agent`).
- 02366e5 precheck dedupe persisted in activity_log (no repeat DMs after deploy), Hebrew reviewer reasons, delivered admin alerts logged (`admin_alerts: delivered`).
- 7472717 / 997782f / 4a2130a guess-poll scoring: `award_quiz_points`, `quiz_winners`, `tag_members` (bot/handlers/polls.py), reveal hook `_award_quiz_reveal` (bot/handlers/calendar.py), endpoint award-quiz-points; points `gamification.quiz_poll_correct: 5`, copy `copy.polls.quiz_winners`. Public welcome on joins turned OFF (`welcome.public_enabled: false`).
- 74b922e hard-riddle rule in operator_prefs.
- ffcafd8 cute topic unmapped from `topics.discussions`.
- Real chat feed: `GET /api/agent/community/messages` works and now captures member messages (music room was active 29/09 night). Read it before any group content.

## Production state (2026-10-01 12:25)
- Riddles posted 30/09: spider/Lullaby (row 876, Iris won +5 and was tagged), hard Cocteau Twins (row 878, 0 winners — Iris called out that the picture gave away the title).
- Draft row **880** (music room 4502): credit to Iris — "🖤 קרדיט לאיריס: ..." — NOT sent: agent text posting was paused on 30/09 by the daily rejection budget (2 false rejections from my pre-fix rubric). Budget resets daily (IL date); on 10/01 agent posting should work again. Ask Noam whether to still send it (he said "give her credit") — it's a plain custom text, so it WILL go through the AI review and may be rejected (counts against budget). Alternative: Noam sends it from the dashboard planner (session sends are not guarded).
- Scheduled this week: row 855 facts_tidbit 10-01 12:00; 865 movies 10-01 13:00; 866 gaming 10-01 19:00; 863 support 10-01 23:30; 870 vegan 10-02 19:00; 858 movies 10-02 20:00. Row 856 (facts_spooky 09-27) failed "facts spooky did not send" — cause unknown, uninvestigated.

## Uncommitted state in the repo (NOT mine — preserve)
Another session's work is uncommitted: AGENTS.md, bot/database/db.py, models.py, bot/handlers/welcome.py, bot/main.py, bot/utils/topic_guard.py, config/operator_prefs.md, config/settings.yaml, tests/test_operator_prefs_canonical.py, tests/test_recent_community_context.py, tests/test_welcome.py, untracked community_replies.py, config/community_reply.yaml, docs/botson-task-routing.md, tests/test_botson_task_selection_policy.py, tests/test_community_replies.py. My committed edits were staged surgically (HEAD blob + my change via `git hash-object -w` + `git update-index --cacheinfo`) so their work stayed uncommitted. The working copies of settings.yaml, operator_prefs.md, welcome.py, test_welcome.py contain BOTH my committed changes and their uncommitted ones. In test_welcome.py working copy I also added (uncommitted, inside their file) an `asyncSetUp` patch enabling the public welcome for their tests + `_public_welcome_enabled` helper — keep it. Use the same staging technique for any file in that list; never `git add` those files wholesale.
HANDOFF.md itself is uncommitted (don't commit it).

## Known pre-existing test failures (not caused by this work)
10 in tests/test_planner_coercion_and_chips.py (FakeCalendarRequest has no .state, 422!=409, prompt budget 28523>28000), 2 in tests/test_send_now_parity.py, 1 in tests/test_bug7_context_grounding.py (semantic review unavailable). Full suite takes >8 min; run focused files.

## House rules (abbreviated — read CLAUDE.md, AGENTS.md, ~/.claude/CLAUDE.md)
- Answers to Noam: 1–4 short plain sentences + "Next steps"; no paths/code in replies.
- No live cloud LLM calls for testing. No SSH+SQL on prod (use agent API / vps-admin.sh read-only). Never edit /opt/robotnik code directly; deploy = commit → push → `ssh -i ~/.ssh/id_ed25519 root@84.46.253.137 '/opt/robotnik/scripts/deploy.sh'` — ask before every deploy.
- No hardcoded user-facing Hebrew/thresholds in code; copy in settings `copy.*` via `load_copy`; guardian `tests/test_no_hardcoded_content.py`.
- Check Israel time with `date` before any scheduling. Topic ids from `vps-admin.sh topics` (verified: music 4502, movies 54, botson_corner 4037, gaming 1517, cute 335).
- lean-ctx: use ctx_shell; heredocs with python may be blocked — use script files.
- Declare a plain-language cockpit task.

## Exact next steps
1. `date +"%Y-%m-%d %H:%M %A"`; read the chat feed (receipt) — check music/movies activity since 30/09.
2. Design the Friday leaderboard (one dispatcher, config-driven day/time/topic, copy in settings, tests). Confirm with Noam where it posts (music? botson_corner 4037?) — ask one short question if unclear.
3. Generate 2 riddle images via Codex GPT Image 2: (a) movie scene recreated with animals as actors (4 options, medium-hard, fan-recognisable but not literal-title); (b) a truly hard dark-80s riddle per the oblique-clue rule. Self-check: "could someone who doesn't know the song solve this from the picture alone?" — if yes, redo.
4. Create them as **drafts** for the proposed slots (evenings when people are around), show Noam images + exact text + options + topic + times, schedule only after approval, with reveal rows carrying the quiz marker.
5. Ask about row 880 (Iris credit).

First command: `cd /media/endlessblink/data/my-projects/ai-development/bots+automation/botson && date +"%Y-%m-%d %H:%M %A" && git status -sb | head -1`
