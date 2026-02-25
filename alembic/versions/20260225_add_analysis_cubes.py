"""Add analysis cube tables and sales analysis materialized view.

Revision ID: 20260225_add_analysis_cubes
Revises: 20260225_add_server_defaults_for_pks_and_booleans
Create Date: 2026-02-25
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260225_add_analysis_cubes"
down_revision: Union[str, Sequence[str], None] = (
    "20260225_add_server_defaults_for_pks_and_booleans"
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    if not inspector.has_table("analysis_cube", schema="rpt"):
        op.create_table(
            "analysis_cube",
            sa.Column(
                "cube_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("code", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("source_view", sa.String(length=120), nullable=False),
            sa.Column(
                "dimensions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "measures",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "default_rows",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "default_columns",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "default_measures",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column("drill_down_url_template", sa.String(length=300), nullable=True),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column(
                "refresh_interval_minutes",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("60"),
            ),
            sa.Column("last_refreshed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
            ),
            sa.PrimaryKeyConstraint("cube_id"),
            sa.UniqueConstraint("code", name="uq_analysis_cube_code"),
            schema="rpt",
        )

    if not inspector.has_table("saved_analysis", schema="rpt"):
        op.create_table(
            "saved_analysis",
            sa.Column(
                "analysis_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("cube_code", sa.String(length=50), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column(
                "row_dimensions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "column_dimensions",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "measures",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=False,
                server_default=sa.text("'[]'::jsonb"),
            ),
            sa.Column(
                "filters",
                postgresql.JSONB(astext_type=sa.Text()),
                nullable=True,
            ),
            sa.Column(
                "is_shared",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["organization_id"],
                ["core_org.organization.organization_id"],
            ),
            sa.PrimaryKeyConstraint("analysis_id"),
            schema="rpt",
        )

    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS rpt.sales_analysis_mv AS
        SELECT
            i.organization_id,
            i.invoice_id,
            i.invoice_number,
            i.invoice_date,
            date_trunc('month', i.invoice_date)::date AS period_date,
            to_char(i.invoice_date, 'Mon YYYY') AS period_label,
            i.customer_id,
            c.legal_name AS customer_name,
            c.customer_type::text AS customer_type,
            i.status::text AS status,
            i.currency_code,
            coalesce(i.subtotal, 0) AS amount_subtotal,
            coalesce(i.tax_amount, 0) AS amount_tax,
            coalesce(i.total_amount, 0) AS amount_total,
            coalesce(i.amount_paid, 0) AS amount_paid,
            coalesce(i.total_amount, 0) - coalesce(i.amount_paid, 0) AS amount_outstanding,
            1::int AS record_count
        FROM ar.invoice i
        LEFT JOIN ar.customer c ON i.customer_id = c.customer_id
        WHERE i.status::text NOT IN ('DRAFT', 'VOID')
        WITH DATA
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uq_sales_analysis_mv_invoice_id
        ON rpt.sales_analysis_mv (invoice_id)
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_sales_analysis_mv_org_period
        ON rpt.sales_analysis_mv (organization_id, period_date)
        """
    )
    op.execute(
        """
        INSERT INTO rpt.analysis_cube (
            cube_id, organization_id, code, name, description, source_view,
            dimensions, measures, default_rows, default_columns, default_measures,
            drill_down_url_template, is_active, refresh_interval_minutes
        )
        VALUES (
            gen_random_uuid(),
            NULL,
            'sales',
            'Sales Analysis',
            'Sales invoices grouped by customer and period.',
            'rpt.sales_analysis_mv',
            '[{"field":"period_label","label":"Period","type":"text"},{"field":"customer_name","label":"Customer","type":"text"},{"field":"status","label":"Status","type":"text"},{"field":"currency_code","label":"Currency","type":"text"}]'::jsonb,
            '[{"field":"amount_total","label":"Total Amount","agg":"sum","type":"currency"},{"field":"amount_paid","label":"Amount Paid","agg":"sum","type":"currency"},{"field":"amount_outstanding","label":"Outstanding","agg":"sum","type":"currency"},{"field":"record_count","label":"Records","agg":"sum","type":"integer"}]'::jsonb,
            '["period_label"]'::jsonb,
            '[]'::jsonb,
            '["amount_total"]'::jsonb,
            '/finance/ar/invoices',
            true,
            60
        )
        ON CONFLICT (code) DO NOTHING
        """
    )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)

    op.execute("DROP MATERIALIZED VIEW IF EXISTS rpt.sales_analysis_mv")
    if inspector.has_table("saved_analysis", schema="rpt"):
        op.drop_table("saved_analysis", schema="rpt")
    if inspector.has_table("analysis_cube", schema="rpt"):
        op.drop_table("analysis_cube", schema="rpt")
