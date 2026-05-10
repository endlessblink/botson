# Hermes Feedback Digests

Drop structured Hermes learnings here when conversation feedback should become Botson behavior.

Use one YAML file per session or topic, for example `2026-05-10-scheduler-quality.yaml`.

Suggested shape:

```yaml
source: hermes-botson
date: "YYYY-MM-DD"
topic: scheduler-content-quality
bad_examples:
  - text: "..."
    reason: "why this failed"
    promote_to:
      - question_quality
      - freshness
      - digest_validator_test
rules:
  - "compact reusable rule to add to config/question_quality.md"
runtime_fragments:
  - "fragment to add to config/freshness.yaml if it is objective enough"
content_edits:
  - file: config/facts.yaml
    item_id: example_id
    issue: "what should be rewritten"
tests:
  - tests/test_digest_quality_consolidation.py
  - tests/test_quality_gate.py
```

Ingestion rule: Hermes does not change production directly. An agent must promote digest items into Botson config/content/tests, run focused tests, then commit/push/deploy only with approval.
