---
source: framework
---

## Guardrails

- Ambiguous → ASK. Never assume or chain-guess.
- Max 3 debug attempts. If stuck, ASK.
- Bug/error fix escalation: attempt fix from knowledge → if fail, check official docs/changelogs → if still fail, web search (official docs > GitHub issues > Stack Overflow). Apply proven solutions, not guesses.
- Prefer proven over novel.
- Read existing code before writing. Grep for patterns/abstractions before creating new ones.
- Verify before assuming: check imports exist, packages are real (AI often hallucinates them), function signatures match, config/env vars are defined.
- Don't assume file paths, API shapes, DB columns, component props, URLs, docs, or version compat — verify from source.
- Derive types from source of truth. Match test fixtures and mocks to actual schemas.
- After changes: typecheck → lint → test. Run full suite after implementation.
- Prefer separating refactoring commits from feature commits.
- Spec precedence: when code and spec (`docs/plan-*.md`, `docs/product-plan.md`, BRD) conflict, either fix code to match spec or update the spec first — in the SAME commit. Never leave them divergent; a stale spec is worse than none.
- For cross-cutting changes: complete one location fully as reference, then apply pattern to remaining.
- For multi-file changes: explain plan and state assumptions before coding.
