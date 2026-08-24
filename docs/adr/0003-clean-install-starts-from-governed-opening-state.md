# ADR-0003 — The composable ERP starts from governed opening state

- **Status:** Accepted
- **Date:** 2026-08-24
- **Decider:** Michael
- **Scope:** ERP composition, clean-instance bootstrap, accounting history and
  final cutover
- **Related:** `docs/architecture/accounting-adoption-boundary.md`,
  `docs/architecture/accounting-gate-d-plan.md`,
  `docs/SOT_RELATIONSHIP_MAP.md`

## Context

The original Accounting gate D plan proposed replaying every legacy posted
journal into `dotmac-accounting` and proving exact period-by-period parity.
Read-only forensics then established that the legacy database contains both
known defects and unresolved conflicts between its general-ledger detail,
audited controls and missing subledger schedules. Replaying that history would
make the new module a more structured copy of the same uncertainty. Restoring
the whole production database into a fresh server would do the same thing.

The product goal is now stronger than replacing one GL implementation: ERP is
to become a composable assembly in which published modules own domain
decisions, while a fresh installation becomes the eventual production target.
Historical data cleanup is deferred. It must not become a prerequisite for
finishing the code boundary, and it must not be smuggled into the clean system
as an unreviewed bulk copy.

## Decision

**The composable ERP starts from governed opening state. It does not import or
replay the legacy transaction history.**

The existing production ERP remains the system of record for all pre-cutover
history. At final cutover it is frozen against business writes, preserved with
verified backups and retained as a read-only historical archive. The clean ERP
becomes authoritative only for state explicitly admitted at cutover and for
transactions created after that instant.

### What may enter the clean installation

Only four classes of data may cross, each through a typed, idempotent product
adapter with source identity, a content fingerprint and reconciliation
evidence:

1. **Reconciled masters and configuration** needed to operate after cutover,
   such as organizations, users, parties, products, warehouses, chart of
   accounts and fiscal calendar. Duplicate, inactive and conflicting identities
   are resolved before admission; database rows are not copied wholesale.
2. **Open operational items** that still require action after cutover, such as
   unpaid invoices, unapplied receipts, open purchase orders, active employment
   obligations and inventory on hand. Each owning domain defines its own
   admissible state and proves its control total.
3. **Approved opening accounting state** at one named cutover instant. Finance
   supplies the signed trial balance and the subsidiary schedules needed to
   support control accounts. Accounting records the opening through its normal
   balanced journal contract. A balancing plug, inferred retained earnings or
   a raw SQL load is forbidden.
4. **Continuity identities** whose sequence or external correlation must survive
   the boundary. The owning service defines the mapping and collision proof;
   a legacy surrogate database key is not automatically a business identity.

Every other legacy row stays in the historical system. In particular, legacy
`gl.journal_entry`, `gl.journal_entry_line`, `gl.posted_ledger_line` and posting
batch history are never loaded into the clean instance.

### What “100% composable” means

The new installation is accepted as composable only when:

- each business decision and state transition has one named module or retained
  ERP owner;
- ERP routes, tasks, jobs, webhooks and commands are adapters over the owner's
  published contract, not parallel writers;
- every stateful module owns its namespace and migration lineage in the ERP
  database, with the assembly supplying only explicit prerequisite bindings;
- module-to-module behaviour is composed by the ERP assembly through typed
  ports; modules do not import each other or ERP internals;
- each derived cache or report has one canonical reconciler from authoritative
  inputs;
- the legacy writer and caller ratchets reach their declared terminal states;
  a disabled fallback writer is not accepted as a second owner; and
- a fresh database can be migrated, bootstrapped and exercised without reading
  the legacy database at runtime.

This definition allows explicitly retained ERP-owned decisions. “Composable”
does not mean that every line of application code moves into a package; it
means that ownership is singular, declared and enforceable.

### Cutover evidence

Before traffic moves, a disposable clean instance must prove:

- migrations from an empty database reach the exact composed heads;
- a synthetic second tenant remains isolated throughout bootstrap and replay;
- every admitted data class reproduces its signed count, amount and digest;
- each module cutover canary passes and every legacy writer/caller row has
  reached its terminal disposition;
- the bootstrap is resumable and a second identical run is a no-op;
- the new books balance exactly at the opening instant; and
- restore, rollback and read-only legacy access have been rehearsed.

Production correction of old accounting defects is no longer a composition
gate. Finance approval of the opening trial balance and supporting schedules is
still a final cutover gate: a clean database cannot decide what the opening
position should be.

Starter ADR-0031's current sealing mechanism assumes the retiring and new
authorities can be observed, locked and switched in one database transaction.
That is not true across the independent legacy and clean databases. Final
traffic cutover is therefore blocked until an accepted decision defines the
cross-database write fence, final evidence instant, failure recovery and switch.
This ADR authorizes the clean-instance programme; it does not weaken that
unresolved sealing invariant.

## Consequences

- The existing historical backfill extractor remains read-only evidence. It has
  no load mode and cannot become the clean-instance bootstrap path.
- Accounting gate D changes from historical journal replay to clean bootstrap
  contracts, behavioural rehearsal and opening-state validation.
- Production dual-write is unnecessary for legacy history. Behavioural parity
  is proved on isolated instances using the same accepted inputs before callers
  are repointed.
- The known bank-fee, composite-reversal, micro-balance and APPROVED-backlog
  populations remain in the legacy record. They do not contaminate the clean
  books. Any item that must remain operational after cutover needs an explicit
  migrate, complete-before-freeze or leave-in-archive disposition.
- The historical ERP is not a fallback writer. After cutover it is a separate,
  read-only record system with no route back into current domain state.

## Alternatives rejected

- **Replay all posted journals after correcting the known defects.** Rejected:
  the known defects are not the complete uncertainty, and the missing signed
  schedules make “corrected history” an accounting assertion Engineering
  cannot make.
- **Restore the full production database into a clean deployment.** Rejected:
  this changes infrastructure, not data quality or authority, and imports the
  same mistakes and legacy ownership paths.
- **Import all history but mark questionable rows.** Rejected: the module would
  still own and report effects that have not been approved, creating a permanent
  second interpretation of legacy books.
- **Start the clean instance empty with no opening controls.** Rejected: a fresh
  database is not a licence to discard assets, liabilities, inventory,
  receivables, payables or other live obligations.
- **Keep the monolith and only clean its database.** Rejected: it leaves the
  parallel decision paths and writer surface that composability is intended to
  remove.
