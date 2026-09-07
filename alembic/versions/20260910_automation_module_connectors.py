"""Extend custom fields across every workflow-connected module.

Revision ID: 20260910_automation_module_connectors
Revises: 20260909_automation_assignments
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260910_automation_module_connectors"
down_revision: str | None = "20260909_automation_assignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ENTITY_TYPES = (
    "BANK_TRANSACTION",
    "RECONCILIATION",
    "CREDIT_NOTE",
    "CASH_ADVANCE",
    "ASSET_DISPOSAL",
    "EMPLOYEE",
    "ATTENDANCE",
    "LEAVE_REQUEST",
    "DISCIPLINARY_CASE",
    "PERFORMANCE_APPRAISAL",
    "PAYROLL_RUN",
    "PAYROLL_ENTRY",
    "SALARY_SLIP",
    "LOAN",
    "RECRUITMENT",
    "FLEET_VEHICLE",
    "FLEET_RESERVATION",
    "FLEET_MAINTENANCE",
    "FLEET_INCIDENT",
    "MATERIAL_REQUEST",
)


def upgrade() -> None:
    for entity_type in ("ITEM", "PROJECT", "ASSET"):
        op.execute(
            f"ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS '{entity_type}'"
        )
    for entity_type in ENTITY_TYPES:
        op.execute(
            "ALTER TYPE custom_field_entity_type "
            f"ADD VALUE IF NOT EXISTS '{entity_type}'"
        )


def downgrade() -> None:
    # PostgreSQL enum values are intentionally additive and are not removed.
    pass
