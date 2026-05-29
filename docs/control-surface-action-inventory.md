# Control-Surface Action Inventory

This is the root inventory for dashboard actions that can create, mutate, send,
or diagnose bot-owned state. It intentionally records unknowns and missing tests;
function existence alone does not prove the operator-visible flow works.

Lifecycle states used below: `preview`, `draft`, `scheduled`, `running`, `sent`,
`failed`, `skipped`, `cancelled`, `unknown`.

## Planner And Calendar

| Action | UI entry / JS handler | Endpoint | Durable mutation | Worker owner | External side effect | Terminal / lifecycle state | Visible feedback | Test reference |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Load calendar | `planner.html` `loadCalendarEvents()` / FullCalendar event source | `GET /api/calendar` | None; reads `scheduled_messages`, computes preview rows | Dashboard read model; scheduler owns persisted rows | None | `preview`, `draft`, `scheduled`, `sent`, `failed`, `skipped` | Calendar cards include `willSend`, diagnostic label/detail, status styling | `tests/test_planner_coercion_and_chips.py::test_calendar_api_does_not_render_static_pool_preview_events`, `::test_calendar_api_exposes_will_send_diagnostics`, `::test_calendar_cards_show_scheduler_trust_state` |
| Create scheduled/draft item from drawer | `planner.html` create wizard submit | `POST /api/calendar` | Inserts `scheduled_messages`; may coerce executable type and create trivia announcement row | Scheduler `calendar_checker` if status is scheduled; executable handlers for game rows | None at create time | Usually `scheduled`; executable companion rows may also become `scheduled` | Toast/refresh; calendar card should show `willSend=true` only for persisted scheduled rows | `tests/test_planner_coercion_and_chips.py::test_explicit_trivia_round_round_trips`, `::test_turning_trivia_live_creates_warmup_as_scheduled`; missing browser E2E tracked by T-154 |
| Update calendar item | `planner.html` edit event submit; `_prompt_modal.html` update path | `PUT /api/calendar/{msg_id}` | Updates allowed fields on `scheduled_messages`; may coerce executable type and schedule trivia companion row | Scheduler if row remains or becomes `scheduled` | None at update time | Existing row stays `draft`, `scheduled`, or chosen status | Toast/refresh; diagnostic card state after reload | `tests/test_planner_coercion_and_chips.py::test_review_schedule_can_move_draft_to_requested_date`; missing broad edit matrix tracked by T-155 |
| Delete calendar item | `planner.html` delete action; `_prompt_modal.html` delete path | `DELETE /api/calendar/{msg_id}` | Marks row cancelled through `delete_scheduled_message` | Scheduler ignores cancelled rows | None | `cancelled` | Row disappears; preview may remain blocked for auto slots | `tests/test_calendar_scheduled_games.py::test_deleted_calendar_item_is_not_due_for_dispatch`; missing modal delete E2E tracked by T-155 |
| Send one row now | Review drafts modal / calendar card action | `POST /api/calendar/{msg_id}/send-now` | On `target=main`, marks row `sent`; on `target=test`, sends without marking sent | Dashboard `_send_scheduled_row`; bypasses scheduler tick | Telegram send to test or main target | `sent` or HTTP `failed`; test target leaves durable state unchanged | Modal result message; row refresh | Missing parity matrix tracked by T-157; scheduler side covered by `tests/test_calendar_scheduled_games.py` and `tests/test_scheduler_e2e_trivia_launch.py` |
| Schedule draft row | Review drafts modal schedule action | `POST /api/calendar/{msg_id}/schedule` | Sets row status to `scheduled`; optional date/time update; may create trivia announcement row | Scheduler `calendar_checker` | None at schedule time | `scheduled`, or 409 `failed` to schedule if past/near due | Modal error or refreshed calendar card | `tests/test_planner_coercion_and_chips.py::test_review_schedule_can_move_draft_to_requested_date`; missing persistence invariant tracked by T-153 |
| Planner day diagnostics | Diagnostics button / direct operator call | `GET /api/diagnostics/planner-day` | None; reads scheduled rows, due rows, verified topics, routing | Dashboard diagnostics read model | None | Diagnostic-only state projection | JSON rows show due eligibility, route/topic status, error message, sent ids | `tests/test_planner_coercion_and_chips.py::test_planner_day_diagnostics_reports_scheduler_state`; missing stale/failure visual checks tracked by T-158 |
| Save prompt modal day slot | `_prompt_modal.html` `savePromptModal()` | `POST /api/weekplan/save-day` | Creates or updates `scheduled_messages` for morning/evening/discussion | Scheduler if row is scheduled | None at save time | `scheduled` by default row creation; update preserves row state behavior needs audit | Modal closes; calendar refresh | Missing focused modal E2E; T-155/T-154 |
| Skip auto planner slot | `_prompt_modal.html` skip path | `POST /api/weekplan/skip-slot` | Creates row then marks it `cancelled` as a skip marker | Calendar preview builder consumes cancelled marker | None | `cancelled` / `skipped` preview suppression | Preview no longer regenerates that slot | Missing focused test for UI path; API behavior indirectly exercised by preview tests |
| Cancel future auto rows | `weekplan.html` maintenance button | `POST /api/weekplan/cancel-auto-future` | Cancels future rows created by auto materialization | Scheduler ignores cancelled rows | None | `cancelled` | Count returned to UI | Missing test |
| Send today's AI drafts now | `planner.html` today summary action | `POST /api/weekplan/send-today-drafts-now` | Sends `ai-fill-today%` drafts; marks sent only for main target | Dashboard `_send_scheduled_row`; bypasses scheduler | Telegram sends in order with delay | `sent` or per-row `failed` | Returns `sent[]` and `failed[]` | Missing E2E; parity tracked by T-157 |

