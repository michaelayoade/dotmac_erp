# Organization-to-Tenant boundary

**Status:** implemented context, projection and lineage-ratchet slices (E8 slices 3–5)  
**Authority:** `core_org.organization`  
**Mapping owner:** `app.tenancy.OrganizationTenantContext`  
**Projection writer:** `app.services.tenant_projection`  
**Session/transaction owner:** `app.db` and `app.db.session_context`

## Decision

An ERP Organization maps to shared-module tenant scope by identity:

```text
tenant_id = organization_id
```

There is no allocated second identifier, nullable mapping, sentinel tenant, or
mapping table. `core_org.organization` remains authoritative for lifecycle,
name, activation and hierarchy. `public.tenants` is a projection of that
Organization and uses the same UUID; it is not a parallel tenant writer.

The adapter is persistence-free. It accepts an already-validated UUID and
returns the two typed names for that one identity. Transport strings are parsed
before this boundary.

## Tenant catalogue projection

ERP migration `20260813_tenant_projection` hosts `public.tenants` and
`public.tenant_domains` with the pinned kernel model's shape, without composing
or stamping the kernel lineage. Runtime imports are narrower still:
`app.services.tenant_projection` may import `Tenant`; every Party, credential,
RBAC and session model remains prohibited by a symbol-level architecture guard.

The projection is deterministic:

- `Tenant.id = Organization.organization_id`;
- `Tenant.slug = "erp-" + Organization.organization_id` — immutable platform
  identity, deliberately independent of the editable careers/product slug;
- `Tenant.name = trim(Organization.legal_name)`, failing rather than truncating
  beyond the kernel's 120-character contract; and
- `Tenant.is_active = Organization.is_active`.

`suspended_at` is not an Organization fact and the reconciler preserves it.
Hard Organization deletion tombstones the Tenant instead of deleting it, so a
shared module's historical foreign keys do not disappear. Create, update,
retirement and projection changes commit in the same ERP-owned transaction;
the projection service only mutates and never commits or rolls back.

The migration is forward-fix only. Once tenant identity exists, a downgrade
cannot safely tell a newly created catalogue from a verified adopted one, and
dropping it would invalidate stateful-module foreign keys. It refuses rather
than performing a destructive or falsely reversible rollback.

The migration refuses an incompatible pre-existing catalogue, source names
that cannot fit, mismatched projections, active orphan tenants and slug
collisions. It inserts only missing truthful rows. It also creates or verifies
`public.app_current_tenant_id()`, the exact function consumed by shared-module
RLS, while keeping both catalogue tables outside RLS and unavailable to
`PUBLIC`/`app_user` writers.

The writer inventory is closed, not conventional: both web Organization update
surfaces, `scripts/create_org.py`, E2E organization creation and the generated
instance bootstrap reconcile before their owning commit. An architecture test
enumerates direct constructors, lifecycle-field assignments and deletes across
`app/` and `scripts/`. The only exemptions are the shadowed legacy admin
fallback and an explicitly archived one-off rename, with both premises checked
in the same test.

## Session and RLS contract

ERP keeps its existing engine and `SessionLocal` as the sole transaction
authority. Adopting shared modules must not import `dotmac_kernel.db`, create a
second session factory, or commit inside a module service.

Every canonical tenant primer now establishes all three enforcement inputs:

1. `session.info["organization_id"]` for ERP's ORM listener;
2. transaction-local `app.current_organization_id` for ERP RLS policies; and
3. transaction-local `app.current_tenant` for shared-module RLS policies.

The two PostgreSQL GUCs are set in one parameterised `set_config` statement and
re-armed together on every SQLAlchemy `after_begin`. A commit or rollback
therefore cannot leave the session claiming one scope while the database uses
another.

`app.rls` is the only writer of `app.current_tenant`. Entry points use
`get_db_with_org`, `get_db_for_org`, `prime_tenant_context`, or
`session_for_org`; they do not spell either tenant GUC themselves.

## Bypass boundary

Runtime code has no writer for the legacy user-settable `app.bypass_rls` GUC.
A clean database at migration heads has 103 tables with policies that still
consult it until a forward migration rewrites them. The earlier 16-table count
described stale production, not ERP's migration design. No application, worker,
CLI, cron SQL, or archived executable can assert the GUC; historical Alembic
revisions remain immutable.

`session.info["allow_cross_org"]` is a separate application-layer boundary. It
continues to bypass the ORM listener for approved pre-auth and administrative
flows, but it does not bypass PostgreSQL RLS. Removing the GUC writer therefore
makes migrated cross-tenant reads fail closed under `app_user`; the runtime-role
cutover is blocked until those callers have individual database contracts.

A cross-organization job that touches an RLS-protected ERP or module table must
enumerate Organizations under an approved discovery path, close that session,
and do each tenant's work under `session_for_org`. `cross_org_session` bypasses
only the ORM listener; it is not a database privilege.

## What remains gated

The projection slice composes no external migration and admits only the exact
kernel `Tenant` model in its named writer. Kernel revision
`0001_initial_tenant_schema` is atomic and still collides with ERP's people,
credentials, roles and audit tables. It must not be copied, conditionally
skipped, or stamped merely because the tenant catalogue is now hosted.

Before `dotmac-files` can be adopted, a later E8 slice must:

1. disposition every collision in kernel revision 0001 so stamping or running
   it is truthful;
2. install the composed-lineage gate and prove fresh plus upgrade migration
   paths; and
3. only then compose the released `fi` lineage, whose tenant table has a real
   foreign key to `public.tenants`.

The disposition matrix is `docs/architecture/kernel-0001-dispositions.md`.
E8 slice 5 now executes the installed Kernel lineage against both ERP's fresh
and predecessor-upgrade schemas and pins its first failure at revision 0001's
attempt to recreate `public.tenants`; it does not resolve the atomic identity,
RBAC, audit, RLS or grant dispositions.

## Evidence

- `tests/db/test_tenant_mapping.py`
- `tests/db/test_rls_tenant_bridge.py`
- `tests/db/test_session_context.py`
- `tests/test_session_context_survives_commit.py`
- `tests/architecture/test_tenancy_mapping_boundary.py`
- `tests/architecture/test_tenant_projection_boundary.py`
- `tests/integration/test_tenant_scope_bridge.py`
- `tests/services/test_tenant_projection.py`
- `tests/migrations/test_tenant_projection_migration.py`
- `tests/integration/test_tenant_projection_migration.py`
- `tests/integration/test_kernel_lineage_rehearsal.py`
