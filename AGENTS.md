# Agent Notes

## Planner AI Populate

- Treat the Planner Populate buttons as a production-critical flow. The user verifies on the VPS dashboard, not local-only screenshots.
- Preserve the modal confirmation invariant: `/api/weekplan/ai-suggest` must write nothing, and `/api/weekplan/ai-suggest-commit` must insert only checked suggestions.
- Do not narrow Populate back to discussion-only. The suggest engine should return a configurable mix across morning, evening, discussion, trivia, emoji, facts, free games, weekly roundup, and weekly leaderboard when those types are configured, enabled, routable, and free.
- Do not hardcode days, times, topic IDs, trivia defaults, warm-up offsets, or content caps in Python. Use `config/settings.yaml`, `topics.discussions`, and `bot_message_routing`.
- Do not hardcode content themes, categories, media types, demo copy, or pool subjects anywhere in backend or templates. This includes hidden fallbacks like Israel trivia, movie/TV emoji themes, first-item facts, or placeholder text that looks like final sendable content. Empty means neutral/general; configured/pinned payloads decide what is sent.
- Any executable/internal Populate row (`trivia_round`, `emoji_puzzle`, facts, free games, weekly summaries/leaderboards, and future executable types) must be previewable before approval. If the runtime will pick a specific item later, the suggestion must pin that item in `poll_options` so the preview and sent content match.
- Day-level Populate intentionally ignores `schedule.*.days`; it uses configured times only and lets the admin approve or reject the suggested mix.
- Never suggest rows for times that have already passed on the server clock. Week-level Populate must skip earlier dates in the current week and earlier times today.
- Emoji Night Populate must suggest an announcement row before the executable game row. The announcement lead, human subject label, and allowed pool media types are admin-configurable in `schedule.emoji_puzzle`; the `emoji_puzzle` row must carry the same payload in `poll_options` so runtime filtering matches the modal.
- Before claiming this flow works, run `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py tests/test_digest_quality_consolidation.py tests/test_calendar_scheduled_games.py -q` and a stubbed suggest flow equivalent to `/tmp/e2e_suggest.py`.
- Push only after showing/understanding the diff. Deploy only after explicit deploy approval.
