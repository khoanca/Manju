---
source: framework
---

## CMS / Content

- Sanitize rich text HTML server-side with allowlist (DOMPurify) before storage.
- Validate uploads by magic bytes, not extension/Content-Type. Store outside web root.
- Canonical URL on every page. Draft/preview URLs get noindex or require auth.
- Explicit draft/review/publish state machine. Save never equals publish.
