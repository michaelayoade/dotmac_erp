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
| DB roles `app_admin`, `app_user`, `platform_api` | Unresolved. A later composition slice must inspect existing cluster roles and privileges and create/adopt them without passwords or privilege escalation. | Blocked |
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

## Executable lineage ratchet

`tests/integration/test_kernel_lineage_rehearsal.py` ports Sub's proven
installed-lineage rehearsal into ERP. It provisions a unique disposable
PostgreSQL database, builds ERP through its real Alembic chain, and then runs
the exact installed Kernel migrations through a separate
`public.dotmac_kernel_alembic_version` table. It exercises both an empty
database upgraded to current head and the real
`20260812_merge_expand_withdrawal` predecessor with an Organization inserted
before the tenant-projection upgrade.

Both paths currently fail at `0001_initial_tenant_schema` when it tries to
create the already hosted `public.tenants` table. The failure is the expected
ratchet state, not a skip: the test asserts PostgreSQL `DuplicateTable`, the
active revision and object name. It also proves the failed transaction records
no Kernel revision and creates none of the three Kernel database roles that
were absent before the attempt. A changed first failure is progress or
regression and must be dispositioned deliberately.

The rehearsal does not make composition admissible. It converts the first
lineage blocker from prose into an executable database fact and stays red if a
future change merely stamps the revision or runs a copied lineage.

## Cutover rule

ERP may finish additive collision dispositions in coherent slices, but the
revision-0001 lineage ratchet moves once and only after every atomic effect is
adjudicated and rehearsed together. The next lineage slice must apply the
accepted independent assembly-lineage/create-or-adopt pattern already being
proved by the Sub reference adopter. The executable rehearsal above is the
first reused part of that contract: verify catalogue/RLS/grant effects, reject
drift and rehearse rollback/stamp behavior without pretending an incompatible
effect ran. A raw Alembic stamp, a product-specific conditional copied into the
kernel migration, or a blanket `IF EXISTS` is not evidence and remains
forbidden.

This ERP slice is not authority to supply a new generic kernel facility during
ADR-0017's adoption gate. Reuse the proven Starter/Sub contract; if ERP exposes
a genuinely missing reusable seam, preserve it as evidence until two adopters
prove the same need.

`dotmac-files` stays gated even though its foreign-key target now exists: its
`fi_0001_stored_files` lineage explicitly depends on
`0001_initial_tenant_schema`. Alembic must be able to resolve that dependency
truthfully before the files lineage is added to ERP's `version_locations`.

## Production preflight, 2026-08-13

Read-only measurement on the explicitly named Seabone ERP database found one
Organization, maximum trimmed legal-name length 23, and zero names that are
blank or exceed the Tenant contract. `public.tenants`,
`public.tenant_domains`, `public.app_current_tenant_id()` and all three kernel
database roles are absent. The candidate migration therefore has no live
catalogue to adopt and no current name-fit blocker; database-role creation and
full revision-0001 lineage authority remain unresolved exactly as the matrix
states. This was measurement only—no production migration or write ran.
