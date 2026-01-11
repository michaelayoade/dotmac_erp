"""Add automation schema and tables.

Revision ID: add_automation_schema
Revises: add_banking_categorization
Create Date: 2025-02-04
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import UUID, JSONB

# revision identifiers, used by Alembic.
revision = "add_automation_schema"
down_revision = "add_banking_categorization"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Create automation schema
    op.execute("CREATE SCHEMA IF NOT EXISTS automation")

    # Create enums
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recurring_entity_type') THEN
                CREATE TYPE recurring_entity_type AS ENUM (
                    'INVOICE', 'BILL', 'EXPENSE', 'JOURNAL'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recurring_frequency') THEN
                CREATE TYPE recurring_frequency AS ENUM (
                    'DAILY', 'WEEKLY', 'BIWEEKLY', 'MONTHLY', 'QUARTERLY', 'SEMI_ANNUALLY', 'ANNUALLY'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recurring_status') THEN
                CREATE TYPE recurring_status AS ENUM (
                    'ACTIVE', 'PAUSED', 'COMPLETED', 'EXPIRED', 'CANCELLED'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'recurring_log_status') THEN
                CREATE TYPE recurring_log_status AS ENUM (
                    'SUCCESS', 'FAILED', 'SKIPPED'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'workflow_entity_type') THEN
                CREATE TYPE workflow_entity_type AS ENUM (
                    'INVOICE', 'BILL', 'EXPENSE', 'JOURNAL', 'PAYMENT', 'CUSTOMER', 'SUPPLIER',
                    'QUOTE', 'SALES_ORDER', 'PURCHASE_ORDER', 'BANK_TRANSACTION', 'RECONCILIATION'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'workflow_trigger_event') THEN
                CREATE TYPE workflow_trigger_event AS ENUM (
                    'ON_CREATE', 'ON_UPDATE', 'ON_DELETE', 'ON_STATUS_CHANGE', 'ON_FIELD_CHANGE',
                    'ON_APPROVAL', 'ON_REJECTION', 'ON_DUE_DATE', 'ON_OVERDUE', 'ON_THRESHOLD'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'workflow_action_type') THEN
                CREATE TYPE workflow_action_type AS ENUM (
                    'SEND_EMAIL', 'SEND_NOTIFICATION', 'VALIDATE', 'UPDATE_FIELD', 'CREATE_TASK', 'WEBHOOK', 'BLOCK'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'workflow_execution_status') THEN
                CREATE TYPE workflow_execution_status AS ENUM (
                    'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'SKIPPED', 'BLOCKED'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'custom_field_entity_type') THEN
                CREATE TYPE custom_field_entity_type AS ENUM (
                    'CUSTOMER', 'SUPPLIER', 'INVOICE', 'BILL', 'EXPENSE', 'QUOTE',
                    'SALES_ORDER', 'PURCHASE_ORDER', 'ITEM', 'PROJECT', 'ASSET', 'JOURNAL', 'PAYMENT'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'custom_field_type') THEN
                CREATE TYPE custom_field_type AS ENUM (
                    'TEXT', 'TEXTAREA', 'NUMBER', 'DECIMAL', 'DATE', 'DATETIME',
                    'BOOLEAN', 'SELECT', 'MULTISELECT', 'EMAIL', 'URL', 'PHONE', 'CURRENCY'
                );
            END IF;
        END$$;
    """)

    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'document_template_type') THEN
                CREATE TYPE document_template_type AS ENUM (
                    'INVOICE', 'CREDIT_NOTE', 'QUOTE', 'SALES_ORDER', 'PURCHASE_ORDER', 'BILL',
                    'RECEIPT', 'STATEMENT', 'PAYMENT_RECEIPT',
                    'EMAIL_INVOICE', 'EMAIL_QUOTE', 'EMAIL_REMINDER', 'EMAIL_OVERDUE', 'EMAIL_PAYMENT', 'EMAIL_NOTIFICATION'
                );
            END IF;
        END$$;
    """)

    # Create recurring_template table
    if not inspector.has_table("recurring_template", schema="automation"):
        op.create_table(
            "recurring_template",
            sa.Column("template_id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("template_name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("entity_type", postgresql.ENUM("INVOICE", "BILL", "EXPENSE", "JOURNAL",
                      name="recurring_entity_type", create_type=False), nullable=False),
            sa.Column("template_data", JSONB, nullable=False),
            sa.Column("frequency", postgresql.ENUM("DAILY", "WEEKLY", "BIWEEKLY", "MONTHLY",
                      "QUARTERLY", "SEMI_ANNUALLY", "ANNUALLY",
                      name="recurring_frequency", create_type=False), nullable=False),
            sa.Column("schedule_config", JSONB, nullable=False, server_default="{}"),
            sa.Column("start_date", sa.Date, nullable=False),
            sa.Column("end_date", sa.Date, nullable=True),
            sa.Column("next_run_date", sa.Date, nullable=True),
            sa.Column("occurrences_limit", sa.Integer, nullable=True),
            sa.Column("occurrences_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_generated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_generated_id", UUID(as_uuid=True), nullable=True),
            sa.Column("auto_post", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("auto_send", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("days_before_due", sa.Integer, nullable=False, server_default="30"),
            sa.Column("notify_on_generation", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("notify_email", sa.String(255), nullable=True),
            sa.Column("status", postgresql.ENUM("ACTIVE", "PAUSED", "COMPLETED", "EXPIRED", "CANCELLED",
                      name="recurring_status", create_type=False), nullable=False, server_default="ACTIVE"),
            sa.Column("source_entity_type", sa.String(50), nullable=True),
            sa.Column("source_entity_id", UUID(as_uuid=True), nullable=True),
            sa.Column("created_by", UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"],
                                    name="fk_recurring_template_org"),
            sa.UniqueConstraint("organization_id", "template_name", name="uq_recurring_template_name"),
            schema="automation",
        )
        op.create_index("idx_recurring_template_org", "recurring_template", ["organization_id"], schema="automation")
        op.create_index("idx_recurring_template_next_run", "recurring_template", ["next_run_date", "status"], schema="automation")
        op.create_index("idx_recurring_template_entity_type", "recurring_template", ["entity_type"], schema="automation")

    # Create recurring_log table
    if not inspector.has_table("recurring_log", schema="automation"):
        op.create_table(
            "recurring_log",
            sa.Column("log_id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("template_id", UUID(as_uuid=True), nullable=False),
            sa.Column("scheduled_date", sa.Date, nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("status", postgresql.ENUM("SUCCESS", "FAILED", "SKIPPED",
                      name="recurring_log_status", create_type=False), nullable=False),
            sa.Column("generated_entity_type", sa.String(50), nullable=True),
            sa.Column("generated_entity_id", UUID(as_uuid=True), nullable=True),
            sa.Column("generated_entity_number", sa.String(50), nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("error_details", sa.Text, nullable=True),
            sa.Column("skip_reason", sa.String(200), nullable=True),
            sa.ForeignKeyConstraint(["template_id"], ["automation.recurring_template.template_id"],
                                    name="fk_recurring_log_template", ondelete="CASCADE"),
            schema="automation",
        )
        op.create_index("idx_recurring_log_template", "recurring_log", ["template_id"], schema="automation")
        op.create_index("idx_recurring_log_generated_at", "recurring_log", ["generated_at"], schema="automation")
        op.create_index("idx_recurring_log_status", "recurring_log", ["status"], schema="automation")

    # Create workflow_rule table
    if not inspector.has_table("workflow_rule", schema="automation"):
        op.create_table(
            "workflow_rule",
            sa.Column("rule_id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("rule_name", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("entity_type", postgresql.ENUM("INVOICE", "BILL", "EXPENSE", "JOURNAL", "PAYMENT",
                      "CUSTOMER", "SUPPLIER", "QUOTE", "SALES_ORDER", "PURCHASE_ORDER",
                      "BANK_TRANSACTION", "RECONCILIATION",
                      name="workflow_entity_type", create_type=False), nullable=False),
            sa.Column("trigger_event", postgresql.ENUM("ON_CREATE", "ON_UPDATE", "ON_DELETE",
                      "ON_STATUS_CHANGE", "ON_FIELD_CHANGE", "ON_APPROVAL", "ON_REJECTION",
                      "ON_DUE_DATE", "ON_OVERDUE", "ON_THRESHOLD",
                      name="workflow_trigger_event", create_type=False), nullable=False),
            sa.Column("trigger_conditions", JSONB, nullable=False, server_default="{}"),
            sa.Column("action_type", postgresql.ENUM("SEND_EMAIL", "SEND_NOTIFICATION", "VALIDATE",
                      "UPDATE_FIELD", "CREATE_TASK", "WEBHOOK", "BLOCK",
                      name="workflow_action_type", create_type=False), nullable=False),
            sa.Column("action_config", JSONB, nullable=False, server_default="{}"),
            sa.Column("priority", sa.Integer, nullable=False, server_default="100"),
            sa.Column("stop_on_match", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("execute_async", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("execution_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("success_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("failure_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("last_executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_by", UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"],
                                    name="fk_workflow_rule_org"),
            sa.UniqueConstraint("organization_id", "rule_name", name="uq_workflow_rule_name"),
            schema="automation",
        )
        op.create_index("idx_workflow_rule_org", "workflow_rule", ["organization_id"], schema="automation")
        op.create_index("idx_workflow_rule_entity", "workflow_rule", ["entity_type"], schema="automation")
        op.create_index("idx_workflow_rule_trigger", "workflow_rule", ["trigger_event"], schema="automation")
        op.create_index("idx_workflow_rule_active", "workflow_rule", ["is_active"], schema="automation")

    # Create workflow_execution table
    if not inspector.has_table("workflow_execution", schema="automation"):
        op.create_table(
            "workflow_execution",
            sa.Column("execution_id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("rule_id", UUID(as_uuid=True), nullable=False),
            sa.Column("entity_type", sa.String(50), nullable=False),
            sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
            sa.Column("trigger_event", sa.String(50), nullable=False),
            sa.Column("trigger_data", JSONB, nullable=True),
            sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("duration_ms", sa.Integer, nullable=True),
            sa.Column("status", postgresql.ENUM("PENDING", "RUNNING", "SUCCESS", "FAILED", "SKIPPED", "BLOCKED",
                      name="workflow_execution_status", create_type=False), nullable=False, server_default="PENDING"),
            sa.Column("result", JSONB, nullable=True),
            sa.Column("error_message", sa.Text, nullable=True),
            sa.Column("error_details", sa.Text, nullable=True),
            sa.Column("retry_count", sa.Integer, nullable=False, server_default="0"),
            sa.Column("max_retries", sa.Integer, nullable=False, server_default="3"),
            sa.Column("triggered_by", UUID(as_uuid=True), nullable=True),
            sa.ForeignKeyConstraint(["rule_id"], ["automation.workflow_rule.rule_id"],
                                    name="fk_workflow_execution_rule", ondelete="CASCADE"),
            schema="automation",
        )
        op.create_index("idx_workflow_execution_rule", "workflow_execution", ["rule_id"], schema="automation")
        op.create_index("idx_workflow_execution_entity", "workflow_execution", ["entity_type", "entity_id"], schema="automation")
        op.create_index("idx_workflow_execution_triggered", "workflow_execution", ["triggered_at"], schema="automation")
        op.create_index("idx_workflow_execution_status", "workflow_execution", ["status"], schema="automation")

    # Create custom_field_definition table
    if not inspector.has_table("custom_field_definition", schema="automation"):
        op.create_table(
            "custom_field_definition",
            sa.Column("field_id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("entity_type", postgresql.ENUM("CUSTOMER", "SUPPLIER", "INVOICE", "BILL", "EXPENSE",
                      "QUOTE", "SALES_ORDER", "PURCHASE_ORDER", "ITEM", "PROJECT", "ASSET", "JOURNAL", "PAYMENT",
                      name="custom_field_entity_type", create_type=False), nullable=False),
            sa.Column("field_code", sa.String(50), nullable=False),
            sa.Column("field_name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("field_type", postgresql.ENUM("TEXT", "TEXTAREA", "NUMBER", "DECIMAL", "DATE", "DATETIME",
                      "BOOLEAN", "SELECT", "MULTISELECT", "EMAIL", "URL", "PHONE", "CURRENCY",
                      name="custom_field_type", create_type=False), nullable=False),
            sa.Column("field_options", JSONB, nullable=True),
            sa.Column("is_required", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("default_value", sa.String(500), nullable=True),
            sa.Column("validation_regex", sa.String(500), nullable=True),
            sa.Column("validation_message", sa.String(200), nullable=True),
            sa.Column("min_value", sa.String(50), nullable=True),
            sa.Column("max_value", sa.String(50), nullable=True),
            sa.Column("max_length", sa.Integer, nullable=True),
            sa.Column("display_order", sa.Integer, nullable=False, server_default="0"),
            sa.Column("section_name", sa.String(100), nullable=True),
            sa.Column("placeholder", sa.String(200), nullable=True),
            sa.Column("help_text", sa.String(500), nullable=True),
            sa.Column("css_class", sa.String(100), nullable=True),
            sa.Column("show_in_list", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("show_in_form", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("show_in_detail", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("show_in_print", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_by", UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"],
                                    name="fk_custom_field_org"),
            sa.UniqueConstraint("organization_id", "entity_type", "field_code", name="uq_custom_field_code"),
            schema="automation",
        )
        op.create_index("idx_custom_field_org", "custom_field_definition", ["organization_id"], schema="automation")
        op.create_index("idx_custom_field_entity", "custom_field_definition", ["entity_type"], schema="automation")
        op.create_index("idx_custom_field_active", "custom_field_definition", ["is_active"], schema="automation")

    # Create document_template table
    if not inspector.has_table("document_template", schema="automation"):
        op.create_table(
            "document_template",
            sa.Column("template_id", UUID(as_uuid=True), primary_key=True,
                      server_default=sa.text("gen_random_uuid()")),
            sa.Column("organization_id", UUID(as_uuid=True), nullable=False),
            sa.Column("template_type", postgresql.ENUM("INVOICE", "CREDIT_NOTE", "QUOTE", "SALES_ORDER",
                      "PURCHASE_ORDER", "BILL", "RECEIPT", "STATEMENT", "PAYMENT_RECEIPT",
                      "EMAIL_INVOICE", "EMAIL_QUOTE", "EMAIL_REMINDER", "EMAIL_OVERDUE", "EMAIL_PAYMENT", "EMAIL_NOTIFICATION",
                      name="document_template_type", create_type=False), nullable=False),
            sa.Column("template_name", sa.String(100), nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("template_content", sa.Text, nullable=False),
            sa.Column("css_styles", sa.Text, nullable=True),
            sa.Column("header_config", JSONB, nullable=True),
            sa.Column("footer_config", JSONB, nullable=True),
            sa.Column("page_size", sa.String(20), nullable=False, server_default="A4"),
            sa.Column("page_orientation", sa.String(20), nullable=False, server_default="portrait"),
            sa.Column("page_margins", JSONB, nullable=True),
            sa.Column("email_subject", sa.String(500), nullable=True),
            sa.Column("email_from_name", sa.String(100), nullable=True),
            sa.Column("version", sa.Integer, nullable=False, server_default="1"),
            sa.Column("is_default", sa.Boolean, nullable=False, server_default="false"),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default="true"),
            sa.Column("created_by", UUID(as_uuid=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                      server_default=sa.text("now()")),
            sa.Column("updated_by", UUID(as_uuid=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(["organization_id"], ["core_org.organization.organization_id"],
                                    name="fk_document_template_org"),
            sa.UniqueConstraint("organization_id", "template_type", "template_name", name="uq_document_template"),
            schema="automation",
        )
        op.create_index("idx_document_template_org", "document_template", ["organization_id"], schema="automation")
        op.create_index("idx_document_template_type", "document_template", ["template_type"], schema="automation")
        op.create_index("idx_document_template_default", "document_template", ["is_default"], schema="automation")


def downgrade() -> None:
    # Drop tables
    op.drop_table("document_template", schema="automation")
    op.drop_table("custom_field_definition", schema="automation")
    op.drop_table("workflow_execution", schema="automation")
    op.drop_table("workflow_rule", schema="automation")
    op.drop_table("recurring_log", schema="automation")
    op.drop_table("recurring_template", schema="automation")

    # Drop schema
    op.execute("DROP SCHEMA IF EXISTS automation CASCADE")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS document_template_type")
    op.execute("DROP TYPE IF EXISTS custom_field_type")
    op.execute("DROP TYPE IF EXISTS custom_field_entity_type")
    op.execute("DROP TYPE IF EXISTS workflow_execution_status")
    op.execute("DROP TYPE IF EXISTS workflow_action_type")
    op.execute("DROP TYPE IF EXISTS workflow_trigger_event")
    op.execute("DROP TYPE IF EXISTS workflow_entity_type")
    op.execute("DROP TYPE IF EXISTS recurring_log_status")
    op.execute("DROP TYPE IF EXISTS recurring_status")
    op.execute("DROP TYPE IF EXISTS recurring_frequency")
    op.execute("DROP TYPE IF EXISTS recurring_entity_type")
