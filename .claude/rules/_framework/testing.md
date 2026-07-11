---
source: framework
---

## Strategy

- Write tests for behavior before or alongside implementation.
- Test like a user: assert on visible behavior, not implementation details.
- E2E tests required for critical paths: auth, payment, core user journey.

## Execution

- AAA structure (Arrange-Act-Assert). Each test fully independent — no shared mutable state between tests.
- Mock only external boundaries. Prefer fake timers over real timers.
- Never use fixed `sleep()`/`wait(ms)` — synchronize on events/conditions instead (avoids flaky tests).
- After implementation: run full test suite, not just new tests.
- For frontend: verify UI renders correctly (dev server + browser). Types passing ≠ feature working.
- Test data/fixtures must match actual schema. Read schema FIRST, then write fixtures.
- Mock API responses must match real API schema.
- Never accept tests that mock the function under test — that tests nothing.
- Every test must have a meaningful assertion. No `expect(true).toBe(true)` or assertion-free tests.
