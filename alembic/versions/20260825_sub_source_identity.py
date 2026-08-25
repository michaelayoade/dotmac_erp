"""Align Sub financial source identities and enforce one PO per delivery.

Revision ID: 20260825_sub_source_identity
Revises: 20260825_retire_dotmac_crm

Sub source identifiers are provider-neutral opaque strings up to 120 characters.
The prefixed correlation keys therefore need enough storage for the full wire
contract.  The purchase-order key also needs a database uniqueness backstop:
an application check cannot prevent two concurrent deliveries from creating two
financial documents.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260825_sub_source_identity"
down_revision = "20260825_retire_dotmac_crm"
branch_labels = None
depends_on = None

_PO_INDEX = "uq_po_sub_source_correlation"


def _refuse_duplicate_po_sources(bind: sa.Connection) -> None:
    duplicate = bind.execute(
        sa.text(
            """
            SELECT organization_id, correlation_id, count(*)
              FROM ap.purchase_order
             WHERE correlation_id LIKE 'sub-wo:%'
             GROUP BY organization_id, correlation_id
            HAVING count(*) > 1
             LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "cannot enforce Sub purchase-order identity: duplicate "
            f"organization/correlation pair {duplicate[0]}/{duplicate[1]} "
            f"has {duplicate[2]} rows"
        )


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        raise RuntimeError("Sub source-identity migration requires PostgreSQL")

    _refuse_duplicate_po_sources(bind)

    op.alter_column(
        "purchase_order",
        "correlation_id",
        schema="ap",
        existing_type=sa.String(length=100),
        type_=sa.String(length=139),
        existing_nullable=True,
    )
    op.alter_column(
        "purchase_order",
        "variation_id",
        schema="ap",
        existing_type=sa.String(length=36),
        type_=sa.String(length=120),
        existing_nullable=True,
    )
    op.alter_column(
        "supplier_invoice",
        "correlation_id",
        schema="ap",
        existing_type=sa.String(length=100),
        type_=sa.String(length=132),
        existing_nullable=True,
    )
    op.add_column(
        "purchase_order",
        sa.Column(
            "source_fingerprint",
            sa.String(length=64),
            nullable=True,
            comment="SHA-256 of the immutable Sub financial command",
        ),
        schema="ap",
    )
    op.add_column(
        "supplier_invoice",
        sa.Column(
            "source_fingerprint",
            sa.String(length=64),
            nullable=True,
            comment="SHA-256 of the immutable Sub financial command",
        ),
        schema="ap",
    )
    op.create_index(
        _PO_INDEX,
        "purchase_order",
        ["organization_id", "correlation_id"],
        schema="ap",
        unique=True,
        postgresql_where=sa.text("correlation_id LIKE 'sub-wo:%'"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    oversized = bind.execute(
        sa.text(
            """
            SELECT EXISTS (
                SELECT 1 FROM ap.purchase_order
                 WHERE length(correlation_id) > 100 OR length(variation_id) > 36
            ) OR EXISTS (
                SELECT 1 FROM ap.supplier_invoice
                 WHERE length(correlation_id) > 100
            )
            """
        )
    ).scalar_one()
    if oversized:
        raise RuntimeError(
            "cannot narrow Sub source identities: values exceed the legacy widths"
        )

    op.drop_index(_PO_INDEX, table_name="purchase_order", schema="ap")
    op.drop_column("supplier_invoice", "source_fingerprint", schema="ap")
    op.drop_column("purchase_order", "source_fingerprint", schema="ap")
    op.alter_column(
        "supplier_invoice",
        "correlation_id",
        schema="ap",
        existing_type=sa.String(length=132),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
    op.alter_column(
        "purchase_order",
        "variation_id",
        schema="ap",
        existing_type=sa.String(length=120),
        type_=sa.String(length=36),
        existing_nullable=True,
    )
    op.alter_column(
        "purchase_order",
        "correlation_id",
        schema="ap",
        existing_type=sa.String(length=139),
        type_=sa.String(length=100),
        existing_nullable=True,
    )
