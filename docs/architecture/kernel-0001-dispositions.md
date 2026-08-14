# Kernel revision 0001 disposition in ERP

**Status:** active executable gate; inventory complete, lineage disposition incomplete  
**Kernel pin measured:** `dotmac-kernel==0.1.0a24`  
**Revision:** `0001_initial_tenant_schema`

Kernel revision 0001 is atomic: it creates database roles, eight tables, one
tenant-scope function, six RLS policy families and grants. ERP cannot stamp it
because only some of those effects are hosted. It cannot run it because the
remaining effects either collide with incompatible ERP tables or would create
a second identity authority.

| Revision 0001 effect | ERP disposition | Gate |
|---|---|---|
| DB roles `app_admin`, `app_user`, `platform_api` | Provided by the explicit privileged bootstrap plus fail-closed `20260814_database_roles` adoption. Existing `postgres`-owned databases still need a reviewed ownership cutover before `app_admin` can migrate them. | Resolved in code; existing-environment rollout blocked |
| `tenants` | Hosted by ERP migration `20260813_tenant_projection`; kernel `Tenant` is read/written only through the Organization projection owner. | Resolved locally; kernel lineage still unrecorded |
| `tenant_domains` | Hosted with the exact kernel shape. No ERP runtime writer is admitted yet. | Resolved locally; kernel lineage still unrecorded |
| `people` | Incompatible name collision with ERP staff identity. Kernel creation, RLS and grants are prohibited; Party/identity migration is outside this E8 slice. | Blocked |
| `user_credentials` | Incompatible collision with ERP authentication authority. | Blocked |
| `auth_sessions` | No same-name collision (`ERP` uses `sessions`), but creating it would be a second session authority. | Blocked |
| `roles` | Incompatible collision: ERP roles are global/local-RBAC shaped, kernel roles are tenant-scoped. | Blocked |
| `person_roles` | Incompatible collision with ERP's current assignment table. | Blocked |
| `audit_events` | Incompatible collision and historical tenancy gap; ERP audit remains authoritative. | Blocked |
| `app_current_tenant_id()` | Hosted by `20260813_tenant_projection`, verified before adoption, reading `app.current_tenant`. | Resolved locally; kernel lineage still unrecorded |
| RLS for the six identity/audit tables | Must not run against ERP's incompatible tables. The dual-GUC bridge does not make those schemas equivalent. | Blocked |
| Grants on identity/audit tables | Must not grant kernel online roles access to ERP identity/audit stores. | Blocked |
| Catalogue grants | ERP keeps Organization projection as the sole writer; `PUBLIC` and `app_user` writes are revoked. | Deliberate assembly override |

The catalogue migration is explicitly forward-fix only: it never drops tenant
identity on downgrade, including when it verified and adopted a pre-existing
catalogue. Later corrections amend the projection in place.

## Permanent negative canary — NOT a forward ratchet

**Corrected 2026-08-14.** This section previously called the rehearsal a ratchet
whose expected failure moves forward as dispositions land. That is false for
ERP, and acting on it would waste a slice chasing a failure that cannot move.

`tests/integration/test_kernel_lineage_rehearsal.py` provisions a unique
disposable PostgreSQL database, builds ERP through its real Alembic chain, and
then runs the exact installed Kernel migrations through a separate
`public.dotmac_kernel_alembic_version` table. It exercises both an empty
database upgraded to current head and the real
`20260812_merge_expand_withdrawal` predecessor with an Organization inserted
before the tenant-projection upgrade.

Both paths fail at `0001_initial_tenant_schema` when it tries to create the
already hosted `public.tenants` table, and **they always will**. Kernel `0001`
creates `public.tenants` unconditionally as its FIRST table, before reaching any
identity, RBAC or audit work. ERP intentionally owns that table
(`20260813_tenant_projection`). The collision therefore happens before every
other disposition in this matrix is even reached, so none of them can advance it.

Sub's equivalent rehearsal IS a forward ratchet, because Sub does not host
`tenants`. The two tests look identical and behave oppositely. Do not port
reasoning from one to the other.

**What the canary is for.** It stays red at `tenants` permanently, on purpose,
and fails if the failure ever CHANGES — which would mean someone dropped ERP's
tenant catalogue, stamped the Kernel revision, or edited the Kernel migration.
All three are prohibited. The test also proves the failed transaction records no
Kernel revision and creates none of the three Kernel database roles.

**The legacy Kernel lineage must never run or be stamped in ERP.** That is the
standing disposition, not a temporary state awaiting more work.

## Cutover rule

ERP may finish additive collision dispositions in coherent slices. What it may
never do is run or stamp the Kernel lineage. A raw Alembic stamp, a
product-specific conditional copied into the kernel migration, or a blanket
`IF EXISTS` is not evidence and remains forbidden.

