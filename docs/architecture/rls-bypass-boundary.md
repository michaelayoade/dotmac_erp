# ERP cross-organization and PostgreSQL RLS boundaries

**Status:** Accepted; corrected against migration heads on 2026-08-15
**Decision owner:** Michael

## Measured state

ERP has two mechanisms that must not be conflated:

| Mechanism | Enforced by | Migration-defined reach | Disposition |
|---|---|---|---|
| `session.info["allow_cross_org"]` | SQLAlchemy ORM listener | Application SELECT filtering across the ERP ORM estate | Retained |
| `app.bypass_rls` | PostgreSQL policies | 105 tables after `alembic upgrade heads` | Runtime writer retired; replacement contracts required before `app_user` cutover |

The first audit measured stale production at
`20260808_open_setting_domain`: 16 RLS-enabled `training.*` tables. It then
incorrectly described that deployment snapshot as ERP's design and concluded
that every GUC writer was a database-layer no-op.

A clean database at migration heads has 420 tables, 158 with RLS enabled, and
105 whose policies consult `should_bypass_rls()`. (Re-measured 2026-08-15; this
prose previously read 418/103. The classification partitions exactly —
312 direct + 85 inherited + 3 platform + 20 unclassified = 420 — so the
correction is two more direct tenant tables and two more dependent policies.)
The no-op claim is withdrawn.
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

## Caller disposition inventory

The post-removal audit is checked in at
`docs/inventories/rls-cross-org-callers.tsv`. It records the exact runtime
syntax under `app/` and active `scripts/`: 198 owning functions and 200
boundary uses across 61 files. A table appearing in the migrated catalog and a
caller appearing in this inventory are separate facts; neither is inferred
from the other.

The reviewed state at 2026-08-15 is:

| Contract | Callers | Cutover meaning |
|---|---:|---|
| ordinary `app_user` | 91 | Ready only because the named target tables have no RLS at migration heads; this is least privilege, not tenant isolation |
| narrow tenant-catalog definer, then `session_for_org` | 35 | Blocked; 36 of the original 71 converted |
| narrow tenant-resolution definer, then `session_for_org` | 27 | Blocked |
| isolated cross-tenant service | 8 | Blocked; no credential or service exists yet |
| replace with the existing tenant session | 2 | Blocked |

The 72 blocked callers are a two-directional ratchet. A new caller fails the
exact inventory; removing or converting one also fails until the inventory and
baseline are lowered in the same review. A `ready` row is structurally refused
when it claims access to an RLS-protected table.

The low-level ORM marker is a closed writer set, independent of the inventory:
`allow_cross_org`, `cross_org_session`, and the API dependency session owner.
A sensitivity proof covers aliased helpers, FastAPI dependencies, and a direct
marker write. The inventory therefore cannot be bypassed by adding a wrapper.

This completes disposition, not remediation. The `app_user` cutover remains
blocked.

## What each caller actually reaches (`target_relations`)

The table above is a snapshot of the reviewed state at 2026-08-15. The
inventory carries 148 rows, and every row names the schema-qualified relations
its bypass region reaches.

That column exists because the disposition was previously unfalsifiable. A row
could describe its own reach in prose and name a relation the caller never
queries — `app/startup.py::warn_unconfigured_webhook_allowlist` claimed
`core_org.organization` and touches `public.domain_settings` and
`automation.workflow_rule`; all three `app/api/audit.py` rows named
`audit.audit_log` where the caller reads `public.audit_events`. Nothing
compared either claim to anything, because there was nothing to compare it to.

`target_relations` is a sorted, deduplicated, `|`-joined list of
`schema.table` names, traced from source one bypass region at a time. `-` is
the sentinel for a region that issues no statement — a legal value and never a
legal disposition, kept so the guard bites on the next such caller rather than
on the last one. The format is parsed by
`tests/architecture/test_cross_org_caller_dispositions.py`, so a name that is
not a relation, or a list that is unsorted or duplicated, fails the build.

### Unbounded reach

Three callers' relation sets are not fixed by their own bodies, and none is
given an invented one. They sit in a named two-directional ratchet in
`tests/architecture/test_cross_org_caller_dispositions.py`:

- `app/services/settings_seed.py::_global_settings_seed.scoped_seed` — the
  bypass region is a decorator body wrapping an arbitrary callable, so its
  reach is fixed by the seven `@_global_settings_seed` call sites. A new
  decorated seed widens it without touching the row.
