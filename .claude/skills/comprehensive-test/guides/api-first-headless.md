# Test: API-First / Headless

## VERSIONING
- Max 2 concurrent versions (current+previous), both tested in CI every commit
- Test mechanisms: URL path(`/v1/`), header(`X-API-Version`), content negotiation
- No version specified → documented default
- Deprecated → `Sunset`+`Deprecation` headers; removed → 410 Gone (not 404)
- v1 behavior unchanged when v2 deployed (version-comparison tests)
- 6-12 month migration window with deprecation warnings

## GRAPHQL
- Test operations (queries/mutations/subscriptions), not endpoints
- Response shape matches selection set exactly (no more/less)
- N+1: DataLoader used, monitor query count in integration tests
- Field-level auth: restricted field for unauth user → clear error (not null)
- Depth+complexity limits: reject before execution
- Subscription: subscribe→initial→updates→cleanup→reconnection
- Schema compat: adding safe, removing/renaming breaking → GraphQL Inspector in CI

Tools: Apollo testing utils, GraphQL Code Generator, GraphQL Inspector, Schemathesis

## REST
- Full method matrix per resource: GET(list)/GET(single)/POST/PUT/PATCH/DELETE
- Status: 200/201/204/400/401/403/404/409/422
- Idempotency: PUT+DELETE idempotent (DELETE 2x→204 then 404)
- Content negotiation: JSON+XML; unsupported→406
- HATEOAS: valid resolvable URLs with correct `rel`
- PATCH single field → others unchanged (verify with GET)
- Contract: Pact/Spring Cloud Contract consumer-driven
- API tests 10-50x faster than UI → push validation to API layer

## OPENAPI TESTING
- Validate every response vs spec in CI (spec=contract, not just docs)
- `spectral lint` every change
- Schemathesis: auto-generate hundreds of edge-case requests from spec
- Dredd vs live endpoint: zero discrepancies
- Spec alongside code (same repo, same PR)
- CI blocks: invalid spec / undocumented endpoints / unexpected shapes

Tools: Spectral, Dredd, Schemathesis, Prism, oasdiff

## WEBHOOKS
**3 pillars:** 1.Delivery within expected window 2.Retry: 5xx/timeout→backoff+jitter(1,2,4,8,16s, cap 5) 3.Idempotency: same event 2x→processed 1x

- Verify signatures (HMAC-SHA256); tampered payload→reject
- Respond 200 immediately, enqueue processing (<5s response)
- Handle out-of-order via timestamps/sequence (not arrival order)
- Local: ngrok/smee.io for dev+CI
- Processed event IDs stored with TTL (~72h)

## RATE LIMITING
- Exact threshold: N requests OK, N+1→429
- Headers: `X-RateLimit-Limit/Remaining/Reset` accurate
- `Retry-After` valid; waiting+retry succeeds
- Window reset→counter zero
- Per-client isolation: A limit≠affect B
- Mock limits in CI (never hit real limits)
- Distributed: enforced globally across all instances

## HATEOAS
- Links: valid `rel`, correct HTTP methods, resolvable URLs
- State-dependent: draft→has `submit` not `cancel-shipment`
- Navigate from API root(`/`) to any resource by following links alone
- Semantics: `self`→current, `next/prev`→pagination
- Automate link chain traversal for complete user journeys

## BACKWARD COMPAT
**Breaking:** remove endpoint/field, change type, optional→required
**Non-breaking:** add endpoint/field/enum value

- `oasdiff`/Specmatic detect breaking changes in CI → fail without version bump
- Consumer contracts (Pact): provider verifies all in CI
- Golden file snapshots: freeze payloads, diff every build
- Never remove/rename without 6+ month deprecation
- Test previous FE/mobile version against new API

## ERROR HANDLING (RFC 7807)
```json
{"type":"uri","title":"...","status":422,"detail":"...","instance":"/path"}
```
- Consistent structure ALL endpoints — validate every 4xx/5xx
- Every code path: 400/401/403/404/409/422/429/500
- `type` URIs resolvable with useful docs
- Never leak internals (stack traces/DB queries/file paths)
- `application/problem+json` with graceful fallback
- 422: identify which field failed and why (not generic)
