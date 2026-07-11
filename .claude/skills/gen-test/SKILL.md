---
name: gen-test
description: Systematic test case generation using formal test design techniques + auto-loop (generate → run → analyze → fill gaps → repeat).
when_to_use: When you need to generate test cases methodically, not just from checklists. Use after implementation, or standalone to improve test coverage. Complements /comprehensive-test (WHAT to test) with HOW to derive test cases.
---

## Input/Output Contract

INPUT:
  - Target: file path, function name, module, or feature to test
  - Existing tests (if any) — read before generating
  - Schema/types for the target (Zod, TypeScript, OpenAPI)
OUTPUT:
  - Test suite generated via formal techniques
  - Coverage + mutation report
  - Gap analysis with each loop iteration
GATE: Coverage targets met AND mutation score ≥ 80% on target code

## Step 1: Analyze Target

Before generating any test, understand the code:

1. Read the target file(s). Identify every exported function/component.
2. For each function, extract:
   - **Inputs**: params, their types, valid ranges, constraints.
   - **Outputs**: return type, side effects, thrown errors.
   - **Dependencies**: external calls (DB, API, filesystem).
   - **Branching**: count if/else/switch/ternary paths.
   - **State**: does it mutate or depend on external state?
3. Read existing tests — note what's already covered to avoid duplication.
4. Read schema source of truth (Zod, Prisma, OpenAPI) — test data MUST match schema.

## Step 2: Select Techniques

Match technique to code shape. Apply ALL that fit — they are complementary, not alternatives.

| Code shape | Primary technique | Why |
|---|---|---|
| Function with input ranges (numbers, strings, dates) | Equivalence Partitioning + BVA | Covers valid/invalid partitions and boundary traps |
| Multiple boolean/enum conditions combined | Decision Table | Exhaustively covers condition combinations |
| Workflow with states (order, auth, subscription) | State Transition | Covers valid transitions + illegal transition rejection |
| Function with 3+ independent params | Pairwise | Reduces combinatorial explosion while catching interaction bugs |
| Data transformation / serialization | Property-Based | Framework finds edge cases humans miss |
| CRUD with schema | Schema-Driven | Auto-derive valid/invalid data from schema definition |

## Step 3: Generate Test Cases

Apply selected techniques in this order. Each technique produces concrete test case specifications before writing code.

### 3a. Equivalence Partitioning (EP)

Divide each input into **equivalence classes** — groups where any value should produce the same behavior.

1. For each input parameter, identify:
   - **Valid partitions**: ranges/groups that should succeed.
   - **Invalid partitions**: ranges/groups that should fail/error.
2. Pick ONE representative value per partition.
3. Generate one test per partition (not per value).

```
Example: validateAge(age: number) → boolean
Valid:   [18-65] → pick 30     [66-120] → pick 80
Invalid: [-∞, 0] → pick -1    [1-17] → pick 10    [121+] → pick 200    NaN, undefined
→ 6 test cases, not hundreds of random ages
```

### 3b. Boundary Value Analysis (BVA)

Test at the exact boundaries of each equivalence partition.

1. For each boundary, test: **on boundary**, **just inside**, **just outside**.
2. Combine with EP — BVA adds precision at the edges.

```
Example: validateAge(age: number), valid range [18, 120]
Test: 17 (reject), 18 (accept), 19 (accept), 119 (accept), 120 (accept), 121 (reject)
Also: 0, -1, MAX_SAFE_INTEGER
```

### 3c. Decision Table

When function has multiple conditions that combine to produce different outcomes:

1. List all conditions (boolean/enum) as columns.
2. List all possible combinations as rows.
3. For each row, determine expected outcome.
4. Eliminate impossible/redundant combinations.
5. Each remaining row = one test case.

```
Example: canAccessResource(isLoggedIn, isOwner, isAdmin)
| loggedIn | owner | admin | result |
|----------|-------|-------|--------|
| false    | -     | -     | 403    |
| true     | false | false | 403    |
| true     | true  | false | 200    |
| true     | false | true  | 200    |
→ 4 test cases covering all meaningful combinations
```

### 3d. State Transition

When code manages state (order lifecycle, auth session, subscription):

1. Draw the state machine: list all states and valid transitions.
2. Generate tests for:
   - **Every valid transition** (happy paths through states).
   - **Every invalid transition** (should be rejected/error).
   - **Full path coverage**: at least one test walks the complete lifecycle.

```
Example: Order states
  draft → confirmed → paid → shipped → delivered
  draft → confirmed → cancelled
  paid → refunded
Invalid: draft → shipped (skip), delivered → draft (backwards)
→ Test each arrow + test each illegal arrow is rejected
```

### 3e. Pairwise / Combinatorial

When function has 3+ independent parameters and full combination is too many:

1. List all parameters and their possible values.
2. Use pairwise algorithm: cover every pair of parameter values at least once.
3. Reduces N^k combinations to ~N^2 test cases.

