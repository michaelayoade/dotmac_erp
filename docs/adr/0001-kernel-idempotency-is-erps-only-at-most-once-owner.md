# ADR-0001 — The kernel is ERP's only at-most-once owner

- **Status:** Accepted
- **Date:** 2026-08-15
- **Decider:** Michael
- **Program slice:** E8 (the decision `docs/PLATFORM_ADOPTION_LEDGER.md` names
  as "After E8 ADR" for `dotmac_kernel.idempotency`)
- **Supersedes:** nothing. First ERP decision record.

## Context

`docs/PLATFORM_ADOPTION_LEDGER.md` classifies `dotmac_kernel.idempotency`
(+ `.idempotency_models`) as **defer-db**, gated "After E8 ADR". That ADR was
never written, so the gate could not clear, and the gate is now load-bearing
for something concrete: `dotmac-numbering 0.1.0a2` declares
`idempotency_ledger.v1` in `ModuleManifest.requires` — **unconditionally, not
per-plane** — so ERP cannot compose the numbering module at all until this is
decided. Supplying those tables IS the deferred decision, not a way around it.

### What ERP has today

**`platform.idempotency_record`** (`app/models/finance/platform/idempotency_record.py`),
owned by `IdempotencyService`
(`app/services/finance/platform/idempotency.py`). Its shape and contract differ
from the kernel's in three ways that matter:

1. **Scope is an HTTP endpoint.** The unique key is
   `(organization_id, endpoint, idempotency_key)`, and every caller passes
   `endpoint=request.url.path`. The kernel deliberately rejected that shape
   (ADR-0014 § 3): the same logical operation reached through a second route
   lands in a second ledger row, so the second route re-executes the effect.
2. **It reserves BEFORE the effect.** `reserve()` writes a `202` placeholder,
   then the caller runs the side effect. The kernel's `execute_once` reserves
   nothing ahead of the effect: a handler that raises rolls back with the
   SAVEPOINT and leaves no row, which is what makes a retry re-drive cleanly
   (ADR-0014 § 5). ERP's placeholder needs a 15-minute lease
   (`RESERVATION_LEASE_MINUTES`) and a stale-takeover path precisely because
   the reservation can outlive the request that made it — machinery the kernel
   contract does not need because it does not create the problem.
3. **It caches an HTTP response.** `response_status` / `response_body` make it
   a request-replay cache as much as an execution ledger. The kernel stores a
   `result` JSON belonging to the operation, not to a transport.

Callers, at `origin/main`: `app/api/expense.py` and `app/api/people/expense.py`
(21 references each), `app/api/idempotency.py`, `app/api/me.py`,
`app/api/sync/sub_attendance.py`, `app/services/dotmac_sub/sync/_payments.py`,
and the four AR/AP posting services.

**`PostingIdempotencyService`** (`app/services/finance/posting/idempotency.py`)
is NOT a second ledger and is NOT in scope for retirement. It detects an
existing `JournalEntry` for a source document by natural key. That is a
uniqueness constraint expressed as a query, not a reservation — the right
pattern, and it stays.

### Two other facts that shape the decision

- ERP hosts `public.tenants` in its OWN lineage since 2026-08-13
  (`alembic/versions/20260813_tenant_projection.py`), bound in
  `app/migration_bindings.py`, and `app/tenancy.py` makes the Organization UUID
  *be* the Tenant UUID with `app/rls.py` priming `app.current_tenant`. **The
  ledger's first stated reason for deferring — that ERP "neither has nor
  migrates" kernel `tenants` — has been stale since that date.**
- ERP's production runtime still connects as the `postgres` superuser
  (`app/db/session_context.py`). A superuser bypasses RLS regardless of
  `rolbypassrls`, so any FORCE RLS on any ERP table is inert today, and any
  isolation proof run on that connection proves nothing.

## Decision

### 1. `dotmac_kernel.idempotency` is ERP's sole durable at-most-once owner

