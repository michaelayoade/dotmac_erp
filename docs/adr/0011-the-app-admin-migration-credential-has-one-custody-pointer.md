# ADR-0011 — The `app_admin` migration credential has one custody pointer, and ERP holds only the pointer

- **Status:** Proposed — **unratified**. Authored 2026-09-04, revised 2026-09-05
  after review. A Dotmac record is not self-certified by its author; this one
  records a proposal and a set of verified facts, and it becomes `Accepted`
  only when Michael accepts it.
- **Date:** 2026-09-04 (revised 2026-09-05)
- **Proposer:** ERP Deployment/Operations
- **Deciders:** Michael (authorization and custody), ERP Deployment/Operations
  (declaration and execution request)
- **Domain:** deployment credential custody —
  `deploy/product.toml [migration].owner_material`, `scripts/deploy.sh`,
  `app/migration_database_roles.py`
- **Inventory:** `docs/inventories/2026-09-04-erp-migration-credential-custody.md`
  carries the verified facts, the declared pointer and the gap table. This ADR
  does not restate them. **The inventory predates this revision** and still
  describes the superseded local-contract shape in its §§ 4–7; where the two
  differ, this ADR wins and the inventory needs a dated correction in the change
  that implements this decision.
- **Adjacent, deliberately not merged:** ADR-0010 (`dotmac_erp_app` → `app_user`
  runtime identity, ERP Deployment/Operations, branch
  `docs/adr-erp-runtime-identity-ownership`). Different credential, different
  owner, different failure mode. See "Alternatives rejected".

---

## Accepting this ADR authorizes no mutation

> **Accepting ADR-0011 explicitly authorizes no OpenBao, PostgreSQL, host or
> deployment mutation.**

It authorizes no read of a credential, no write of a secret version, no
`ALTER ROLE`, no host access, no deploy, and no rotation. It declares where
authority sits and what an authorized operation would have to bind. Every
mutation this document describes requires its own separately issued, expiring,
single-use authorization from Deployment Control at the time it is performed,
and none has been issued.

This clause is placed here, before the reader forms a model of the document,
because this programme has repeatedly paid for corrections that exist several
hundred lines away from the claim they correct.

Nothing in this repository holds credential material. The custody pointer below
is **mount, path and field only**; a value has never appeared and may never
appear in any file, ticket, log, receipt, commit or review comment in this
repository.

---

## Ownership — ERP keeps almost none of this

The first draft of this ADR put custody, authorization, rotation sequencing,
verification and receipt semantics inside ERP, as a 524-line product-local
contract (`app/migration_credential_custody.py`). That is the wrong shape. It
would make ERP a second fleet dialect for authorization, rotation and receipts —
three vocabularies that already have fleet owners — and a second dialect of a
shared record is exactly the failure this programme keeps paying for.

| responsibility | owner |
|---|---|
| declare the ERP role, the custody pointer and the runtime exclusion | **ERP** |
| store credential material | **OpenBao** |
| issue signed, expiring, single-use authorization | **Deployment Control** |
| define the generic plan, its sequencing and its receipt | **Foundation** (`dotmac-deployment-foundation`) |
| perform the privileged PostgreSQL effect | **the target provider** |
| attest what actually executed | **an independent observation signer** |

> **ERP retains only a thin `MigrationCredentialBindingV1`** — a declaration
> naming the role, the canonical custody pointer and field, the runtime
> exclusion, and the target binding requirements. It declares; it does not
> authorize, rotate, verify or attest.

**The 524-line local contract must not become a second fleet authorization,
rotation or receipt dialect.** Its replacement by `MigrationCredentialBindingV1`
is required future work, recorded below and deliberately **not performed in the
change that carries this ADR** — an ADR records a decision and does not
implement one (`docs/adr/README.md`).

---

## Context

