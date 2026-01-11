"""Add common schema and attachment table.

Revision ID: add_common_attachment
Revises: add_expense_cost_allocation
Create Date: 2025-01-10
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_common_attachment"
down_revision = "add_expense_cost_allocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the common schema
    op.execute("CREATE SCHEMA IF NOT EXISTS common")

    from app.db import Base
    import app.models.ifrs  # noqa: F401 - register models

    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[t for t in Base.metadata.sorted_tables if t.schema == "common"],
    )


def downgrade() -> None:
    from app.db import Base
    import app.models.ifrs  # noqa: F401

    bind = op.get_bind()

    # Drop all tables in common schema
    common_tables = [
        t for t in reversed(Base.metadata.sorted_tables) if t.schema == "common"
    ]

    for table in common_tables:
        table.drop(bind=bind, checkfirst=True)

    # Drop the enum type
    op.execute("DROP TYPE IF EXISTS attachment_category CASCADE")

    # Drop the schema
    op.execute("DROP SCHEMA IF EXISTS common CASCADE")
