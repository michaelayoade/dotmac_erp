"""Require one employee owner per organization and Selfcare account.

Revision ID: 20260905_selfcare_mapping_unique
Revises: 20260905_repair_sales_mv_index
"""

from alembic import op
import sqlalchemy as sa

revision = "20260905_selfcare_mapping_unique"
down_revision = "20260905_repair_sales_mv_index"
branch_labels = None
depends_on = None

CONSTRAINT = "uq_employee_org_selfcare_account"


def upgrade() -> None:
    # This migration needs a complete census. row_security=off does NOT bypass
    # RLS: PostgreSQL refuses the query if the executor would see filtered rows.
    # Block writes between the census and the non-deferrable constraint's DDL.
    op.execute("SET LOCAL row_security = off")
    op.execute("LOCK TABLE hr.employee IN SHARE ROW EXCLUSIVE MODE")
    duplicate = (
        op.get_bind()
        .execute(
            sa.text(
                """
            SELECT organization_id, dotmac_sub_account_id
            FROM hr.employee
            WHERE dotmac_sub_account_id IS NOT NULL
            GROUP BY organization_id, dotmac_sub_account_id
            HAVING count(*) > 1
            LIMIT 1
            """
            )
        )
        .first()
    )
    if duplicate is not None:
        raise RuntimeError(
            "Duplicate Selfcare employee mappings prevent migration. "
            "An administrator must review ALL conflicting records, including "
            "inactive employees, using docs/operations/selfcare-employee-mappings.md. "
            "Correct mappings only after ownership review, then rerun migration. "
            "Do not delete employees or attendance history. No data was changed."
        )
    op.create_unique_constraint(
        CONSTRAINT,
        "employee",
        ["organization_id", "dotmac_sub_account_id"],
        schema="hr",
    )


def downgrade() -> None:
    op.drop_constraint(CONSTRAINT, "employee", schema="hr", type_="unique")
