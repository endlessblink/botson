# Dropoff — 2026-09-06 09:11 Sunday IDT

You are continuing work in Botson at `/media/endlessblink/data/my-projects/ai-development/bots+automation/botson` on branch `main`.

## Current task & next step
Status: `in_progress`. User wants the repeated generic conversation starters removed everywhere and a complete list of hardcoded messages like them, including other hiding places. Latest instruction was `$dropoff`; no cleanup edits have been made. Next: write a scoped cleanup plan and regression tests covering automatic generation, static-pool sends, and queued messages before changing behavior.

## Files touched / in flight
Only `HANDOFF.md` was added by this session. Preserve pre-existing dirty work: `AGENTS.md`, `bot/scheduler/jobs.py`, `config/settings.yaml`, `dashboard/app.py`, `dashboard/templates/prompts.html`; untracked `bot/handlers/weekly_state_review.py`, `tests/test_weekly_state_review.py`. These are unrelated weekly-review work and are not staged in the dropoff.

Local `cf79e31` already added freshness/quality rules and tests before this session. It was one commit ahead of origin at dropoff. The dropoff push includes that existing commit; deployment is separate.

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
Branch: main | Pre-dropoff HEAD: cf79e31 fix: silence context-free Botson prompts.
Remote: https://github.com/endlessblink/botson.git. Production: `ssh -i ~/.ssh/id_ed25519 root@84.46.253.137`, host vmi2922149, checkout `/opt/robotnik`, SQLite `/opt/robotnik/data/bot.db`. Both botson.service and botson-dashboard.service were active; production HEAD d99b555. Alias root@vps does not resolve. Read-only sqlite3 over SSH works; never read .env/auth files. No code, config, database, or deployment changes were made in the investigation.

Start by: write the cleanup plan with the exact removal boundary and falsifiable regression cases, using the proven origins above.