## Weekplan And AI Populate

| Action | UI entry / JS handler | Endpoint | Durable mutation | Worker owner | External side effect | Terminal / lifecycle state | Visible feedback | Test reference |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Get discussion sample | Prompt modal category switch | `GET /api/weekplan/discussion-sample` | None | Dashboard read model | None | `preview` | Sample text in modal | Missing test |
| AI fill week | `weekplan.html` AI fill button | `POST /api/weekplan/ai-fill` | Inserts `scheduled_messages` rows as `draft` via `created_by='ai-fill'` | Human review, then scheduler after schedule | LLM call only | `draft` or `failed` generation errors | Count/errors in UI | Missing focused route test |
| AI regenerate week/day | Planner/Weekplan regenerate controls | `POST /api/weekplan/ai-fill-regenerate` | Deletes `ai-fill%` `draft`/`scheduled` rows in window; recreates drafts | Human review, then scheduler after schedule | LLM call only | `draft`, `skipped`, `failed` errors | Count/errors in UI | Missing focused route test |
| AI fill trivia rows | Planner pool growth / legacy controls | `POST /api/weekplan/ai-fill-trivia` | Inserts trivia warm-up and round rows as `draft` | Human review, then scheduler/game handler | None until scheduled/sent | `draft`, `skipped`, `failed` | Count/errors in UI | `tests/test_planner_coercion_and_chips.py::test_content_inventory_scheduler_types_are_exposed`; broader behavior missing |
| AI fill pool rows | Planner pool growth controls | `POST /api/weekplan/ai-fill-pool-rows` | Inserts executable pool rows as `draft` | Human review, then scheduler/game/facts handlers | None until scheduled/sent | `draft`, `skipped`, `failed` | Count/errors in UI | `tests/test_planner_coercion_and_chips.py::test_populate_calls_pool_growth_endpoints`; route-level gaps remain |
| Start AI suggest job | Planner Populate modal | `POST /api/weekplan/ai-suggest` | None; stores transient in-memory job only | Dashboard background task | LLM call only | `preview` suggestions, `failed` job | Modal progress/status | `tests/test_planner_coercion_and_chips.py::test_ai_suggest_calendar_returns_mixed_types_without_writes`, `::test_ai_suggest_calendar_skips_past_times_today` |
| Poll AI suggest job | Planner Populate modal | `GET /api/weekplan/ai-suggest/{job_id}` | None | Dashboard job registry | None | `preview`, `failed`, `unknown` if job lost | Modal progress/result/error | Covered through AI suggest tests; missing browser polling E2E |
| Commit AI suggestions | Planner Populate modal approval | `POST /api/weekplan/ai-suggest-commit` | Inserts approved suggestions as `scheduled_messages.status='scheduled'` | Scheduler/executable handlers | None at commit time | `scheduled`, per-item rejected/failed | Modal returns inserted ids/errors; calendar refresh | `tests/test_planner_coercion_and_chips.py::test_ai_suggest_commit_schedules_approved_rows`; persistence invariant tracked by T-153 |
| Fill today digest | Day-level Populate / Fill Today | `POST /api/weekplan/ai-fill-today` | Inserts event reminders, regular slots, and executable rows as `draft` | Human review, then scheduler after schedule | LLM call only | `draft`, `skipped`, `failed` | Summary, notes, errors | `tests/test_planner_coercion_and_chips.py::test_fill_today_uses_server_today_token_not_stale_calendar_date`; missing browser E2E |
| Today summary | Planner today panel | `GET /api/weekplan/today-summary` | None | Dashboard read model | None | Diagnostic/read-only | Counts and draft/scheduled lists | Missing focused test |
| Update prompt pool item | Prompt edit in weekplan modal | `POST /api/weekplan/update-prompt` | Writes `config/prompts.yaml` or `config/discussions.yaml` | Bot reload if signaled elsewhere; config consumer | None | `sent/succeeded` config write or `failed` | Toast/result | Missing test |

