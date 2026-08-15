"""The narrow tenant-catalog discovery contract.

`core_org.organization` is RLS-protected at migration heads. A batch entry
point therefore has a bootstrapping problem: it must learn *which* tenants
exist before it can open a tenant-scoped session for any of them, but reading
the catalog is itself a protected read. Under the `postgres` superuser that
problem is invisible. Under `app_user` (NOSUPERUSER/NOBYPASSRLS) the same query
returns zero rows and every scheduled task silently becomes a no-op that
exits 0.

## Why a SECURITY DEFINER function and not a credential

The rejected alternative is a second login with `BYPASSRLS` in the ordinary
application pool. That trades a narrow, auditable, single-purpose hole for a
general cross-tenant capability held by every worker in the deployment, for the
lifetime of the deployment.

This function is the narrow hole instead:

- It returns `SETOF uuid` and nothing else. Not a row, not a name, not a
  status — the minimum identifier a caller needs to then open
  `session_for_org`. A caller that wants an organization's *data* still gets it
  only through a tenant-scoped session, under RLS.
- It runs as its owner. Migrations execute as `app_admin`, which the role
  contract pins to `BYPASSRLS NOSUPERUSER`, so the function reads past the
  catalog policy while the *caller* (`app_user`) gains no such attribute.
- `search_path` is pinned to `pg_catalog` and every reference in the body is
  schema-qualified. Without both, a caller could prepend a schema, plant its own
  `organization` relation, and have this definer read it with `app_admin`'s
  rights — the classic definer hijack.
- `EXECUTE` is revoked from `PUBLIC` and granted only to `app_user`.
  `platform_api` is deliberately not granted: it has no batch entry point.

## Why the `include_inactive` argument exists

Two callers legitimately enumerate every organization rather than only the
active ones (`app/tasks/ar_allocation.py`, `app/tasks/gl_posting.py`: financial
allocation and GL posting must still settle a deactivated organization's open
documents). Splitting that into a second function would double the definer
surface for one boolean, so it is one argument with the safe default —
`false`, active only. The argument is a `boolean`, so it cannot smuggle a
predicate.

Revision ID: 20260815_tenant_catalog_discovery
Revises: 20260814_database_roles
Create Date: 2026-08-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "20260815_tenant_catalog_discovery"
down_revision = "20260814_database_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The one role that may discover tenant identifiers. Kept as a tuple so the
#: grant loop and the architecture test read the same list.
DISCOVERY_GRANTEES = ("app_user",)

SCHEMA = "tenant_catalog"
FUNCTION = "organization_ids"
SIGNATURE = f"{SCHEMA}.{FUNCTION}(boolean)"

_CREATE_FUNCTION = f"""
CREATE OR REPLACE FUNCTION {SCHEMA}.{FUNCTION}(include_inactive boolean DEFAULT false)
RETURNS SETOF uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $function$
    SELECT o.organization_id
    FROM core_org.organization AS o
    WHERE include_inactive OR o.is_active
    ORDER BY o.organization_id
$function$
"""


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite (the unit-test lane) has neither RLS nor SECURITY DEFINER.
        # `app.tenant_catalog` carries the matching dialect fallback.
        return

    # Checked BEFORE the function exists. The definer's whole security
    # argument is "it runs as app_admin"; if Alembic ran as something else the
    # function would inherit that identity's rights instead. Creating it first
    # and objecting afterwards would leave the wrongly-owned definer installed
    # in the failed transaction's wake on any non-transactional path.
    owner = str(bind.scalar(sa.text("SELECT current_user")))
    if owner != "app_admin":
        raise RuntimeError(
            f"tenant-catalog definer would be owned by {owner!r}, not "
            "'app_admin'. A SECURITY DEFINER function executes with its "
            "owner's privileges, so installing it under any other identity "
            "changes what this revision was reviewed to grant."
        )

    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.execute(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM PUBLIC")
    op.execute(_CREATE_FUNCTION)

    # A function is EXECUTE-able by PUBLIC the moment it is created, so the
    # revoke is not decoration: without it every role in the cluster could
    # enumerate tenants. Revoke first, then grant the exact list.
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    for role in DISCOVERY_GRANTEES:
        op.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}")
        op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO {role}")


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute(f"DROP FUNCTION IF EXISTS {SIGNATURE}")
    # The schema holds nothing else; dropping it non-cascading is safe and
    # fails loudly if a later revision added an object without updating this.
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