```
Example: createUser(role: 3 values, plan: 3 values, region: 4 values)
Full: 3×3×4 = 36 combinations
Pairwise: ~12 test cases covering all pairs
```

Use `pict` CLI or manual construction for small sets. For large sets, use a pairwise generator.

### 3f. Property-Based (fast-check)

When code should satisfy an invariant for ALL valid inputs:

1. Identify the **property** (invariant that must always hold).
2. Define the **arbitrary** (generator for valid inputs — derive from Zod with `zod-fast-check` when possible).
3. Let fast-check generate 100+ random inputs and verify the property.
4. fast-check auto-shrinks failures to minimal reproducing input.

```typescript
// Property: encode then decode is identity
fc.assert(
  fc.property(fc.string(), (input) => {
    expect(decode(encode(input))).toBe(input);
  })
);

// Property: sort output is same length as input
fc.assert(
  fc.property(fc.array(fc.integer()), (arr) => {
    expect(sortFn(arr)).toHaveLength(arr.length);
  })
);
```

Common properties to test:
- **Round-trip**: encode/decode, serialize/deserialize, format/parse.
- **Idempotency**: f(f(x)) === f(x) — for normalization, caching.
- **Invariant preservation**: sort preserves length, filter result ⊆ input.
- **Commutativity**: merge(a,b) === merge(b,a) — for merge/combine operations.
- **No crash**: function doesn't throw for any valid input.

### 3g. Schema-Driven

When Zod, Prisma, or OpenAPI schema exists:

1. Read the schema as source of truth.
2. Generate **valid fixtures** that satisfy all constraints.
3. Generate **invalid fixtures** that violate each constraint individually:
   - Missing required field → one test per required field.
   - Wrong type per field → one test per field.
   - Out-of-range per constrained field (.min, .max, .regex).
4. Use `zod-fast-check` to auto-derive arbitraries from Zod schemas.
5. For API testing: use Schemathesis to auto-generate requests from OpenAPI spec.

## Step 4: Write & Run

1. Write test file(s) using the test cases derived above.
2. Follow naming convention: `[Unit][Scenario][Expected]`.
3. AAA pattern: Arrange → Act → Assert. One assertion focus per test.
4. Run: `<test-command> --coverage --reporter=verbose`.
5. Record: pass count, fail count, coverage %, execution time.

## Step 5: Analyze Gaps

After the first run, identify what's still missing:

### 5a. Coverage Gaps
```bash
# Check uncovered lines/branches
<test-command> --coverage
```
- Read coverage report. Identify uncovered branches and lines.
- For each uncovered branch: determine which input/state would reach it.
- Generate additional test case using EP or Decision Table for that branch.

### 5b. Mutation Gaps
```bash
# Run mutation testing on target (incremental, scoped)
npx stryker run --mutate 'src/target/**/*.ts'
```
- Review surviving mutants — each survivor means a test is too weak.
- For each survivor: strengthen the assertion or add a new test that kills it.
- Focus on mutants in logic operators, boundary conditions, and return values.

### 5c. Failure Analysis
- **Test fails on wrong assertion** → fix test, not code (if behavior is correct).
- **Test fails on correct assertion** → found a real bug. Switch to `/debug`.
- **Flaky test** → likely timing/state issue. Add retry analysis, don't just skip.

## Step 6: Fill Gaps & Repeat

1. Generate additional tests for gaps found in Step 5.
2. Re-run full suite.
3. Re-check coverage and mutation score.
4. **Loop exit criteria:**
   - Line coverage ≥ target (70-90% depending on project).
   - Branch coverage ≥ target (60-80%).
   - Mutation score ≥ 80% on target code.
   - All critical paths have at least one E2E test.
   - No surviving mutants in critical logic (auth, payment, data integrity).
5. If targets not met after 3 iterations → report remaining gaps to user with analysis of WHY they're hard to cover (unreachable code, external dependencies, etc.).

## Auto-Loop Summary

```
┌─────────────────────────────────────────────────┐
│ 1. ANALYZE  → Read code, types, schema          │
│ 2. SELECT   → Pick techniques per code shape    │
│ 3. GENERATE → Derive test cases via techniques  │
│ 4. RUN      → Execute tests + collect coverage  │
│ 5. ANALYZE  → Coverage gaps + mutation gaps      │
│ 6. FILL     → Generate tests for gaps            │
│     ↻ Repeat 4-6 until targets met (max 3x)     │
└─────────────────────────────────────────────────┘
```

## Relationship with Other Skills

- `/comprehensive-test` defines **WHAT** to test (categories, domain checklists, tool stack, coverage targets). Use it for test strategy and planning.
- `/gen-test` defines **HOW** to derive test cases (formal techniques, auto-loop). Use it for systematic test generation.
- `/debug` Step 3.5 calls for a failing test. Use `/gen-test` 3a-3f to write that test methodically rather than ad-hoc.
- Typical flow: `/comprehensive-test` (plan) → `/gen-test` (execute) → `/review-changes` (review).
