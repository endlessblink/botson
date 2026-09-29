# Botson agent API

The agent API gives an automation client direct access to scheduled content without a dashboard browser session.
It uses the dashboard service and the existing calendar validation and send handlers.

## Authentication

Configure `BOTSON_AGENT_API_TOKEN` on the dashboard service with a newly generated, high-entropy token.
Send it as `Authorization: Bearer <token>`; the dashboard password and Telegram bot token are not substitutes.
When the variable is unset, bearer authentication fails closed.

The token is accepted only by the calendar endpoints and the restricted community-message read endpoint described below.
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
