# Test: Multi-Tenant SaaS

## TENANT ISOLATION
- Create data as A, switch to B → zero results. Run on every deploy.
- Test at every layer: DB(RLS), API middleware, gateway, cache, file storage, background jobs
- Grep queries missing `tenant_id` WHERE — bg jobs+admin endpoints are top leakage vectors
- IDOR: every endpoint with resource ID, access with different tenant → 403/404
- Cache keys include tenant_id; file paths tenant-scoped
- Concurrent cross-tenant tests for race conditions in tenant context

## RLS (Row-Level Security)
- Always test as non-superuser (superusers bypass RLS)
- pgTAP in CI — RLS regressions are silent (wrong data, no error)
- `SET LOCAL app.current_tenant` → verify SELECT/INSERT/UPDATE/DELETE respect policy
- INSERT mismatched tenant_id → RLS violation error
- App never connects as superuser/BYPASSRLS role
- Test with prod-scale data (performance assertions)

## AUTHORIZATION
- Tenant-scoped roles: "admin in THIS tenant?" not just "is admin?"
- Admin in A → zero elevated privileges in B
- RBAC+ABAC combination testing
- Service accounts/API keys scoped to single tenant
- Auth outage → default DENY not ALLOW
- JWT tenant claim tampering → reject
- Permission cache invalidation within SLA after role change

## CONFIGURATION
- Feature flag: enable A, disable B → verify in same test run
- Change for A → B completely unchanged
- New tenant zero config → all defaults work
- New config key → existing tenants get correct default
- Canary rollout: small cohort → validate → wider

## CROSS-TENANT ACCESS
- Systematic IDOR: own resource / other tenant's / modified tenant header
- URL path tenant GUID tampering
- Search/listing/autocomplete/export/report isolation
- Admin tool isolation
- GraphQL/ORM eager loading respects tenant boundaries

## ONBOARDING/OFFBOARDING
**On:** E2E smoke (DB+RLS+config+auth+CRUD) · idempotent (run 2x, no fail/dupes) · tier-based provisioning · failure rollback (no orphans)

**Off:** All data removed, files purged, cache evicted, integrations disconnected, keys revoked · deleting A≠affect B · re-onboard same domain→no data resurrection

## BILLING PER TENANT
- Tier enforcement: every feature gate per tier
- Metering accuracy: 1000 calls → meter records 1000
- Upgrade→immediate access; downgrade→restriction
- Overage: hard block/soft warn/auto-bill per config
- Mid-cycle proration
- Webhook idempotency (same event 2x → no double-charge)
- Grace period after payment failure

## RATE LIMITING
- **A hitting limit does NOT affect B** (most important test)
- Test through prod path (LB/gateway)
- Headers: `X-RateLimit-Limit/Remaining/Reset`, `Retry-After`
- Tier-based limits (Free 100/Pro 1K/Enterprise 10K per min)
- Distributed consistency across all gateway instances
- Reset → immediate full throughput

## DB MIGRATION
- Test with prod-scale data (2s dev=2h prod)
- Expand-contract: add new→migrate→update app→remove old
- `lock_timeout` on migrations; `CONCURRENTLY` for indexes
- Backfill in batches, never single txn
- Verify new RLS policy BEFORE dropping old (pgTAP)
- Every migration has tested rollback; never modify committed
- Canary: single-tenant DB first

## NOISY NEIGHBOR
- 1 tenant 50K req/min → others P99 within SLA
- Connection pool quotas: A exhaust≠starve B
- Query timeout per tier (Enterprise 60s, Basic 15s)
- CPU/memory limits: OOM kills tenant process not node
- Per-tenant job concurrency limits
- Cascade failure: kill A's resources→others unaffected
- Observability: filter metrics/logs/traces by tenant_id

## WHITE-LABEL
- A branding never appears for B
- Subdomain routing correct; unknown→404
- Email/PDF/SMS use correct tenant branding+sender
- Custom CSS sanitized for XSS
- Disabled modules completely inaccessible
- Locale per tenant, no bleed
- Custom domain SSL provisioning+renewal
