---
name: debug
description: Systematic debugging workflow. Reproduce → isolate → root cause → fix → verify no regression.
when_to_use: When the user reports a bug, error, or unexpected behavior. Also when a test fails unexpectedly or a feature stops working.
---

## Input/Output Contract

INPUT:
  - Bug report or error description from user
  - `docs/project-state.md` (current context)
  - Error logs, stack traces, screenshots (if provided)
OUTPUT:
  - Root cause analysis
  - Targeted fix with verification
  - Regression test added
NEXT: `/review-changes` if fix touches multiple files
GATE: User confirms the bug is fixed before closing

## Fast-Track Mode (Production Emergency)

When user reports a production incident:
1. Skip business/product phases entirely — go straight to reproduce → fix → test.
2. Use `hotfix/` branch prefix.
3. Fix ONLY the immediate issue. No refactoring, no improvements.
4. Test: critical path only (not full suite if time-sensitive, but NEVER skip tests entirely).
5. Deploy with rollback plan ready.
6. After incident resolved: create follow-up task for root cause analysis and comprehensive fix if needed.

## Step 1: Reproduce

**Routing note:** A single-file obvious bug → fix directly (CLAUDE.md Layer 1). Use `/debug` when the root cause is unclear or reproduction touches multiple modules; escalate to `/research-codebase` (Layer 2 scope) or `/plan-feature` for a multi-file structural fix.

**MANDATORY: Complete Steps 1-3 before writing ANY fix code.**

Before anything, reproduce the issue:
1. Ask user to paste the **raw error/stack trace** — read the original error first, ignore any user interpretation.
2. Read the error message/stack trace carefully. Identify the exact file and line.
3. Run the failing test or trigger the behavior described by user.
4. If cannot reproduce → ask user for exact steps, environment, and input data.
5. Document: expected behavior vs actual behavior.

**Never guess the cause before reproducing.**

## Step 2: Isolate

Narrow down the scope:
1. Read the file/function where the error occurs.
2. Trace the call chain: who calls this function? What data flows in?
3. Check recent changes: `git log --oneline -10 -- <file>` and `git diff HEAD~5 -- <file>`.
4. **If regression** (worked before, now broken): use `git bisect` to binary-search the breaking commit.
   - `git bisect start`, mark current as `bad`, mark last known good as `good`.
   - Automate with `git bisect run <test-command>` when a test captures the failure.
5. Check if the issue is in our code or a dependency (read node_modules source if needed).
6. Use `grep` to find all callers of the broken function.
7. For cascading failures: trace upstream — is this error a consequence of an earlier failure elsewhere? Fix the origin, not the symptom.

**One hypothesis at a time. Log each: Hypothesis → Prediction → Experiment result → Conclusion.**
Keeping a hypothesis log prevents retrying the same idea and creates an audit trail.

## Step 3: Root Cause

Identify WHY it fails, not just WHERE:
1. Read the actual values at the failure point (add temporary logging if needed).
2. Check types: is the data shaped as expected? Read the type definition.
3. Systematically check categories (Ishikawa):
   - **Code**: logic error, off-by-one, wrong operator, missing return.
   - **Data**: null, undefined, empty, malformed, unexpected shape.
   - **Timing**: race condition, unresolved promise, missing await, event ordering.
   - **Environment**: env vars, config, database state, OS differences.
   - **Dependencies**: version mismatch, breaking change, upstream bug.
   - **Config**: feature flags, build settings, routing rules.
4. Consider **contributing factors** — complex bugs often have multiple causes (code + config + timing), not a single root cause. List all contributing factors found.

**State your confidence level:**
- HIGH: reproduced, traced to exact line, understood mechanism.
- MEDIUM: likely cause identified, but edge cases possible.
- LOW: multiple possible causes, need more investigation.

If LOW after 3 attempts → STOP and ask user for more context.

## Step 3.5: Write Failing Test (Red-Green)

**Before writing any fix code, capture the bug in a test:**
1. Write a test that reproduces the exact failure — it MUST fail on current code.
2. Run it to confirm it fails for the right reason (not a test setup error).
3. This test becomes your verification: when the fix is correct, this test turns green.
4. If you cannot write a failing test, explain why (UI-only, timing-dependent, etc.) and document the manual reproduction steps instead.

**Why before fix:** Confirms you understand the bug. Prevents fixing the wrong thing. The test stays as a permanent regression guard.

## Step 4: Fix

**Do NOT reach this step without stating root cause + confidence level first.**

Apply the minimal fix:
1. Fix ONLY the root cause. No refactoring, no "while I'm here" changes.
2. Match existing code style exactly.
3. Verify the fix doesn't break the function signature or return type.
4. Run typecheck → lint → test after the fix.

## Step 5: Verify No Regression

1. Run the originally failing test/scenario → now passes.
2. Add a regression test for this specific bug if one doesn't exist.
3. Run the full test suite — no new failures.
4. If frontend: verify in browser that the fix works AND nothing else broke.

## Anti-Hallucination Rules for Debugging

- Never claim "this must be the cause" without evidence from tool output.
- Never assume a variable's value — read it or log it.
- Never assume a function's behavior — read its source code.
- If a stack trace points to node_modules, read the actual dependency source.
- If the bug involves data, verify the actual data shape from DB/API, not from types alone.
- Don't fix symptoms. If adding a null check "fixes" it, investigate WHY it's null.

## Step 6: Post-Fix Retrospective

After confirming the fix works, answer these questions:
1. **Why did this escape?** — What test, review, or process should have caught this bug before it reached the user?
2. **Is the same pattern elsewhere?** — Grep for similar code that could have the same bug.
3. **What to improve?** — Should a lint rule, type constraint, or test category be added to prevent this class of bug?

For production incidents: document findings in a blameless post-incident note — focus on contributing factors and systemic improvements, not individual blame.