`deploy/product.toml:386` declares `[migration].owner_material =
"MIGRATION_DATABASE_URL"`, and `deploy/product.toml:70` excludes that name from
`[runtime_materials]`. `scripts/deploy.sh:127-131` exits `2` without it and
never falls back to `DATABASE_URL`. `app_admin` — non-superuser, `BYPASSRLS`
(`app/migration_database_roles.py:19` `ROLE_CONTRACT`) — is therefore the only
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
rotate; no authorization binding an operation to a specific target, database,
role and pointer; no proof taken from the actual one-shot migration container;
and no receipt recording which secret version was installed and verified.

Every one of those absences has the same consequence: a credential that cannot
be *reconciled*. Reconciliation is the operation that makes a credential
survivable — without it, the only repair available is a hand-pasted DSN, which
is precisely the shape that produced the current state.

### One absence is worse than missing — it is contradicted

While inventorying, a live defect surfaced. The legacy `docker-compose.yml` that
the entrypoint census records as what actually runs in production gives
`env_file: - .env` to `app` (`61-62`), `worker` (`150-151`) and `beat`
(`180-181`); `docs/inventories/2026-08-30-erp-deployment-entrypoint-census.md`
row **E2** records `MIGRATION_DATABASE_URL` as present in that same
`/root/dotmac/.env`; and `.env.example:14-17` ships the key under a comment
instructing that it be left blank "so runtime processes never hold migration
authority" — an instruction nothing reads and nothing checks. (`.env.example:13`
is a separate defect of the same family: the runtime `DATABASE_URL` example
defaults to the cluster superuser.)

Composed, ERP's own checked-in evidence says a `BYPASSRLS` DDL credential is
visible to three long-running network-facing processes in production today. The
descriptor forbids it, the rendered artifact does not contain it, and the host
appears to have it anyway — which is exactly the gap between *declared* and
*established* that this ADR's negative-proof requirement exists to close.

---

## Decision

**The `app_admin` migration DSN has exactly one custody pointer, one
authorization type issued by one owner, one execution owner, and one closed
receipt — and ERP owns only the first of those.**

### D1 — Custody is OpenBao, at one declared pointer

Mount `secret`, path `dotmac/postgres/erp-shared-primary/app_admin`, field
`MIGRATION_DATABASE_URL` — a sibling of the pointer ERP already wrote down for
the same cluster's superuser (`docs/runbooks/database-restore.md:92-93`). The
pointer is checked in as **path and field only**.

ERP's record stores **only the OpenBao pointer** — never a credential, never a
DSN, never a hash or any other derivation of material. `MigrationCredentialBindingV1`
is its machine-readable declaration.

### D2 — Deployment/Operations owns `app_admin` custody

`app_admin` custody belongs to Deployment/Operations, not to any ERP runtime
service, not to application code, and not to `scripts/deploy.sh`.

- **`app_admin` is migration-only. It never reaches `app`, `worker` or `beat`.**
  Not in an env file, not in a rendered artifact, not on the host.
- **Bootstrap, credential rotation and migration execution are three separate
  authorities.** The identity that creates a role is not the identity that
  rotates its password is not the identity that runs `alembic upgrade`. A single
  authority spanning any two of them collapses repair into diagnosis.
- **Migration execution requires direct authenticated `app_admin`**, an exact
  target-and-database binding, and a purpose-bound authorization. No `SET ROLE`
  escalation from a lesser identity, and no ambient host credential.

### D3 — The authorization is `CredentialReconciliationGrantV1`, issued by Deployment Control

A change to this credential requires a signed, expiring, **single-use**
authorization issued by Deployment Control. ERP does not mint it, does not
define its dialect, and cannot issue one to itself.

The grant must bind **all** of the following:

1. the Deployment Control **operation ID**, and the **digest of the signed
   authorization envelope** it was carried in;
2. the **immutable Control target ID** — the fleet's own stable identifier for
   the target, independent of any address;
3. the **authenticated host identity**;
4. the **expected TLS peer identity, or a pinned server-certificate
   fingerprint**;
5. the PostgreSQL **`system_identifier`** and the **database name**;
6. the **endpoint** (host, port) recorded as a **diagnostic observation, not as
   identity** — an address never establishes which cluster answered;
7. the exact role — `app_admin`, named literally, not a pattern;
8. the **canonical OpenBao pointer and field**;
9. the **expected canonical KV version**;
10. the **per-operation candidate path**;
11. the **operation**, the **plan digest**, and an **expiry**.