- `app/tasks/data_health.py::_task_session` — the bypass region returns a
  session context to its caller rather than issuing a statement, so its reach
  is the union of the ten data-health tasks that pass `organization_id=None`.
  A new task widens it without touching the row.
- `app/tasks/finance.py::refresh_analysis_cubes` — the None-organization
  branch issues `REFRESH MATERIALIZED VIEW` on a name read out of
  `rpt.analysis_cube.source_view`, so the refreshed relation is a row *value*.
  It also reads `pg_catalog.pg_matviews`, which no tenant catalog can contain.

Their `target_relations` is still populated: unbounded means the recorded set
is a lower bound, not that it is unknown. The backlog shrinks by narrowing a
caller, never by moving a row into it.

## The `app_user` proof, and what it does not prove

Everything above is static: relations traced from source, dispositions written
against them. Nothing had executed a statement as the role the whole programme
is about. `tests/integration/test_app_user_cross_org_reachability.py` does, in
the PostgreSQL lane, after `scripts/bootstrap_database_roles.py` and
`alembic upgrade heads`. For each of the 48 relations the ledger's
`target_relations` column names, it ATTEMPTS the read and records the outcome in
`tests/integration/app_user_reachability.tsv` — exact rows, not counts.

Becoming the role. The bootstrap deliberately sets no password, so no direct
`app_user` DSN generally exists and the lane connects as the superuser and
issues `SET LOCAL ROLE`. That is sound for what is asserted here: PostgreSQL
evaluates both the superuser exemption and `rolbypassrls` against
`current_user`, and the test asserts `current_user`, `(rolsuper, rolbypassrls)
= (false, false)`, `should_bypass_rls()` false, and no organization GUC — the
precondition, not an assumption. An `APP_USER_DATABASE_URL` knob selects a
direct login where a deployment provides one, and refuses a URL carrying a
password: credentials go in `PGPASSWORD`, as the CI bootstrap step already
does.

Today's measurement is almost entirely `denied-no-grant`: no migration issues a
table-level `GRANT … TO app_user`, only `EXECUTE` on two functions, so
`app_user` holds `SELECT` on 1 of 420 relations. That produces four honest
limits the module's own docstring carries:

- **A `ready` row is not proved reachable.** All 91 have an unreachable target,
  recorded as a two-directional ratchet rather than asserted, because 91 red
  rows on the first run is how a gate gets deleted. It becomes a real assertion
  in the change that adds the grants.
- **A `blocked` row is not proved refused.** `denied-no-grant` is the absence of
  a privilege, not the presence of a boundary; 158 `known_gaps` relations carry
  no RLS at all and would return every organization's rows once granted.
- **Reachability is not isolation.** Every policy's first disjunct is
  `should_bypass_rls()`, and `app.bypass_rls` is `PGC_USERSET` — `app_user` can
  set it itself. The measurement is conditional on that GUC being unset, which
  is why the test asserts it.
- **It measures relations, not callers**, and the design catalog, not
  production.

The expectation file is a DERIVATION, not a typed claim: `app_user_priv`
decides granted-or-not and `rls_enabled` decides the rest, both read out of
`tests/integration/tenant_table_inventory.tsv`, and a test asserts that every
recorded row reproduces from it. This is the one part of the bridge programme
that is real evidence about the live system rather than intent, which is why it
survives the retirement reframing below.

## The tenant-catalog discovery contract

Revision `20260815_tenant_catalog_discovery` installs
`tenant_catalog.organization_ids(include_inactive boolean)`: `SECURITY
DEFINER`, `RETURNS SETOF uuid`, `search_path` pinned to `pg_catalog`, body
schema-qualified, `EXECUTE` revoked from `PUBLIC` and granted to `app_user`
alone. `app/tenant_catalog.py` is its only caller; `for_each_organization` in
`app/db/session_context.py` composes it with `session_for_org`.

**No credential was created.** The privilege lives in the function's owner —
`app_admin`, already pinned `BYPASSRLS NOSUPERUSER` by the role contract — not
in the application's login. `app_user` gains the ability to learn *which*
tenants exist and nothing else; every read of a tenant's data still happens in
a tenant-scoped session under RLS. The rejected alternative, a second
`BYPASSRLS` login in the ordinary worker pool, would have traded one narrow
audited hole for a general cross-tenant capability held by every worker.