## Trivia Controls

| Action | UI entry / JS handler | Endpoint | Durable mutation | Worker owner | External side effect | Terminal / lifecycle state | Visible feedback | Test reference |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Save generated trivia questions | Planner trivia generator | `POST /api/trivia/questions` | Writes `config/trivia.yaml` after verification | Trivia runtime reads pool | None | `sent/succeeded` config write or `failed` | Count/errors | `tests/test_planner_coercion_and_chips.py::test_trivia_topup_generates_reviews_and_persists_missing_questions`; shape coverage also in trivia tests |
| Reset trivia scores | Trivia/settings control | `POST /api/trivia/reset` | Resets trivia score tables | Trivia runtime | None | `sent/succeeded` reset or `failed` | Toast/result | Missing test |
| Start live trivia round | Planner live trivia form | `POST /api/trivia/round/start` | Writes `data/trivia_round_trigger.json` after preflight | Bot trigger watcher | Bot later sends live trivia to Telegram | `running`, then bot-owned terminal state unknown to dashboard | Immediate persisted-trigger response; later logs only | `tests/test_planner_coercion_and_chips.py::test_trivia_form_defaults_not_hardcoded_to_israel`; route preflight partly covered; end-to-end gap tracked by T-156 |
| Stop live trivia round | Planner live trivia stop | `POST /api/trivia/round/stop` | Writes `data/trivia_round_stop` | Bot trigger watcher | Bot stops active round | `cancelled`/`skipped` unknown | Toast/result | Missing test |
| Suggest trivia round config | Planner suggestion button | `POST /api/trivia-round/suggest` | None | Dashboard LLM generation | LLM call only | `preview` or `failed` | Form prefill | Missing test |

## Pools And Content Libraries

