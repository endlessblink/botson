# Conversation starter cleanup

Status: in_progress. Local implementation and regression verification are underway; no production behavior or queue data has changed. The initial plan preceded behavior edits. Production observations explicitly dated September 7 below supersede the inherited pending count.

## Candidate and falsifiable claim

Remove reusable generic conversation starters as both positive generation examples and direct send sources. Apply the existing semantic quality review contract to all generated morning, evening, and discussion content, including the last send boundary. Repair affected pending rows separately through the authenticated calendar surface.

The candidate changes permission to publish a conversation starter: an old pool entry, a new paraphrase, or a previously queued row must no longer bypass semantic review. It should stop task-list, diary, weekly reflection, and generic check-in prompts even when their wording differs from the reported sentence. It fails if any covered entry point sends a rejected paraphrase, falls back to a static starter on provider failure, or suppresses a valid game announcement or concrete discussion. A phrase blacklist alone cannot satisfy this claim.

## Exact removal boundary

- Remove all eight morning/evening strings in `config/prompts.yaml` from active examples and automatic/direct sends; retain quoted negatives only in regression fixtures or the inventory.
- Retire the reusable conversation-starter pool in `config/discussions.yaml` from direct delivery and generation examples. Inventory every category and item before deletion. Preserve category/routing semantics where still needed; do not replace the pool with another set of generic questions.
- Stop `daily_prompts` seeding/reset/reuse from resurrecting the retired content. Existing database rows require explicit retirement, not an assumption that YAML edits synchronize them. Preserve audit/history; no table-wide deletion.
- Remove instructions that force weekly reflection, task completion, planning, or emotional check-ins solely because of the weekday/time. Keep accurate date/time context, configured schedules, and operator-selected subjects. Inventory `weekday_rubrics.yaml`, prompt builders, baseline/live preferences, and positive anchors separately; do not erase unrelated learned preferences.
- Cover existing pending morning/evening/discussion rows regardless of `created_by`. Assess content and executable payload before action. Do not delete sent row 780, sent history, unrelated authored messages, or executable announcements disguised as discussions.
- Preserve curated facts, trivia, emoji puzzles, free games, event/RSVP announcements, weekly summaries/leaderboards, functional UI copy, routing, and manual scheduling. Inventory their static content as a separate class, not as defective conversation starters.
- Preserve all pre-existing weekly-review changes. No dependencies, broad refactor, scheduling redesign, or learning-store rewrite.

## Phase 0 — source inventory and current evidence

Known sources, with local code references to recheck when implementing:

| Source | Role / hidden behavior | Required inventory evidence |
| --- | --- | --- |
| `config/prompts.yaml` | 3 morning + 5 evening positive examples and seed source | Exact text, YAML key, all consumers |
| `config/discussions.yaml` | Categorized static sendable starters | Every exact item, category, count, all consumers |
| `bot/database/db.py:427,473` | `seed_prompts` seeds only an empty table; `get_random_prompt` recycles exhausted entries | Current bot-owned rows and seed/reset callers |
| `bot/scheduler/materializer.py:293-518` | Generation samples examples; skips failures and existing slots | Prompt inputs, review boundary, insertion and skip behavior |
| `dashboard/app.py:886` | `send_prompt_now` directly consumes static pools | Every content branch and actual delivery call |
| `dashboard/app.py:4485,6146,7840` | Semantic discussion review, suggest, commit | Morning/evening bypasses, write-free preview and checked-only commit |
| `config/weekday_rubrics.yaml` | Seven day-specific instructions | Exact instructions and time-context consumers |
| `dashboard/app.py:8096-8550` | Additional day/time/theme prompt influences | Exact positive examples/defaults, separate from negative guidance |
| `bot/utils/prefs_store.py`, `config/operator_prefs.md`, live `data/operator_prefs.md` | Baseline reconciliation, live rules/anchors, removal tombstones | Bot-owned guidance only; no raw private transcripts |
| `bot/handlers/calendar.py:905-925` | Stored text delivery | Due-row filtering, payload coercion, validation and send ordering |
| live `scheduled_messages` | Materialized text survives generation changes | Fresh pending inventory by ID, text, type, author, date, payload, state |

