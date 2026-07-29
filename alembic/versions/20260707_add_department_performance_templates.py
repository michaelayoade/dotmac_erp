"""Add department performance templates.

Revision ID: 20260707_dept_perf_templates
Revises: 20260706_add_expense_claim_crm_id
Create Date: 2026-07-07
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260707_dept_perf_templates"
down_revision: Union[str, None] = "20260706_add_expense_claim_crm_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "department_performance_template",
        sa.Column(
            "template_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kra_name", sa.String(length=200), nullable=False),
        sa.Column("kpi_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_value", sa.Numeric(12, 2), nullable=False),
        sa.Column("unit_of_measure", sa.String(length=30), nullable=True),
        sa.Column("weightage", sa.Numeric(5, 2), nullable=False),
        sa.Column("metric_source_key", sa.String(length=100), nullable=True),
        sa.Column("lower_is_better", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["people.id"],
        ),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["hr.department.department_id"],
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["core_org.organization.organization_id"],
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"],
            ["people.id"],
        ),
        sa.PrimaryKeyConstraint("template_id"),
        sa.UniqueConstraint(
            "organization_id",
            "department_id",
            "kra_name",
            "kpi_name",
            name="uq_dept_perf_template_kpi",
        ),
        schema="perf",
    )
    op.create_index(
        "idx_dept_perf_template_dept",
        "department_performance_template",
        ["organization_id", "department_id", "is_active"],
        unique=False,
        schema="perf",
    )
    op.create_index(
        op.f("ix_perf_department_performance_template_organization_id"),
        "department_performance_template",
        ["organization_id"],
        unique=False,
        schema="perf",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_perf_department_performance_template_organization_id"),
        table_name="department_performance_template",
        schema="perf",
    )
    op.drop_index(
        "idx_dept_perf_template_dept",
        table_name="department_performance_template",
        schema="perf",
    )
    op.drop_table("department_performance_template", schema="perf")