| Action | UI entry / JS handler | Endpoint | Durable mutation | Worker owner | External side effect | Terminal / lifecycle state | Visible feedback | Test reference |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Suggest emoji puzzles | Planner pool growth | `POST /api/pool/emoji-puzzles/suggest` | Inserts generated rows into `emoji_puzzles` immediately | Emoji Night handler later consumes pool | LLM call only | `sent/succeeded` pool insert or partial `failed` | Inserted items/errors | `tests/test_planner_coercion_and_chips.py::test_pool_growth_buttons_removed_from_toolbar`, `::test_populate_calls_pool_growth_endpoints`; route insert behavior missing |
| Suggest facts | Planner pool growth | `POST /api/pool/facts/suggest` | None; returns candidates with validation | Facts handler later consumes config after append | LLM call only | `preview` or `failed` | Suggestions with validation errors | Missing focused route test; config shape covered by `tests/test_facts_pool.py` |
| Append fact | Planner fact approval | `POST /api/pool/facts/append` | Appends one validated fact to `config/facts.yaml` | Facts scheduler/runtime | None | `sent/succeeded` config write or validation `failed` | Approval result | `tests/test_facts_pool.py` validates resulting pool shape; route missing |
| Create emoji puzzle | Puzzles page form | `POST /api/puzzles/create` | Inserts `emoji_puzzles` row | Emoji Night handler later consumes pool | None | `sent/succeeded` pool insert or `failed` | Table refresh/result | Missing test |
| Edit emoji puzzle | Puzzles page inline edit | `PATCH /api/puzzles/{puzzle_id}` | Updates `emoji_puzzles` row | Emoji Night handler later consumes pool | None | `sent/succeeded` update or `failed` | Table refresh/result | Missing test |
| Delete emoji puzzle | Puzzles page delete | `DELETE /api/puzzles/{puzzle_id}` | Soft/hard deletes puzzle via DB helper | Emoji Night no longer selects it | None | `cancelled`/removed | Table refresh/result | Missing test |
| Save emoji schedule | Puzzles schedule form | `POST /api/puzzles/schedule` | Writes `config/settings.yaml` feature/schedule/gamification fields; signals bot reload | Bot scheduler reload | SIGHUP to bot if running | `sent/succeeded` config write or `failed` | Reload result | Missing test |
| Run emoji puzzles now | Puzzles page run-now buttons | `POST /api/puzzles/run-now` | Creates emoji session/runtime records through handler | Emoji Night handler | Telegram send to selected target | `running` or `failed` | Session id/target | Missing test; production-safe smoke tracked by T-159 |

## Bot Controls And Send-Now

| Action | UI entry / JS handler | Endpoint | Durable mutation | Worker owner | External side effect | Terminal / lifecycle state | Visible feedback | Test reference |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Reload bot config | Settings/control button | `POST /api/bot/reload` | None | OS process | Sends SIGHUP to PID in `data/bot.pid` | `sent/succeeded` signal or `failed` | JSON status/message | Missing test |
| Send prompt now | Prompts page send buttons | `POST /api/bot/send-prompt` | Logs activity | Dashboard direct Telegram call | Telegram send to main group/topic | `sent` or `failed` | Prompt/category returned | Missing test; risky because main target only |
| Send custom message | Planner/settings manual send | `POST /api/bot/send-message` | Logs activity | Dashboard direct Telegram call | Telegram send to test/main; optional poll/cover | `sent` or `failed` | Message id or error | `tests/test_topic_guard.py` covers topic guard lower layer; route/browser missing |
| Fetch bot logs | Logs panel | `GET /api/bot/logs` | None | Dashboard file read | None | Diagnostic/read-only | Tail lines | Missing test |
| Restart bot | Control button | `POST /api/bot/restart` | None | OS process/supervisor | Sends SIGTERM to PID | `sent/succeeded` signal or `failed` | JSON status/message | Missing test |

## Settings, Routing, Moderation

