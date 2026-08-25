# Durable imports adoption boundary

**Status:** `dotmac-imports` 0.1.0a2 released and the first adopter proven on
PostgreSQL; real-data shadow cutover pending  
**Mechanism owner:** `dotmac-imports`  
**Customer meaning owner:**
`app.services.finance.import_export.durable_customers`  
**Customer mutation owner:** `app.services.finance.ar.customer_service`  
**Stored-object owner:** `dotmac-files`

## Ownership

`dotmac-imports` owns only reusable mechanics: a tenant-scoped run, immutable
partition plan, atomic lease, resumable checkpoint, promotion and minimised
per-row outcome. It stores file identifiers, checksums, row fingerprints and
typed safe errors; it never stores source or mapped row values and never names
an ERP entity.

ERP owns the customer field vocabulary, transforms, validation, duplicate
policy and mutation. The vocabulary is one function shared by the retiring and
durable paths. A valid durable row reaches only the canonical customer service;
the adapter mutates and flushes but never commits, rolls back or constructs a
session.

`dotmac-files` owns immutable stored-object identity and lifecycle. ERP's
`DotmacFilesS3Provider` is a provider adapter over the existing MinIO owner. It
contains no database session or customer decision and replaces no existing
object.

## Transaction and worker phases

Each worker repeats three bounded phases:

1. Under `session_for_org`, atomically claim one pending or expired partition,
   authorize its file row, and commit the lease.
2. With no session, read the exact authorized object and verify its SHA-256,
   byte size and row count.
3. Under a new `session_for_org`, recheck the claim token, compare dry-run
   verdicts or apply through the customer owner, record minimised outcomes and
   advance the checkpoint in the same transaction.

A lost or expired claim refuses settlement. A provider failure leaves no
partial checkpoint. An unexpected domain exception rolls back the whole
partition; only typed `RowRejected` and `RowSkipped` outcomes may be persisted.

## Shadow and cutover

The durable route begins in dry-run mode. Before the module records a row
verdict, ERP runs the retiring `CustomerImporter` over the same raw row and
compares accepted, rejected and skipped status. Any mismatch aborts the
partition and leaves the lease available for explicit retry/repair.

Promotion creates exactly one apply run from a completed error-free dry run and
clones its immutable partition plan. Apply uses one worker until a database
constraint owns customer-name uniqueness. Parallel dry-run workers are safe
because partition claims are atomic and token-gated.

Retirement requires all of the following:

- the released a2 module and exact ERP lock pin — completed in this slice;
- fresh and predecessor-upgrade PostgreSQL proof of `mod_imports`, including
  `tenant_id NOT NULL`, ENABLE+FORCE RLS and the complete table manifest —
  completed in this slice;
- at least two concurrent dry-run claims proving disjoint ownership;
- real customer files with row-for-row clean shadow comparisons; and
- deletion of the legacy decoding/mapping/run loop with its caller baseline
  reduced in the same change.

XLS/XLSX, suppliers and other entity types remain outside this first slice.
They extend the same product-owned ports; they do not create another run
ledger, storage adapter or worker engine.
