# Agent Notes

## Planner AI Populate

- Treat the Planner Populate buttons as a production-critical flow. The user verifies on the VPS dashboard, not local-only screenshots.
- Preserve the modal confirmation invariant: `/api/weekplan/ai-suggest` must write nothing, and `/api/weekplan/ai-suggest-commit` must insert only checked suggestions.
- Do not narrow Populate back to discussion-only. The suggest engine should return a configurable mix across morning, evening, discussion, trivia, emoji, facts, free games, weekly roundup, and weekly leaderboard when those types are configured, enabled, routable, and free.
- Do not hardcode days, times, topic IDs, trivia defaults, warm-up offsets, or content caps in Python. Use `config/settings.yaml`, `topics.discussions`, and `bot_message_routing`.
- Day-level Populate intentionally ignores `schedule.*.days`; it uses configured times only and lets the admin approve or reject the suggested mix.
- Before claiming this flow works, run `PYTHONPATH=. uv run pytest tests/test_planner_coercion_and_chips.py -q` and a stubbed suggest flow equivalent to `/tmp/e2e_suggest.py`.
- Push only after showing/understanding the diff. Deploy only after explicit deploy approval.
