# ERP cross-organization and PostgreSQL RLS boundaries

**Status:** Accepted; Step 2 implemented in this branch
**Date:** 2026-08-15
**Decision owner:** Michael

## Measured state

ERP has two mechanisms whose old composition made them look like one bypass:

| Mechanism | Enforced by | Current reach | Disposition |
|---|---|---|---|
| `session.info["allow_cross_org"]` | SQLAlchemy ORM listener | Organization filtering across the ERP ORM estate | Retained |
| `app.bypass_rls` | 16 `training.*` PostgreSQL policies | No runtime caller queries those tables | Runtime writer retired |

No application, worker, CLI, cron SQL, or archived executable caller of the
GUC touches any of the 16 RLS-protected tables. The GUC therefore changes no
runtime result today. It remains dangerous: as RLS coverage grows, a surviving
writer would silently pre-defeat every new policy that copied the legacy
predicate.

`allow_cross_org` is not dead. The ORM listener in `app/db/org_listener.py`
uses it across the application estate, which is where almost all current ERP
tenant filtering occurs. Removing or silently narrowing it would change live
authorization behavior.

## Exact runtime disposition

| Runtime source | Dead GUC use removed | Effective behavior retained |
|---|---|---|
| `app/rls.py` | async, sync, context-manager, and connection writers | tenant-scope primers |
| `app/api/deps.py` | request-session setter | `allow_cross_org`, lifecycle, guards |
| `app/db/session_context.py` | cross-session setter and re-arming | dedicated session and ORM marker |
| `app/services/auth_dependencies.py` | admin-auth setter | live admin-role revalidation |
| `app/services/careers/careers_service.py` | slug-lookup wrapper | `Organization` remains ORM deny-listed, then the caller primes tenant scope |
| `app/services/people/hr/onboarding.py` | token-lookup wrapper | unchanged unprimed pre-auth query; the removed GUC never affected the ORM listener |
| `scripts/archive/remediate_splynx_credit_note_gl.py` | two wrappers | existing `allow_cross_org` blocks |
| `scripts/archive/fix_splynx_credit_note_signs.py` | wrapper | existing unprotected raw-SQL behavior |
| `scripts/archive/migration/2026-06-04_parent_multisite_customers.py` | raw setter | existing `allow_cross_org` block |
| `scripts/archive/reconstruct_inventory_valuation.py` | raw setter | existing unprotected raw-SQL behavior |
| `scripts/audit_subledger_to_gl.sql` | raw setter | current read-only SQL behavior |
| `scripts/audit_tax_monthly_recon.sql` | raw setter | current read-only SQL behavior |
| `scripts/merge_duplicate_departments.py` | raw setter | current raw-connection SQL behavior; its future protected-domain slice owns disposition |

Alembic revisions and the PostgreSQL policy-integration fixture are not runtime
callers. They remain until the Step 3 forward migration changes the installed
policies and their test setup.

## Accepted boundary

### Runtime code cannot assert a PostgreSQL bypass

`app.rls` owns only the two tenant-scope GUCs:

- `app.current_organization_id`; and
- `app.current_tenant`.

The async, sync, and connection-level bypass helpers are deleted. A structural
guard scans every runtime entry-point family under `app/` and `scripts/`,
including archived executable scripts and raw SQL, and rejects helper calls or
SQL that sets/reads the bypass GUC. Its sensitivity proof injects both a Python
caller and a raw-SQL setter and proves both are found.

Historical Alembic revisions remain immutable and are outside that runtime
guard. PostgreSQL integration setup may continue to exercise the installed
legacy policies until Step 3 owns their forward rewrite.

### `allow_cross_org` remains application-layer only

`get_db_admin_bypass`, `get_db_auth_bypass`, and `cross_org_session` retain
their existing public names for caller compatibility, but set only
`session.info["allow_cross_org"]`. They do not set any PostgreSQL bypass and
cannot read through RLS-protected tables without a tenant context.

The names must not be cited as evidence of database authority. Their route
guards and existing individual authorization checks remain required.

### Future database-wide authority stays deferred

No cross-tenant database login, pool, service, or third credential is created
in this step. Such a boundary has little meaning while only 16 of 420 tables
have RLS, and the caller dispositions may eliminate most of its demand.

When identity/pre-auth tables receive RLS, slug/token bootstrap lookups move to
the narrowest reviewed PostgreSQL contract, normally a purpose-specific
`SECURITY DEFINER` function returning only the tenant identifier. That work
belongs to the domain migration that creates the protection; this step does not
ship speculative functions over unprotected tables.

## Ordered follow-up

1. Step 3 rewrites the 16 `training.*` policies to the tenant predicate only,
   drops `should_bypass_rls()` in a forward migration, and turns the strict
   inventory xfail into a passing assertion.
2. The least-privilege cutover proves routes, jobs, workers, scripts, ownership,
   and grants under `app_user`. It is not called tenant isolation.
3. Each later RLS domain slice dispositions its cross-organization callers and
   database-enforced tenant relationships before enabling its policies.

## Rejected shapes

- Restoring the GUC because a future policy might need it. That would recreate
  the unprivileged escape before the protected domain exists.
- Deleting `get_db_*_bypass` dependencies wholesale. Those dependencies also
  own the real ORM-listener boundary and their removal changes current behavior.
- Granting the ordinary application login `BYPASSRLS`. That would make every
  pooled runtime connection permanently cross-tenant.
- Building the isolated cross-tenant service now. Its genuine residual demand
  can only be measured after database isolation exists.
