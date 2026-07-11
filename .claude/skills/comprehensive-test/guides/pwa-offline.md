# Test: PWA / Offline-First

## SERVICE WORKER
- Full lifecycle: register→install→activate→update
- `navigator.serviceWorker.ready` before assertions
- Playwright: `context.waitForEvent('serviceworker')` with `serviceWorkers:'allow'`
- Update: `skipWaiting()`+`clients.claim()` behavior, user notified, new version after reload
- Dedicated SW test suite separate from app tests

## OFFLINE
- Test on real devices (not just DevTools) — SW update breaks offline on real devices with cached state
- App shell loads offline: toggle offline→reload→nav chrome appears
- Pre-cache `offline.html` fallback (branded, not browser error)
- Playwright: `setOffline(true)` — kill at mid-load/mid-mutation/idle
- iOS: storage evicted ~7d inactivity unless persistent storage granted
- 3G separately from offline (timeout≠immediate refusal)

## CACHE
- Version caches; clean old in `activate`
- Strategy: cache-first(static) · network-first(dynamic) · stale-while-revalidate(semi-dynamic)
- Hit rate >80% static; <60% = over-aggressive invalidation
- Max entries+age via Workbox expiration; `navigator.storage.estimate()`

## BACKGROUND SYNC
- Queue mutations offline, drain when online (Background Sync API)
- Fallback for iOS Safari (NOT supported) — replay on SW startup
- IndexedDB: op type, payload, timestamp, idempotency key
- Drain FIFO; partial failure: failed items stay queued
- Test via Chrome DevTools Periodic Background Sync

## PUSH
- Full flow: permission→subscription→display(bg)→tap-to-open
- iOS 16.4+: works only when PWA installed (not browser tab)
- 3 states: foreground/background/closed
- Permission: granted/denied/dismissed
- Payload: title/body/icon/badge/action URL/data

## INSTALLABILITY
- Manifest: name, short_name, start_url, display(standalone), colors
- Icons: min 192+512px PNG; add 256/384/1024; SVG+maskable (center 80% safe zone)
- Install prompt: Android(auto) vs iOS(manual)
- HTTPS everywhere, no mixed content

## MUTATION QUEUE
- IndexedDB: URL, method, headers, body, timestamp, idempotency key
- Drain: retrieve FIFO→transmit(batch)→responses→resolution→update local→remove synced
- Clear ONLY after confirmed server processing
- Survive app restart/reload
- Partial fail: mutations 1-2 cleared, 3-5 remain if 3 fails

## CONFLICT RESOLUTION
- LWW (80% cases) or CRDTs (Yjs/Automerge for collab editing)
- Create conflicts: modify same record offline on 2 devices, bring both online
- Never silently discard data
- Server logical clocks/version vectors (not client timestamps — clock skew)
- Out-of-order replay must not corrupt state

## NETWORK SIMULATION
Playwright CDP `Network.emulateNetworkConditions` · Cypress `.intercept()` throttleKbps/delay

4 conditions: full offline · slow 3G · flaky (intermittent) · normal
Combined: start on 3G→offline mid-req→WiFi→verify completion

## PLATFORM DIFFS
| | Android Chrome | iOS Safari |
|---|---|---|
| Install | Auto prompt | Manual |
| Push | Full | 16.4+ installed only |
| Bg Sync | Yes | No |
| Storage | `requestPersistent()` | 7d eviction |