This ERP slice is not authority to supply a new generic kernel facility during
ADR-0017's adoption gate. Reuse the proven Starter/Sub contract; if ERP exposes
a genuinely missing reusable seam, preserve it as evidence until two adopters
prove the same need.

## `dotmac-files` is unblocked by contract, not by convergence

`fi_0001_stored_files` used to declare
`depends_on = ("0001_initial_tenant_schema",)`, which ERP could never satisfy.
Starter's **ADR-0006 D1 amendment** (kernel `0.1.0a56`) replaced that physical
edge with LOGICAL prerequisites: a module declares the database EFFECTS it needs
and each assembly binds them to its own truthful revisions.

Files needs exactly two — a tenant catalogue to point a foreign key at
(`tenant_scope_catalog.v1`) and three roles to grant to
(`module_database_roles.v1`) — and neither requires the Kernel's identity estate.

ERP's bindings:

| Prerequisite | ERP provider |
|---|---|
| `tenant_scope_catalog.v1` | `20260813_tenant_projection` |
| `module_database_roles.v1` | the database-role bootstrap (below) |

Pursuing full Kernel-0001 convergence merely to unlock stored bytes is
explicitly rejected: it would couple byte storage to credentials, sessions, RBAC
and audit for no domain reason.

## The database-role bootstrap

`app_admin`, `app_user` and `platform_api` are cluster-wide identities every
Dotmac module grants to. `CREATE ROLE` needs superuser or `CREATEROLE`, which an
ordinary `alembic upgrade` must never hold, so creation and verification are
split:

| Step | Who runs it | What it does |
|---|---|---|
| `scripts/bootstrap_database_roles.py` | operator, **explicitly elevated** via `BOOTSTRAP_DATABASE_URL` | creates or adopts the three roles; `--dry-run` and opt-in `--repair` |
| `20260814_database_roles` | ordinary unprivileged `app_admin` | **verifies only**, and fails closed naming the bootstrap |

Alembic has a separate, mandatory `MIGRATION_DATABASE_URL`. Its environment
verifies `current_user = 'app_admin'` and the exact role posture before running
any revision; it never falls back to the application's `DATABASE_URL`. Deploy
uses the bootstrap's shared `--verify-only` path, so preflight and migration
cannot carry independent copies of the role contract. Real bootstrap changes
are planned before DDL and commit in one transaction; unapproved drift produces
no partial role creation.

The same preflight also proves that the database and every non-extension schema,
relation, enum/domain and routine Alembic may alter are owned by `app_admin`.
Changing a database owner does not re-own its existing objects. Therefore an
existing installation created by `postgres` needs a separately reviewed,
elevated ownership cutover after role bootstrap and before its first
`app_admin` migration. The role bootstrap deliberately does not perform that
broader operation; deploy fails before DDL until the cutover is complete.

Adopt-only was rejected as the sole mechanism because it strands new
installations: nothing in the deploy path would ever create the roles, so a
fresh cluster could never satisfy the prerequisite (decision, 2026-08-14).

The contract is `(rolbypassrls, rolsuper)`: `app_admin` `(true, false)`,
`app_user` `(false, false)`, `platform_api` `(false, false)`. Both attributes
are checked because **a superuser bypasses RLS regardless of `rolbypassrls`** —
an `app_user SUPERUSER NOBYPASSRLS` would pass an existence check while silently
defeating tenant isolation for every module. `app_admin` is BYPASSRLS and NOT
superuser: its requirement is reading past RLS, and accepting a superuser would
certify cluster-wide authority to satisfy it.

`tests/architecture/test_database_role_contract.py` pins the runtime owner and
migration snapshot to the same contract, proves the migration issues no role
DDL, and checks every CI migration entry-point family with a sensitivity proof.

**Still outstanding for the files adoption:** ERP pins kernel `0.1.0a24` and the
logical-prerequisite contract arrived in `0.1.0a56`. Repinning is its own slice.
Until then this revision is the *intended* provider for
`module_database_roles.v1` but is not yet bound to it, and `dotmac-files` is not
in ERP's `version_locations`.

## Historical production preflight, 2026-08-13

Read-only measurement on the explicitly named Seabone ERP database found one
Organization, maximum trimmed legal-name length 23, and zero names that are
blank or exceed the Tenant contract. `public.tenants`,
`public.tenant_domains`, `public.app_current_tenant_id()` and all three kernel
database roles were absent. At that date the candidate migration therefore had
no live catalogue to adopt and no current name-fit blocker; database-role
creation and full revision-0001 lineage authority were unresolved. This was
measurement only—no production migration or write ran. The later role-provider
decision above supersedes only the role disposition, not this measurement.