| Action | UI entry / JS handler | Endpoint | Durable mutation | Worker owner | External side effect | Terminal / lifecycle state | Visible feedback | Test reference |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Save topic mappings | Settings/prompts page forms | `POST /api/settings/topics` | Writes `config/settings.yaml:topics`; rejects untrusted `general` writes | Bot reload/config consumer | SIGHUP if bot PID exists | `sent/succeeded` config write or `failed` | Reload flag/result | Missing route test |
| Save antispam | Spam/settings form | `POST /api/settings/antispam` | Writes `config/settings.yaml:antispam` | Bot reload/config consumer | SIGHUP if bot PID exists | `sent/succeeded` or `failed` | Reload flag/result | Missing route test |
| Save schedule | Prompts/settings schedule forms | `POST /api/settings/schedule` | Writes `config/settings.yaml:schedule` | Bot scheduler reload/config consumer | SIGHUP if bot PID exists | `sent/succeeded` or `failed` | Reload flag/result | Missing route test |
| Save holiday blackouts | Settings holiday form | `POST /api/settings/holiday-blackouts` | Writes `config/settings.yaml:holiday_blackouts` | Planner/AI fill reads block list | SIGHUP if bot PID exists | `sent/succeeded` or `failed` | Count/reload flag | Missing route test |
| Save gamification | Settings form | `POST /api/settings/gamification` | Writes gamification settings | Bot reload/config consumer | SIGHUP if bot PID exists | `sent/succeeded` or `failed` | Reload flag/result | Missing route test |
| Save features | Prompts/events/spam/settings toggles | `POST /api/settings/features` | Writes feature toggles | Bot reload/config consumer | SIGHUP if bot PID exists | `sent/succeeded` or `failed` | Reload flag/result | Missing route test |
| Save prompt pools | Prompts page save buttons | `POST /api/prompts/save` | Writes `config/prompts.yaml` or `config/discussions.yaml` | Bot reload/config consumer | SIGHUP if bot PID exists | `sent/succeeded` config write or `failed` | Toast/result | `tests/test_digest_quality_consolidation.py` and quality tests cover content, not route |
| List observed topics | Settings/routing page | `GET /api/topics/forum` | None | Dashboard read model | None | Diagnostic/read-only | Topic list | Missing route test |
| List verified topics | Settings/routing page | `GET /api/topics/verified` | None | Dashboard read model | None | Diagnostic/read-only | Topic list | Missing route test |
| Upsert verified topic | Settings/routing dot-test workflow | `POST /api/topics/verified` | Upserts `verified_forum_topics`; signals reload | Topic guard/routing consumers | SIGHUP if bot PID exists | `sent/succeeded` or `failed` validation | Entry/reload flag | `tests/test_planner_coercion_and_chips.py::test_grouped_channels_includes_welcome_and_botson_corner`; route missing |
| Delete verified topic | Settings/routing cleanup | `DELETE /api/topics/verified/{category_key}` | Deletes verified topic mapping; signals reload | Topic guard/routing consumers | SIGHUP if bot PID exists | `cancelled` mapping or `failed` | Category/reload flag | Missing route test |
| Add observed topic manually | Legacy settings action | `POST /api/topics/forum` | None; always rejected | None | None | `failed` | Error message | Missing test |
| List handler routing | Settings/routing page | `GET /api/handler-routing` | None | Dashboard read model | None | Diagnostic/read-only | Routing list | Missing route test |
| Save handler routing | Settings/routing page | `POST /api/handler-routing/save` | Upserts `bot_message_routing`; validates verified topics | Scheduler/executable handlers route through it | SIGHUP if bot PID exists | `sent/succeeded` route config or `failed` | Handler and route ids | Missing route test; production impact high |
| Save moderation settings | Moderation page toggle | `POST /api/moderation/settings` | Writes moderation settings | Bot moderation handlers | None/reload unknown | `sent/succeeded` or `failed` | Toast/result | Missing test |

## Review Drafts Modal

