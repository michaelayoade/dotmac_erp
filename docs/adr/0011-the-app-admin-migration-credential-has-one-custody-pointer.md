# ADR-0011 — The `app_admin` migration credential has one custody pointer

- **Status:** Proposed — **unratified**. Authored 2026-09-04 and not accepted.
  A Dotmac record is not self-certified by its author; this one records a
  proposal and a set of verified facts, and it becomes `Accepted` only when
  Michael accepts it.
- **Date:** 2026-09-04
- **Proposer:** ERP Deployment/Operations
- **Deciders:** Michael (authorization and custody), ERP Deployment/Operations
  (request and execution)
- **Domain:** deployment credential custody —
  `deploy/product.toml [migration].owner_material`, `scripts/deploy.sh`,
  `app/migration_database_roles.py`, `app/migration_credential_custody.py`
- **Inventory:** `docs/inventories/2026-09-04-erp-migration-credential-custody.md`
  carries the verified facts, the declared pointer and the gap table. This ADR
  does not restate them.
- **Adjacent, deliberately not merged:** ADR-0010 (`dotmac_erp_app` → `app_user`
  runtime identity, ERP Deployment/Operations, branch
  `docs/adr-erp-runtime-identity-ownership`). Different credential, different
  owner, different failure mode. See "Alternatives rejected".

## On the number

ERP's ADR-number register (`docs/adr/reservations.toml`, on the unmerged
`docs/adr-number-allocation-and-quote-first`) sets `next_free = 10` and records
0006, 0008 and 0009 as claimed off `main`. 0010 has since been taken by the
runtime-identity ADR named above. Surveying every local and remote branch in
this repository, **no branch carries a `docs/adr/0011-*`**, so 0011 is the
lowest number that collides with nothing.

The register's protocol says to land the reservation row on `main` *before*
writing the ADR. This change cannot follow it: it is local and unpushed by
instruction, and the register itself is not on `main`. That is recorded here as
a known deviation rather than papered over — **when the register lands, this ADR
needs a `[[reservation]]` row with `number = 11`, `status = "authored"`**, and
whoever lands it should confirm 0011 is still uncontested at that moment.

## Context

`deploy/product.toml:386` declares `[migration].owner_material =
"MIGRATION_DATABASE_URL"`, and `deploy/product.toml:70` excludes that name from
`[runtime_materials]`. `scripts/deploy.sh:127-131` exits `2` without it and
never falls back to `DATABASE_URL`. `app_admin` — non-superuser, `BYPASSRLS`
(`app/migration_database_roles.py:ROLE_CONTRACT`) — is therefore the only
identity that can execute a migration.

That separation is correct and was hard-won. What was never written down is the
other half of it.

### The credential has an owner, a consumer, and no custodian

