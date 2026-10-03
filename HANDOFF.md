# HANDOFF — Botson weekly schedule (min 4 slots/day), deploy pending (eleventh instance, written 2026-10-03 ~21:00 IDT Saturday)

Project dir: /media/endlessblink/data/my-projects/ai-development/bots+automation/botson
Date check first: `date +"%Y-%m-%d %H:%M %A"`. Reply style to Noam: 1-4 short plain sentences, no paths/code/commands, no option menus (global CLAUDE.md changed: "End with a plain sentence at most"). NEVER tell Noam to run anything.

## Noam's requests and corrections (his words, in order of importance)
- Original: continue the previous handoff (riddle series, SSH, deploy). All done earlier: SSH works (`ssh -o BatchMode=yes bina-ci ...` via mcp__lean-ctx__ctx_shell raw=true; load schema with ToolSearch `select:mcp__lean-ctx__ctx_shell`).
- "I want to see it here + have you use the questions tool to review each one" -> ALL content review goes through AskUserQuestion, one item per question, the actual post text IS the question text (options: Approve / Change the wording / Replace it / Skip). Never in chat text, never only in a preview/table/artifact. Never ask the same thing twice; never re-ask what he answered.
- "today is saturday, ask me for each day in order" -> walk the week day by day starting today.
- "make sure to not show me the answers [of riddles]" -> image + question + options only, no answer/reveal.
- Quality (his words): blocked questions were generic filler, forced/cringe tone, too much effort or too hard. A good one: concrete anchor, fresh from group chat, real angle, one-word answer. "dont repeat 1 to 1 what was said in another channel! also the conversation stopped there, there is nothing to add". When no strong idea: bring candidates (AskUserQuestion). "none are good, find something else" is a valid answer: bring a different one.
- "this is too little ... should be minimum 4" -> every day needs >=4 content slots. A riddle (poll+reveal) = 1 slot; daily digest 09:30 and free-games 10:00 don't count; weekly roundup/leaderboard cron and Friday riddle leaderboard 19:00 are automatic and don't count. Noam allowed ONLY "Text questions" and "Polls" as filler types (no more riddles/games).
- "what I tell you here should be changed in the harness ... so it wont default again and again" -> done in project CLAUDE.md "Content review workflow" + memory + learned rules (below).
- On the server AI quality check: "if I approved it that goes above that system because I built it ... a system that just disqualifies results is a bad system" -> (1) operator approval must outrank the reviewer (DONE in code, not yet deployed), (2) the reviewer should SUGGEST better versions instead of only rejecting (NOT BUILT; Noam hasn't been asked to confirm scope; it does not weaken any guardrail so it is allowed).
- Last message: "ok so did you schedule them?" -> answer was: no; Tuesday 18:00 question is a draft because the fix is not deployed.

## What is scheduled (live on VPS, verified via GET /api/calendar, all status=scheduled)
Sat 3/10: 893 movies 16:30 q (superhero movie); 899 music room 4502 17:30 q (ruined song); 885+886 gaming riddle 19:30 (+reveal 20:30); 887+888 TV riddle 21:30 (+22:30). [4 slots] (+cron roundup/leaderboard 18:00)
Sun 4/10: 894 WFH channel 9029 09:00 q (desk item); 900 gaming 1517 13:00 q (unfinished game); 901 fitness 5438 17:00 q (dropped exercise); 881+882 movie riddle 20:30 (+21:30). [4]
Mon 5/10: 902 WFH 9029 10:30 POLL (music or silence + genre, 6 options); 903 movies 54 14:00 q (second viewing); 895 gaming 18:00 q (board game concept cooler than game); 904 music 4502 20:30 q (secret lyrics). [4]
Tue 6/10: 905 movies 54 10:30 q (surprise series); 896 vegan 7 13:00 q (salad sauce); 883+884 music riddle 21:00 (+22:00). [3]. DRAFT 907 gaming 1517 18:00 "אם אפשר לשמור רק משחק אחד מהספרייה שלכם לכל החיים – איזה?" Noam APPROVED it but scheduling got 429 (agent publishing paused for the day after 2 quality rejections). Needs scheduling after deploy using the new header.
Wed 7/10: 892 movies 54 20:00 q (soundtrack better than movie); 898 trivia warm-up 20:00 (bot corner 4037); 897 trivia round 21:00 (general, 5 q). [2 slots]
Thu 8/10: 889+890 movie riddle 20:30 (+21:30). [1 slot]. Thursday daytime: Noam rejected my keyboard Q, onesie Q, and all three candidates ("Skip Thursday daytime, notes: nothing works?"). Needs new ideas, need 3 more.
Fri 9/10: nothing but automatic riddle leaderboard 19:00 (music room). Noam said "Leave Friday as is" BEFORE the min-4 rule -> needs 4 slots now; ask again.
Needed to reach 4/day: Tue +1 (907 pending), Wed +2, Thu +3, Fri +4.
Row 891 (old Monday gaming) and 906 (rejected Tuesday poll) were deleted. Row 880 (Iris credit draft in music room) stays a draft, never send/delete.

## Code/repo state (branch main; local ahead of origin; NOT pushed/deployed since ee02dda)
Pushed+deployed earlier: ee02dda (planner drafts list shows poll image/options/correct answer/date).
Committed locally, NOT pushed, NOT deployed:
- b4c5c1b fix(dashboard): scheduling a trivia round crashed on sqlite row (500) [`_ensure_trivia_pool_ready_for_round(dict(row))` in schedule_calendar_item]
- 9a98546, 58e85a6, 4347350 CLAUDE.md workflow docs; 507be41 + 4347350 operator_prefs rules (fresh anchor/arguable angle; don't recycle other-channel phrases or finished threads) — committed as separate hunks via `git apply --cached` because config/operator_prefs.md also has another session's uncommitted hunks.
- 8114dc5 feat(agent-api): header `X-Operator-Approved: true` makes `_agent_publish_guard` skip the AI quality review, per-row rejection check and the 2/day budget; logs activity `agent_operator_approved` (dashboard/app.py; test tests/test_agent_publish_guardrails.py::test_operator_approved_post_skips_review_and_budget). Tests: 21 passed (guardrails + hardcoded-content guardian).
- Permission: I added `"Edit(dashboard/app.py)"` to `.claude/settings.local.json` (gitignored) at Noam's explicit request after the auto-mode classifier ([Security Weaken]) blocked the edit; that is why the edit finally worked. Do not add other permission rules yourself without his explicit ask.
Uncommitted files NOT mine (other session, never `git add` wholesale): AGENTS.md, MASTER_PLAN.md, bot/database/db.py, models.py, bot/handlers/welcome.py, bot/main.py, bot/utils/topic_guard.py, config/operator_prefs.md (other hunks), config/settings.yaml, tests/test_operator_prefs_canonical.py, test_recent_community_context.py, test_welcome.py, untracked bot/handlers/community_replies.py, config/community_reply.yaml, docs/botson-task-routing.md, tests/test_botson_task_selection_policy.py, tests/test_community_replies.py, BlenderMCP/, applications/. This HANDOFF.md is also uncommitted.
Deploy needs Noam's explicit "deploy" (memory: pause before SSH deploy). He has NOT yet said deploy for the new commits; I asked ("say deploy and I'll push it") and he replied with "ok so did you schedule them?". Next step: ask in one plain sentence / or just confirm, then `git push origin main` then `ssh -o BatchMode=yes bina-ci '/opt/robotnik/scripts/deploy.sh'` (guardians run; push once only).
After deploy: schedule 907 by POST /api/calendar/907/schedule WITH header `X-Operator-Approved: true` (plus fresh Idempotency-Key + X-Community-Context-Receipt from GET /api/agent/community/messages?hours=24&limit=200 — hours must be 24, other values 400). Use the header ONLY for items Noam approved in AskUserQuestion.

## How posting works (proven)
Scripts live in scratchpad (/media/endlessblink/data/.dev-tmp/endlessblink/claude-1000/-media-endlessblink-data-my-projects-ai-development-bots-automation-botson/53ae1678-60f2-495f-bbd7-df58fa49981d/scratchpad/): text_drafts.py (ITEMS list; run `ssh -o BatchMode=yes bina-ci 'python3 -' < text_drafts.py`; creates draft then schedules; message_type discussion needs "category" matching settings topics.discussions (support, fitness, gaming, movies, music, politics, singles, vegan); topics not in that list (9029 WFH) use message_type "custom"; polls use message_type "poll" + poll_options list), cal_today.py (lists calendar 10-03..10-10), trivia.py, feed.py (24h chat feed), riddle_*.py. Idempotency keys derive from tag: a reused key after a failed call returns "Previous request outcome uncertain" -> rename tag. slot_clash 409 if an existing draft/scheduled row has the same date+time+topic (delete the draft first: DELETE /api/calendar/{id}). Server AI quality review rejects repeats/generic items (422) and after 2 rejections/day pauses agent publishing (429); the header bypass above fixes that once deployed.
Never SSH+SQL prod. Read-only dumps: `ssh -o BatchMode=yes bina-ci '/opt/robotnik/scripts/vps-admin.sh topics|routing|schedule|logs dash 120'`.

## Verified channels (vps-admin topics, 2026-10-03; Noam changed several channels yesterday)
7 vegan, 54 movies/series, 59 singles, 153 funny, 335 cute (bot Qs disabled), 341 welcome, 347 support, 1431 politics, 1517 gaming, 2184 now named "קשקשת ברשת" (was יום יום, chatter), 3113 AI/tech, 4037 bot corner, 4502 music room, 5438 fitness, 9029 "עבודה מהבית ופרודוקטיביות" (NEW, auto-detected, Noam confirmed it as the Sunday desk-question channel). Facts/trivia/emoji route to 4037.

## Quality lessons already persisted
CLAUDE.md "Content review workflow" (items 1-6 plus 5a/5b), memory files feedback_riddle_review_hide_answers, feedback_use_questions_tool_for_content_review, feedback_min_four_content_slots_per_day, feedback_no_recycling_group_chat, learned Hebrew rules in config/operator_prefs.md (committed hunks). The bot's live prefs reach prod only after deploy.

## Other open items
- Trivia warm-up row 898 text says "בעוד שעה, בשעה 21:00" fine; game 897 scheduled via PUT {"status":"scheduled"} workaround (schedule endpoint crashed before b4c5c1b).
- pages/week-plan.html is a stale April snapshot (project rule says update it when the weekly plan changes) — not updated; ask or update later.
- Weekly smoke check: Noam chose "tests only"; ran locally, 314 passed, 14 failed all in tests/test_planner_coercion_and_chips.py (pre-existing). Health-guard record not updated.
- Optional: build the "reviewer suggests better versions" feature (conversation_quality.review_conversation returns only (bool, reason); add a suggestion step, attach to the 422 detail for agent callers; Hebrew prompt text must come from config, not code).
- Artifact page of the first three riddles exists (claude.ai/artifact/E55R3ZYdAToWWJkYQVVCo2), contains answers; don't share/republish with answers.
- Global rule: no live cloud LLM test calls; images only GPT Image 2 / Seedream 5 (Codex `exec` image_generation works: see gen_images.sh).
- The auto-mode classifier blocks guardrail-weakening edits and prod deploys the first time; Noam's explicit approval + a retry worked for deploy; for permission-type blocks he must add rules (done for dashboard/app.py).

## Exact next steps
1. `date +"%Y-%m-%d %H:%M %A"`; run cal_today.py to confirm the schedule above is intact.
2. Answer Noam's pending question plainly: Tuesday 18:00 question not scheduled yet (needs deploy of 8114dc5). Get his "deploy", push once, deploy, verify (`vps-admin.sh status`, guardians passed), then schedule 907 with the header.
3. Continue the day-by-day AskUserQuestion walk: Tuesday done after 907; Wednesday +2, Thursday +3, Friday +4. For each slot bring candidates only when no strong live idea; avoid recycling chat phrases; texts/polls only. Poll note: server calls generic either-or polls "no concrete angle" — give concrete angles.
4. Update pages/week-plan.html when the week is final; commit only own files.
