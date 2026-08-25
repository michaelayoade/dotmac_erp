"""Retire the direct Dotmac CRM runtime and seal its historical evidence.

Revision ID: 20260825_retire_dotmac_crm
Revises: 20260825_retire_ext_tickets
Create Date: 2026-08-25

The archive is deliberately inaccessible to online roles.  It preserves the
ambiguous legacy mappings and identifiers without relabelling any of them as
Sub authority.  Credentials and authentication hashes are not archived.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260825_retire_dotmac_crm"
down_revision = "20260825_retire_ext_tickets"
branch_labels = None
depends_on = None

_ARCHIVE_SCHEMA = "archive"
_ARCHIVE_TABLE = "retired_crm_records"
_ARCHIVE_RELATION = f"{_ARCHIVE_SCHEMA}.{_ARCHIVE_TABLE}"
_PLATFORM_ARCHIVE_TABLE = "retired_crm_scheduled_tasks"
_PLATFORM_ARCHIVE_RELATION = f"{_ARCHIVE_SCHEMA}.{_PLATFORM_ARCHIVE_TABLE}"
_ONLINE_ROLES = ("app_user", "platform_api")
_CRM_SOURCE_SYSTEMS = ("crm", "dotmac_crm", "dotmac-crm")

_REQUIRED_COLUMNS = {
    ("sync", "integration_config"): {
        "integration_type",
        "is_active",
        "api_key",
        "api_secret",
        "updated_at",
    },
    ("public", "api_keys"): {"label", "is_active", "revoked_at"},
    ("public", "scheduled_tasks"): {"id", "task_name"},
    ("sync", "sync_entity"): {
        "sync_id",
        "organization_id",
        "source_system",
    },
    ("sync", "sync_history"): {
        "history_id",
        "organization_id",
        "source_system",
    },
    ("sync", "crm_sync_mapping"): {"mapping_id", "organization_id"},
    ("ar", "customer"): {"customer_id", "organization_id", "crm_id"},
    ("inv", "material_request"): {
        "request_id",
        "organization_id",
        "source_system",
        "crm_id",
    },
    ("expense", "expense_claim"): {"claim_id", "organization_id", "crm_id"},
}
_REQUIRED_TYPES = ("sync.crm_entity_type", "sync.crm_sync_status")


def _has_table(bind: sa.Connection, schema: str | None, table: str) -> bool:
    return sa.inspect(bind).has_table(table, schema=schema)


def _columns(bind: sa.Connection, schema: str, table: str) -> set[str]:
    return {
        column["name"] for column in sa.inspect(bind).get_columns(table, schema=schema)
    }


def _unique_constraints(bind: sa.Connection, schema: str, table: str) -> set[str]:
    return {
        constraint["name"]
        for constraint in sa.inspect(bind).get_unique_constraints(table, schema=schema)
        if constraint.get("name")
    }


def _indexes(bind: sa.Connection, schema: str, table: str) -> set[str]:
    return {
        index["name"]
        for index in sa.inspect(bind).get_indexes(table, schema=schema)
        if index.get("name")
    }


def _require_source_catalog(bind: sa.Connection) -> None:
    missing_tables: list[str] = []
    missing_columns: list[str] = []
    for (schema, table), expected_columns in _REQUIRED_COLUMNS.items():
        qualified = f"{schema}.{table}"
        if not _has_table(bind, schema, table):
            missing_tables.append(qualified)
            continue
        for column in sorted(expected_columns - _columns(bind, schema, table)):
            missing_columns.append(f"{qualified}.{column}")

    missing_types = [
        type_name
        for type_name in _REQUIRED_TYPES
        if not bind.scalar(
            sa.text("SELECT to_regtype(:type_name) IS NOT NULL"),
            {"type_name": type_name},
        )
    ]
    if missing_tables or missing_columns or missing_types:
        details: list[str] = []
        if missing_tables:
            details.append("tables=" + ", ".join(missing_tables))
        if missing_columns:
            details.append("columns=" + ", ".join(missing_columns))
        if missing_types:
            details.append("types=" + ", ".join(missing_types))
        raise RuntimeError(
            "CRM retirement requires the exact current-head source catalog; "
            + "; ".join(details)
        )


def _create_sealed_archive(bind: sa.Connection) -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {_ARCHIVE_SCHEMA}")
    op.execute(f"REVOKE ALL PRIVILEGES ON SCHEMA {_ARCHIVE_SCHEMA} FROM PUBLIC")

    if not _has_table(bind, _ARCHIVE_SCHEMA, _ARCHIVE_TABLE):
        op.create_table(
            _ARCHIVE_TABLE,
            sa.Column(
                "archive_id",
                sa.BigInteger(),
                sa.Identity(),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_relation", sa.Text(), nullable=False),
            sa.Column("source_primary_key", sa.Text(), nullable=False),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column(
                "retired_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "source_relation",
                "source_primary_key",
                name="uq_retired_crm_record_source",
            ),
            schema=_ARCHIVE_SCHEMA,
        )
        op.create_index(
            "idx_retired_crm_records_org",
            _ARCHIVE_TABLE,
            ["organization_id"],
            schema=_ARCHIVE_SCHEMA,
        )

    if not _has_table(bind, _ARCHIVE_SCHEMA, _PLATFORM_ARCHIVE_TABLE):
        op.create_table(
            _PLATFORM_ARCHIVE_TABLE,
            sa.Column(
                "source_primary_key",
                sa.Text(),
                primary_key=True,
                nullable=False,
            ),
            sa.Column("payload", postgresql.JSONB(), nullable=False),
            sa.Column(
                "retired_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            schema=_ARCHIVE_SCHEMA,
        )

    op.execute(f"ALTER TABLE {_ARCHIVE_RELATION} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_ARCHIVE_RELATION} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"DROP POLICY IF EXISTS retired_crm_records_tenant_isolation "
        f"ON {_ARCHIVE_RELATION}"
    )
    op.execute(
        f"""
        CREATE POLICY retired_crm_records_tenant_isolation
            ON {_ARCHIVE_RELATION}
            USING (
                organization_id IS NOT NULL
                AND organization_id::text = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )
            )
            WITH CHECK (
                organization_id IS NOT NULL
                AND organization_id::text = NULLIF(
                    current_setting('app.current_organization_id', true), ''
                )
            )
        """
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE {_ARCHIVE_RELATION} FROM PUBLIC")
    op.execute(f"ALTER TABLE {_PLATFORM_ARCHIVE_RELATION} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_PLATFORM_ARCHIVE_RELATION} DISABLE ROW LEVEL SECURITY")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON TABLE {_PLATFORM_ARCHIVE_RELATION} FROM PUBLIC"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA "
        f"{_ARCHIVE_SCHEMA} FROM PUBLIC"
    )
    for role in _ONLINE_ROLES:
        op.execute(
            f"""
            DO $revoke$
            BEGIN
                IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                    REVOKE ALL PRIVILEGES
                        ON SCHEMA {_ARCHIVE_SCHEMA} FROM {role};
                    REVOKE ALL PRIVILEGES
                        ON TABLE {_ARCHIVE_RELATION} FROM {role};
                    REVOKE ALL PRIVILEGES
                        ON TABLE {_PLATFORM_ARCHIVE_RELATION} FROM {role};
                    REVOKE ALL PRIVILEGES
                        ON ALL SEQUENCES IN SCHEMA {_ARCHIVE_SCHEMA} FROM {role};
                END IF;
            END
            $revoke$
            """
        )


def _archive_platform_schedule_evidence(bind: sa.Connection) -> None:
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {_PLATFORM_ARCHIVE_RELATION} (
                source_primary_key,
                payload
            )
            SELECT source.id::text,
                   to_jsonb(source)
              FROM public.scheduled_tasks AS source
             WHERE source.task_name LIKE 'app.tasks.crm.%'
            ON CONFLICT (source_primary_key) DO UPDATE
            SET payload = EXCLUDED.payload
            """
        )
    )
    source_count = bind.scalar(
        sa.text(
            "SELECT count(*) FROM public.scheduled_tasks "
            "WHERE task_name LIKE 'app.tasks.crm.%'"
        )
    )
    archive_count = bind.scalar(
        sa.text(f"SELECT count(*) FROM {_PLATFORM_ARCHIVE_RELATION}")
    )
    if int(source_count or 0) != int(archive_count or 0):
        raise RuntimeError(
            "CRM retirement archive parity failed for public.scheduled_tasks: "
            f"source={source_count}, archive={archive_count}"
        )