The privilege manifest (PR #454) authors grants that only `app_admin` can apply,
and the latest production observation is that `app_admin` **fails to
authenticate from the one-shot migration container**. So that change has no
executor.

The wider point is that this is not that change's problem. The canonical ERP
production deployment path invokes migrations with `MIGRATION_DATABASE_URL` for
*every* candidate. A typo fix is blocked by the same gap as a privilege change.
One credential gates the entire deployment path, and nothing in the repository
says where it lives.

### Six things were missing

Enumerated with evidence in the inventory, § 2. In short: no canonical OpenBao
path and field; "the approved secret source" written as prose rather than a
resolvable pointer; no accepted authorization naming who may reconcile or
rotate; no authorization binding an operation to a specific environment,
database, role and pointer; no proof taken from the actual one-shot migration
container; and no receipt recording which secret version was installed and
verified.

Every one of those absences has the same consequence: a credential that cannot
be *reconciled*. Reconciliation is the operation that makes a credential
survivable — without it, the only repair available is a hand-pasted DSN, which
is precisely the shape that produced the current state.

### One absence is worse than missing — it is contradicted

While inventorying, a live defect surfaced. The legacy `docker-compose.yml` that
the entrypoint census records as what actually runs in production gives
`env_file: - .env` to `app` (61-62), `worker` (150-151) and `beat` (180-181);
`docs/inventories/2026-08-30-erp-deployment-entrypoint-census.md` row **E2**
records `MIGRATION_DATABASE_URL` as present in that same `/root/dotmac/.env`;
and `.env.example:13-17` ships the key under a comment instructing that it be
left blank "so runtime processes never hold migration authority" — an
instruction nothing reads and nothing checks.

Composed, ERP's own checked-in evidence says a `BYPASSRLS` DDL credential is
visible to three long-running network-facing processes in production today. The
descriptor forbids it, the rendered artifact does not contain it, and the host
appears to have it anyway — which is exactly the gap between *declared* and
*established* that this ADR's step 8 exists to close.

## Decision

**The `app_admin` migration DSN has exactly one custody pointer, one
authorization type, one execution owner, and one evidence artifact.**

1. **Custody is OpenBao, at one declared pointer.** Mount `secret`, path
   `dotmac/postgres/erp-shared-primary/app_admin`, field
   `MIGRATION_DATABASE_URL` — a sibling of the pointer ERP already wrote down
   for the same cluster's superuser (`docs/runbooks/database-restore.md:93`).
   The pointer is checked in as **path and field only**; a value never appears
   in any file, ticket, log, receipt or commit. `app/migration_credential_custody.py`
   is its machine-readable owner.

2. **A change to that credential requires a typed authorization**
   (`ReconciliationAuthorization`) binding environment, database identity, role,
   pointer, **expected current secret version**, operation, an authorization
   reference and an **expiry**. Michael approves each production reconciliation
   individually, until a separately approved scoped service principal exists.
   No standing grant is created here.

3. **Rotation is two-phase, in this order, and the order is the safety
   property:** stage the candidate at a separate path without printing it →
   install on the database role → **verify from the actual one-shot migration
   container** → promote to the canonical path → **retire the predecessor only
   after verification**.

4. **The reconciler refuses** — before any change — if the observed
   environment, database, role, pointer, secret version or authorization
   differs from what was authorized, reporting *every* mismatch rather than the
   first.

5. **Verification is `scripts/bootstrap_database_roles.py --verify-only` and
   `alembic current`, from the same one-shot migration context**, recording the
   image digest they ran in.

6. **Verification asserts `current_user = app_admin`, the correct database,
   `NOSUPERUSER` and `BYPASSRLS`.** Three of those four are already asserted by
   the existing preflight; **the database identity is not**, and adding it is
   part of this decision.

7. **Every reconciliation writes a receipt** carrying versions, pointer,
   database identity, verification and outcome — and structurally incapable of
   carrying material, a digest of material included.

8. **App, worker and Beat cannot see `MIGRATION_DATABASE_URL`, and that is
   established rather than asserted** — at three layers, each with a
   sensitivity proof, per the inventory § 8.

**Execution is a one-shot privileged credential reconciler.** Not ERP runtime,
and not `scripts/deploy.sh`. The deploy path *consumes* this credential; a
consumer that can also change its own credential can repair a failed
authentication by installing whatever makes the failure stop, which is not
reconciliation.

### The window between install and promote — the question this ADR must answer

Between step 3's *install* and its *promote*, the database and the store
disagree: the role holds the candidate material, the canonical pointer still
resolves to the predecessor. A rotation that cannot say what happens here
strands the credential. This is what happens.

**The window is survivable by construction, because neither half is ever
lost.** The predecessor is still readable at canonical version *N*. The material
that is live on the role is still readable at the candidate path. What is broken
is *agreement*, not *availability* — and the reconciler is the only writer, so
it knows which side is which.

The invariant, stated so it can be checked:

> **At every instant of a reconciliation, at least one OpenBao version readable
> under the declared custody pointer set contains material that authenticates as
> `app_admin` on the target database.**

Staging-before-install and retire-after-verify are the two orderings that keep
that true. Every other ordering breaks it.

If verification fails inside the window, there are exactly two moves, and the
choice is decided by a **fresh read**, never by assumption:

- **Rollback — preferred, and only when positively proven safe.** Re-install the
  predecessor material (still at canonical version *N*) onto the role, then
  re-verify *the predecessor* from the migration container. On success: destroy
  the candidate staging version, write a receipt with outcome
  `VERIFICATION_FAILED_ROLLED_BACK`, and the deployment is exactly where it
  started. **This move is available only because retirement is last.** If the
  predecessor had been retired before verification, there would be no material
  in the store that can log in, and `app_admin` could not be repaired without
  falling back to the superuser bootstrap path — which the deploy identity
  deliberately does not hold. That is the stranding this ordering prevents, and
  it is the whole reason for it.

- **Forward-fix — when rollback cannot be positively proven.** If the outcome is
  ambiguous — the `ALTER ROLE` result was not observed, the connection dropped
  mid-statement, or the rollback `ALTER ROLE` itself fails — then promote the
  candidate anyway so the store matches what the database most likely holds,
  write outcome `VERIFICATION_FAILED_DIVERGED`, and **retire neither version**.
  Both stay readable, the receipt names both, and the next deployment is blocked
  by design — `--verify-only` will fail if the wrong material is live — rather
  than proceeding on a guess.

  ERP already takes this exact shape elsewhere. `resolve_ambiguous_activation_failure`
  in `scripts/deploy.sh` defaults to forward-fix-only because "Alembic process
  failure is not proof that its transaction rolled back: a container/transport
  failure may be reported after PostgreSQL committed." The same reasoning
  applies to `ALTER ROLE`, and the same default is taken.

Three moves are forbidden inside the window, each because it breaks the
invariant:

- **Promoting before verifying.** A failed verification then leaves the
  canonical pointer naming material that does not work, and the predecessor is
  one `destroy` away from unrecoverable.
- **Retiring the predecessor before verifying.** The stranding case above.
- **Treating "the reconciler process exited non-zero" as "the role was not
  changed."** It is not evidence of that, and acting on it as though it were is
  how a rollback overwrites a change that actually committed.

**Blast radius during the window.** Only *deployability* degrades. App, worker
and Beat connect as `app_user` via `DATABASE_URL` and keep serving; the runtime
admission step (`scripts/deploy.sh:556-566`) deliberately withholds the
migration URL for exactly this reason. A credential incident here costs
deployments, not availability — which is the second reason the separation is
worth having, and the reason the window is an acceptable risk at all.

## Consequences

- Every production reconciliation is gated on Michael personally, until a
  scoped service principal is approved. This is a deliberate bottleneck and a
  real one; it is the cost of not creating a standing grant to a `BYPASSRLS`
  credential.
- `bootstrap_database_roles.py --verify-only` gains a database-identity
  assertion it does not have today. Until it does, a reconciliation pointed at
  the wrong database passes every check that script makes.
- The rendered-artifact gate (inventory § 8, layer 2) is new work.
- The `.env` leak (inventory § 8, layer 3) becomes a named, tracked defect with
  a named remediation, instead of an unexamined line in a census.
- **This ADR is a decision, not an implementation.** Per `docs/adr/README.md`,
  the reconciler, the gates, the tests and the ledger update land in their own
  changes and cite this one. Nothing here is enforced until they do.

### Enforcement status, per starter ADR-0018

Stated plainly, because "we decided this" and "this is checked" are different
claims and the difference is where this class of failure lives.

**Enforceable today:** the descriptor's exclusion of the owner material from
every runtime role (load-time refusal, named test, existing sensitivity proof);
`deploy.sh`'s refusal to run without the variable and its refusal to fall back;
`--verify-only`'s assertions on `current_user` and role posture; the
no-committed-credential-literal guard.

**Unmonitored — not exempt:** everything this ADR proposes. The pointer is bound
to nothing; database identity at migration time is unchecked; the
rendered-artifact gate does not exist; the running host has never been
inspected; no authorization is consumed because no reconciler exists; no receipt
is written for the same reason. The receipt's own material refusal is
structural but untested, so it counts as unmonitored too — a guard with no
sensitivity proof is a guard nobody has watched bite.

An exemption must state an enforceable premise. None of these can, yet. So they
are recorded as unmonitored regions with named remediations, which is the honest
description and the one that does not decay into an allowlist.

## Alternatives rejected

- **Keep the credential in the host `.env` and document that.** This is the
  status quo, and it is the defect: `env_file: - .env` hands every key to app,
  worker and Beat, so a `BYPASSRLS` DDL credential is distributed to three
  network-facing processes as a side effect of storing it conveniently.
  Documenting it would make the leak official rather than fix it.

- **Let `scripts/deploy.sh` rotate the credential when authentication fails.**
  Superficially attractive: the failure is discovered there, so repair it there.
  Rejected because a consumer that can rewrite its own credential can make any
  authentication failure stop without establishing *why* it failed — the repair
  and the diagnosis collapse into each other, and the receipt would be written
  by the party with an interest in the run succeeding. `deploy.sh` also runs
  under whatever authority the operator's shell has, which is the eight
  unrestricted root SSH keys the entrypoint census names.

- **Promote the new OpenBao version first, then install on the role.** Simpler
  to write: the store leads and the database follows. Rejected because it
  inverts the recoverable direction. With promote-first, a failed install leaves
  the canonical pointer naming material no role accepts, and every consumer
  reading the pointer — including the migration container trying to diagnose the
  problem — gets the broken value. Install-first keeps the canonical pointer
  resolving to *working* material for the entire window.

- **One path, relying on KV version pinning instead of a candidate path.**
  Rejected because a KV v2 read without an explicit version returns the newest,
  so writing the candidate to the canonical path makes it the default answer for
  every consumer the moment it is written — before it has been verified. A
  separate staging path means the canonical path only ever holds material that
  has been proven to work.

- **Fold this into ADR-0010's `dotmac_erp_app` → `app_user` runtime-identity
  migration.** Both are credential work on the same cluster, and doing them
  together is tempting. Rejected: they are different credentials with different
  owners and opposite failure modes — this one degrades deployability, that one
  degrades availability. Sequenced into one movement, a failure could not be
  attributed to either, and the rollback for one is not the rollback for the
  other. They stay separate, and ADR-0010 keeps its own branch and its own
  owner.

- **Verify with a `psql` from the deploy host instead of the migration
  container.** Rejected because the claim being made is "the migration container
  can authenticate and execute". A host `psql` uses a different client, a
  different network path, a different user namespace and a different TLS
  configuration. It can succeed while the container fails — which is precisely
  the observation that started this.

- **Record a hash of the material in the receipt, "so we can tell versions
  apart".** Rejected: the KV version number already distinguishes versions, and
  a hash is an offline guessing oracle. A receipt carrying one has not avoided
  disclosing the credential; it has disclosed a slower version of it.
