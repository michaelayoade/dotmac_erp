"""Add expense schema and tables.

Revision ID: add_expense_schema
Revises: add_tax_schema_tables
Create Date: 2025-02-04
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_expense_schema"
down_revision = "add_tax_schema_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS exp")

    from app.db import Base
    import app.models.ifrs  # noqa: F401 - register models

    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[t for t in Base.metadata.sorted_tables if t.schema == "exp"],
    )


def downgrade() -> None:
    from app.db import Base
    import app.models.ifrs  # noqa: F401

    bind = op.get_bind()

    exp_tables = [
        t for t in reversed(Base.metadata.sorted_tables) if t.schema == "exp"
    ]

    for table in exp_tables:
        table.drop(bind=bind, checkfirst=True)

    op.execute("DROP SCHEMA IF EXISTS exp CASCADE")