| Action | UI entry / JS handler | Endpoint | Durable mutation | Worker owner | External side effect | Terminal / lifecycle state | Visible feedback | Test reference |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Load drafts/scheduled cards | `planner.html` review drafts modal refresh | `GET /api/calendar` | None | Dashboard read model | None | `draft`, `scheduled`, `sent`, `failed` projection | Modal rows with status/action buttons | `tests/test_planner_visual.py::test_review_drafts_modal_title_clears_sidebar`; missing lifecycle proof tracked by T-155 |
| Edit draft text/time/topic | Review modal edit form | `PUT /api/calendar/{msg_id}` | Updates `scheduled_messages` | Scheduler after scheduled | None | `draft` or `scheduled` | Row refresh/error | Missing browser E2E; T-155 |
| Schedule draft | Review modal schedule button | `POST /api/calendar/{msg_id}/schedule` | Sets status `scheduled` | Scheduler | None | `scheduled` or 409 `failed` | Row/card diagnostic refresh | `tests/test_planner_coercion_and_chips.py::test_review_schedule_can_move_draft_to_requested_date`; T-153/T-155 |
| Send draft now | Review modal send button | `POST /api/calendar/{msg_id}/send-now` | Marks sent only for main target | Dashboard direct send | Telegram send | `sent` or `failed` | Message id/error | Missing E2E; T-157 |
| Delete draft | Review modal delete button | `DELETE /api/calendar/{msg_id}` | Marks cancelled | Scheduler ignores | None | `cancelled` | Row removed | Missing E2E; T-155 |

## Coverage Checklist

Routes used by dashboard JS and explicitly covered above:

- `/api/calendar`, `/api/calendar/{msg_id}`, `/api/calendar/{msg_id}/send-now`, `/api/calendar/{msg_id}/schedule`
- `/api/diagnostics/planner-day`
- `/api/weekplan/discussion-sample`, `/api/weekplan/save-day`, `/api/weekplan/skip-slot`, `/api/weekplan/cancel-auto-future`, `/api/weekplan/send-today-drafts-now`, `/api/weekplan/today-summary`, `/api/weekplan/update-prompt`
- `/api/weekplan/ai-fill`, `/api/weekplan/ai-fill-regenerate`, `/api/weekplan/ai-fill-trivia`, `/api/weekplan/ai-fill-pool-rows`, `/api/weekplan/ai-fill-today`, `/api/weekplan/ai-suggest`, `/api/weekplan/ai-suggest/{job_id}`, `/api/weekplan/ai-suggest-commit`
- `/api/trivia/questions`, `/api/trivia/reset`, `/api/trivia/round/start`, `/api/trivia/round/stop`, `/api/trivia-round/suggest`
- `/api/pool/emoji-puzzles/suggest`, `/api/pool/facts/suggest`, `/api/pool/facts/append`
- `/api/puzzles/create`, `/api/puzzles/{puzzle_id}`, `/api/puzzles/schedule`, `/api/puzzles/run-now`
- `/api/bot/reload`, `/api/bot/send-prompt`, `/api/bot/send-message`, `/api/bot/logs`, `/api/bot/restart`
- `/api/settings/topics`, `/api/settings/antispam`, `/api/settings/schedule`, `/api/settings/holiday-blackouts`, `/api/settings/gamification`, `/api/settings/features`
- `/api/prompts/save`, `/api/topics/forum`, `/api/topics/verified`, `/api/topics/verified/{category_key}`, `/api/handler-routing`, `/api/handler-routing/save`, `/api/moderation/settings`

Known gaps moved into the E2E lane:

- T-153: prove scheduled calendar cards have matching durable scheduled rows.
- T-154: browser E2E for create drawer state isolation.
- T-155: review-draft lifecycle proof.
- T-156: scheduler due-row lifecycle proof.
- T-157: send-now versus scheduler parity.
- T-158: failure visibility diagnostics.
- T-159: production-safe Sherlocks Den smoke harness.
- T-160: one-command local/CI trust gate.
