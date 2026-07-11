---
source: framework
paths:
  - "**/service-worker*"
  - "**/sw.{js,ts}"
  - "**/workbox*"
  - "**/manifest.{json,webmanifest}"
---

## PWA / Offline-first

- Version service worker caches. Delete all previous versions in activate handler.
- Server-side version-check endpoint with no-cache headers. Force-reload on new version.
- Scope cached user data by user ID. Purge all user caches on logout.
- Offline mutation queue with conflict resolution and retry on reconnect.
