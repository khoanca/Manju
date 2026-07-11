# Test: Real-Time / WebSocket / Chat

## TESTING LAYERS
**Unit:** Mock server (`vitest-websocket-mock`/`mock-socket`) · validate serialization/schema/event logic
**Integration:** Real `ws` + dynamic port · full lifecycle: handshake→auth→exchange→heartbeat→close · verify `readyState===OPEN`, close codes 1000/1006
**E2E:** Playwright · multi-tab, cross-browser, full flows
**Protocol:** Autobahn Testsuite for RFC 6455

## CHAT DELIVERY
- Send text/emoji/GIF/file → receipt on ALL clients
- Delivery indicators: sent→delivered→read
- Unread count accuracy across reconnections
- Persist: disconnect→reconnect→history loads
- Profanity filter + injection prevention on content

## ORDERING & DEDUP
**Order:** Monotonic sequence numbers per channel · client detects gaps→request replay · inject out-of-order→verify reorder before display

**Dedup:** Track recent message IDs, ignore dupes · sequence: reject msg≤current high-water · idempotency key+atomic storage · "effectively exactly-once"=at-least-once+idempotent · send 3x same key→effect 1x

**Cross-channel:** Same notif via WS AND push→display once · `BroadcastChannel` for cross-tab dedup

## PRESENCE

| Scenario | Expected |
|---|---|
| Open app | Online <1s, broadcast |
| Close tab | Offline after 30s heartbeat timeout |
| Network drop | Offline after timeout, no premature flash |
| Multi-device | Online if ANY connected |
| 10K users | Batched updates, no storm |

## TYPING INDICATORS
- Debounce: fire max 1x per 2-3s · timeout: clear after 5s idle · send msg→clear immediately · close tab→clear (no ghost) · 50 typing→"N people typing..."

## CONNECTION RESILIENCE
**Backoff:** base 500ms, 2x, cap 30s, jitter 0-1000ms, max 10-15 attempts

**Scenarios:** Server down→increasing delays · WiFi→cellular→reconnect · sleep/wake→detect dead+reconnect · rolling restart→graceful handoff · max retry boundary→show error, stop

**Session:** Server issues session ID→client presents on reconnect→any server restores state

**Queue during disconnect:** Outbound: queue locally+ack tracking, retry unacked on reconnect · Inbound: server buffers N msgs+TTL, replays on reconnect · disconnect mid-msg→no loss, no dupes

## COLLABORATION (CRDT/OT)
- 2 users edit same content simultaneously
- CRDT: all replicas converge identical state regardless of op order
- OT: transform functions correct for all op pairs
- Simulate N clients random ops→sync→verify identical
- Figma: byte-by-byte comparison, 400K+ validations before rollout, <1s data loss target
- Cursors: 30 FPS updates, cap (200 visible), cleanup on disconnect

## SOCKET.IO
```js
// Setup: createServer→new Server→listen→dynamic port→ioc connect→io.on("connection")→done
// Teardown: io.close(); clientSocket.disconnect();
```
Test: client↔server emission · ack callbacks · room broadcasting · namespace isolation · middleware/auth rejection · disconnect/reconnect · binary data

## SSE
Verify format: `data:`/`event:`/`id:`/`retry:` · auto-reconnect with `Last-Event-ID` · client respects `retry:` directive · named vs default `message` events · connection drop+re-establish

## NOTIFICATIONS
**Tiers:** Critical=WS AND push(never drop) · Important=WS first, push fallback after delay · Info=WS only, skip/batch offline

**Tests:** User active→WS only, no push · offline→push+inbox · back online→sync, no dupes · cross-tab→BroadcastChannel · fan-out 1K+ users within SLA

## LOAD TESTING
**Targets:** Connection<500ms · round-trip<100ms · loss=0% critical · memory ~64KB/conn

**Scenarios:** Auth→subscribe→send with 5-10s intervals · `ramping-vus` · `checks:['rate>0.9']` · single server: 10K-100K conns in ~64MB

**Backpressure:** `ws.bufferedAmount` throttle at 1MB · slow-consumer scenarios · server-side per-conn buffer limits+TTL

**Chaos:** Inject latency/packet loss/partition/crash → verify reconnection handles each · Chaos Mesh/Gremlin/Toxiproxy