def _archive_rows(
    bind: sa.Connection,
    *,
    schema: str | None,
    table: str,
    source_relation: str,
    primary_key_sql: str,
    organization_id_sql: str,
    payload_sql: str,
    where_sql: str = "TRUE",
) -> None:
    if not _has_table(bind, schema, table):
        return

    qualified = f"{schema}.{table}" if schema else table
    bind.execute(
        sa.text(
            f"""
            INSERT INTO {_ARCHIVE_RELATION} (
                organization_id,
                source_relation,
                source_primary_key,
                payload
            )
            SELECT
                {organization_id_sql},
                :source_relation,
                ({primary_key_sql})::text,
                {payload_sql}
            FROM {qualified} AS source
            WHERE {where_sql}
            ON CONFLICT (source_relation, source_primary_key) DO UPDATE
            SET organization_id = EXCLUDED.organization_id,
                payload = EXCLUDED.payload
            """
        ),
        {"source_relation": source_relation},
    )

    source_count = bind.scalar(
        sa.text(f"SELECT count(*) FROM {qualified} AS source WHERE {where_sql}")
    )
    archive_count = bind.scalar(
        sa.text(
            f"SELECT count(*) FROM {_ARCHIVE_RELATION} "
            "WHERE source_relation = :source_relation"
        ),
        {"source_relation": source_relation},
    )
    if int(source_count or 0) != int(archive_count or 0):
        raise RuntimeError(
            f"CRM retirement archive parity failed for {source_relation}: "
            f"source={source_count}, archive={archive_count}"
        )


