---
source: framework
---

## Real-time (WebSocket/SSE/Chat)

- Remove all listeners/timers/subscriptions in both close and error handlers.
- Max-size limit on per-connection message buffers. Evict oldest on overflow.
- Reconnect with exponential backoff + jitter. Max 10-15 retries.
- Track message sequence numbers. Sync missed messages on reconnect.
- Authenticate at handshake. Implement token renewal for long-lived sessions.
