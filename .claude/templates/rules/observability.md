---
source: framework
---

## Observability (OTel / Sentry / Datadog)

- Structured tracing (OpenTelemetry spans), not `console.log`. Traces are searchable, logs are not.
- Propagate trace context (`traceparent` header) across service boundaries. Without it, distributed traces break.
- Alert on error rate spikes and P99 latency, not just availability.
- Capture context in error reports: user_id, request_id, relevant state. Stack trace alone is useless.
- Use vendor-neutral OTel instrumentation. Avoid vendor-specific SDKs for portability.
