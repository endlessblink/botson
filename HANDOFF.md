# Current handoff — 2026-09-08

Status: `manual_action_required`. The cleanup is implemented, committed, pushed and deployed at `72edf31d960d61cf9bf879e28784d5f1ceffc117`. **Next: obtain an authenticated browser session by having the operator sign in at http://127.0.0.1:18080/login, then finish the already-approved four queue repairs and exact preference retirement.** An SSH tunnel forwards local 18080 to the production dashboard at localhost:8080. Do not inspect credentials or bypass login.

Deployment and repairs were explicitly approved with "go"; do not ask again. Both production services are active, all 34 release files match the commit, and four deployment guardians passed. Final isolated release tests: 409 passed, 1 skipped, 316 subtests passed; four existing deprecation warnings. Prior real-provider evaluation: 6/6 matched. No Telegram test sends or authenticated visual acceptance have been performed.

Approved queue repairs: rows 786, 787, 813, 814 to draft using `POST /api/calendar/{id}/quarantine-conversation` with the unchanged full expected snapshots in `docs/conversation-queue-repair-manifest.json`. Fresh post-deploy read-only SQL found all four still scheduled and matching. First read the authenticated calendar; changed state requires reassessment, never silently revise expectations. Read back each mutation and repeat the original request for idempotence. Rows 780 and 785 are sent; preserve them. Rows 792 and newly observed 819 are outside the repair. All four protected rows match the pre-deploy backup.

Preference repair: use the exact full old bullet and authenticated untrain request in `docs/conversation-preference-repair-manifest.json`. Post-deploy read-back found the new replacement present, all prior bullets preserved, and exactly one old bullet still present. It has not been removed. Preserve runtime-only learned rules and verify the removal tombstone. Eight retired daily_prompts rows remain archived; their send path is disabled.

Private backup: `/opt/robotnik-backups/conversation-cleanup-20260908T200929Z`, directory 700 and files 600; database quick_check passed, live preferences copied. No tombstone file existed before deployment. Previous production revision: `d99b5552c4e3129e0f437e8c30f72c9e5ee5b89c`. Deployment log is private there; do not dump raw logs or credentials.

Registry correction outside the workspace was explicitly approved and applied; YAML and exact Botson stanza were validated/read back. It records deployed 72edf31 and pending authenticated repairs without claiming identity/onboarding or visual proof. `docs/botson-registry-proposed.patch` is retained evidence of the already-applied patch; do not reapply.

Preserve unrelated dirty weekly-review work in AGENTS.md, bot/scheduler/jobs.py, config/settings.yaml, dashboard/app.py, dashboard/templates/prompts.html and untracked bot/handlers/weekly_state_review.py, tests/test_weekly_state_review.py. None was included in the release. Read `docs/conversation-cleanup-release-checklist.md`, cleanup plan and exact inventory for implemented scope. Documentation-only follow-up commits do not require another service restart.

Tools: extension browser connection timed out; standard Playwright opened only about:blank and no existing authenticated session. Disposable visual child `release_visual` is checking the tunnel login page. Parent must not view images. No test Telegram sends authorized. Local ctx_shell cwd outside project is rejected and silently runs project root; use explicit git -C for external read-only operations. Native lean-ctx wrapper caps jobs at 120 seconds; long tests use ctx_shell background inside project. One local bash syntax-check command was blocked by allowlist; do not retry it through a bypass.

---

# Historical dropoff — 2026-09-06 09:41 Sunday IDT

The following is historical diagnosis, superseded by the current handoff above.

You are continuing work in Botson at `/media/endlessblink/data/my-projects/ai-development/bots+automation/botson` on branch `main`.

## Current task & next step
Status: `in_progress`. User wants the repeated generic conversation starters removed everywhere and a complete list of hardcoded messages like them, including other hiding places. Latest instruction was `$dropoff`; no cleanup edits have been made. Next: write a scoped cleanup plan and regression tests covering automatic generation, static-pool sends, and queued messages before changing behavior.

## Files touched / in flight
Only `HANDOFF.md` was updated by this session. Preserve pre-existing dirty work: `AGENTS.md`, `bot/scheduler/jobs.py`, `config/settings.yaml`, `dashboard/app.py`, `dashboard/templates/prompts.html`; untracked `bot/handlers/weekly_state_review.py`, `tests/test_weekly_state_review.py`. These are unrelated weekly-review work and are not staged in the dropoff.

Commit `cf79e31` already added freshness/quality rules and tests before this session. The previous handoff commit `7e83347` and `cf79e31` are already pushed: live `git ls-remote origin refs/heads/main` matched local HEAD `7e83347` before this update. Deployment is separate.

