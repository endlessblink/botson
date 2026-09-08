# Conversation cleanup release gate

Status: manual_action_required. Cleanup commit `72edf31d960d61cf9bf879e28784d5f1ceffc117` is pushed and deployed. The four queue repairs and exact preference retirement are authorized but remain unapplied pending an authenticated dashboard session and visual verification. Sign in at `http://127.0.0.1:18080/login` through the production SSH tunnel; do not share credentials in chat.

September 8 release evidence: the isolated committed tree passed 409 tests and 316 subtests, with one skipped test and four existing FastAPI deprecation warnings. All four production deployment guardians passed (12, 7, 4 and 5 tests; 10 subtests). Both services restarted successfully. All 34 deployed release-file SHA-256 hashes match the commit. The unauthenticated calendar returns HTTP 401. No Telegram test message was sent.

Post-deployment read-back: rows 786, 787, 813 and 814 remain scheduled and exactly match their approved snapshots. Rows 780, 785, 792 and 819 are unchanged from the private pre-deployment backup. All previous preference bullets are preserved; the replacement rule is present and exactly one obsolete bullet remains for authenticated untrain. Eight legacy daily prompts remain archived; the release disables their send path. The external Botson registry correction was approved, applied, YAML-validated and read back, with identity and visual proof explicitly unverified.

September 8 preflight: production remains d99b5552c4e3129e0f437e8c30f72c9e5ee5b89c with no tracked changes and both services active. The four repair snapshots still match. Row 785 is now sent and must remain untouched; row 819 is a new concrete cooking discussion outside the four-row repair. Row 780 remains sent with its original text and timestamp. A private SQLite backup passed `quick_check`; live preferences were also backed up at `/opt/robotnik-backups/conversation-cleanup-20260908T200929Z`. No preference tombstone file existed at preflight.

## Exact pending repair

The companion conversation-queue-repair-manifest.json records the September 7 read-only snapshot. Approved action: move rows 786, 787, 813 and 814 to drafts using the authenticated `POST /api/calendar/{id}/quarantine-conversation` endpoint with the complete `expected` snapshot. Preserve text, schedule, author and history. Row 785 has since been sent and must remain untouched; preserve row 792 for separate semantic assessment.

Before each action, read the current row through the authenticated calendar. Any changed field or dispatch claim requires a new assessment; do not rewrite the manifest automatically to make it pass. After each action, read the row back and verify status `draft`, marker `conversation_cleanup`, unchanged text and metadata. Repeat the same request to verify idempotence. Never mutate sent row 780.

## Deployment preparation and rollback

1. Complete the final regression run after the dispatch-race fixes; inspect the scoped diff and preserve unrelated weekly-review work. Commit only cleanup changes with Constraint, Rejected and Tested trailers.
2. Before publishing, verify branch compatibility and exact remote revision. Production was d99b555 at 2026-09-07 00:06 +03:00; its tracked config diff was empty against that revision. Compare relevant configs to the proposed release; absence of a production dirty diff does not establish parity.
3. Obtain explicit deployment approval. The existing deploy script resets the production checkout to origin/main, so first check production status and make a private backup of bot-owned database and live preference artifacts without exposing their contents or credentials. Preserve all live learned guidance.
4. Deploy the approved revision, run pre-deploy guardians, and verify both services and deployed revision/config. Do not send a test message to the group without separate authorization.
5. Apply only the reviewed authenticated repairs and verify them visually in the real dashboard. Browser access currently has no authenticated dashboard tab. Port 8080 was mapped to the dashboard service PID and responded locally over SSH; public reachability and authenticated visual behavior remain unverified.
6. On regression, restore the recorded previous revision and restart the two services. Keep repaired generic rows held in drafts; reverting code must not automatically reschedule them. Restore a held row only after explicit content review through the calendar. Database schema is unchanged.

## Registry correction to apply with this release

The external bots-directory registry has malformed indentation in the Botson stanza and unsupported active/onboarding claims. Its existing candidate username is not proof of identity. The scoped correction should preserve that candidate and set unverified owner/channel/onboarding fields, record the observed host `84.46.253.137` (`vmi2922149`), checkout `/opt/robotnik`, and services `botson.service` and `botson-dashboard.service`. Add a Botson runtime surface with September 7 evidence and the exact deployed revision after release.

Do not invent a public dashboard/health URL or mark identity verified. The service uses a secret-manager reference; record only its reference after confirming the service configuration, never secret values. Registry writes are outside the writable workspace and need filesystem approval. Validate YAML and read back the exact Botson stanza after correction; preserve unrelated entries.

## Remaining proof gates

- Local evidence complete: final isolated release verification passed 409 tests and 316 subtests (1 skipped); earlier 6/6 real-provider cases matched. Python compilation and diff whitespace checks passed. Ruff unavailable. Bounded independent review found both reported send blockers addressed.
- Live preference rule retirement through its supported authenticated write path, preserving runtime-only learned rules.
- Authenticated dashboard access, pending-row repair and visual confirmation.
- Deployment and registry correction completed; production message-generation and actual send behavior have no fresh end-to-end visual proof.

Passing local tests or the six-case real-provider evaluation does not satisfy these production gates.

The cleanup release excludes the unrelated weekly-review edits, which remain dirty locally. `botson-registry-proposed.patch` is the applied registry correction retained for review; do not reapply it. The registry's candidate identity remains unverified. Deployment approval persists; no second deployment approval is needed to finish the already-authorized authenticated repairs.
