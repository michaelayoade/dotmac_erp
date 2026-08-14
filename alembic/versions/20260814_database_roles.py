"""Adopt the three Dotmac database roles, and refuse to run without them.

This is ERP's provider for the logical prerequisite `module_database_roles.v1`
(Starter ADR-0006 D1 amendment). It **verifies**; it never creates.

## Why a migration does not create a role

`CREATE ROLE` needs superuser or `CREATEROLE`. An ordinary `alembic upgrade`
runs as unprivileged `app_admin` and must never hold those privileges — a
migration that creates roles is a second authority over cluster access, and one
that quietly escalates to get there is worse. Creation is a separate, explicitly
elevated operator step: `scripts/bootstrap_database_roles.py`.

So this revision fails closed. If the roles are absent or wrong-shaped, the
upgrade stops here, before any dependent module migration can grant to an
identity that does not exist or is not isolated.

## Why the attributes are checked, not just existence

A role of the right NAME with the wrong POSTURE is worse than a missing one,
because everything downstream then appears to work:

- **A superuser bypasses RLS regardless of `rolbypassrls`.** An
  `app_user SUPERUSER NOBYPASSRLS` would satisfy a naive existence check while
  defeating tenant isolation for every module in the deployment, silently.
- An `app_admin` that can neither bypass RLS nor act as superuser turns every
  offline maintenance job into a zero-row success that exits 0.

`app_admin` is required to be `BYPASSRLS` and **not** superuser: its real
requirement is reading past RLS, and accepting a superuser would certify
cluster-wide authority to satisfy it.

## Deliberately not a kernel import

The check is plain SQL rather than `dotmac_kernel.migrations.verify`, because
ERP still pins kernel `0.1.0a24` and the prerequisite contract arrived in
`0.1.0a56`. Repinning is its own slice. When it happens, this revision becomes
the bound provider for `module_database_roles.v1` without changing what it does.

Revision ID: 20260814_database_roles
Revises: 20260813_tenant_projection
Create Date: 2026-08-14
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision = "20260814_database_roles"
down_revision = "20260813_tenant_projection"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: `(rolbypassrls, rolsuper)`. Copied deliberately rather than imported: a
#: migration is a snapshot of an accepted decision, and importing a mutable
#: runtime value would let a later edit change what an applied revision meant.
#: `tests/architecture/test_database_role_contract.py` pins these against
#: `scripts/bootstrap_database_roles.py` so the two cannot drift.
ROLE_CONTRACT: dict[str, tuple[bool, bool]] = {
    "app_admin": (True, False),
    "app_user": (False, False),
    "platform_api": (False, False),
}

_BOOTSTRAP = "scripts/bootstrap_database_roles.py"


def _posture(bypassrls: bool, superuser: bool) -> str:
    return (
        f"{'BYPASSRLS' if bypassrls else 'NOBYPASSRLS'}/"
        f"{'SUPERUSER' if superuser else 'NOSUPERUSER'}"
    )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    rows = bind.execute(
        sa.text(
            "SELECT rolname, rolbypassrls, rolsuper FROM pg_roles "
            "WHERE rolname = ANY(:names)"
        ),
        {"names": list(ROLE_CONTRACT)},
    ).all()
    observed = {str(r[0]): (bool(r[1]), bool(r[2])) for r in rows}

    missing = sorted(set(ROLE_CONTRACT) - set(observed))
    if missing:
        raise RuntimeError(
            f"database role(s) {missing} do not exist. A migration never creates "
            f"a role — run the explicitly privileged `{_BOOTSTRAP}` with "
            "BOOTSTRAP_DATABASE_URL first. Refusing to continue, because a "
            "later module migration would otherwise grant to an identity that "
            "is not there."
        )

    problems: list[str] = []
    for role, expected in ROLE_CONTRACT.items():
        if observed[role] != expected:
            problems.append(
                f"{role} is {_posture(*observed[role])}, contract requires "
                f"{_posture(*expected)}"
            )
    if problems:
        raise RuntimeError(
            "database role posture violates the contract: "
            + "; ".join(problems)
            + f". A superuser bypasses row-level security whether or not "
            f"rolbypassrls is set, so both attributes are checked. Correct it "
            f"with `{_BOOTSTRAP} --repair` and re-run."
        )


def downgrade() -> None:
    """Nothing to undo: this revision creates no object and owns no role.

    Roles are cluster-wide and shared with every other database that composes
    Dotmac modules, so dropping one on downgrade would break neighbours that
    this migration never created anything for.
    """
