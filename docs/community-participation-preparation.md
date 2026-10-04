# Disabled participation preparation

`config/community_participation.yaml` remains disabled for news and replies.
Sources, topics, caps, quiet hours, timezones and freshness windows stay empty
or unselected. There is no bot startup import, dashboard endpoint, scheduled
collector, model call, calendar insertion or send hook for these modules.

The policies accept only moderator previews and always return
`can_publish: false`. A news preview requires an explicitly enabled selected
source/domain, canonical link and stable event identity, current publication
and verification evidence, relevance and exact-summary review, and recent
same-topic context. Reply previews additionally require a current immutable
member/message/conversation identity, privacy/moderation approval, opt-out
checks and explicit decisions about unsolicited replies and cooldown
scope. The booleans/scores are **trusted future-adapter evidence**, never
claims that this policy itself fetched an article or evaluated language.
An HTTP client must not be allowed to manufacture those verdicts.

`community_participation_store.ParticipationReviewStore` prepares persistent
bookkeeping on an explicitly selected SQLite file. Initialization is a
separate explicit operation; previews use read-only connections and do not
create files, store drafts, reserve capacity, change opt-outs or claim delivery.
It stores only explicitly approved Botson payload snapshots, not chat-history
copies. The owner-approved preview digest includes its full destination and
source metadata. Authentication and obtaining the actual human approval are
the caller's responsibility; no public approval endpoint is introduced.

The independent approval reservation rechecks evidence, durable identities,
opt-outs and global/topic/conversation budgets inside `BEGIN IMMEDIATE`.
Idempotency retries preserve the same immutable snapshot; changed payloads or
scopes conflict. Canonical URL and event dedupe survive restarts, with explicit
chat/topic scope. Caps follow the configured wall-clock date, including a
later timezone change; cooldowns retain aware instants across midnight.

Reserved, scheduled, sent and uncertain items hold their budget and identity.
Cancellation releases an unsent reservation. A sent item is terminal and
requires an external message identity. Uncertain outcomes cannot release
their identity/cap or be blindly re-sent; explicit evidence can resolve them
as sent. Bookkeeping transitions never invoke Telegram or a provider.
Member opt-out writes require the actor's own immutable ID; topic opt-outs are
prepared moderator controls with caller-enforced authentication.

Before actual integration/activation:

1. The owner selects source adapters/feeds, topics, caps, freshness, timezone,
   quiet hours and dedupe scope. A source adapter must verify the actual
   article/publisher/date/link, and semantic/context evaluation must be real.
2. Choose the dispatcher/approval owner. Adopt existing scheduled/sent rows
   into bookkeeping or fail closed until that history is accounted for.
   Preview and reservation alone do not account for unrelated legacy sends.
3. An eventual dispatcher must revalidate policy, context/opt-out/routing,
   intended send time and capacity immediately before the external send,
   including a changed day/timezone. It must use the stored exact approved
   payload and reconcile delivery uncertainty without duplicate retries.
4. Reconcile the existing separate mentions-only handler WIP before replying;
   this preparation neither replaces nor enables it. Decide whether to build
   occasional participation and whether to allow unsolicited replies at all.
5. Add the real authenticated moderator interface, own-ID opt-out interface
   and approval receipt integration, then obtain publication/deployment and
   activation approval. Synthetic tests do not prove real news quality,
   human acceptance, authenticated visuals or actual delivery.

The preparation uses the existing `aiosqlite` dependency and stdlib only.
Offline tests exercise real temporary-file transactions, concurrent approvals,
reopening, exact snapshots, event/URL identities, caps, uncertainty, timezone,
cooldown and opt-out behavior; provider/network/Telegram calls remain blocked.
