---
source: framework
---

## Dashboard / Data-heavy

- Virtualize tables/lists > 100 rows.
- Large exports via server-side streaming with chunked transfer. Never build in browser.
- Cursor-based pagination with enforced max page size.
- Release DB connections in finally. Alert when pool waiting connections spike.
