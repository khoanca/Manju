# Test: Legal / Compliance (LegalTech)

## DOCUMENT GENERATION
- Template all variable permutations — conditional clauses by jurisdiction/party type
- Output fidelity: Word/PDF formatting, page breaks, headers, footers, numbering
- Boundary: special chars in names, long clauses, empty optional fields
- Clause library: insert/remove/reorder → valid docs, no orphaned refs
- Concurrent gen: no data bleed between parallel generations
- AI-suggested clauses from approved library; flagged when deviating

## COMPLIANCE RULE ENGINE
- Rule matrix: every rule with conditions, outcomes, edge cases
- Each rule isolated, then in combination (interactions produce unexpected results)
- **Deterministic:** identical inputs→identical results (audit requirement)
- Versioning: old version→historical data, new→from effective date
- Automated regression every deploy (85% error reduction with automation)
- Every execution→audit trail: input, rule applied, output

## AUDIT TRAIL
- **Immutable:** never alterable/deletable even by admin — test DB manipulation/API bypass/admin tools
- Complete: every CRUD on sensitive data → who/what/when/where/why
- Storage full → fail safely (block ops) or rotate without loss
- Timestamps: clock changes, timezone crossing, DST
- Query+export: filter/search in human+machine-parsable formats
- Retention: regulatory requirement (e.g. 7yr financial), accessible+readable long-term

## DATA RETENTION (GDPR/CCPA)
- Data map: every category with retention period, legal basis, deletion method
- Automated deletion: actually purged (not soft-delete) at expiry — all layers (DB/cache/backup/search/logs)
- Right-to-erasure E2E: request→verify identity→locate→delete→confirm (GDPR 1-month)
- Backup: deleted data doesn't reappear on restore
- Anonymization: cannot re-identify via combination
- Synthetic/masked data in ALL test envs, never real PII
- Consent withdrawal → dependent processing stops immediately

## DOCUMENT ACCESS (RBAC)
- Role-access matrix: partner/associate/paralegal/client/external counsel × view/edit/download/share/delete
- Positive paths (works) AND negative paths (denied with proper error)
- Role transition: access updates immediately, no stale permissions
- Multi-tenancy: cross-org impossible (API manipulation/URL guessing)
- Privilege escalation: param tampering/direct API/client-side state
- Purpose-based PII access

## E-SIGNATURES
- Cryptographic integrity: any post-signature modification detected
- Cert chain: full to trusted CA + CRL/OCSP revocation
- Algorithm: SHA-256+ required; reject weaker
- eIDAS: EU Commission eSignature validation test cases
- Multi-party: sequential/parallel/counter-sign/delegation
- LTV: verifiable years after signing, even after cert expiry

## LEGAL WORKFLOW
- Every path: happy/reject-at-each-stage/escalation/delegation/timeout-auto-escalation/recall
- Only latest version enters workflow
- Concurrent approvals: race conditions handled gracefully
- Notifications at each stage: email/in-app/integrated
- SLA: deadlines→escalation/reminders, overdue in dashboards
- Audit: every transition→actor/action/timestamp/document version

## REGULATORY AUTOMATION
- Compliance-as-code: requirements→automated tests in CI/CD
- Each test mapped to regulation (GDPR Art.17, CCPA §1798.105) for traceability
- Update tests when regulations change
- EU AI Act (Aug 2026): legal AI=high-risk → transparency/human oversight/risk mgmt
- Automated compliance reports with evidence artifacts
- Cross-jurisdictional: different rules per jurisdiction

## PRIVACY
- Masked data usable but doesn't expose individuals; re-identification risk analysis
- Synthetic data mirrors real patterns without real PII
- PII detection: free-text/attachments/metadata (not just structured)
- Data minimization: only necessary per purpose
- Cross-system PII flow tracking (caches/logs/analytics/3rd-party) → controls at every point
- Anonymization irreversible

## VERSION CONTROL
- Full lifecycle: v1.0→v1.1→v2.0→archive
- Diff accuracy: additions/deletions/modifications/formatting
- Rollback creates new version (not overwrite); restored=identical to original
- Concurrent edit conflict: detect→resolve→no silent data loss
- Metadata: author/timestamp/description/approval per version, cannot retroactively alter
- Long-term: docs from years ago openable+readable, history intact

## KEY REGULATIONS
GDPR(EU): erasure 1mo, portability, consent, DPIAs · CCPA(CA): opt-out, deletion, non-discrimination · eIDAS(EU): e-sig validation, cross-border · EU AI Act: high-risk legal AI, transparency · SOX(US public): financial integrity, audit · HIPAA(US health): PHI, access controls