One owner, per the fleet source-of-truth standard and ADR-0014. Every new
at-most-once decision in ERP goes through `execute_once` /
`execute_once_platform`. ERP writes no new at-most-once ledger, in any schema,
under any name.

### 2. `platform.idempotency_record` may coexist ONLY as transitional legacy state

It is not a second authority; it is state that has not been retired yet, and it
is bounded by three conditions that must all hold at once:

- **Disjoint operation scopes.** A given logical operation is owned by exactly
  one ledger. The mapping from ERP endpoint scope to kernel `scope` is written
  down in the migration slice that moves it, and no operation appears in both.
  An operation that is ambiguous is not moved until it is disambiguated.
- **A no-new-callers ratchet.** A checked-in test pins the set of files
  referencing `IdempotencyService` / `IdempotencyRecord`. It fails when the set
  GROWS — a new caller is a new dependency on a retiring owner — and it fails
  when the set SHRINKS without the pin being lowered in the same change, so
  retirement progress is recorded rather than silently absorbed (ADR-0018's
  two-directional ratchet). "Grandfathered" and "reviewed and correct" stay
  distinct labels in that pin.
- **A retirement gate.** The legacy table is dropped when the pinned set is
  empty AND the historical rows have been dispositioned per § 6. Not before,
  and not by inference from a green suite.

### 3. Transactional database effects migrate to kernel `execute_once`

An effect that commits inside ERP's own transaction is guarded by
`execute_once`, which joins that transaction. This is the majority case in the
posting services.

### 4. Non-transactional and external effects move through the outbox

An effect that leaves the database — a provider call, an email, a webhook, a
file push — is NOT made idempotent by wrapping it in a ledger row, because the
row and the effect cannot commit together. Those move through the transactional
outbox, where the write and the intent commit atomically and delivery is
at-least-once with a deduplicating receiver. `dotmac_kernel.messaging`'s
storage/relay remains `defer-db` under its own ledger row; this ADR states the
target, not its schedule.

### 5. Long-running workflow state stays workflow state

A multi-step or long-running operation is a workflow with its own states,
owner, and observable progress. It must not be disguised as an idempotency
reservation. ERP's `202 "Request in progress"` placeholder with a 15-minute
lease is exactly that disguise: it encodes "work started, outcome unknown" in a
ledger whose contract has no such state, and then needs a takeover path to dig
itself out. Where a caller genuinely has a long-running operation, the fix is a
workflow record, not a longer lease.

### 6. ERP supplies `idempotency_ledger.v1` through its own truthful lineage

ERP declares the effect and binds it to an ERP revision that actually creates
the tables. **ERP must never run the legacy kernel root and never stamp it.**
Composing the kernel lineage would put a second root into ERP's revision graph
sharing one `public.alembic_version`, and `alembic upgrade heads` would then
execute kernel DDL implicitly — what the E1 acceptance forbids. Stamping is
worse: it asserts an effect that no statement produced, and
`require_prerequisites` exists precisely to catch that lie against the live
catalogue.

**Both kernel-shaped tables are required.** `idempotency_ledger.v1` is a COMMON
prerequisite in numbering's manifest, not a per-plane one, because
`dotmac_kernel.idempotency` publishes `execute_once` and
`execute_once_platform` from one module — a consumer cannot link the tenant
half without referencing the platform table. ERP therefore creates BOTH
`public.idempotency_records` and `public.platform_idempotency_records`, to the
exact contract kernel `0.1.0a66`'s `verify_idempotency_ledger` checks: the full
column set, `fingerprint` as its own nullable column, the unique key per plane,
the `expires_at` index, FORCEd RLS on the tenant ledger and none on the
platform peer.

Creating the platform table with no platform caller today is deliberate and is
not dead weight: the alternative is a partial effect that satisfies the name
and fails the verifier.

### 7. This ADR does not change the ledger status

The adoption-ledger row for `dotmac_kernel.idempotency` **stays `defer-db`**.
This ADR's own PR corrects the row's stale rationale — ERP has had
`public.tenants` since 2026-08-13, and the surviving reason is the second-owner
question this ADR answers — and leaves the class alone.

