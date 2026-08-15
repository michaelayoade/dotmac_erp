# ERP cross-organization and PostgreSQL RLS boundaries

**Status:** Accepted; corrected against migration heads on 2026-08-15
**Decision owner:** Michael

## Measured state

ERP has two mechanisms that must not be conflated:

| Mechanism | Enforced by | Migration-defined reach | Disposition |
|---|---|---|---|
| `session.info["allow_cross_org"]` | SQLAlchemy ORM listener | Application SELECT filtering across the ERP ORM estate | Retained |
| `app.bypass_rls` | PostgreSQL policies | 103 tables after `alembic upgrade heads` | Runtime writer retired; replacement contracts required before `app_user` cutover |

The first audit measured stale production at
`20260808_open_setting_domain`: 16 RLS-enabled `training.*` tables. It then
incorrectly described that deployment snapshot as ERP's design and concluded
that every GUC writer was a database-layer no-op.

A clean database at migration heads has 418 tables, 158 with RLS enabled, and
103 whose policies consult `should_bypass_rls()`. The no-op claim is withdrawn.
Several administrative, pre-auth and batch paths touch protected domains in
that migrated design.

Production currently connects as PostgreSQL `postgres`, whose superuser status
bypasses RLS independently of both mechanisms. Removing the GUC writer therefore
does not change that stale deployment's result, but superuser execution is not
evidence that the application contract is complete or tenant-safe.

`allow_cross_org` remains effective and separate. The listener in
`app/db/org_listener.py` uses it for application-layer SELECT filtering.
Deleting the route dependencies would remove that live boundary while claiming
to remove the PostgreSQL escape, so their public names remain for now.

## Runtime removal

The user-settable GUC cannot be a legitimate cross-tenant capability. Any
ordinary PostgreSQL role can set a custom `app.*` parameter, so retaining the
writer would let `app_user` grant itself the same database reach as an approved
administrator.

The runtime writer is removed from these source families:

| Runtime source | PostgreSQL GUC use removed | Behavior retained |
|---|---|---|
| `app/rls.py` | async, sync, context-manager and connection writers | tenant-scope primers |
| `app/api/deps.py` | request-session setter | `allow_cross_org`, lifecycle and guards |
| `app/db/session_context.py` | cross-session setter and re-arming | dedicated session and ORM marker |
| `app/services/auth_dependencies.py` | admin-auth setter | live admin-role revalidation |
| careers and onboarding services | slug/token wrappers | existing lookup path followed by tenant priming |
| seven executable/archive scripts | helper or raw-SQL setters | their existing ORM/raw-SQL flow, now without database bypass |

A structural guard scans Python, shell and SQL entry points under `app/` and
`scripts/`. It rejects helper calls and direct reads/writes of
`app.bypass_rls`; its sensitivity proof plants both a Python helper call and a
raw SQL setter.

Historical Alembic revisions remain immutable, and the integration fixture may
exercise the installed legacy policies until the forward policy migration owns
their removal.

## Accepted boundary

### Runtime code cannot assert database-wide authority

`app.rls` owns only tenant scope:

- `app.current_organization_id`; and
- `app.current_tenant`.

`get_db_admin_bypass`, `get_db_auth_bypass`, and `cross_org_session` retain
their compatibility names but now set only `allow_cross_org`. They cannot read
an RLS-protected table without tenant context. That fail-closed result is
deliberate; the names must not be cited as database authority.

### `app_user` cutover requires caller-by-caller disposition

The runtime-role cutover is blocked until every administrative, pre-auth,
worker, job, CLI and maintenance caller that needs cross-tenant data has one
reviewed contract:

1. enumerate tenant identifiers through an approved catalog path, close that
   session, then process each tenant through `session_for_org`;
2. use a narrow `SECURITY DEFINER` function for pre-auth identity discovery,
   returning only the minimum tenant identifier/result; or
3. for irreducible database-wide operations, use an isolated process/service
   with a distinct `NOSUPERUSER BYPASSRLS` credential, reviewed grants, no object
   ownership, and no substitution into the ordinary application pool.

`app_admin` remains migration/offline-only. No application route, worker pool or
web process receives that credential.

This PR creates no cross-tenant role, pool, service, Bao field or credential.
Those are added only for residual, measured demand after individual
disposition—not as a blanket replacement for the GUC.

## Ordered follow-up

1. The migrated catalog inventory is the policy/caller audit input; the stale
   production snapshot remains a deployment-drift report only.
2. Disposition cross-organization callers against the 103-table policy set.
3. Forward-migrate every dependent policy to tenant predicates only, add FORCE
   where missing, prove `pg_depend` has no remaining reference, and drop
   `should_bypass_rls()`.
4. Turn the strict GUC-policy xfail into a pass and prove tenant visibility and
   cross-tenant denial through a separate real `app_user` connection, never
   `SET ROLE`.
5. Perform the least-privilege runtime cutover only after routes, jobs, workers,
   scripts, ownership and grants pass under their final credentials.

## Rejected shapes

- Treating the production snapshot as migration design.
- Restoring the custom GUC as an authorization mechanism.
- Deleting `get_db_*_bypass` dependencies wholesale; they still own the live
  ORM-listener boundary.
- Granting `app_user` `BYPASSRLS` or placing a cross-tenant credential in the
  ordinary application pool.
- Calling this removal a no-op. It intentionally removes database authority
  that the future `app_user` runtime cannot safely retain.
