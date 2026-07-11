---
name: deploy
description: Pre-deployment checklist and release workflow. Verify readiness before shipping.
when_to_use: When the user wants to deploy, release, or ship a version. Also when a wave is complete and ready to go live.
---

## Input/Output Contract

INPUT: `docs/project-state.md`, `docs/product-plan.md`, git diff vs main
OUTPUT: Pre-deploy checklist, release notes draft, updated `docs/project-state.md`
NEXT: Next wave or project complete
GATE: User must approve. Never auto-deploy.

## Step 1: Readiness Check

1. All wave features status = "Complete" in `docs/project-state.md`.
2. Verify against priority matrix in `docs/product-plan.md`.
3. Warn if any incomplete.
4. Run: typecheck (0 errors) → lint (0 errors) → test (all pass) → build (no warnings).

## Step 2: Code Audit

`git diff main...HEAD --stat` then check:
- [ ] No `.env`/secrets, `console.log`, debug statements.
- [ ] No TODO/FIXME/HACK, hardcoded localhost/dev config.
- [ ] No test-only code in production paths.
- [ ] All migrations have rollback. Deps pinned exact.
- [ ] No HIGH/CRITICAL in `npm audit`. No stale feature flags.
- [ ] Bundle size within budget.

## Step 3: Environment Verification

1. All env vars documented in `.env.example` and set in target environment.
2. Migrations ready, reversible, no destructive changes without 2-deploy rollout.
3. Third-party: API keys configured, rate limits adequate, webhooks pointing to production.

## Step 4: Release Notes

Draft: version (semver), features added, bugs fixed, breaking changes, migration steps.

## Step 5: Deploy Checklist

- [ ] Tests pass, build succeeds.
- [ ] No secrets in code. Env vars configured.
- [ ] Migrations ready. Rollback plan documented.
- [ ] Release notes reviewed. Monitoring in place.

**Never deploy without explicit user approval.**

## After Deployment

1. Smoke test critical paths (auth, core journey, payment).
2. **First 30 min monitoring:**
   - [ ] Error rate not increased vs baseline.
   - [ ] p95/p99 not degraded >20%.
   - [ ] No new error types. Health check 200 OK.
   - [ ] Key business metrics normal.
3. **Rollback triggers:** error rate >5%, p99 >2x baseline, health check fails 3x.
4. Update `docs/project-state.md`: phase 4 complete, release version.
5. Monitoring schedule: 30min active → 3x/day first 24h → daily first week.
