"""Harden automation tenant isolation and preserve workflow history.

Revision ID: 20260911_automation_security_lifecycle
Revises: 20260910_automation_module_connectors
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260911_automation_security_lifecycle"
down_revision: str | None = "20260910_automation_module_connectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TENANT_SETTING = """
NULLIF(current_setting('app.current_organization_id', true), '')::uuid
"""


def _protect_direct_table(table: str) -> None:
    qualified = f"automation.{table}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
        ON {qualified}
        USING (organization_id = {_TENANT_SETTING})
        WITH CHECK (organization_id = {_TENANT_SETTING})
        """
    )


def _protect_rule_child(table: str) -> None:
    qualified = f"automation.{table}"
    op.execute(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation
        ON {qualified}
        USING (
            EXISTS (
                SELECT 1
                FROM automation.workflow_rule AS tenant_rule
                WHERE tenant_rule.rule_id = {table}.rule_id
                  AND tenant_rule.organization_id = {_TENANT_SETTING}
            )
        )
        WITH CHECK (
            EXISTS (
                SELECT 1
                FROM automation.workflow_rule AS tenant_rule
                WHERE tenant_rule.rule_id = {table}.rule_id
                  AND tenant_rule.organization_id = {_TENANT_SETTING}
            )
        )
        """
    )


def upgrade() -> None:
    op.add_column(
        "workflow_rule",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        schema="automation",
    )
    op.add_column(
        "workflow_rule",
        sa.Column("archived_by", postgresql.UUID(as_uuid=True), nullable=True),
        schema="automation",
    )
    op.create_index(
        "idx_workflow_rule_archived",
        "workflow_rule",
        ["organization_id", "archived_at"],
        schema="automation",
    )

    _protect_direct_table("workflow_rule")
    _protect_direct_table("custom_field_definition")
    _protect_rule_child("workflow_rule_version")
    _protect_rule_child("workflow_execution")


def downgrade() -> None:
    for table in ("workflow_rule_version", "workflow_execution"):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON automation.{table}"
        )
        op.execute(f"ALTER TABLE automation.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE automation.{table} DISABLE ROW LEVEL SECURITY")

    for table in ("workflow_rule", "custom_field_definition"):
        op.execute(
            f"DROP POLICY IF EXISTS {table}_tenant_isolation ON automation.{table}"
        )
        op.execute(f"ALTER TABLE automation.{table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE automation.{table} DISABLE ROW LEVEL SECURITY")

    op.drop_index(
        "idx_workflow_rule_archived",
        table_name="workflow_rule",
        schema="automation",
    )
    op.drop_column("workflow_rule", "archived_by", schema="automation")
    op.drop_column("workflow_rule", "archived_at", schema="automation")