The definer returns identifiers only, and that is enforced rather than
documented (`tests/architecture/test_tenant_catalog_contract.py`). Two
consequences are deliberate:

- A caller that needs an organization *column* reads it inside that
  organization's own session. `app/tasks/pms.py` filters on
  `pms_ohcsf_enabled` this way, because widening the definer to carry a domain
  column would make it a cross-tenant read path for that column.
- Callers that previously scanned a domain table cross-tenant to find "orgs
  with due work" must fan out over the catalog and let each tenant's own
  scoped query answer. That is a query-count change, not a correctness one, and
  it is why the remaining 38 are a separate slice rather than a mechanical
  rewrite.

33 callers converted in #306. PR #307 converted the first three of the
domain-scan shape — the `app/tasks/discipline.py` reminder jobs — as a proving
slice: enumerate through the catalogue, then let each tenant's own scoped
session answer "what is due here". The grouping helper and the per-organization
id re-fetch it fed both disappeared, because a tenant-scoped service query
returns that organization's rows directly.

That slice widened no definer and added no credential. 35 remain in this
family, to be taken in coherent domain groups.

Two practical notes for those groups.

First, learned in #306: tests that monkeypatch `cross_org_session` on a
converting module fail with `AttributeError` before asserting anything.
Inventory the patch targets before moving a seam. `discipline.py` had none,
which is part of why it went first; `app/tasks/data_health.py` has 17, and its
single ledger row hides nine entry points, so it gets a dedicated slice.

Second, and more important: **test-patch count is not a grouping key.**
`audit.py`, `notifications.py` and `outbox_relay.py` carry the
`tenant_resolution_definer` contract, not this one, and are blocked on a
contract that does not exist yet — sequencing them by patch count would have
started work that cannot finish. `hooks.py` is mixed: its cleanup path is a
catalog fan-out, its execution path is resolution, so one owner takes it across
both phases rather than two owners colliding in the same file. Group by the
`contract` column; treat the label as a first pass and re-check the shape.

## Ordered follow-up

1. The migrated catalog inventory is the policy/caller audit input; the stale
   production snapshot remains a deployment-drift report only.
2. **Caller dispositions.** Convert the remaining catalog fan-outs. A
   `tenant_catalog_definer` label is a first pass, not a verdict: several rows
   carrying it are resolution- or special-shaped, and reclassifying one does
   **not** lower the blocker count. The ratchet falls only when a caller is
   actually converted.
3. **Resolution contracts.** Design the tenant-resolution contract, then convert
   the callers that resolve which organization owns one specific row. This is
   the gate for `audit.py`, `notifications.py`, `outbox_relay.py` and the
   execution half of `hooks.py`.
4. **Isolated/offline boundaries** for the irreducible remainder.
5. **Exact catalog classification.** Land the corrected 420/312/105 baseline and
   make inherited tenant debt enforceable — 79 inherited tenant tables have no
   RLS and no ratchet watching them. Repairing policies against a catalog that
   under-counts the surface would certify the gap.
6. **RLS/FORCE/GUC repair.** Forward-migrate every dependent policy to tenant
   predicates only, add FORCE where missing, prove `pg_depend` has no remaining
   reference, and drop `should_bypass_rls()`. Turn the strict GUC-policy xfail
   into a pass and prove tenant visibility and cross-tenant denial through a
   separate real `app_user` connection, never `SET ROLE`.
7. **Production-only table disposition.**
8. **Deployment preflight**, OpenBao-mediated secret injection, and a migration
   rehearsal against a production clone.
9. **Two deployment stages, deliberately separate.** First ship
   `app_user`-compatible code and migrations **while retaining the current
   runtime credential** — that release changes no identity and is revertible on
   its own. Then, after burn-in, cut the runtime over to `app_user` as its own
   change. Combining them would put a code regression and a privilege regression
   in one blast radius, with a single rollback for two unrelated failures.

Production deployment happens only after every gate above passes, and only
against a host named explicitly at the time.

## Rejected shapes

- Treating the production snapshot as migration design.
- Restoring the custom GUC as an authorization mechanism.
- Deleting `get_db_*_bypass` dependencies wholesale; they still own the live
  ORM-listener boundary.
- Granting `app_user` `BYPASSRLS` or placing a cross-tenant credential in the
  ordinary application pool.
- Calling this removal a no-op. It intentionally removes database authority
  that the future `app_user` runtime cannot safely retain.
