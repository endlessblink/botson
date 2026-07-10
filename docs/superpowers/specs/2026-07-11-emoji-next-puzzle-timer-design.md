# Emoji Night — "next puzzle" countdown timer

## Goal

Give Emoji Night the same live-ticking timer feel as trivia: between puzzles,
show a countdown to when the **next** puzzle drops, refreshed in place with a
progress bar. Purely additive — puzzles still stay open for the whole game and
players can still answer earlier ones late (option 2 of the 2026-07-11 brainstorm).

## Non-goals

- Puzzles do **not** close when the timer ends (that was option 1, rejected).
- The end-of-game wrap countdown is unchanged.
- Trivia's timer code is not refactored; only a tiny shared bar helper is extracted.

## Behavior

- After a puzzle is posted, the bot edits that same puzzle message to append a
  countdown line + bar, e.g. `⏳ החידה הבאה בעוד 0:30` over `▓▓▓▓▓▓░░░░`.
- The line refreshes at configured tick points as the gap runs down.
- Applies to puzzles 1 … n-1. The last puzzle has no "next," so no countdown is
  appended; the existing wrap countdown takes over.
- The puzzle's base text (index line + emoji + question) is preserved on every
  edit — only the countdown block changes.

## Mechanics

- Total countdown = the existing `schedule.emoji_puzzle.interval_seconds` (60s).
- Tick marks come from a new config list
  `schedule.emoji_puzzle.next_puzzle_timer_ticks` (remaining-seconds, e.g.
  `[45, 30, 15, 5]`), filtered to values `< interval_seconds`. No hardcoded
  numbers in code.
- In the session loop, the single `await asyncio.sleep(interval_seconds)`
  between puzzles becomes a ticking wait: sleep to each tick, edit the message,
  continue to the next drop.
- Edits use `bot.edit_message_text` directly (the message lives in an
  already-verified topic, same as trivia's timer edits). Failures ("message is
  not modified", flood control) are caught and logged at debug — non-fatal.

## Config (no hardcoded content)

- `config/settings.yaml`
  - `schedule.emoji_puzzle.next_puzzle_timer_ticks: [45, 30, 15, 5]`
  - `copy.emoji_puzzle.next_puzzle_countdown: "⏳ החידה הבאה בעוד {time}\n{bar}"`
    (the `{time}` is mm:ss; `{bar}` is the progress bar). Read via `load_copy`.

## Components

- `bot/utils/countdown.py` (new, small, shared):
  - `timer_bar(remaining_s, total_s, blocks=10) -> str` — filled/empty blocks.
  - `format_mmss(seconds) -> str` — `0:30`, `1:05`.
  - `countdown_tick_marks(total_s, ticks) -> list[int]` — remaining-seconds
    marks that are `> 0` and `< total_s`, sorted descending. Pure.
- `bot/handlers/emoji_puzzle.py`:
  - a `_render_next_puzzle_countdown(base_text, remaining_s, total_s) -> str`
    that appends the config line + bar to the puzzle's base text.
  - the session loop uses the tick marks to edit between puzzles.

## Error handling

- `interval_seconds <= 0` or a single puzzle → no countdown, behaves as today.
- Any edit exception is swallowed (debug log), never aborts the session.
- Empty/invalid `next_puzzle_timer_ticks` → no intermediate edits, plain wait
  (graceful degradation, still ships the game).

## Tests

- `countdown_tick_marks` filters and orders correctly (pure).
- `timer_bar` / `format_mmss` rendering (pure).
- Session loop: with a mocked bot, asserts `edit_message_text` is called at the
  expected remaining-times for puzzles 1…n-1 and that the base puzzle text is
  preserved in each edit.
- Last puzzle: no countdown edit is issued for it.
- Copy key `copy.emoji_puzzle.next_puzzle_countdown` resolves (not a
  `[copy missing]` placeholder), and the no-hardcoded-content guardian passes.

## Deploy

Normal flow: commit → push → `scripts/deploy.sh` on the VPS.