Search the full tracked runtime/config/template/script surface for seed loaders, pool selectors, prompt examples, fallback text, generation builders, queue writes, recurrence/copy paths, and sends. Classify every result as active sendable, generation influence, persisted duplicate, negative fixture, curated activity, or UI copy. Include excluded classes and search coverage in the final inventory; do not claim completeness from keyword matches alone. Do not inspect secrets, auth files, or raw transcripts.

Inherited runtime evidence: row 780 was recorded sent September 6; row 787 was pending September 13; six auto and three ai-fill-flex rows were pending. These counts and states are not current proof. The registry was read: Botson has no runtime_surfaces entry. Its identity/onboarding claims must not be strengthened without evidence. The canonical vault index has no Botson link; filename and _System searches found no Botson policy/note, so durable context discovery remains incomplete.

Allowed existing APIs: `Database.seed_prompts`, `Database.get_random_prompt`, `send_prompt_now`, `_review_discussion_quality`, `_ai_suggest_calendar`, `ai_suggest_commit`, and authenticated `/api/calendar` operations. Read actual signatures, request schemas, and tests before copying any pattern. Do not import dashboard infrastructure into the bot to reuse review; identify the smallest existing shared utility or extract the existing contract with regression protection.

## Phase 1 — regression cases before behavior edits

Create focused fixtures containing the two reported sentences and independently worded variants of the same diary/task/weekly-check-in pattern. Keep them out of active examples. Pair them with valid concrete discussions and functional announcements. Stub provider/reviewer and Telegram delivery for deterministic path tests; deterministic stubs prove wiring, not semantic model accuracy.

| Case | Setup and observable assertion |
| --- | --- |
| Automatic rejection | Generator returns a generic paraphrase; reviewer rejects. Materializer inserts no sendable row containing it. No static fallback is inserted. |
| Automatic success | Generator and reviewer accept concrete content. Exactly one configured future slot is inserted; routing, date, learned rules, and duplicate-slot protection remain intact. |
| Provider failure | Generator or reviewer times out, returns malformed output, or is unavailable. No unreviewed text or old pool entry becomes sendable; failure is observable. |
| Pool independence | Seed an old daily_prompts database, exhaust its used flags, and supply old YAML pools. Send Now cannot deliver an old entry or recycle it; accepted fresh generation sends once. |
| Empty install/restart | Empty and previously populated databases boot with retired pools. Restart does not reintroduce retired sendable content. |
| Planner parity | Reject identical generic candidates for morning, evening, and discussion. Suggest writes zero rows; commit inserts only checked valid suggestions. Mixed game/fact suggestions remain available. |
| Queue protection | A due generic row created before the change is rejected before Telegram is called. It is not recorded sent; existing supported failure/audit state prevents silent retry storms. |
| Valid queued text | A valid pre-existing starter sends its stored approved text once; no silent regeneration changes an approved preview. |
| Activity protection | Discussion-shaped executable/announcement rows retain their payload, routing, RSVP and game behavior; do not subject internal payloads to prose review. |
| Scoped repair | A fixture with sent, pending rejected, pending valid, and executable rows changes only explicitly selected pending rejected rows. A concurrently edited/sent row is not overwritten; rerunning is idempotent. |
| No resurrection | Baseline reconciliation, restart, materialization, and pool exhaustion cannot restore retired sources. Unrelated learned rules survive. |
| Time framing | Prompt capture retains actual date/time and configured subject but does not force diary/task reflection from weekday alone. |

Run the new tests against unchanged code and record the expected failing assertions, separately from baseline failures. Reuse existing materializer, quality gate, planner, prefs durability, and calendar game fixtures. Do not weaken prior freshness, day-anchor, no-hardcoded-content, or no-verbatim-rule guardians.

