# Durable imports adoption boundary

**Status:** ERP source adoption of `dotmac-imports` 0.2.0a1 for native customer
CSV preview and mapping inference. The prior durable adopter was proven on
PostgreSQL; real-data shadow cutover remains pending.

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

## Native release and ERP adoption

The published release is `dotmac-imports` **0.2.0a1**. Its tag
`dotmac-imports-v0.2.0a1` has object
`e850ff3bef530db272dccae44b15e51e4c390aaf`, peeling to commit
`008ccaeaebda6855ba34b8e70740e3859954a37b`. Release run **38726218479**
succeeded ([release verification](https://github.com/michaelayoade/dotmac_starter_mt/actions/runs/38726218479)). The prebuilt wheel is
`dotmac_imports-0.2.0a1-cp312-cp312-manylinux_2_35_x86_64.whl`, with SHA-256
`2ca848468f74547a4b050f2c039ecaa2ea53458e880b3de09f3c4f55d9772b17`.

The supported prebuilt target is Linux x86_64, CPython 3.12, compatible with
Debian Bookworm. A CPython 3.11 source build requires Rust 1.90; the published
wheel does not serve that interpreter. ERP trusted-bootstrap PR #714 merged as
`f51c454697aabe53be94d912803b5a31418811bd`. The
[trusted lock run 38738243662](https://github.com/michaelayoade/dotmac_erp/actions/runs/38738243662)
resolved that exact commit and passed acquisition, resolution and attestation.
The adopted `pyproject.toml` and `poetry.lock` are the unchanged pair from its
`erp-lock-0.2.0a1` artifact, with pair binding
`375f03590ce3bcdfe151c9271cbfb593b997172231026f3f8b42d83333cadc25`.
The ZIP SHA-256 is
`b7fa5084d15416e8508b220252dd8518c5ddfae379b3426ca3d3f82b83f7f1b0`.

The ERP canary exercises `prepare_customer_import` with the installed
wheel's `_native` loaded by `ExtensionFileLoader` from site-packages. It wraps
the actual `normalize_rows` function and delegates to the original, checks the
customer preview and inferred mapping, and checks that preparation makes no
database or customer-writer calls. Required hosted checks also verify the
product manifest against the installed assembly and the composition
observations against their pinned contract.

This native path normalizes decoded rows only in the first durable customer
CSV partition used for preview and mapping inference. Initial source
partitioning already trims values in Python. This adoption establishes no
native acceleration of initial partition trimming or apply workers, and no
throughput claim. The composed imports migration head remains
`im_0001_import_runs`; the tenant, storage and customer ownership boundaries
above continue to apply.

Rollback restores the whole prior dependency/lock/adoption commit together,
including its matching consumer contract tests. A package-only downgrade with
the new lock or adoption assertions retained is not a coherent rollback. The
rollback adds no runtime fallback and changes no stored customer or import
ledger data.

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
