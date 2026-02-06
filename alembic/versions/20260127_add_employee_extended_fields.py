"""Add extended employee fields for ERPNext sync.

Adds new fields to hr.employee:
- Address fields: current_address, permanent_address (JSONB)
- Personal fields: marital_status, blood_group
- Passport fields: passport_number, passport_valid_upto
- Housing field: current_accommodation_type
- Compensation fields: ctc, salary_mode

Revision ID: 20260127_add_employee_extended_fields
Revises: 20260127_add_salary_slip_bank_branch_code
Create Date: 2026-01-27
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from app.alembic_utils import ensure_enum

# revision identifiers, used by Alembic.
revision = "20260127_add_employee_extended_fields"
down_revision = "7496299622c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = [c["name"] for c in inspector.get_columns("employee", schema="hr")]

    # Create new enum types
    ensure_enum(
        bind,
        "hr_marital_status",
        "SINGLE",
        "MARRIED",
        "DIVORCED",
        "WIDOWED",
        "PREFER_NOT_TO_SAY",
    )
    ensure_enum(
        bind,
        "hr_blood_group",
        "A+",
        "A-",
        "B+",
        "B-",
        "AB+",
        "AB-",
        "O+",
        "O-",
        "UNKNOWN",
    )
    ensure_enum(
        bind,
        "hr_accommodation_type",
        "OWNED",
        "RENTED",
        "COMPANY_PROVIDED",
        "OTHER",
    )
    ensure_enum(
        bind,
        "hr_salary_mode",
        "BANK",
        "CASH",
        "CHEQUE",
    )

    # Add personal fields
    if "marital_status" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "marital_status", sa.Enum(name="hr_marital_status"), nullable=True
            ),
            schema="hr",
        )
    if "blood_group" not in columns:
        op.add_column(
            "employee",
            sa.Column("blood_group", sa.Enum(name="hr_blood_group"), nullable=True),
            schema="hr",
        )

    # Add address fields (JSONB for flexible structure)
    if "current_address" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "current_address",
                JSONB,
                nullable=True,
                comment="Current residential address",
            ),
            schema="hr",
        )
    if "permanent_address" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "permanent_address",
                JSONB,
                nullable=True,
                comment="Permanent address",
            ),
            schema="hr",
        )

    # Add passport fields
    if "passport_number" not in columns:
        op.add_column(
            "employee",
            sa.Column("passport_number", sa.String(50), nullable=True),
            schema="hr",
        )
    if "passport_valid_upto" not in columns:
        op.add_column(
            "employee",
            sa.Column("passport_valid_upto", sa.Date, nullable=True),
            schema="hr",
        )

    # Add housing field
    if "current_accommodation_type" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "current_accommodation_type",
                sa.Enum(name="hr_accommodation_type"),
                nullable=True,
            ),
            schema="hr",
        )

    # Add compensation fields
    if "ctc" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "ctc",
                sa.Numeric(20, 2),
                nullable=True,
                comment="Cost to Company (annual)",
            ),
            schema="hr",
        )
    if "salary_mode" not in columns:
        op.add_column(
            "employee",
            sa.Column(
                "salary_mode",
                sa.Enum(name="hr_salary_mode"),
                nullable=True,
                comment="Salary payment mode (Bank/Cash/Cheque)",
            ),
            schema="hr",
        )


def downgrade() -> None:
    # Remove columns
    op.drop_column("employee", "salary_mode", schema="hr")
    op.drop_column("employee", "ctc", schema="hr")
    op.drop_column("employee", "current_accommodation_type", schema="hr")
    op.drop_column("employee", "passport_valid_upto", schema="hr")
    op.drop_column("employee", "passport_number", schema="hr")
    op.drop_column("employee", "permanent_address", schema="hr")
    op.drop_column("employee", "current_address", schema="hr")
    op.drop_column("employee", "blood_group", schema="hr")
    op.drop_column("employee", "marital_status", schema="hr")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS hr_salary_mode")
    op.execute("DROP TYPE IF EXISTS hr_accommodation_type")
    op.execute("DROP TYPE IF EXISTS hr_blood_group")
    op.execute("DROP TYPE IF EXISTS hr_marital_status")
