"""Add tax schema tables.

Revision ID: add_tax_schema_tables
Revises: add_banking_schema
Create Date: 2025-02-04
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "add_tax_schema_tables"
down_revision = "add_banking_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS tax")

    from app.db import Base
    import app.models.ifrs  # noqa: F401 - register models

    bind = op.get_bind()
    Base.metadata.create_all(
        bind=bind,
        tables=[t for t in Base.metadata.sorted_tables if t.schema == "tax"],
    )


def downgrade() -> None:
    from app.db import Base
    import app.models.ifrs  # noqa: F401

    bind = op.get_bind()

    tax_tables = [t for t in reversed(Base.metadata.sorted_tables) if t.schema == "tax"]

    for table in tax_tables:
        table.drop(bind=bind, checkfirst=True)

    op.execute("DROP SCHEMA IF EXISTS tax CASCADE")