**Why `system_identifier` plus database name is insufficient on its own.**
`pg_basebackup` produces an exact file-level copy of a cluster, so a physical
backup reproduces the cluster's `system_identifier` and its database names on a
different host. Cluster identity therefore does not distinguish the production
primary from a restored copy of it, and a reconciliation that trusted it alone
could rotate the credential on a clone while believing it had rotated
production — or the reverse. That is why (2) the immutable Control target ID and
(4) the expected TLS peer identity are in the binding list, and why (6) the
endpoint is explicitly demoted to a diagnostic observation.

**The operation is acquired and consumed atomically through the existing shared
single-use mechanism.** ERP **must not** create another idempotency ledger; ERP
already has exactly one at-most-once owner and one ADR saying so (ADR-0001). A
second reservation table for reconciliation operations would be the same mistake
in a new domain.

### D4 — The rotation state machine

```
PREFLIGHT → CANDIDATE_STAGED → DATABASE_CHANGE_ATTEMPTED
→ AUTHENTICATION_CLASSIFIED → CANONICAL_PROMOTED
→ PREDECESSOR_RETIRED → PROVED
```

**The candidate path derives from the operation ID.** It is not a fixed
`app_admin_candidate` path: a fixed staging path is a shared mutable location
that two operations can collide on, and a collision there is two rotations
overwriting each other's staged material with no record that it happened.

**Before any mutation, the reconciler must establish all of:**

- the **incumbent authenticates** against the target;
- the **candidate refuses** — proving the candidate material is not already live
  and that the two are distinguishable;
- the path uses **real password authentication, not `trust`** — a `trust` line
  makes every authentication test vacuous, so a successful login under `trust`
  proves nothing about the credential;
- **target, role, KV version and authorization all match** what was authorized.

The reconciler refuses before any change if any of these fails, and reports
**every** mismatch rather than the first.

**After `ALTER ROLE`, both materials are tested from the actual one-shot
migration container** — the same image, the same network path, the same user
namespace and the same TLS configuration that a real migration uses.

| candidate | predecessor | meaning |
|---|---|---|
| succeeds | refuses | promote the candidate with an OpenBao **CAS** write |
| refuses | succeeds | keep the incumbent; **no promotion** |
| succeeds | succeeds | **the authentication oracle is unfit** — stop; a test that accepts both materials distinguishes nothing |
| refuses | refuses | credential stranded, or the target is wrong |
| unknown | unknown or known | retain both; require a fresh observation |

**Ambiguity never promotes.** An ambiguous outcome **keeps both versions, blocks
deployment, and resumes the same operation after a fresh observation. It never
mints a second candidate.** The first draft of this ADR contained a
"forward-fix" rule that promoted the candidate on ambiguity so the store would
match "what the database most likely holds"; that rule is **removed**. Promoting
on a guess writes an unverified value to the canonical pointer that every
consumer — including the container trying to diagnose the failure — then reads
as authoritative, which converts an ambiguous state into a confidently wrong
one.

Resuming *the same* operation, rather than starting a new one, is what keeps the
single-use mechanism meaningful: a new operation would mint a new candidate and
lose the correspondence between the authorization and the material actually
installed.

### D5 — Retirement and cleanup

**The predecessor is destroyed only after** the canonical promotion has been
**read back** from OpenBao **and** a further candidate authentication has
succeeded from the real one-shot executor. Retiring earlier is the stranding
case: if the promoted material turns out not to work, there is no material left
in the store that can log in, and `app_admin` cannot be repaired without the
superuser bootstrap path — which the deploy identity deliberately does not hold.

**Candidate cleanup has a separate authorization and its own retention rule.**
It is not a tail step of the rotation grant. A grant that authorizes a rotation
does not thereby authorize destroying the artifact that proves what the rotation
installed.

**Three moves are forbidden**, each because it breaks the invariant that at
every instant at least one readable version under the declared pointer set
contains material that authenticates as `app_admin` on the target:

- promoting before verifying;
- retiring the predecessor before verifying;
- treating "the reconciler process exited non-zero" as "the role was not
  changed" — it is not evidence of that, and acting on it is how a rollback
  overwrites a change that actually committed.

### D6 — The receipt is `CredentialReconciliationReceiptV1`, and it is closed

Every reconciliation writes one receipt. It is a **closed** document — a fixed
set of typed fields, with **no free-form exception field, no free-form detail
field, no DSNs, no credential hashes and no material of any kind.** A free-form
field on a receipt is the channel through which material eventually reaches a
ticket.

It binds:

- the **consumed operation** and the **authorization envelope digest**;
- the **plan digest** and the **reconciler artifact digest**;
- the **target-identity observation**;
- the **pre- and post-reconciliation OpenBao versions**;
- the **phase reached** in D4's state machine;
- the **migration-container image digest**;
- the **candidate and predecessor authentication classification** (D4's table
  row);
- the **independent signed execution observation**.

**Authentication verification is labelled honestly.** It proves that the
credential and the executor authenticate. **It does not prove that a future
migration will succeed** — a migration can fail on privileges, on lock
contention, on a schema conflict, or on its own SQL, none of which an
authentication test touches. The receipt says what was proved, in those terms.

A credential-material **hash is excluded deliberately**: the KV version number
already distinguishes versions, and a hash is an offline guessing oracle. A
receipt carrying one has not avoided disclosing the credential; it has disclosed
a slower version of it.

### D7 — Both authority policies pass before a production migration

**`RuntimeRoleAuthorityPolicyV1` and `MigrationExecutorAuthorityPolicyV1` must
both pass before a production migration runs.** They are two separate claims —
that the runtime role holds no more authority than it should, and that the
migration executor holds exactly the authority it needs — and neither implies
the other.

Both policies are being implemented in a **concurrent, separately owned lane**
(`feat/two-authority-policies-one-catalogue-observation`). **Neither exists on
this branch.** This ADR states the gate; it does not supply it, and it must not
be read as evidence that the gate is in place.

### D8 — App, worker and Beat cannot see `MIGRATION_DATABASE_URL`, established rather than asserted

At three layers, each with its own sensitivity proof, per the inventory § 8:
the descriptor (enforced today), the rendered artifact (not enforced — new
work), and the running host (unmonitored, and currently contradicted by ERP's
own census — see Context).

### Blast radius, and why the separation is worth its cost

During a reconciliation, only *deployability* degrades. App, worker and Beat
connect as the runtime role via `DATABASE_URL` and keep serving; the runtime
admission step (`scripts/deploy.sh:556-566`) deliberately withholds the
migration URL for exactly this reason. A credential incident here costs
deployments, not availability — which is the second reason the separation is
worth having, and the reason a reconciliation window is an acceptable risk at
all.

---

## Required future work — none of it done here

This change carries **the ADR and its number reservation only**. It changes no
code, no script, no test, no migration and no deployment asset. Each item below
lands in its own change, cites this ADR, and is owned as noted.

1. **Replace `app/migration_credential_custody.py` with a thin
   `MigrationCredentialBindingV1`.** The existing module's
   `ReconciliationAuthorization`, `ReconciliationOutcome`,
   `VerificationOutcome`, `ReconciliationReceipt`, `authorization_refusals` and
   `receipt_disclosure_refusals` are a product-local dialect of authorization,
   rotation and receipt vocabulary that D3/D4/D6 assign to Deployment Control
   and Foundation. They must be removed, not extended. *(ERP, blocked on the
   Foundation and Control contracts existing.)*
2. **Delete the `VERIFICATION_FAILED_DIVERGED` outcome and every code path that
   promotes on ambiguity.** It encodes the rule D4 removes. *(ERP.)*
3. **Derive the candidate path from the operation ID**, retiring the fixed
   `CANDIDATE_PATH` constant. *(ERP.)*
4. **Define `CredentialReconciliationGrantV1`** with D3's eleven bindings, its
   signature and expiry semantics, and its atomic acquire/consume against the
   existing shared single-use mechanism. *(Deployment Control — not ERP.)*
