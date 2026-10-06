# Bounded participation runtime

The existing group can receive source-linked news at configured ordinary daily
times and reviewed replies to explicit bot mentions/replies. Public defaults
remain disabled and contain no audience, sources or topic selection. Activation
uses one versioned `config/community_participation.yaml` through both the
authenticated dashboard `/participation` and scoped agent API.

Configuration preview writes nothing. Activation requires its exact expiring
preview receipt and explicit approval. Revision conflicts fail rather than
overwriting another operator's edits. The reload affects only participation
jobs. The normal deploy script preserves versioned participation configuration
under its cross-process lock, independently of the existing weekly lock.

News reads only explicitly configured HTTPS RSS publishers. Redirects, private
network addresses, oversized feeds, undated entries, future publication dates
and links outside the selected publisher domains are refused. The adapter
supplies publisher/date/link evidence; HTTP callers cannot assert verification
or model scores. A separate reviewer binds the exact Hebrew summary to the
publisher excerpt, checks topic/context relevance and cross-publisher event
duplication. Failure or no worthwhile fresh story produces no filler.

News and replies each use their own configured context freshness window during
preparation and send revalidation; a longer reply window cannot admit old news
context. Reads stay topic-scoped and do not prune the stored cache.
Member preference buttons retain both opt-out and opt-in actions for everyone.
A click changes only that member's preference and private callback acknowledgement,
without rewriting the shared message's keyboard for other members.

Replies are limited to configured verified topics and recent explicit mentions
or replies to the bot. There is no unsolicited reply mode. Their handler is
nonblocking and is the sole registered owner of this feature; the original
worktree's separate mentions-only WIP is not merged or registered. Weekly
membership commands remain with the existing own-ID weekly handler. Both
generation and review reject sensitive personal topics, profiling and invented
context. Names/account IDs are not included in model context; handles, phone
numbers, emails and recognizable secret forms are redacted.

Short multi-turn exchanges can be configured explicitly. Only a reply to a
confirmed ledger-owned Botson answer, from the same person in the same topic,
inside the configured follow-up window can continue the existing conversation.
That verified continuation avoids the initial-conversation cooldown; group,
topic, thread and turn caps, quiet hours, opt-outs, review and send revalidation
still apply. Another person, fabricated/uncertain parent or expired exchange
cannot claim this exception. The prompt uses the prior bot answer without
inventing memories, repeating introductions or automatically adding questions.

The existing CLI/model supplies generation. For this path inherited paid API
routing is removed, API fallback is disabled, tools/MCP/setting discovery are
restricted and session persistence is disabled. No new provider, paid API,
credential or dependency is introduced. Missing subscription CLI output fails
closed. Production content quality still requires real observation; mocks are
not proof of model quality.

The independent SQLite ledger owns new feature payloads, once-only identities,
caps, cooldowns and opt-outs. Explicit activation initializes it. Previews do
not initialize files, prune chat history, reserve capacity or schedule content.
Caps are checked before source/model work and reserved atomically before send.
The dispatcher rechecks the exact stored snapshot, current configuration,
verified topic, same-topic context marker, quiet hours, caps and opt-outs
immediately before sending. Changed state cancels an unsent reservation.
Uncertain external sends retain their identity/cap and cannot be blindly retried.
Confirmed sends require a real external message ID. Source excerpts and raw
member conversation never enter the ledger or preview response.

The reply's own-ID button disables future responses to the clicking member;
the subsequent button can restore them. Both affect only the authenticated
Telegram sender, without an extra group confirmation post. News sends and
replies are silent and do not pin messages.

General calendar/send-now pin replacement uses only `bot_pins`, serialized
across the bot and dashboard. The replacement must pin successfully before any
recorded previous pin is removed. Failed unpins retain ownership for retry.
Weekly posts continue using their separate `weekly_checkin_posts` ledger and
silent weekly-only replacement. Members' or unknown pins are not discovered.

## Shared API

- `GET /api/agent/participation` (dashboard alias `/api/settings/participation`)
  returns configuration and runtime/ordinary-cycle metadata.
- `POST /api/agent/participation/preview` validates a complete configuration
  with `expected_revision` and returns the exact activation receipt.
- `PUT /api/agent/participation` (dashboard POST alias) saves it after exact
  approval. Agent mutation retains authentication, fresh context receipt and
  durable idempotency requirements.
- `POST /api/agent/participation/news/preview` accepts only `topic_id` and
  `source_id`, fetching/reviewing through the same trusted server path. It never
  claims, schedules or sends a post. No HTTP reply-evidence fabrication route
  exists; reply triggers come from actual Telegram updates.

## Deployment and observation

Back up settings and SQLite state privately on the existing server before the
normal exact-SHA deployment. Keep runtime audience/weekly state unchanged.
After activation inspect active flags, loaded configuration revision, registered
ordinary news jobs and cycle metadata. An overnight quiet-hour skip is valid
cycle evidence, not delivered-news evidence. Actual delivery and semantic
quality are separate outcomes. Authenticated dashboard visual verification
remains mandatory before claiming its real production UI works.

Rollback restores the previous code revision through the normal service flow
and the private pre-activation participation configuration. Preserve receipts
for uncertain/sent outcomes; never erase delivery identity merely to retry.
This feature does not alter weekly check-in settings or unapproved calendar
fillers, add audiences, or implement the separate proposed arcade service.
