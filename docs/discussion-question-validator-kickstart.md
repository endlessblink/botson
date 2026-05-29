# Discussion Question Validator Kickstart

Paste this into a new Codex instance:

```text
We are in repo:
/media/endlessblink/data/my-projects/ai-development/bots+automation/botson

Task: build an enforceable discussion-question validation workflow for Botson.

Context:
- Botson already has canonical question quality guidance in config/question_quality.md.
- Existing quality/freshness tests include:
  - tests/test_quality_rules_wiring.py
  - tests/test_quality_gate.py
  - tests/test_freshness_day_anchor.py
  - bot/utils/freshness.py
  - bot/utils/quality_rules.py
- Discussion pools live in config/discussions.yaml.
- AGENTS.md says no hardcoded user-facing content in production code, and validation must be proven with focused tests.
- Goal is not merely a Codex skill. The enforceable layer should be repo-local script/test coverage. A Codex skill can be optional afterward as a curation wrapper.

Please implement:
1. Inspect current config/discussions.yaml structure and existing quality/freshness helpers.
2. Add a deterministic validator for discussion questions.
   Suggested checks:
   - valid YAML shape: mapping of category -> list[str]
   - non-empty string questions
   - exactly one question mark or at least a clear single-question form, consistent with existing pool style
   - no obvious English jargon except allowed brand/proper nouns
   - no banned freshness fragments via bot.utils.freshness.freshness_rejection
   - no exact duplicates across all categories
   - no near-duplicates within/across categories using normalized Hebrew text and repeated stems
   - reject very short/generic prompts such as "איזה מוסד?", "שאלה על מידע?", "נושא מקומי?"
   - category names should match configured discussion topics if that invariant already exists in settings
3. Add focused tests, preferably tests/test_discussion_pool_quality.py, that validate current config/discussions.yaml and include small fixture-style bad examples for each rule.
4. If the current pool fails, do not silently delete lots of content. Either:
   - make the validator support a baseline/allowlist file for known existing issues, or
   - fix a small obvious set and report remaining failures clearly.
   Prefer small, reviewable diffs.
5. Add a script entry point if useful, e.g. scripts/validate_discussions.py, so future agents/operators can run it directly.
6. Run focused verification:
   PYTHONPATH=. uv run pytest tests/test_discussion_pool_quality.py tests/test_quality_gate.py tests/test_quality_rules_wiring.py -q

Important constraints:
- No new dependencies.
- Do not add production user-facing Hebrew strings.
- Use existing helpers/patterns where possible.
- Keep changes small and reversible.
- Final answer must include changed files, what the validator catches, tests run, and remaining risks.
```
