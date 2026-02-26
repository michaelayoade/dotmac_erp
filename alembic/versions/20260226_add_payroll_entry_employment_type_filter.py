"""Add employment_type_id filter to payroll.payroll_entry.

Revision ID: 20260226_add_payroll_entry_employment_type_filter
Revises: 20260225_add_item_wac_ledger
Create Date: 2026-02-26
"""

import sqlalchemy as sa

from alembic import op

revision = "20260226_add_payroll_entry_employment_type_filter"
down_revision = "20260225_add_item_wac_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("payroll_entry", schema="payroll"):
        return

    columns = {
        col["name"] for col in inspector.get_columns("payroll_entry", schema="payroll")
    }
    if "employment_type_id" not in columns:
        op.add_column(
            "payroll_entry",
            sa.Column("employment_type_id", sa.UUID(as_uuid=True), nullable=True),
            schema="payroll",
        )

    fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys("payroll_entry", schema="payroll")
        if fk.get("name")
    }
    if "fk_payroll_entry_employment_type" not in fks:
        op.create_foreign_key(
            "fk_payroll_entry_employment_type",
            "payroll_entry",
            "employment_type",
            ["employment_type_id"],
            ["employment_type_id"],
            source_schema="payroll",
            referent_schema="hr",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("payroll_entry", schema="payroll"):
        return

    fks = {
        fk["name"]
        for fk in inspector.get_foreign_keys("payroll_entry", schema="payroll")
        if fk.get("name")
    }
    if "fk_payroll_entry_employment_type" in fks:
        op.drop_constraint(
            "fk_payroll_entry_employment_type",
            "payroll_entry",
            schema="payroll",
            type_="foreignkey",
        )

    columns = {
        col["name"] for col in inspector.get_columns("payroll_entry", schema="payroll")
    }
    if "employment_type_id" in columns:
        op.drop_column("payroll_entry", "employment_type_id", schema="payroll")
