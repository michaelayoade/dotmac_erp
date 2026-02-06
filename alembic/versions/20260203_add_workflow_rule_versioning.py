"""Add workflow rule versioning and scheduling fields.

Revision ID: 20260203_add_workflow_rule_versioning
Revises: 20260203_add_notification_entity_types
Create Date: 2026-02-03
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID


revision = "20260203_add_workflow_rule_versioning"
down_revision = "20260203_add_notification_entity_types"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add rule versioning table, new enums, and scheduling fields."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Extend enums for new workflow entity types (IF NOT EXISTS is safe)
    op.execute("ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'CREDIT_NOTE'")
    op.execute("ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'CASH_ADVANCE'")
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'ASSET_DISPOSAL'"
    )
    op.execute("ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'EMPLOYEE'")
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'LEAVE_REQUEST'"
    )
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'DISCIPLINARY_CASE'"
    )
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'PERFORMANCE_APPRAISAL'"
    )
    op.execute("ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'PAYROLL_RUN'")
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'PAYROLL_ENTRY'"
    )
    op.execute("ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'SALARY_SLIP'")
    op.execute("ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'LOAN'")
    op.execute("ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'RECRUITMENT'")
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'FLEET_VEHICLE'"
    )
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'FLEET_RESERVATION'"
    )
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'FLEET_MAINTENANCE'"
    )
    op.execute(
        "ALTER TYPE workflow_entity_type ADD VALUE IF NOT EXISTS 'FLEET_INCIDENT'"
    )

    # Extend enums for trigger event and action type
    op.execute(
        "ALTER TYPE workflow_trigger_event ADD VALUE IF NOT EXISTS 'ON_SCHEDULE'"
    )
    op.execute("ALTER TYPE workflow_action_type ADD VALUE IF NOT EXISTS 'TRIGGER_RULE'")

    # Add scheduling/throttling columns to workflow_rule
    if inspector.has_table("workflow_rule", schema="automation"):
        columns = {
            col["name"]
            for col in inspector.get_columns("workflow_rule", schema="automation")
        }
        if "cooldown_seconds" not in columns:
            op.add_column(
                "workflow_rule",
                sa.Column("cooldown_seconds", sa.Integer, nullable=True),
                schema="automation",
            )
        if "schedule_config" not in columns:
            op.add_column(
                "workflow_rule",
                sa.Column("schedule_config", postgresql.JSONB, nullable=True),
                schema="automation",
            )

    # Create workflow_rule_version table
    if not inspector.has_table("workflow_rule_version", schema="automation"):
        op.create_table(
            "workflow_rule_version",
            sa.Column(
                "version_id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column("rule_id", UUID(as_uuid=True), nullable=False),
            sa.Column("version_number", sa.Integer, nullable=False),
            sa.Column("rule_name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("entity_type", sa.String(50), nullable=False),
            sa.Column("trigger_event", sa.String(50), nullable=False),
            sa.Column(
                "trigger_conditions",
                postgresql.JSONB,
                nullable=False,
                server_default="{}",
            ),
            sa.Column("action_type", sa.String(50), nullable=False),
            sa.Column(
                "action_config",
                postgresql.JSONB,
                nullable=False,
                server_default="{}",
            ),
            sa.Column("priority", sa.Integer, nullable=False),
            sa.Column("cooldown_seconds", sa.Integer, nullable=True),
            sa.Column("schedule_config", postgresql.JSONB, nullable=True),
            sa.Column("changed_by", UUID(as_uuid=True), nullable=True),
            sa.Column("change_summary", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.text("now()"),
            ),
            sa.ForeignKeyConstraint(
                ["rule_id"],
                ["automation.workflow_rule.rule_id"],
                name="fk_workflow_rule_version_rule",
                ondelete="CASCADE",
            ),
            schema="automation",
        )

    if inspector.has_table("workflow_rule_version", schema="automation"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes(
                "workflow_rule_version", schema="automation"
            )
            if idx.get("name")
        }
        if "idx_rule_version_rule" not in indexes:
            op.create_index(
                "idx_rule_version_rule",
                "workflow_rule_version",
                ["rule_id"],
                schema="automation",
            )
        if "idx_rule_version_created" not in indexes:
            op.create_index(
                "idx_rule_version_created",
                "workflow_rule_version",
                ["created_at"],
                schema="automation",
            )


def downgrade() -> None:
    """Downgrade is a best-effort; enum value removal is not supported."""
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if inspector.has_table("workflow_rule_version", schema="automation"):
        indexes = {
            idx["name"]
            for idx in inspector.get_indexes(
                "workflow_rule_version", schema="automation"
            )
            if idx.get("name")
        }
        if "idx_rule_version_created" in indexes:
            op.drop_index(
                "idx_rule_version_created",
                table_name="workflow_rule_version",
                schema="automation",
            )
        if "idx_rule_version_rule" in indexes:
            op.drop_index(
                "idx_rule_version_rule",
                table_name="workflow_rule_version",
                schema="automation",
            )
        op.drop_table("workflow_rule_version", schema="automation")

    if inspector.has_table("workflow_rule", schema="automation"):
        columns = {
            col["name"]
            for col in inspector.get_columns("workflow_rule", schema="automation")
        }
        if "schedule_config" in columns:
            op.drop_column("workflow_rule", "schedule_config", schema="automation")
        if "cooldown_seconds" in columns:
            op.drop_column("workflow_rule", "cooldown_seconds", schema="automation")

    # Enum value removal not supported in PostgreSQL.
    pass
