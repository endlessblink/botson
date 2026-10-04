# Botson agent API

The agent API gives an automation client direct access to scheduled content without a dashboard browser session.
It uses the dashboard service and the existing calendar validation and send handlers.

## Authentication

Configure `BOTSON_AGENT_API_TOKEN` on the dashboard service with a newly generated, high-entropy token.
Send it as `Authorization: Bearer <token>`; the dashboard password and Telegram bot token are not substitutes.
When the variable is unset, bearer authentication fails closed.

The token is accepted only by the calendar endpoints, restricted community-message read endpoint and validated weekly check-in endpoints described below.
It does not grant access to other dashboard APIs.

## Read and edit the schedule

- `GET /api/agent/calendar?start=YYYY-MM-DD&end=YYYY-MM-DD` returns full scheduled-message rows, including drafts and cancelled rows. The default range is today through 14 days ahead; the maximum range is 94 days.
- `POST /api/calendar` creates a row. Agent requests default to `status: "draft"`; supply `status: "scheduled"` only when the row should be approved for automatic dispatch.
- `PUT /api/calendar/{id}` edits content or schedule fields. Setting `status: "scheduled"` approves it for automatic dispatch.
- `POST /api/calendar/{id}/schedule` validates the row and schedules it.
- `DELETE /api/calendar/{id}` cancels a row.
- `POST /api/calendar/{id}/send-now` explicitly sends one row now. It uses the regular Botson content checks and dispatch handlers.
- `GET /api/agent/community/messages?hours=24&limit=100` returns recent text context from the configured main group. It requires the same private bearer token, returns at most 200 messages, and cannot request more than the configured retention window.

The bot captures non-command text and captions from non-bot members in the configured main group only, and includes sent calendar messages addressed to the main group. Entries expire after `bot.community_context_recent_hours` (24 hours by default); expired member rows are deleted during writes and reads. The endpoint does not expose other groups or DMs. Direct Bot API replies that do not create a calendar row are not yet included. Telegram does not provide a bot API for backfilling arbitrary group history, so member-message collection starts after the bot version with this capture handler is deployed.

### Chat-read gate

Every agent create, edit, schedule, or send-now request must carry `X-Community-Context-Receipt`, copied from the `context_receipt` field of a `GET /api/agent/community/messages` response. Without it the request is refused (428). The receipt expires after `bot.agent_context_receipt_ttl_minutes` and is refused (409) when any new message was captured after the read, so a proposal always reflects the chat as it is now. Cancelling a row does not need a receipt. The schedule and activity log are not chat history and never satisfy this gate. The feed only holds what was captured since deployment and within retention; say so rather than claiming to have seen older or deleted messages.

### Publishing guardrails

Agents may publish to the main group without operator review, inside these server-side limits (`agent_guardrails` in settings):

- Every agent text post is quality-reviewed when it is scheduled or sent, whatever its `message_type`; labelling a post `custom` does not skip review. Pool-backed games are exempt.
- A rejected post (422) cannot be edited and retried on the same row (409). Write a genuinely different post as a new row.
- After `max_quality_rejections_per_day` rejections, agent publishing pauses until the next day (429).
- `send-now` only fires a row due within `send_now_early_minutes`; future rows must be scheduled (409).
- A reviewer outage returns 503 and is not counted as a rejection.
- Agent-created rows are labelled `created_by: agent`. Once scheduled, they are sent like operator-approved rows: at send time only hard rules (banned fragments, recent duplicates) can block them.

Existing validation remains active, including message quality, slot-conflict, game-payload, and schedule-time checks.

## Safe retries

Every agent `POST`, `PUT`, and `DELETE` mutation requires a unique `Idempotency-Key` header (8–128 letters, numbers, dots, underscores, colons, or hyphens).
The same key and request replay the recorded response; reusing a key for different input returns `409`.
If the service stops after claiming a key but before recording its response, a retry returns `409` with an uncertain outcome and will not repeat the mutation or send.
Read `GET /api/agent/calendar` and verify Telegram delivery before taking any follow-up action; use a new key only after the outcome is known.

```sh
curl --fail-with-body \
  -H "Authorization: Bearer $BOTSON_AGENT_API_TOKEN" \
  "${BOTSON_DASHBOARD_URL}/api/agent/calendar?start=2026-09-28&end=2026-10-12"
```

```sh
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $BOTSON_AGENT_API_TOKEN" \
  -H "Idempotency-Key: $(python3 -c 'import uuid; print(uuid.uuid4())')" \
  -H "Content-Type: application/json" \
  -d '{"text":"...","message_type":"custom","channel_topic_id":341,"scheduled_date":"2026-09-29","scheduled_time":"12:00"}' \
  "${BOTSON_DASHBOARD_URL}/api/calendar"
```

`BOTSON_AGENT_API_TOKEN` and `BOTSON_DASHBOARD_URL` must be provided by the caller's approved runtime; neither belongs in source control.

## Weekly AI check-in: one configuration, two interfaces

The dashboard's weekly check-in card and these agent endpoints use **the same
`config/settings.yaml:weekly_state_review`**. Do not create a second project
file or edit YAML directly as an agent workaround. Runtime participant IDs,
subscriptions and audit entries must not be committed to a public repository.
Normal deployment preserves the versioned runtime section under its write lock.

- `GET /api/agent/weekly-checkin`: current configuration/revision and the bot's
  last observed scheduler-registration status. This is a read-only endpoint.
- `POST /api/agent/weekly-checkin/preview`: validate exact text, selected member
  handles or `selected_user_ids`, AI destination, weekday, time, IANA timezone,
  `mode` (`draft` or `auto_send`), `pin_enabled` and `expected_revision`. Returns
  exact rendered text/ID-bound audience, next occurrence and an expiring signed
  `preview_receipt`; no settings, draft or Telegram message is written.
- `PUT /api/agent/weekly-checkin`: save those same fields. Requires the normal
  agent token, fresh `X-Community-Context-Receipt`, and unique `Idempotency-Key`.
  `expected_revision` prevents losing concurrent moderator or member changes.
  Completed retries replay their receipt; an uncertain attempt must be inspected.

To enable either weekly mode, also provide the matching `preview_receipt` and
`activation_approved: true` **only after the human approved that exact audience,
text, destination and delivery mode**. A quality-override header grants no
weekly activation approval. Changes to the candidate, revision or expired
receipt require a new preview. Unknown/ambiguous/reassigned handles, arbitrary
IDs, unverified destinations, opted-out members and malformed schedules refuse
validation. The currently supported scope is the configured verified AI topic.

The configured timezone determines the local Sunday-start week. Nonexistent DST
times are skipped; ambiguous times use the first fold only. A durable weekly
claim prevents repeats and uncertain transport retries. `not_before` prevents
catch-up before the approved start date. Draft mode creates one held calendar
draft; it cannot auto-send. A draft whose configuration or recipient snapshot
changed must be reviewed again rather than delivering stale mentions.

Recipients may join/leave only by replying with the configured exact command to
a recorded check-in post in the correct group/topic. The authenticated sender's
own Telegram ID is the only affected member. Opt-out affects future previews
and posts; already delivered text is not rewritten. The request and moderator
updates merge atomically and create minimal audit entries. No member-history
summary or activity attribution is part of this feature.

Pinning is silent. Only the previous recorded weekly check-in pin is replaced;
other pins remain intact. There is no automatic expiry. Disabling the feature
stops future jobs without deleting old posts or pins. For a rollback to code
predating this feature, disable it through the current validated interface
before restoring old code.
