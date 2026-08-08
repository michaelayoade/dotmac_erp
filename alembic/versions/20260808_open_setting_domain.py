"""domain_settings.domain becomes an open VARCHAR, and the settingdomain enum is dropped.

A PostgreSQL enum makes the set of domains a schema fact, so adding one costs an
`ALTER TYPE ... ADD VALUE` migration — `20260224_add_settingdomain_banking.py` is
exactly that and nothing else, and `add_settingdomain_values.py` before it. Which
domains are real is now a runtime declaration owned by the module that owns the
settings, validated by `app.services.setting_domains` at startup and at every
write (Governance ADR 0007).

The conversion is `USING domain::text`, which preserves every existing value
verbatim — including `operations`, which no module declares any more. Those rows
stay readable and simply become unwritable; nothing is deleted.

`DROP TYPE` runs only after `pg_depend` shows the type has no dependants left.
**Never `CASCADE`**: cascade would silently drop whatever still referenced the
type — another table's column, a view, a function — and the whole point of
checking is to find out rather than to bulldoze. PostgreSQL always creates an
array type (`_settingdomain`) alongside an enum and records that as an internal
dependency, so that one dependency is expected and ignored; anything else is a
real dependant and fails the migration loudly.

Revision ID: 20260808_open_setting_domain
Revises: 20260802_add_outbox_claim_lease_columns
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "20260808_open_setting_domain"
down_revision: str = "20260802_add_outbox_claim_lease_columns"
branch_labels = None
depends_on = None

_ENUM_TYPE = "settingdomain"

# The domains the enum carried. Only used by `downgrade`, which must recreate it.
_LEGACY_MEMBERS = (
    "auth",
    "audit",
    "scheduler",
    "automation",
    "email",
    "features",
    "reporting",
    "payments",
    "operations",
    "support",
    "inventory",
    "projects",
    "fleet",
    "procurement",
    "settings",
    "payroll",
    "banking",
    "coach",
    "notifications",
    "expense",
    "gl",
)


def _external_dependants(connection: sa.engine.Connection) -> list[str]:
    """Everything still depending on the enum type, minus PostgreSQL's own array.

    `pg_depend.deptype = 'i'` marks the internal dependency between an enum and
    the array type PostgreSQL creates for it. That one is not a caller; every
    other row is something we would be breaking.
    """
    rows = connection.execute(
        sa.text(
            """
            SELECT DISTINCT
                   d.deptype,
                   COALESCE(c.relname, p.proname, t2.typname, d.classid::regclass::text)
              FROM pg_depend d
              JOIN pg_type t ON t.oid = d.refobjid
         LEFT JOIN pg_class c ON c.oid = d.objid
         LEFT JOIN pg_proc p ON p.oid = d.objid
         LEFT JOIN pg_type t2 ON t2.oid = d.objid
             WHERE t.typname = :typname
               AND t.typnamespace = 'public'::regnamespace
               AND d.deptype <> 'i'
            """
        ),
        {"typname": _ENUM_TYPE},
    ).fetchall()
    return [f"{deptype}:{name}" for deptype, name in rows]


def upgrade() -> None:
    connection = op.get_bind()

    op.execute(
        sa.text(
            "ALTER TABLE public.domain_settings "
            "ALTER COLUMN domain TYPE VARCHAR(120) USING domain::text"
        )
    )

    # The history column is already text, but at VARCHAR(50) — narrower than the
    # live column now is. Left alone, a domain of 51-120 characters would store
    # successfully and then fail the moment its change was recorded.
    op.execute(
        sa.text(
            "ALTER TABLE public.domain_setting_history "
            "ALTER COLUMN domain TYPE VARCHAR(120)"
        )
    )

    if connection.dialect.name != "postgresql":
        return

    remaining = _external_dependants(connection)
    if remaining:
        raise RuntimeError(
            f"public.{_ENUM_TYPE} still has dependants after converting "
            f"domain_settings.domain: {sorted(remaining)}. Convert them first — "
            "this migration will not DROP ... CASCADE, because that would drop "
            "whatever they are rather than telling you about it."
        )
    op.execute(sa.text(f"DROP TYPE IF EXISTS public.{_ENUM_TYPE}"))


def downgrade() -> None:
    """Recreate the enum and narrow the column back. DESTRUCTIVE.

    Any row whose domain is not one of the original members cannot satisfy the
    restored type, so it is deleted — the same honest cost kernel migration
    `0014` carries. A deployment that has written a domain of its own since the
    upgrade loses those rows here.
    """
    connection = op.get_bind()
    members = ", ".join(f"'{member}'" for member in _LEGACY_MEMBERS)

    op.execute(
        sa.text(f"DELETE FROM public.domain_settings WHERE domain NOT IN ({members})")
    )
    op.execute(
        sa.text(
            "ALTER TABLE public.domain_setting_history "
            "ALTER COLUMN domain TYPE VARCHAR(50)"
        )
    )
    if connection.dialect.name == "postgresql":
        op.execute(sa.text(f"CREATE TYPE public.{_ENUM_TYPE} AS ENUM ({members})"))
        op.execute(
            sa.text(
                "ALTER TABLE public.domain_settings "
                f"ALTER COLUMN domain TYPE public.{_ENUM_TYPE} "
                f"USING domain::public.{_ENUM_TYPE}"
            )
        )
    else:
        op.execute(
            sa.text(
                "ALTER TABLE public.domain_settings "
                "ALTER COLUMN domain TYPE VARCHAR(50)"
            )
        )
