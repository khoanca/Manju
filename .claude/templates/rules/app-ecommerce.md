---
source: framework
---

## E-commerce / Payments

- Idempotency key on every Stripe payment creation request.
- Verify Stripe webhook signatures using raw body BEFORE JSON parsing.
- Validate pricing/totals server-side. Never trust client-submitted amounts.
- Atomic DB operations for inventory decrement. No read-then-write without lock.
- Never embed sk_live_ keys in client code, VCS, or logs.
