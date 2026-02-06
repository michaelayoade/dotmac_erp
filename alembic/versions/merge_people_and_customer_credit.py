"""Merge heads: create_people_schemas and add_customer_credit_hold.

Revision ID: merge_people_and_customer_credit
Revises: create_people_schemas, add_customer_credit_hold
Create Date: 2025-01-20

This migration merges two parallel development branches:
- create_people_schemas: People/HR module schemas
- add_customer_credit_hold: Customer credit hold column
"""

# revision identifiers, used by Alembic.
revision = "merge_people_and_customer_credit"
down_revision = ("create_people_schemas", "add_customer_credit_hold")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge heads - no changes needed
    pass


def downgrade() -> None:
    # Merge heads - no changes needed
    pass