def _archive_runtime_evidence(bind: sa.Connection) -> None:
    crm_sources = ", ".join(f"'{value}'" for value in _CRM_SOURCE_SYSTEMS)

    # The former retired_crm_sync_mapping relation is represented as one
    # source_relation inside the sealed archive, alongside every other retired
    # CRM record.  A single archive makes the retirement parity auditable.
    _archive_rows(
        bind,
        schema="sync",
        table="crm_sync_mapping",
        source_relation="sync.crm_sync_mapping",
        primary_key_sql="source.mapping_id",
        organization_id_sql="source.organization_id",
        payload_sql="to_jsonb(source)",
    )
    _archive_rows(
        bind,
        schema="sync",
        table="sync_entity",
        source_relation="sync.sync_entity:crm",
        primary_key_sql="source.sync_id",
        organization_id_sql="source.organization_id",
        payload_sql="to_jsonb(source)",
        where_sql=f"lower(source.source_system) IN ({crm_sources})",
    )
    _archive_rows(
        bind,
        schema="sync",
        table="sync_history",
        source_relation="sync.sync_history:crm",
        primary_key_sql="source.history_id",
        organization_id_sql="source.organization_id",
        payload_sql="to_jsonb(source)",
        where_sql=f"lower(source.source_system) IN ({crm_sources})",
    )
    _archive_rows(
        bind,
        schema="ar",
        table="customer",
        source_relation="ar.customer.crm_id",
        primary_key_sql="source.customer_id",
        organization_id_sql="source.organization_id",
        payload_sql="jsonb_build_object('crm_id', source.crm_id)",
        where_sql="source.crm_id IS NOT NULL",
    )
    _archive_rows(
        bind,
        schema="inv",
        table="material_request",
        source_relation="inv.material_request.crm_id",
        primary_key_sql="source.request_id",
        organization_id_sql="source.organization_id",
        payload_sql=(
            "jsonb_build_object('crm_id', source.crm_id, "
            "'source_system', source.source_system)"
        ),
        where_sql="source.crm_id IS NOT NULL",
    )
    _archive_rows(
        bind,
        schema="expense",
        table="expense_claim",
        source_relation="expense.expense_claim.crm_id",
        primary_key_sql="source.claim_id",
        organization_id_sql="source.organization_id",
        payload_sql="jsonb_build_object('crm_id', source.crm_id)",
        where_sql="source.crm_id IS NOT NULL",
    )
    _archive_platform_schedule_evidence(bind)


def _replace_material_request_identity(bind: sa.Connection) -> None:
    if not _has_table(bind, "inv", "material_request"):
        return
    columns = _columns(bind, "inv", "material_request")
    if "source_id" not in columns:
        op.add_column(
            "material_request",
            sa.Column(
                "source_id",
                sa.String(120),
                nullable=True,
                comment=(
                    "Opaque source request ID; populated only for named source systems"
                ),
            ),
            schema="inv",
        )
    if "crm_id" in columns:
        # Historical source_system = 'crm' remains exactly that; only proven
        # Sub rows receive a live source identifier.
        op.execute(
            """
            UPDATE inv.material_request
               SET source_id = crm_id
             WHERE source_system = 'sub'
               AND crm_id IS NOT NULL
               AND source_id IS NULL
            """
        )
        op.execute(
            "ALTER TABLE inv.material_request "
            "DROP CONSTRAINT IF EXISTS uq_material_request_org_crm_id"
        )
        op.drop_column("material_request", "crm_id", schema="inv")

    constraints = _unique_constraints(bind, "inv", "material_request")
    if "uq_material_request_org_source_id" not in constraints:
        op.create_unique_constraint(
            "uq_material_request_org_source_id",
            "material_request",
            ["organization_id", "source_system", "source_id"],
            schema="inv",
        )
    indexes = _indexes(bind, "inv", "material_request")
    if "idx_material_request_source_id" not in indexes:
        op.create_index(
            "idx_material_request_source_id",
            "material_request",
            ["source_id"],
            schema="inv",
            postgresql_where=sa.text("source_id IS NOT NULL"),
        )


