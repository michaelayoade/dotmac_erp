# ADR-0012: Invoice synchronization failures are durable outcomes

**Status:** Proposed  
**Date:** 2026-09-06  
**Decider:** Finance and platform approval pending

## Context

ERP currently treats every Self-Care invoice that cannot be translated into an
accounting document as a transient exception. The incremental cursor then stops
at the failed row and every scheduled run retries the same unchanged document.
Missing ERP sales-tax configuration and contradictory source tax totals have
therefore produced hundreds of thousands of repeated failures without adding
new diagnostic evidence.

The transport, the source document, and ERP accounting policy have different
owners. Self-Care owns the issued invoice header, lines, tax and discount facts.
ERP owns account and tax-code mappings and the accounting entries it posts.
Neither system may silently repair or reinterpret the other's facts.

A source document can be reachable and well-formed while still being unsuitable
for posting. Treating that outcome like a network failure makes the cursor a
retry queue, hides recurrence behind duplicate logs, and prevents later valid
documents from being observed.

## Decision

Self-Care exposes a versioned, typed accounting projection. Each invoice
revision has a stable source identity, `updated_at` revision, source kind,
disposition, normalized issue codes and numeric evidence. It never exposes ERP
account mappings and ERP never writes back a guessed correction.

ERP records every consumed projection as a durable outcome before advancing its
cursor. The durable identity is organization, source invoice and source
revision. The record also carries the contract version and a normalized
projection fingerprint:

- `ready` permits the existing finance posting owner to process the document;
- `blocked` is a successful observation with durable issue evidence and never
  posts accounting entries;
- `not_applicable` is a successful observation that never posts entries.

An exact replay increments recurrence on the existing outcome. A different
fingerprint for the same source revision is a source-contract contradiction and
fails closed. A later `ready` revision resolves older blocked evidence without
deleting it.

Only transport, authentication, database and malformed-contract failures abort
the run without cursor advancement. A blocked document does not. Automatic
retry occurs only when Self-Care publishes a later `updated_at` revision.
Explicit `invoice_id` replay is allowed for operator validation, but it neither
rewinds the global cursor nor deletes prior evidence.

Outcome and issue tables are tenant scoped, protected by forced row-level
security, and contain only typed identifiers, issue codes and numeric evidence.
They do not store customer names, invoice memos or arbitrary source payloads.

The attempt limit counts all attempted documents, including blocked and failed
ones. Discounts remain blocked until an approved allocation contract exists.
No reconciliation tolerance is loosened to make a document postable.

## Consequences

- Persistent data or configuration defects produce one durable outcome plus a
  recurrence count instead of an unbounded exception storm.
- The cursor can continue past observed blocked documents while retaining an
  auditable remediation queue.
- Operators can distinguish source-data contradictions, missing ERP mappings
  and infrastructure failures without reading free-form logs.
- The change requires additive ERP persistence and an additive Self-Care API.
  Immutable tax snapshots require their own expand/backfill/cutover sequence.
- Posting remains behind the existing finance service and its RBAC, tenant,
  idempotency and balancing controls.

## Alternatives rejected

### Keep exceptions and freeze the watermark

This guarantees retries but turns one persistent document defect into a system
wide backlog and repeated load. It records no durable evidence.

### Skip invalid invoices without storing an outcome

This lets the cursor move but destroys the audit trail and makes missing
accounting entries indistinguishable from successful synchronization.

### Let ERP mutate Self-Care invoices or infer missing tax facts

This violates source ownership and can change customer billing evidence to fit
an accounting configuration.

### Loosen reconciliation tolerances

This can post entries that do not equal the issued invoice and obscures the
underlying defect.

### Key outcomes only by invoice identifier

This overwrites history and cannot distinguish a corrected source revision from
an exact replay.

### Rewind the global cursor for targeted replay

This reprocesses unrelated documents and recreates the load amplification that
the compound cursor was introduced to prevent.
