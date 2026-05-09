# Agent Notes

## Planner AI Populate

- Treat the Planner Populate buttons as a production-critical flow. The user verifies on the VPS dashboard, not local-only screenshots.
- Preserve the modal confirmation invariant: `/api/weekplan/ai-suggest` must write nothing, and `/api/weekplan/ai-suggest-commit` must insert only checked suggestions.
- Do not narrow Populate back to discussion-only. The suggest engine should return a configurable mix across morning, evening, discussion, trivia, emoji, facts, free games, weekly roundup, and weekly leaderboard when those types are configured, enabled, routable, and free.
- Do not hardcode days, times, topic IDs, trivia defaults, warm-up offsets, or content caps in Python. Use `config/settings.yaml`, `topics.discussions`, and `bot_message_routing`.
- Do not hardcode content themes, categories, media types, demo copy, or pool subjects anywhere in backend or templates. This includes hidden fallbacks like Israel trivia, movie/TV emoji themes, first-item facts, or placeholder text that looks like final sendable content. Empty means neutral/general; configured/pinned payloads decide what is sent.
- **Defaults that steer outcomes count as hardcoding too.** `value="ישראל"` on a form, sort-by-pool-size when the largest pool correlates with one topic, `media_types=["movie","tv"]` fallback in emoji schedule — all rejected. Defaults must be blank, random, or operator-configured. Use random tiebreaks (`random.random()`) instead of deterministic ones.
- **Verify before claiming "all X cleaned".** Never report a category-of-fix done without grepping the full codebase. If scope is uncertain, say "fixed the instances I found" with an explicit search list. Premature completion claims are treated as overclaim.
- Any executable/internal Populate row (`trivia_round`, `emoji_puzzle`, facts, free games, weekly summaries/leaderboards, and future executable types) must be previewable before approval. If the runtime will pick a specific item later, the suggestion must pin that item in `poll_options` so the preview and sent content match.
- Day-level Populate intentionally ignores `schedule.*.days`; it uses configured times only and lets the admin approve or reject the suggested mix.
- Never suggest rows for times that have already passed on the server clock. Week-level Populate must skip earlier dates in the current week and earlier times today.
- Emoji Night Populate must suggest an announcement row before the executable game row. The announcement lead, human subject label, and allowed pool media types are admin-configurable in `schedule.emoji_puzzle`; the `emoji_puzzle` row must carry the same payload in `poll_options` so runtime filtering matches the modal.
- **Warm-up RSVP system (60-min pre-game gate):** trivia and Emoji Night announcements use `message_type="trivia_warmup_rsvp"` so calendar dispatch attaches an inline "🙋 אני בפנים" button (callback `trivint_<scheduled_msg_id>`). The handler at `bot/handlers/trivia_interest.py` writes to `trivia_interest_responses(scheduled_msg_id, user_id)` and fires a confirmation when the threshold is reached. Confirmation copy uses `poll_options.activity_label` so it works for any activity (trivia, emoji night, future types). Default lead time `trivia.populate_defaults.warmup_offset_min: 60` (was 35); `warmup_reminder_offset_min: 20` is a settings placeholder for T-126 (second reminder) — not yet wired.
- **Distinct from the in-game ready gate:** the warm-up RSVP fires ~60 min before the game on a `trivia_warmup_rsvp` row in topic 341. The pre-roll ready button (`trivready` callback) on the trivia_round announcement is a separate mechanism. Don't conflate them. Open follow-up T-127 will connect them (cancel game if warm-up RSVP < `min_ready_players`).
- Open RSVP follow-ups in `MASTER_PLAN.md`: **T-125** (RSVP buttons on remaining activity types + global toggle), **T-126** (second warm-up reminder; skip if threshold met), **T-127** (cancel game at fire time if no signups).
- Before claiming this flow works, run `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py tests/test_digest_quality_consolidation.py tests/test_calendar_scheduled_games.py -q` and a stubbed suggest flow equivalent to `/tmp/e2e_suggest.py`.
- Push only after showing/understanding the diff. Deploy only after explicit deploy approval.

## No Hardcoded User-Facing Content (HARD RULE)

Every Hebrew string a user sees, every magic number that shapes UX, every fallback must be sourced from a config file the operator can edit. Code carries no user-visible Hebrew except as an explicit `[copy missing]`-style placeholder for graceful degradation when the config key is absent.

**Forbidden in production code (`bot/handlers/**`, `bot/utils/**`, `bot/scheduler/**`, `dashboard/app.py`, `dashboard/templates/**`):**
- Hebrew string literals not passed through a config-read helper or `# noqa: hardcoded-content (reason)`'d.
- Hardcoded `chat_id=-100…` or `message_thread_id=<int>` literals (route through `bot_message_routing` or env).
- Hardcoded LLM-prompt thresholds (`עד 140 תווים`, `1-3 שורות`, `5-8` generation counts) — must come from `config/settings.yaml:llm.prompt_rules.*`.
- Module-level `_DEFAULT_*` / `_FALLBACK_*` constants holding user-facing Hebrew.
- `[internal:*]` placeholder strings stored in `scheduled_messages.text`.
- English mid-Hebrew prompt strings, English admin-DM tags, English image-prompt mood/instruction strings.
- `selected` attribute on `<option>` defaults that bias content choice without an `{% if %}` operator-state gate.

**Where things live:**
- `config/settings.yaml` — UX thresholds, gamification, schedule, LLM prompt rules, `bot.community_context`, `copy.*` namespace.
- `config/copy/*.yaml` — long-form templates (welcome, events, etc.) when settings.yaml gets too dense.
- `config/freshness.yaml` — content-validation rejection fragments (canonical ban list shared with the guardian test).
- `bot_message_routing` table — chat/topic ids per handler.
- Env vars — secrets, model versions, API endpoints.

**Enforcement:**
- `tests/test_no_hardcoded_content.py` — guardian test runs the full pattern set. Failures list every offending file:line. The test is the live spec.
- `scripts/deploy.sh` — runs the guardian as a pre-deploy step. Regressions block deploy unless explicitly bypassed (`SKIP_HARDCODED_GUARDIAN=1 ./deploy.sh`, audit-logged).
- `scripts/pre-commit.sample` — optional pre-commit hook (advisory until guardian fully green, then blocking).
- Saved feedback memory `feedback_hardcoded_content_enforcement.md` — flags this rule for every future Claude session.

**Reading user-facing copy from code**: use `bot.utils.copy.load_copy(namespace, key, **fmt)` — never read `settings["copy"]` directly in handler code. The helper centralizes the missing-key warning and the placeholder fallback.

**Escape hatch**: `# noqa: hardcoded-content (specific reason)` on the offending line. The reason is non-empty and audit-able. Use sparingly — preferably never in handler code.

**Cross-references**: this rule consolidates and supersedes the narrower memories `feedback_no_hardcoded_slot_config.md` (slot config), `feedback_no_content_bias.md` (defaults bias), `feedback_no_default_toggles.md` (toggle bloat), `feedback_verify_before_claiming_done.md` (incomplete audits). Those remain as historical context; this section is the active rule.
