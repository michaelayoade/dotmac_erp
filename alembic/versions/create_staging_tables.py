"""Create staging tables for ERPNext sync.

Revision ID: create_staging_tables
Revises: add_integration_config
Create Date: 2026-01-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "create_staging_tables"
down_revision: Union[str, None] = "add_integration_config"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create staging_sync_batch table
    op.create_table(
        "staging_sync_batch",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(50), nullable=False, server_default="erpnext"),
        sa.Column("entity_types", postgresql.JSONB, nullable=True),
        sa.Column("status", sa.String(30), nullable=False, server_default="SYNCING"),
        sa.Column("total_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invalid_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("imported_records", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("validation_summary", postgresql.JSONB, nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("synced_at", sa.DateTime(), nullable=True),
        sa.Column("validated_at", sa.DateTime(), nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=True),
        sa.Column("initiated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("batch_id"),
        schema="sync",
    )
    op.create_index("idx_staging_batch_org", "staging_sync_batch", ["organization_id"], schema="sync")
    op.create_index("idx_staging_batch_status", "staging_sync_batch", ["status"], schema="sync")

    # Create staging_department table
    op.create_table(
        "staging_department",
        sa.Column("staging_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(140), nullable=False),
        sa.Column("source_modified", sa.DateTime(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB, nullable=True),
        sa.Column("department_code", sa.String(50), nullable=False),
        sa.Column("department_name", sa.String(140), nullable=False),
        sa.Column("parent_department_name", sa.String(140), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", postgresql.JSONB, nullable=True),
        sa.Column("validation_warnings", postgresql.JSONB, nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=True),
        sa.Column("imported_department_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("staging_id"),
        schema="sync",
    )
    op.create_index("idx_staging_dept_org", "staging_department", ["organization_id"], schema="sync")
    op.create_index("idx_staging_dept_status", "staging_department", ["validation_status"], schema="sync")
    op.create_index("idx_staging_dept_batch", "staging_department", ["batch_id"], schema="sync")

    # Create staging_designation table
    op.create_table(
        "staging_designation",
        sa.Column("staging_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(140), nullable=False),
        sa.Column("source_modified", sa.DateTime(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB, nullable=True),
        sa.Column("designation_code", sa.String(50), nullable=False),
        sa.Column("designation_name", sa.String(140), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", postgresql.JSONB, nullable=True),
        sa.Column("validation_warnings", postgresql.JSONB, nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=True),
        sa.Column("imported_designation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("staging_id"),
        schema="sync",
    )
    op.create_index("idx_staging_desg_org", "staging_designation", ["organization_id"], schema="sync")
    op.create_index("idx_staging_desg_status", "staging_designation", ["validation_status"], schema="sync")

    # Create staging_employment_type table
    op.create_table(
        "staging_employment_type",
        sa.Column("staging_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(140), nullable=False),
        sa.Column("source_modified", sa.DateTime(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB, nullable=True),
        sa.Column("type_code", sa.String(30), nullable=False),
        sa.Column("type_name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", postgresql.JSONB, nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=True),
        sa.Column("imported_employment_type_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("staging_id"),
        schema="sync",
    )
    op.create_index("idx_staging_emptype_org", "staging_employment_type", ["organization_id"], schema="sync")

    # Create staging_employee_grade table
    op.create_table(
        "staging_employee_grade",
        sa.Column("staging_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(140), nullable=False),
        sa.Column("source_modified", sa.DateTime(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB, nullable=True),
        sa.Column("grade_code", sa.String(30), nullable=False),
        sa.Column("grade_name", sa.String(100), nullable=False),
        sa.Column("default_base_pay", sa.Numeric(15, 2), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", postgresql.JSONB, nullable=True),
        sa.Column("imported_at", sa.DateTime(), nullable=True),
        sa.Column("imported_grade_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("staging_id"),
        schema="sync",
    )
    op.create_index("idx_staging_grade_org", "staging_employee_grade", ["organization_id"], schema="sync")

    # Create staging_employee table
    op.create_table(
        "staging_employee",
        sa.Column("staging_id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(140), nullable=False),
        sa.Column("source_modified", sa.DateTime(), nullable=True),
        sa.Column("raw_data", postgresql.JSONB, nullable=True),
        # Mapped fields
        sa.Column("employee_code", sa.String(30), nullable=False),
        sa.Column("employee_name", sa.String(200), nullable=False),
        sa.Column("first_name", sa.String(100), nullable=True),
        sa.Column("last_name", sa.String(100), nullable=True),
        sa.Column("gender", sa.String(20), nullable=True),
        sa.Column("date_of_birth", sa.Date(), nullable=True),
        # Contact
        sa.Column("company_email", sa.String(255), nullable=True),
        sa.Column("personal_email", sa.String(255), nullable=True),
        sa.Column("preferred_email", sa.String(255), nullable=True),
        sa.Column("cell_number", sa.String(50), nullable=True),
        # References
        sa.Column("department_name", sa.String(140), nullable=True),
        sa.Column("designation_name", sa.String(140), nullable=True),
        sa.Column("employment_type_name", sa.String(140), nullable=True),
        sa.Column("grade_name", sa.String(140), nullable=True),
        sa.Column("reports_to_name", sa.String(140), nullable=True),
        # Employment
        sa.Column("date_of_joining", sa.Date(), nullable=True),
        sa.Column("date_of_leaving", sa.Date(), nullable=True),
        sa.Column("status", sa.String(20), nullable=True),
        # Bank
        sa.Column("bank_name", sa.String(100), nullable=True),
        sa.Column("bank_ac_no", sa.String(50), nullable=True),
        # Validation
        sa.Column("validation_status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("validation_errors", postgresql.JSONB, nullable=True),
        sa.Column("validation_warnings", postgresql.JSONB, nullable=True),
        # Import tracking
        sa.Column("imported_at", sa.DateTime(), nullable=True),
        sa.Column("imported_employee_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("imported_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("staging_id"),
        schema="sync",
    )
    op.create_index("idx_staging_emp_org", "staging_employee", ["organization_id"], schema="sync")
    op.create_index("idx_staging_emp_status", "staging_employee", ["validation_status"], schema="sync")
    op.create_index("idx_staging_emp_batch", "staging_employee", ["batch_id"], schema="sync")
    op.create_index("idx_staging_emp_source", "staging_employee", ["source_name"], schema="sync")


def downgrade() -> None:
    op.drop_table("staging_employee", schema="sync")
    op.drop_table("staging_employee_grade", schema="sync")
    op.drop_table("staging_employment_type", schema="sync")
    op.drop_table("staging_designation", schema="sync")
    op.drop_table("staging_department", schema="sync")
    op.drop_table("staging_sync_batch", schema="sync")