## Key decisions & gotchas
- Exact reported text: `ראשון בבוקר - מה הדבר שאתם לוקחים איתכם מהשבוע שעבר?`. Production `scheduled_messages` row 780: `created_by=auto`, created `2026-08-22 21:06:50`, scheduled `2026-09-06 09:00`, recorded sent `2026-09-06 09:00:34`. No recurrence. No independent Telegram visual check was performed.
- Similar pending row 787: `☀️ ראשון בבוקר — איזה דבר אתם משאירים למחצית השבוע?`, auto-created August 29, scheduled September 13 at 09:00. Six auto and three ai-fill-flex morning/evening/discussion rows were pending at inspection. Re-query before mutation.
- `bot/scheduler/materializer.py:293-395,445-518` generates new text using static examples and writes `created_by=auto`; no semantic review here. On generation failure it skips, not static fallback. Existing scheduled rows are skipped, not regenerated. Do not claim the exact sentence is a Python literal: it was found only in the live scheduled row.
- `config/prompts.yaml` has 3 morning and 5 evening examples steering toward tasks/reflection. `config/discussions.yaml` is another static pool. Inventory exact entries before removing sendable sources; curated facts/games and functional UI copy must be identified separately rather than blindly deleted.
- Hidden duplicate: live `daily_prompts` contains 3 morning + 5 evening rows. `bot/database/db.py:427` seeds only when the entire table is empty; YAML edits do not refresh it. `get_random_prompt:473` resets exhausted rows and repeats them.
- `dashboard/app.py:885-954` Send Now directly sends morning/evening from daily_prompts and discussions from discussions.yaml, without fresh generation or freshness checks.
- `config/weekday_rubrics.yaml` dictates weekday themes. `dashboard/app.py:8303,8332,8533` embeds more timing/themes, including “Sunday morning is the time to summarize the weekend.” These are additional generation influences, not proven provenance for row 780.
- Live learned preferences/anchors are `data/operator_prefs.md`, seeded/reconciled from tracked `config/operator_prefs.md` through `bot/utils/prefs_store.py`. Inspect only bot-owned rules/examples, never raw private transcripts or credentials.
- Diagnostic reproduction: `.venv/bin/python` calling `freshness_rejection(exact_text, scheduled_date='2026-09-06')` returned `None` (accepted). The shorter banned wording does not match the paraphrase. This was baseline diagnosis, not acceptance evidence.
- Planner semantic review at `dashboard/app.py:6567` only runs for discussion; morning/evening bypass it. `bot/handlers/calendar.py:905-925` sends stored text without freshness revalidation. Fixing future generation alone does not repair pending rows.
- Production freshness.yaml and question_quality.md differ from local: the earlier cf79e31 fix was not deployed. Materializer, freshness.py, calendar.py, and prompts.yaml had identical local/server SHA-256 checksums.
- Keep negative regression fixtures distinct from active generation examples. Do not solve this by adding one more literal phrase ban; address the generation/send paths and provide the requested inventory.
- Follow AGENTS.md, continuation contract, skill router and registry. No Botson runtime_surfaces entry exists in registry; host was verified from project deploy instructions. Runtime mutations should use authenticated dashboard/API, not SSH SQL. User authorizes removal; determine deployment authority from current instructions before release. Never push unrelated dirty work or erase sent history without explicit scope.
- lean-ctx ctx_compose/ctx_session were unavailable in exposed tools. ctx_search skips dashboard/app.py (>512 KB): use rg through ctx_shell; use ctx_read start_line/limit for precise reads. Remote host has no rg; use grep. The explore subagent failed on a Spark usage limit; no child completed work. Skill router returned no relevant cleanup skill; dropoff SKILL.md was read and followed. No tests during dropoff.

## Env / run state
Branch: main | Pre-update HEAD: 7e83347 wip: dropoff handoff — remove static conversation starters.
Remote: https://github.com/endlessblink/botson.git. Production: `ssh -i ~/.ssh/id_ed25519 root@84.46.253.137`, host vmi2922149, checkout `/opt/robotnik`, SQLite `/opt/robotnik/data/bot.db`. At the prior inspection both botson.service and botson-dashboard.service were active; production HEAD was d99b555. Production observations above were inherited from the supplied handoff and were not rechecked during this dropoff. Local Docker lists no Botson-named container. Alias root@vps did not resolve; read-only sqlite3 over SSH worked in the prior investigation. Never read .env/auth files. No code, config, database, deployment changes, or tests were performed during this dropoff.

Start by: write the cleanup plan with the exact removal boundary and falsifiable regression cases, using the proven origins above.
