# Botson community activation

Status: operating proposal; production schedule changes are not verified.
Last reviewed: 2026-09-28.

## Live activity record — 2026-09-28

- The group ran the poll: “אפשר לבחור כוח־על קטן ליום אחד — מה לוקחים?”
- Aggregate read-back showed 3 votes for falling asleep within a minute and 2 for understanding every language.
- Botson sent one contextual follow-up in the same topic at 16:29 IDT, referencing those two choices. Calendar row 872 read back as `sent`, Telegram message 8776; no duplicate send was observed.
- The follow-up is a response layer to the poll, not a second scheduled anchor. Re-read live context before another broadcast.
- The private recent-message feed is implemented locally with 24-hour retention and regression tests; production capture and authenticated read-back remain pending deployment.

## Goal

Help recent joiners make a first, low-pressure contribution and give them a
natural reason to return on another day. Optimize for conversation and human
responses, not message volume, tags, or poll counts.

## Short activation sequence

Use no more than one planned anchor activity per day. Add a response layer only
when it refers to something members actually said.

1. **Today — continue the live poll.** The poll has one grounded, playful
   follow-up already. Let members answer; do not stack another prompt onto it.
2. **Next day — turn the poll into a group joke.** If the poll still has a
   clear leading choice, post: “אחת ההצעות המובילות הייתה להירדם תוך דקה 😴
   בואו ניתן לכוח הזה שם של כפתור בטלפון. ״מצב טיסה למוח״ הוא מועמד — מה שם
   יותר טוב?” If the leader changes, replace the first sentence with the
   actual leading choice. Check fresh group context and the live schedule
   before sending once. This gives members an easy creative reply without
   asking newcomers to explain themselves or do work for the group.
3. **Following day — reflect real answers.** Mention the winning choice or an
   interesting answer and ask one useful follow-up. If the poll had little
   response, switch to a simpler open question rather than repeating it.
4. **Day four — member-shaped mini-game.** Build a small quiz, ranking, or
   emoji round from themes members actually raised. Keep it optional and short.
5. **Day five — close the loop.** Share a light recap of what the group chose
   and invite one next suggestion. Do not imply that quiet members failed.

Regulars should be invited individually by the operator when useful; never
mass-tag the group. Botson should reply to direct mentions and clear references
to its earlier messages, with humor that follows the conversation and does not
pretend to remember facts it cannot see.

## Quality and repetition gates

- Keep each activity about one recognizable group interest and answerable in a
  few words or one tap.
- Before posting, compare the proposed text and activity pattern against
  recent Botson posts. Suppress exact or near-duplicate prompts and duplicate
  welcome batches.
- A poll is a format, not a reason to post. Use it only when options create a
  real choice that can be followed up on.
- After the anchor, prefer contextual replies to another scheduled broadcast.
- Never ask newcomers to justify joining, explain inactivity, or do the work of
  making the group welcoming; offer a light prompt they can answer or ignore.
- Never send a newcomer private messages; the bot cannot start a private chat.

## Learning record

Keep one canonical, privacy-minimized record with three views:

- **Daily:** activity type and topic, post time, unique voters/responders,
  number of member-to-member replies, Botson follow-up, and one brief note on
  what made participation easy or hard.
- **Weekly:** first contributions by recent joiners, return contributions on a
  different day, response time, which formats led to member-to-member
  conversation, and the next week's single experiment.
- **Monthly:** newcomer cohorts and 7/30-day return participation, active
  contributors, conversation balance, repeated-content incidents, and a short
  decision log on what to keep, change, or stop.

Use aggregate counts and paraphrased themes. Do not copy private transcripts,
collect unnecessary personal details, or label silent readers as failures.
Separate observed facts from interpretation, and keep each proposed change
testable against the next comparable week.

## Bot activity watchdog

The daily and weekly health checks cover schedule/health status; they do not
provide a verified per-action activity feed. Add one append-only event record
for each scheduled send, direct reply, content write, approval, and failure.
Alert immediately on duplicate sends, failed or unapproved sends, and text that
does not match the approved row. Send ordinary successful activity as a quiet
daily digest so alerts stay useful. Keep the feed restricted to admins and
store message ids, action/status, content hash, and timestamps rather than
duplicating member transcripts. The activation record above is a schema and
operating proposal; automated daily/weekly/monthly summaries are not yet built.

## Evidence and limits

Research suggests that considerate responses to newcomers can increase replies
and encourage another contribution, while a one-time welcome alone may not
improve later contribution quality. Earlier work also points to first
participation and responsive replies as useful retention signals. These findings
come from other online communities and do not prove this cadence will work for
Botson; use the weekly record to learn locally.

- Liu et al., *A Meta-Analysis of Organizational Socialization Interventions*,
  Journal of Applied Psychology (2024):
  https://pubmed.ncbi.nlm.nih.gov/38376909/
- Pethig et al., *Behavior Toward Newcomers and Contributions to Online
  Communities*, MIS Quarterly (2025):
  https://aisel.aisnet.org/misq/vol49/iss2/16/
- Joyce and Kraut, *Predicting Continued Participation in Newsgroups*, Journal
  of Computer-Mediated Communication (2006):
  https://academic.oup.com/jcmc/article/11/3/723/4617705
- Telegram Bot API `sendPoll` reference:
  https://core.telegram.org/bots/api#sendpoll

### Follow-up research prompt

> Research evidence-based ways to help newcomers become returning participants
> in small online communities, with particular attention to group chats and
> Telegram. Search broadly across peer-reviewed studies, field experiments,
> meta-analyses, platform documentation, and well-documented community case
> studies. Compare personalized or public welcomes, meaningful first tasks,
> timely peer replies, regular-member buddying, recurring rituals, polls,
> quizzes and games, co-creation, and gamification. For each finding, report
> the source and date, setting and sample, research design, measured outcomes
> (first contribution, return on another day, 7/30-day retention), effect size
> when available, and limitations; separate causal evidence from correlation
> and anecdotes. Assess privacy, notification fatigue, spam, tagging, lurker
> inclusion, and risks of optimizing for votes instead of conversation. Then
> recommend a practical seven-day plan for a small Hebrew Telegram group with
> several recent joiners: at most one anchor activity per day, a human-response
> plan, no mass tagging, and a minimal privacy-preserving measurement scheme.
> Rank recommendations by evidence strength, state what is unknown or
> unsupported, and provide direct links to primary sources.

## Current execution gate

The local code includes an idempotent agent calendar API; its five local API
regression tests pass. Production SSH read-back found both services active at
revision `01b8386`; unauthenticated `GET /api/calendar` returned 401. This
confirms the route requires authentication, not that the agent calendar API is
deployed. The authenticated visual control surface remains unavailable, so
this proposal and local tests do not prove schedule changes or publication.
