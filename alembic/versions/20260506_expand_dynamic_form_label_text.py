"""expand_dynamic_form_label_text

Revision ID: 20260506_form_label_text
Revises: 20260506_dynamic_forms
Create Date: 2026-05-06 13:15:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260506_form_label_text"
down_revision = "20260506_dynamic_forms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "form_field",
        "label",
        existing_type=sa.String(length=240),
        type_=sa.Text(),
        existing_nullable=False,
        schema="forms",
    )
    op.alter_column(
        "form_answer",
        "field_label_snapshot",
        existing_type=sa.String(length=240),
        type_=sa.Text(),
        existing_nullable=False,
        schema="forms",
    )


def downgrade() -> None:
    op.alter_column(
        "form_answer",
        "field_label_snapshot",
        existing_type=sa.Text(),
        type_=sa.String(length=240),
        existing_nullable=False,
        schema="forms",
    )
    op.alter_column(
        "form_field",
        "label",
        existing_type=sa.Text(),
        type_=sa.String(length=240),
        existing_nullable=False,
        schema="forms",
    )
