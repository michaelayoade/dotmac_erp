"""Host the kernel at-most-once ledger contract in ERP's own lineage.

ERP cannot run or stamp the kernel root: both lineages would claim
``public.tenants``, and ERP's tenant catalogue is an Organization projection.
This revision therefore supplies ``idempotency_ledger.v1`` directly and binds
that effect from ``app/migration_bindings.py``.

Both planes are one prerequisite.  The tenant ledger is protected by ENABLE +
FORCE RLS over ``app_current_tenant_id()``; the platform peer has no tenant and
no RLS, is reachable by ``platform_api``, and is fully revoked from
``app_user``.  No legacy ``platform.idempotency_record`` row or caller moves in
this slice.  ADR-0001 keeps that table as ratcheted transitional state until
each operation receives an explicit scope and replay-window disposition.

The final verifier call is intentional.  A migration that merely creates two
tables with familiar names can still supply the wrong unique key, omit the
fingerprint, or invert the plane posture.  The exact pinned kernel contract
refuses that drift before Alembic records this provider revision.

Revision ID: 20260820_idempotency_ledger
Revises: 20260815_academy_course_projection,
         20260815_academy_learning_sync,
         20260816_platform_owned_webhook_ssrf_policy,
         20260818_dotmac_sub_customer_metrics
Create Date: 2026-08-20
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from dotmac_kernel.migrations.verify import require_prerequisites

revision = "20260820_idempotency_ledger"
down_revision: str | tuple[str, ...] = (
    "20260815_academy_course_projection",
    "20260815_academy_learning_sync",
    "20260816_platform_owned_webhook_ssrf_policy",
    "20260818_dotmac_sub_customer_metrics",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REQUIRES = ("idempotency_ledger.v1",)

_TENANT_TABLE = "idempotency_records"
_PLATFORM_TABLE = "platform_idempotency_records"
_COMMON_COLUMNS = (
    "id",
    "scope",
    "key",
    "fingerprint",
    "operation",
    "status",
    "result",
    "correlation_id",
    "expires_at",
    "created_at",
    "updated_at",
)


def _ledger_columns() -> list[sa.Column[Any]]:
    return [
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("scope", sa.String(length=120), nullable=False),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("correlation_id", sa.String(length=200), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    ]


def _create_tenant_ledger() -> None:
    op.create_table(
        _TENANT_TABLE,
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        *_ledger_columns(),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["public.tenants.id"],
            ondelete="CASCADE",
            name="fk_idempotency_records_tenant",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "scope",
            "key",
            name="uq_idempotency_records_tenant_scope_key",
        ),
        schema="public",
    )
    op.create_index(
        "ix_idempotency_records_tenant_id",
        _TENANT_TABLE,
        ["tenant_id"],
        schema="public",
    )
    op.create_index(
        "ix_idempotency_records_expires_at",
        _TENANT_TABLE,
        ["expires_at"],
        schema="public",
    )
    op.execute("ALTER TABLE public.idempotency_records ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.idempotency_records FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY idempotency_records_tenant_isolation
            ON public.idempotency_records
            USING (tenant_id = public.app_current_tenant_id())
            WITH CHECK (tenant_id = public.app_current_tenant_id())
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "public.idempotency_records TO app_user, platform_api"
    )


def _create_platform_ledger() -> None:
    op.create_table(
        _PLATFORM_TABLE,
        *_ledger_columns(),
        sa.UniqueConstraint(
            "scope",
            "key",
            name="uq_platform_idempotency_records_scope_key",
        ),
        schema="public",
    )
    op.create_index(
        "ix_platform_idempotency_records_expires_at",
        _PLATFORM_TABLE,
        ["expires_at"],
        schema="public",
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE "
        "public.platform_idempotency_records TO platform_api, app_admin"
    )
    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_idempotency_records "
        "FROM app_user"
    )
    columns = ", ".join(_COMMON_COLUMNS)
    for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
        op.execute(
            f"REVOKE {privilege} ({columns}) ON TABLE "
            "public.platform_idempotency_records FROM app_user"
        )


def upgrade() -> None:
    _create_tenant_ledger()
    _create_platform_ledger()
    require_prerequisites(op.get_bind(), REQUIRES)


def downgrade() -> None:
    op.drop_index(
        "ix_platform_idempotency_records_expires_at",
        table_name=_PLATFORM_TABLE,
        schema="public",
    )
    op.drop_table(_PLATFORM_TABLE, schema="public")
    op.execute(
        "DROP POLICY IF EXISTS idempotency_records_tenant_isolation "
        "ON public.idempotency_records"
    )
    op.drop_index(
        "ix_idempotency_records_expires_at",
        table_name=_TENANT_TABLE,
        schema="public",
    )
    op.drop_index(
        "ix_idempotency_records_tenant_id",
        table_name=_TENANT_TABLE,
        schema="public",
    )
    op.drop_table(_TENANT_TABLE, schema="public")
