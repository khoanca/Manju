---
name: comprehensive-test
description: Generate comprehensive test suites. Identifies app type and applies domain-specific testing strategy.
when_to_use: When user asks to write tests, create a test plan, or audit test coverage.
---

## Input/Output Contract

INPUT: `docs/project-state.md` (active feature), `docs/product-plan.md` (acceptance criteria), implemented code
OUTPUT: Test suites covering acceptance criteria, updated `docs/project-state.md` (status = "Tested")
NEXT: `/review-changes`
GATE: All tests must pass before proceeding

## Prerequisites

1. Read `docs/project-state.md` — active feature.
2. Read acceptance criteria from `docs/product-plan.md`.
3. Each acceptance criterion → ≥1 test case.
4. BRD Gherkin scenarios (Section 11) → canonical test specs.
5. No product plan → derive from code behavior.

## Entry Point

1. Detect app type from codebase.
2. Read matching `guides/` directory file (ecommerce, realtime, multi-tenant, ai-llm, pwa, cms, dashboard, api-first, legal, mobile).
3. Combine universal strategy below with domain guide.

## Strategy

| Architecture | Shape | Ratio |
|---|---|---|
| Monolith | Pyramid | 70u/20i/10e |
| SPA | Trophy | Static>Unit>**Integration**>E2E |
| Microservices | Honeycomb | Integration-heavy, contracts at boundaries |

Test behavior, not implementation. Coverage 70-90%, never 100%. AAA pattern. `[Unit][Scenario][Expected]` naming.

## Mandatory Test Categories

### Regression
Every feature adds regression tests. Golden path + ≥2 alternative paths. Existing tests must pass after refactor.

### Edge Cases
For functions handling input/data: null/undefined/empty, boundary values (0, -1, MAX_SAFE_INTEGER), type edges (NaN, Infinity, unicode, XSS), state edges (first/last/single/duplicate).

### Error Scenarios
Per external dependency: timeout, network error, partial failure, malformed response, rate limiting (429), auth failure (401/403).

### Fault Injection
Deliberately inject: dependency timeout, partial outage, data corruption, resource exhaustion. Use MSW `delay('infinite')` or `HttpResponse.error()`.

### Accessibility
axe-core scan on interactive pages. Keyboard navigation (Tab, Enter/Space, Escape). Focus management. Screen reader (ARIA labels, live regions).

### Contract Tests (if >1 consumer)
Pact or similar. Derive OpenAPI from Zod (`zod-to-openapi`). Run in CI.

## Tool Stack

**Core:** Vitest · Playwright · MSW · Testing Library · TS+ESLint
**Property:** fast-check · zod-fast-check
**Mutation:** Stryker (incremental, PR-scoped)
**Visual:** Playwright `toHaveScreenshot()`
**Contract:** Pact · zod-to-openapi
**A11y:** axe-core · Playwright a11y
**Security:** Semgrep · ZAP · Snyk · Gitleaks
**Perf:** k6 · Lighthouse CI · size-limit

## Coverage Targets

Line 70-90% · Branch 60-80% · E2E critical paths 100% · Mutation ≥ 80%

## Test-Driven Debugging

From `/debug` or bug report:
1. Write failing test capturing exact bug (Red).
2. Confirm fails for right reason.
3. Fix → test turns green without modification.
4. Test stays as permanent regression guard.

Fault localization: compare coverage of passing vs failing tests (SBFL). Check common upstream cause before investigating each failure.

## After Tests Pass

1. Update `docs/project-state.md`: feature = "Tested", record coverage.
2. Suggest: "Run `/review-changes` before committing."
