# Test: E-Commerce & Payments

## STRIPE TEST CARDS
`4242424242424242` success · `4000000000000002` decline · `4000000000009995` insufficient · `4000000000009987` lost · `4000000000009979` stolen · `4000000000003220` 3DS required · `4000002760003184` 3DS challenge · `4100000000000019` Radar blocked · `4000000000004954` highest risk

## GATEWAY
- Sandbox only, never prod creds in tests
- Every outcome: success, expired, wrong CVV, insufficient, timeout, retry
- Soft decline (retryable) vs hard decline (permanent) - both paths
- Kill API mid-checkout, drop network, force timeout → graceful retry+log
- 3DS/SCA E2E: redirect→OTP→return→cancel mid-auth
- Ambiguous response (timeout no result) → order state consistent
- International cards (40+ countries) multi-currency

## WEBHOOKS
**Signature:** `stripe.webhooks.constructEvent` only · `express.raw()` on webhook route · secrets in env vars · invalid sig→400

**Idempotency:** Store `event.id` UNIQUE · idempotency record+business logic same DB transaction · `processing`→`completed`/`failed` status

**Retry:** Stripe waits 10s for 2xx · events NOT chronological - state machine tolerant of any order · transient→5xx(retry) vs permanent→200+DLQ

**Processing:** Verify sig→enqueue→return 200 immediately. Never inline emails/ERP.

**Local:** `stripe listen --forward-to localhost:3000/webhooks` · `stripe trigger checkout.session.completed` · recovery script via `stripe.events.list()`

## CART
- Add/remove/modify qty, cart icon correct count
- Persist across sessions (logout/login)
- Empty after successful order
- Validate UI AND API level (prevent price manipulation)
- Last-item race: 2 users buy last → exactly 1 success
- Qty > stock → cap or error; same item → increment not duplicate
- Mixed: physical+digital, taxable+exempt, different shipping weights

## CHECKOUT
- E2E: cart→shipping→method→payment→review→confirmation
- Navigate back → data preserved; browser back button; page refresh → state preserved
- Double-click "Place Order" → 1 order only (idempotency)
- Payment fail → retry without re-entering info
- Guest vs authenticated separately
- Abandoned cart persists, triggers recovery emails; session expires → inventory released

## INVENTORY
**Concurrency:** 2 users buy last item → 1 success + 1 out-of-stock · pessimistic lock `SELECT FOR UPDATE` · optimistic lock (version field) · distributed lock Redis SETNX+TTL · concurrency tests in CI continuously

**Reservation:** Separate available/reserved qty · checkout→atomic decrement available+increment reserved · pay success→committed · pay fail→release · timeout auto-release 5-15min · idempotent ops (retry≠duplicate)

## PRICE CALCULATION
**Money:** Never float → integer cents or BigNumber · full precision intermediates, round only final · Banker's Rounding HALF_EVEN · currency units: USD/EUR=2, JPY=0, KWD=3, CHF→0.05

**Tax:** Exclusive: `subtotal*rate=tax` · Inclusive: `total*(1-1/(1+rate))=tax` · discount-then-tax≠tax-then-discount (document which) · round per-line then verify sum · jurisdiction rules (GST/VAT/US state)

**Discount:** %, fixed, BXGY, tiered, stacked · boundaries: 0%, 100%, exceeds price · coupon expiry/limits/min-purchase/product-specific

## ORDER LIFECYCLE
**States:** Created→Approved→Allocated→Picked→Packed→Shipped→Delivered · post: Delivered→Return Requested→Received→Refund · cancel at every stage (inventory release/return flow) · invalid transitions rejected

**Refunds:** Full+partial, correct in system AND gateway · refund+return→stock replenished · refunded charges can still be disputed

**Fulfillment:** Split (multi-warehouse) · failure handling · tracking generated+emails sent · authorize-then-capture: capture only after fulfillment

**Disputes:** Creation→evidence→resolution · test webhooks `charge.dispute.*` · inquiry vs dispute (inquiry≠immediate withdrawal)

## PCI DSS 4.0
- Req3: never store PAN unencrypted, AES-256, tokenization
- Req4: TLS 1.2+ all cardholder connections
- Req6.4.3(NEW): inventory ALL JS on payment pages, each authorized
- Req8: MFA admin, 12-char min passwords
- Req10: SIEM all CDE access, 1yr retention, 90d quick access
- Req11: quarterly vuln scans, annual pentest
- Req11.6.1(NEW): monitor payment pages for unauthorized mods (weekly min)
- No card details in logs ever; prod errors never expose card data

## FRAUD
**Velocity:** Rapid same IP/email/card → flag/block · card testing (rapid small amounts) → block after N · multi-dimensional: IP, email, card, device, address

**AVS/CVV:** AVS full/partial/no match → define actions · CVV mismatch → reject · compound signal AVS+CVV

**Patterns:** Geo anomaly (billing≠IP≠shipping country) · same device multiple cards · unusual amount/time/address changes

## SUBSCRIPTIONS
**Lifecycle:** Signup→trial→activate→renew→upgrade→downgrade→cancel→reactivate · trial-to-paid anchor reset · free($0) and paid trials · trial ending email 3d before

**Upgrade/Downgrade:** Mid-cycle proration (charge/credit) · monthly↔annual with proration · add/remove items proration

**Dunning:** Payment fail → retry schedule (3,5,7d) · grace period (3-7d access) · all retries fail → cancel+revoke+notify · card expiry → prompt update · concurrent plan changes → only 1 applied
