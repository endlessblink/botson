# Game Warmup Topic Routing

Game warmups are public RSVP teasers. The intended flow is:

- Post one warmup in the relevant topic when the game subject maps cleanly.
- Launch the actual game in the configured play topic, usually Botson's corner.
- Do not create public follow-up reminder rows.
- Delete old public warmups after `trivia.warmup_public_cleanup_minutes`.

The relevant-topic behavior is controlled by `config/settings.yaml`:

```yaml
game_warmup_topic_routes:
  enabled: true
```

Set `enabled: false` to keep all warmups in the game play topic.

Subject routing is also config-owned. Trivia uses
`game_warmup_topic_routes.trivia_categories`; Emoji Night uses
`game_warmup_topic_routes.emoji_media_types`. Values point to keys under
`topics.discussions`, so changing a topic ID stays centralized.
