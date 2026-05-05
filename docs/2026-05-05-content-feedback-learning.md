# Content Feedback Learning

Botson should learn from rejected or edited AI Populate suggestions without turning every dislike into a hardcoded validator rule.

## Goal

Make planner-generated Hebrew content improve over time from operator feedback while preserving production safety.

## Non-goals

- Do not let AI feedback learning deploy or apply itself without review.
- Do not edit production SQLite directly as the learning path.
- Do not replace `_validate_draft_text` with subjective style rules.
- Do not claim generated content is 100% reliable.

## Proposed Model

Add two persistent concepts.

`content_feedback` records examples:
- id
- created_at
- source, such as `planner_ai_suggest`
- content_type, such as `discussion`, `morning`, `evening`, `trivia_warmup`
- topic_key or category when available
- original_text
- verdict, such as `rejected`, `bad_wording`, `accepted_after_edit`
- reason
- corrected_text
- suggestion_metadata JSON

`content_style_profile` stores the current generation guidance:
- id or key, probably `planner_hebrew_default`
- updated_at
- version
- markdown guidance
- source_feedback_ids JSON
- status, such as `draft` or `active`

## Dashboard UX

Add feedback controls to AI suggestion rows:
- `Reject`
- `Bad wording`
- editable accepted text
- optional short reason field

When the operator approves edited text, store both original and corrected text.

Add a `Learn from feedback` admin action that:
- reads recent feedback
- asks the AI to propose a compact style-profile update
- shows a before/after diff in the dashboard
- applies only after operator confirmation

## Generation Flow

Every AI Populate prompt should include:
- the active style profile
- a small set of recent relevant feedback examples
- existing hard rules from `config/question_quality.md`

Hard validators should remain for objective defects only, such as malformed Hebrew, empty content, wrong type, or repeated known bad patterns.

## Safety

- Feedback collection can be local/dashboard-side and low risk.
- Applying a style-profile update requires confirmation.
- Deploying schema/code changes still follows normal Botson deploy rules.
- Production verification should use dashboard behavior and read-only diagnostics where possible.

## Suggested Implementation Order

1. Add DB schema helpers and tests for recording feedback.
2. Add feedback controls to the AI suggestion modal.
3. Include active style profile and recent feedback in planner prompts.
4. Add the reviewable `Learn from feedback` profile update flow.
5. Add tests that feedback examples affect prompt construction without bypassing validators.