5. **Define the generic reconciliation plan, its sequencing, and
   `CredentialReconciliationReceiptV1`** as a closed document. *(Foundation —
   not ERP.)*
6. **Provide the independent signed execution observation.** *(Observation
   signer — not ERP.)*
7. **Add a database-identity assertion to
   `scripts/bootstrap_database_roles.py --verify-only`.** Today it asserts
   `current_user`, `NOSUPERUSER` and `BYPASSRLS` but **not** which database it
   is connected to, so a reconciliation pointed at the wrong database passes
   every check that script makes. *(ERP.)*
8. **Add the `trust`-authentication precondition check** of D4. An
   authentication test run against a `trust` line is vacuous, and nothing
   detects that today. *(ERP.)*
9. **Add the rendered-artifact gate** (D8, layer 2). *(ERP.)*
10. **Remediate the `.env` leak** (D8, layer 3) under its own authorization.
    This ADR authorizes no host access; see the clause at the top.
11. **Correct `docs/inventories/2026-09-04-erp-migration-credential-custody.md`
    §§ 4–7**, which still describe the superseded local-contract shape. *(ERP;
    the inventory is outside this lane's file set.)*

---

## Consequences

- ERP's surface shrinks to a declaration. That is the point, and it means this
  decision **cannot be executed until Deployment Control and Foundation supply
  their halves.** ERP declaring a binding against a contract that does not exist
  yet is honest, but it is not capability.
- Every production reconciliation is gated on an individually issued Deployment
  Control grant. No standing grant to a `BYPASSRLS` credential is created here.
- `bootstrap_database_roles.py --verify-only` gains a database-identity
  assertion it does not have today.
- The `.env` leak becomes a named, tracked defect with a named remediation,
  instead of an unexamined line in a census.
- **This ADR is a decision, not an implementation.** Nothing here is enforced
  until the changes listed above land.

### Enforcement status, per starter ADR-0018

Stated plainly, because "we decided this" and "this is checked" are different
claims, and the difference is where this class of failure lives.

**Enforceable today:** the descriptor's exclusion of the owner material from
every runtime role (load-time refusal, named test, existing sensitivity proof);
`deploy.sh`'s refusal to run without the variable and its refusal to fall back;
`--verify-only`'s assertions on `current_user` and role posture; the
no-committed-credential-literal guard.

**Unmonitored — not exempt:** everything this ADR proposes. The pointer is bound
to nothing; database identity at migration time is unchecked; `trust`
authentication is undetected; the rendered-artifact gate does not exist; the
running host has never been inspected; no grant is consumed because no issuer
exists; no receipt is written for the same reason; and both authority policies
of D7 live on another branch. A receipt's structural refusal to carry material
is untested, so it counts as unmonitored too — a guard with no sensitivity proof
is a guard nobody has watched bite.

An exemption must state an enforceable premise. None of these can, yet. So they
are recorded as unmonitored regions with named remediations, which is the honest
description and the one that does not decay into an allowlist.

---

## Alternatives rejected

- **Keep custody, authorization, rotation and receipts in a product-local ERP
  contract.** This was the first draft. Rejected because it makes ERP a second
  fleet dialect for three vocabularies that already have owners: a grant minted
  by the party that consumes it is not an authorization, a receipt written by
  the party with an interest in the run succeeding is not evidence, and two
  dialects of a rotation record cannot be reconciled after the fact. ERP keeps
  the declaration; everything else moves to its owner.

- **Promote the candidate on an ambiguous verification ("forward-fix").** Also
  in the first draft, reasoned by analogy to
  `resolve_ambiguous_activation_failure` in `scripts/deploy.sh:291`, which
  defaults forward because "Alembic process failure is not proof that its
  transaction rolled back". Rejected here because the analogy does not hold: for
  a migration, forward-fixing converges on a state a later migration can inspect
  and repair; for a credential, promoting an unverified value makes the
  canonical pointer answer *authoritatively* with material nothing has proved,
  to every consumer including the one diagnosing the failure. Ambiguity keeps
  both versions and blocks.

- **Keep the credential in the host `.env` and document that.** This is the
  status quo, and it is the defect: `env_file: - .env` hands every key to app,
  worker and Beat, so a `BYPASSRLS` DDL credential is distributed to three
  network-facing processes as a side effect of storing it conveniently.
  Documenting it would make the leak official rather than fix it.

- **Let `scripts/deploy.sh` rotate the credential when authentication fails.**
  Superficially attractive: the failure is discovered there, so repair it there.
  Rejected because a consumer that can rewrite its own credential can make any
  authentication failure stop without establishing *why* it failed — repair and
  diagnosis collapse into each other. `deploy.sh` also runs under whatever
  authority the operator's shell has, which is the eight unrestricted root SSH
  keys the entrypoint census names.

- **Promote the new OpenBao version first, then install on the role.** Rejected
  because it inverts the recoverable direction: a failed install leaves the
  canonical pointer naming material no role accepts, and every consumer reading
  the pointer gets the broken value. Install-first keeps the canonical pointer
  resolving to *working* material for the entire window.

- **One path, relying on KV version pinning instead of a candidate path.**
  Rejected because a KV v2 read without an explicit version returns the newest,
  so writing the candidate to the canonical path makes it the default answer for
  every consumer the moment it is written — before it has been verified.

- **A single fixed candidate path (`.../app_admin_candidate`).** Rejected in
  this revision: a fixed staging path is shared mutable state that two
  operations can collide on, and the collision is silent. The candidate path
  derives from the operation ID.

- **Trust `system_identifier` plus database name as the target binding.**
  Rejected: `pg_basebackup` reproduces both on a restored copy, so they do not
  distinguish the production primary from a clone of it. The binding needs the
  immutable Control target ID and the expected TLS peer identity as well.

- **Record a hash of the material in the receipt, "so we can tell versions
  apart".** Rejected: the KV version already distinguishes versions, and a hash
  is an offline guessing oracle.

- **Verify with a `psql` from the deploy host instead of the migration
  container.** Rejected because the claim being made is "the migration container
  can authenticate and execute". A host `psql` uses a different client, network
  path, user namespace and TLS configuration. It can succeed while the container
  fails — which is precisely the observation that started this.

- **Fold this into ADR-0010's runtime-identity migration.** Both are credential
  work on the same cluster. Rejected: different credentials, different owners,
  opposite failure modes — this one degrades deployability, that one degrades
  availability. Sequenced into one movement, a failure could not be attributed
  to either, and the rollback for one is not the rollback for the other.

---

## On the number

ERP's ADR-number register is `docs/adr/reservations.toml`. It is **not on
`main`**: it lives on the local, unpushed branch
`docs/adr-number-allocation-and-quote-first` at commit `5b2a035f`, and it sets
`next_free = 10`.

Surveying every local and remote ref in this repository for `docs/adr/001*`,
**0010 is claimed by `docs/adr-erp-runtime-identity-ownership` and 0011 is
claimed by nothing but this branch.** 0011 is therefore still uncontested, and
is the correct number.

The register's protocol says to land the `[[reservation]]` row on `main` before
writing the ADR. **That step is not executable from here**, for a reason that is
a property of the register rather than of this lane: the register itself is not
on `main`, and this lane has no merge authority. What this change does instead,
and the deviation it still carries, is recorded honestly:

- **Done:** the register is carried on this branch with rows for **10** and
  **11** and `next_free` raised to **12**. Row 10 records a claim this lane does
  not own — that is the register's declared job (it already records 6, 8 and 9
  the same way), not a ruling on that ADR. Both rows take `status = "reserved"`
  with a branch note, matching the shape row 9 already uses for a number whose
  file exists only off `main`.
- **Deviation, unresolved:** two branches now carry `reservations.toml`. The
  register was designed so that this is *contended*, not silent — `next_free` is
  one scalar on one line, and two branches that both add the file cannot
  auto-merge. The conflict is the lock being held. But it is still two copies of
  one record, and **which branch is the register's single writer is a question
  for Michael, not for this lane.** If the answer is the register's own branch,
  this branch's copy should be dropped and the rows added there instead.
