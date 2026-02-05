"""Add Task-Ticket and Asset-Project foreign keys.

Improves cross-module integration:
- Tasks can now be linked to support tickets (ticket_id)
- Assets can now be assigned to projects (project_id)
- Asset custodian now uses proper FK to Employee (custodian_employee_id)

Revision ID: 20260124_task_asset_fks
Revises: 20260124_ticket_contact
Create Date: 2026-01-24

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260124_task_asset_fks"
down_revision: Union[str, None] = "create_project_management_tables"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add ticket_id to pm.task for Task↔Ticket linkage
    op.add_column(
        "task",
        sa.Column(
            "ticket_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Link to support ticket that spawned this task",
        ),
        schema="pm",
    )
    op.create_index(
        "idx_task_ticket",
        "task",
        ["ticket_id"],
        schema="pm",
    )
    op.create_foreign_key(
        "fk_task_ticket",
        "task",
        "ticket",
        ["ticket_id"],
        ["ticket_id"],
        source_schema="pm",
        referent_schema="support",
    )

    # Add project_id to fa.asset for Asset↔Project linkage
    op.add_column(
        "asset",
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment="Project this asset is assigned to",
        ),
        schema="fa",
    )
    op.create_index(
        "idx_asset_project",
        "asset",
        ["project_id"],
        schema="fa",
    )
    op.create_foreign_key(
        "fk_asset_project",
        "asset",
        "project",
        ["project_id"],
        ["project_id"],
        source_schema="fa",
        referent_schema="core_org",
    )

    # Rename and add FK constraint to custodian field in fa.asset
    # First rename the column
    op.alter_column(
        "asset",
        "custodian_user_id",
        new_column_name="custodian_employee_id",
        schema="fa",
    )
    # Add FK constraint to employee as NOT VALID to avoid failing on legacy data
    op.execute(
        """
        ALTER TABLE fa.asset
        ADD CONSTRAINT fk_asset_custodian_employee
        FOREIGN KEY (custodian_employee_id)
        REFERENCES hr.employee (employee_id)
        NOT VALID
        """
    )


def downgrade() -> None:
    # Remove FK and rename custodian column back
    op.drop_constraint("fk_asset_custodian_employee", "asset", schema="fa", type_="foreignkey")
    op.alter_column(
        "asset",
        "custodian_employee_id",
        new_column_name="custodian_user_id",
        schema="fa",
    )

    # Remove project_id from fa.asset
    op.drop_constraint("fk_asset_project", "asset", schema="fa", type_="foreignkey")
    op.drop_index("idx_asset_project", table_name="asset", schema="fa")
    op.drop_column("asset", "project_id", schema="fa")

    # Remove ticket_id from pm.task
    op.drop_constraint("fk_task_ticket", "task", schema="pm", type_="foreignkey")
    op.drop_index("idx_task_ticket", table_name="task", schema="pm")
    op.drop_column("task", "ticket_id", schema="pm")
