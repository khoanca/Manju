---
source: framework
---

## Multi-tenant SaaS

- Enforce tenant_id at DB layer (RLS or ORM global scope). Never rely on app-code WHERE.
- Include tenant_id in every cache key, queue job, file path, and log entry.
- Propagate tenant context explicitly in background job payloads. Never derive from session.
- Never trust client-supplied tenant identifiers. Derive from authenticated token.
