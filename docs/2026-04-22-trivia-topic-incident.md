# 2026-04-22 Trivia Topic Incident

## Summary

This note documents the trivia scheduling and topic-targeting mistakes that happened during the Israel-themed trivia setup on 2026-04-21 and 2026-04-22.

The core failure was simple: topic/channel targeting was handled with assumptions instead of live verification.

## What Went Wrong

1. A trivia round and reruns were scheduled based on an inferred meaning of topic `7`.
2. The stored `forum_topics.name` value in SQLite for topic `7` was stale and misleading.
3. The label `כללי` / `General` was used in reasoning even though the user had not confirmed that label was correct in Telegram UI.
4. The assistant treated an inferred mapping as certain and scheduled/sent before the topic identity was verified live.
5. The assistant also assumed a root/default posting context at one stage, even though the group is forum-based and the user explicitly required a topic.

## What Actually Happened

1. The Israel-themed trivia round was prepared for the main group.
2. The assistant scheduled announcement/trivia actions before the target topic was fully confirmed.
3. The user later clarified that the intended destination was the topic currently shown in Telegram UI as `כל מה שאין לו ערוץ`.
4. A screenshot was provided showing the intended topic in Telegram UI.
5. The user then added an explicit dot marker in Telegram UI so that topic would be unmistakable in future checks.

## Live Facts Confirmed Now

1. The main group is `-1003873409631`.
2. The test group is `-1003747545764`.
3. The welcome/updates topic is `341`.
4. The current local/deployed assumptions about topic `7` were wrong.
5. Topic `7` must now be treated as unverified and unsafe for targeting.
6. The local `forum_topics` table is not a Telegram source of truth for names; it can contain stale placeholders or even message text.

## Corrections Made

1. `CLAUDE.md` was updated to say that topic/channel assumptions must not be made.
2. `CLAUDE.md` was updated to remove the false claim that topic `7` is the verified target for `כל מה שאין לו ערוץ`.
3. `config/settings.yaml` must no longer treat `topics.general = 7` as trusted.
4. A live dashboard API endpoint exists to query known topics directly from the DB: `/api/topics/live`, but that DB table itself is not sufficient proof of Telegram UI truth.
5. The earlier reasoning that topic `7` was `כל מה שאין לו ערוץ` is now explicitly treated as incorrect.

## Additional Trivia Work That Happened In The Same Window

1. The Israel-themed trivia pool was expanded and then tightened to remove obvious questions.
2. Telegram trivia round messaging was improved:
   - stronger announcement text
   - reveal messages include remaining question count
   - reveal messages include a live leaderboard snapshot
   - final results include participant count and a wider leaderboard slice
3. A pre-round announcement was scheduled earlier so users could organize ahead of the game.
4. The round was run live and logs confirmed successful button handling and 3 human participants in at least one completed round.

## Root Cause

The root cause was not missing code. It was premature certainty.

The assistant acted before the topic identity was 100% verified from a live source of truth.

## Hard Rules Going Forward

1. Never assume topic/channel identity from memory, old notes, stale DB labels, or previous incident writeups.
2. If a target topic matters, verify it from a live source of truth first.
3. The local `forum_topics` table is not enough to prove Telegram UI truth; it can contain stale or corrupted names.
4. In this project, no forum-group send should be treated as "root/default" when a topic is expected.
5. Topic `7` is currently unverified and must not be used as a trusted general mapping.
6. When the user says a specific Telegram thread is the right one, do not reinterpret the label. Use the ID and the user's confirmation.
7. If certainty is below 100%, stop and ask.

## Operational Reminder

Before any future schedule/send operation that targets a forum thread:

1. confirm current Israel time
2. confirm target chat ID
3. confirm target topic ID from live data or explicit user confirmation
4. only then schedule or send

## Status After Documentation

1. The no-assumptions rule is now written into project guidance.
2. Topic `7` is now explicitly treated as unverified, not as a safe alias for `כל מה שאין לו ערוץ`.
3. Future work must treat the local `forum_topics` table as a hint cache, not as Telegram UI truth.