The status changes to adopted in a LATER composition PR, and only when all four
of these have landed together:

1. the two tables in an ERP revision;
2. the `idempotency_ledger.v1` binding in `app/migration_bindings.py`;
3. `require_prerequisites` called by the requiring migration and passing;
4. PostgreSQL proofs — positive, plus a specific refusal per observable.

The ledger's own rule (`docs/PLATFORM_ADOPTION_LEDGER.md`: a `defer-db` module
joins the allowlist "in the slice that adopts them, in the same change that
updates this table") governs that PR.

### 8. Cutover stays blocked while the runtime is a superuser

No at-most-once cutover is accepted while ERP's production runtime connects as
`postgres`. **RLS acceptance must be proven on a non-BYPASSRLS, non-superuser
runtime role.** A proof run as superuser is not weak evidence, it is no
evidence: the policy is never consulted. `scripts/cutover_database_ownership.py`
is merged; the runtime has not moved.

### 9. Historical rows are classified, with an explicit replay-window policy

Existing `platform.idempotency_record` rows are not migrated wholesale. Each
row is classified:

- **completed** (a stored terminal response) — may be carried across as an
  executed ledger entry only if its operation scope has been mapped and its
  request identity survives the mapping;
- **reserved / in-progress** (the `202` placeholder) — is NOT a success and is
  never carried across as one. It is either resolved against the real effect or
  dropped;
- **expired** (past `expires_at`) — dropped.

**No fabricated successes.** A migration must not invent an executed row for an
effect nobody can prove ran; the failure mode is a retry that silently does
nothing while the caller believes the work is done.

The replay window is stated explicitly in the migrating change — which
operations remain replay-protected across the cutover instant, and for how long
— rather than inherited from the legacy `DEFAULT_TTL_HOURS = 24`. Retention is
a product policy (ADR-0014 § 6), applied through `purge_expired`.

## Consequences

- ERP can compose `dotmac-numbering` once the composition PR lands, and any
  future module that guards effects with the kernel ledger.
- `IdempotencyService` becomes a retiring owner the day this is accepted: it
  keeps working, it gains no new callers, and its caller count only falls.
- Two kernel-shaped tables exist in ERP before either is heavily used. That is
  the price of a common prerequisite honestly satisfied.
- The endpoint-scoped key does not survive the move. Each migrating caller must
  choose a real operation scope, and two routes reaching one operation must
  choose the SAME one — which is the defect being fixed, surfacing as work.
- Effects that cannot be made transactional are exposed as needing the outbox
  rather than being wrapped in a ledger that cannot protect them.

## Alternatives rejected

- **Adopt the kernel ledger and keep `IdempotencyService` for HTTP replay
  indefinitely.** Two durable at-most-once owners with overlapping scope is the
  prohibited state, and "just for HTTP" is not a boundary anyone can enforce —
  the endpoint scope is precisely what makes overlap invisible.
- **Migrate `platform.idempotency_record` into the kernel shape in place.** The
  contracts differ in kind, not in column names: reserve-before-effect versus
  reserve-nothing, endpoint scope versus operation scope, cached response versus
  operation result. A rename would carry a different contract under the kernel's
  name, which is worse than two visibly separate tables.
- **Run or stamp the kernel lineage to obtain the tables cheaply.** Running it
  creates a second root and implicit kernel DDL under `upgrade heads`; stamping
  asserts an effect nothing produced. Both are the exact failures
  `require_prerequisites` was built to catch.
- **Declare `idempotency_ledger.v1` per-plane so ERP needs only the tenant
  table.** The prerequisite is common by construction in the kernel, and
  numbering declares it in `requires`. Narrowing it in ERP would be a local fork
  of a fleet contract.
- **Defer again until the runtime moves off `postgres`.** The role cutover
  gates ACCEPTANCE of the at-most-once cutover (§ 8); it does not gate deciding
  the ownership boundary. Deferring the decision is what left a named gate
  pointing at a document that did not exist.