def _replace_expense_claim_identity(bind: sa.Connection) -> None:
    if not _has_table(bind, "expense", "expense_claim"):
        return
    columns = _columns(bind, "expense", "expense_claim")
    if "source_id" not in columns:
        op.add_column(
            "expense_claim",
            sa.Column(
                "source_id",
                sa.String(120),
                nullable=True,
                comment="Opaque Sub expense request ID used for idempotency",
            ),
            schema="expense",
        )
    # The old column has no source-system discriminator.  Its values are
    # archived, never guessed to be Sub identifiers.
    if "crm_id" in columns:
        op.execute(
            "ALTER TABLE expense.expense_claim "
            "DROP CONSTRAINT IF EXISTS uq_expense_claim_org_crm_id"
        )
        op.drop_column("expense_claim", "crm_id", schema="expense")

    constraints = _unique_constraints(bind, "expense", "expense_claim")
    if "uq_expense_claim_org_source_id" not in constraints:
        op.create_unique_constraint(
            "uq_expense_claim_org_source_id",
            "expense_claim",
            ["organization_id", "source_id"],
            schema="expense",
        )
    indexes = _indexes(bind, "expense", "expense_claim")
    if "idx_expense_claim_source_id" not in indexes:
        op.create_index(
            "idx_expense_claim_source_id",
            "expense_claim",
            ["source_id"],
            schema="expense",
            postgresql_where=sa.text("source_id IS NOT NULL"),
        )


def _drop_customer_crm_identity(bind: sa.Connection) -> None:
    if not _has_table(bind, "ar", "customer"):
        return
    if "crm_id" in _columns(bind, "ar", "customer"):
        op.drop_column("customer", "crm_id", schema="ar")


def _retire_live_runtime_rows(bind: sa.Connection) -> None:
    crm_sources = ", ".join(f"'{value}'" for value in _CRM_SOURCE_SYSTEMS)

    if _has_table(bind, "sync", "integration_config"):
        bind.execute(
            sa.text(
                """
                UPDATE sync.integration_config
                   SET is_active = false,
                       api_key = NULL,
                       api_secret = NULL,
                       updated_at = now()
                 WHERE integration_type::text = 'DOTMAC_CRM'
                """
            )
        )
    if _has_table(bind, "public", "api_keys"):
        bind.execute(
            sa.text(
                """
                UPDATE public.api_keys
                   SET is_active = false,
                       revoked_at = COALESCE(revoked_at, now())
                 WHERE label ILIKE 'dotmac-crm-service-%'
                """
            )
        )
    if _has_table(bind, "public", "scheduled_tasks"):
        bind.execute(
            sa.text(
                "DELETE FROM public.scheduled_tasks "
                "WHERE task_name LIKE 'app.tasks.crm.%'"
            )
        )
    if _has_table(bind, "sync", "sync_entity"):
        bind.execute(
            sa.text(
                f"DELETE FROM sync.sync_entity "
                f"WHERE lower(source_system) IN ({crm_sources})"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE sync.sync_entity SET source_system = 'sub' "
                "WHERE lower(source_system) = 'dotmac_sub'"
            )
        )
    if _has_table(bind, "sync", "sync_history"):
        bind.execute(
            sa.text(
                f"DELETE FROM sync.sync_history "
                f"WHERE lower(source_system) IN ({crm_sources})"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE sync.sync_history SET source_system = 'sub' "
                "WHERE lower(source_system) = 'dotmac_sub'"
            )
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("CRM retirement migration requires PostgreSQL")

    _require_source_catalog(bind)
    _create_sealed_archive(bind)
    _archive_runtime_evidence(bind)
    _replace_material_request_identity(bind)
    _replace_expense_claim_identity(bind)
    _drop_customer_crm_identity(bind)
    _retire_live_runtime_rows(bind)

    if _has_table(bind, "sync", "crm_sync_mapping"):
        op.drop_table("crm_sync_mapping", schema="sync")
        op.execute("DROP TYPE IF EXISTS sync.crm_sync_status")
        op.execute("DROP TYPE IF EXISTS sync.crm_entity_type")


def downgrade() -> None:
    raise RuntimeError(
        "20260825_retire_dotmac_crm is forward-only: restoring retired "
        "credentials, API keys, schedules, or ambiguous CRM identities would "
        "violate the retirement boundary. Restore the pre-migration backup "
        "under an approved recovery procedure instead."
    )