## Phase 2 — narrow implementation

Retire the inventory's active generic sources, centralize the existing review contract only as needed, and wire it to generation, direct-send, and stored-text delivery. Use editable config for policy/copy/thresholds. Preserve existing failure observability and implement a bounded, auditable blocked-content state using established database/API patterns. No silent fallback, arbitrary literal bans, replacement stock questions, or blanket disabling of all discussion content.

Verify focused regressions and existing protections after each coherent change. Review overlapping dirty-file hunks without staging unrelated work. Inventory documentation must record exact removed entries and remaining hidden-source coverage.

## Phase 3 — pending content and release

Refresh the server clock and pending calendar via the authenticated dashboard/API. Produce an exact before/action/after manifest. Retire only reviewed generic pending rows using supported API operations; preserve sent history and detect concurrent changes. If the current API cannot provide a reversible, race-safe action, implement and test that first; never substitute SSH SQL writes. Production queue mutation remains a distinct operation from code deployment.

Prepare the narrow diff, tests, rollback procedure, registry update with evidence and unverified fields, and reviewable pending-row manifest before requesting explicit deployment approval. Reconcile the missing registry runtime surface using observed service/dashboard evidence; do not invent a health URL, container, or channel identity. Runtime and registry writes outside this workspace may require filesystem approval.

## Phase 4 — verification and completion

Run `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py tests/test_digest_quality_consolidation.py tests/test_calendar_scheduled_games.py -q`, the new regression suite, applicable materializer/freshness/prefs tests, repository lint/type/static checks, and `git diff --check`. Run a stubbed suggest flow exercising all affected types and confirmation invariants.

Only after the candidate is implemented and local evidence is read, run a bounded real generation check to test semantic rejection of unseen paraphrases and acceptance of valid content. Record model failures as failures, not as evidence of good content. No live group send is authorized by this plan. Use authenticated dashboard visual proof through a disposable visual agent; API and service health alone are insufficient.

After explicit deployment approval, verify deployed revision/config, refreshed pending state, exact repaired rows, retired pool behavior, and authenticated visual state. Report local-only, committed, pushed, deployed, and production-verified separately. The task remains in_progress until the complete inventory, regression evidence, pending repair, and production read-back exist.

## Implementation evidence — September 7

- Retired 287 active pool entries (8 morning/evening, 279 discussions) and seven forced weekday rubrics; exact originals are archived in the inventory, outside generation inputs.
- Shared strict semantic review now protects generated conversation types and stored prose delivery. Empty/malformed/unavailable review cannot authorize delivery. Database pool APIs no longer seed or recycle old rows.
- New regression cases failed against the previous implementation before the corresponding fixes. Combined verification after the dashboard race/drawer fixes: 408 passed, 1 skipped, 300 subtests. Final removal of three newly added literal bans and migration of those fixtures to semantic assertions: 71 focused tests and 54 subtests passed. No new literal phrase ban remains. Existing hardcoded-content, dual-dispatch and no-verbatim-rule guardians passed; Python compilation and git diff --check passed. Ruff was unavailable and no dependency was installed.
- One bounded real-provider evaluation: all six expected verdicts matched (two reported negatives, two unseen diary variants, two concrete positive discussions). This is semantic candidate evidence, not production send proof.
- Read-only production observation at 2026-09-07 00:06 +03:00: revision d99b555; both services active. Pending snapshot near 00:09 contains six prose rows, captured in conversation-queue-repair-manifest.json. Four calendar-led reflections are selected for reversible draft quarantine; two topic-specific discussions require separate review. The manifest is not applied and must be compared with fresh authenticated data before use.
- Production deployment, queue repair, authenticated visual verification, live preference/anchor inventory and registry reconciliation remain outstanding. No commit, push, deployment, or Telegram test send is claimed by this evidence.
