# Planner flexible routing

`config/settings.yaml:ai_populate.flex.day` and `.week` each select a
`topic_strategy`. Populate uses the selected strategy before generation and
returns `skip_reasons.code=missing_flex_routing` when it cannot resolve a
configured, verified destination. It does not substitute another topic.

- `discussion_category`: choose among the active `topics.discussions` mappings
  that also appear in the verified-topic registry.
- `topics.welcome` or `topics.goals`: use exactly that configured topic.
- `handler_routing`: use the `bot_message_routing` row named by the scope's
  `handler` setting. A missing handler or row is a routing failure.

Every destination must appear in the verified-topic registry. `discussion`
rows additionally require a matching active `topics.discussions` category,
so the generation/review rubric and commit route remain consistent. Use
`allowed_types: [custom]` for a configured general topic. This does not add a
handler, verify a topic, or activate a schedule.

The existing public configuration still selects `discussion_category` in
both scopes. Windows, lead time, caps, enabled content types, occupied slots,
and checked-only commit behavior retain their existing contracts. Suggest
remains write-free; it never approves or sends a generated row.
