# Dropoff — 2026-07-23 13:20 Thursday IDT

```text
You are continuing work in Botson on branch incident/ai-populate-auth-20260722.

## Current task & next step
Restore AI Populate end-to-end after both CLI provider sessions expired — next: obtain explicit approval to display one-time Claude/Codex login URLs or device codes, then reauthenticate both providers on the VPS.

## Files touched / in flight
- Committed in 1621448: dashboard/app.py; bot/utils/redaction.py; scripts/botson_health_guard.py; scripts/capture_showcase.py; scripts/e2e_den_smoke.py; tests/conftest.py; tests/test_botson_health_guard.py; tests/test_planner_generation_pipeline.py; tests/test_planner_visual.py; tests/test_security_defaults.py; tests/test_sensitive_redaction.py.
- HANDOFF.md is the only new handoff change after that commit.
- The original worktree on fix/planner-day-coverage still contains the same 11 repair changes uncommitted; preserve it. The clean recovery worktree is /tmp/botson-ai-repair.

## Key decisions & gotchas
- Root cause is production authentication, not Telegram or service health: Claude reports loggedIn=false/authMethod=none; Codex has no usable login and previously returned 401/missing bearer.
- The repair classifies provider auth/transport failures, fails over quickly, redacts secrets, and removes insecure dashboard-password defaults; it does not alter Populate preview/commit semantics.
- Five runtime files on the VPS were byte-identical to the preserved local patch before reconstruction; backups are under /tmp/botson-incident-20260722 locally and on the VPS.
- PR #9 is open from this fresh branch against current main: https://github.com/endlessblink/botson/pull/9.
- Do not inspect or print saved authentication panes/codes without explicit user approval; a prior attempt was policy-rejected. Do not deploy or send Telegram messages without explicit deploy approval.
- A randomized rolling-week test can intermittently exceed its stale call-count assertion. Do not change production scheduling to satisfy it. Repair-focused tests passed 65/65; planner/guardian tests passed 203/203. The full-suite baseline has three unrelated facts-pool minimum deficits: support 15/25, fitness 15/25, general 24/25.
- A stale tmux session named claudeauth existed from July 17, with no active auth child process. /opt/robotnik/.codex-home is owned by botson, but root-owned marketplace cache artifacts remain; avoid broad destructive cleanup.

## Env / run state
Branch: incident/ai-populate-auth-20260722 | Last commit: 1621448 fix: fail fast when AI providers need login
Running: VPS botson and botson-dashboard systemd services are active; local unrelated containers are running; no Botson container is relevant.
Git: branch pushed to origin; PR #9 open with no reported checks. Production has not been redeployed from this branch.
Auth: Claude logged out; Codex login unavailable. Expected commands are `sudo -u botson -H claude auth login --claudeai` and `sudo -u botson -H env CODEX_HOME=/opt/robotnik/.codex-home codex login --device-auth`.

Start by: ask the user to explicitly authorize displaying the two one-time login URLs/device codes.
```
