# Invoice quarantine IO boundary

The invoice consumer commits already accepted row work before requesting remote
v2 quarantine evidence. The compound cursor deliberately stays unchanged at
these checkpoints: a later unpositioned parse failure still freezes the run's
original cursor. Interrupted work can therefore replay committed rows/outcomes
through existing idempotency checks, but cannot skip missing evidence.

Remote evidence is fetched and validated without an open ORM transaction. Tenant
scope is re-armed before a fresh savepoint persists the outcome. Final cursor
advancement never commits ahead of its supporting evidence. Intermediate
checkpoints may make evidence durable before the cursor, which is safe replay,
not an assertion that an entire sync phase is atomic.

The existing 50-quarantine limit remains. A cumulative 120-second remote evidence
budget is checked between requests, and an exhausted upstream rate limit ends
the batch rather than issuing another request for every remaining bad row.
An individual request still obeys the existing client's timeouts and Retry-After
policy. This does not guarantee an exact 120-second phase duration.

No source totals, fingerprints, blocked-revision conflicts or validation rules
are bypassed. Actual historical source discrepancies still need correction.
