"""Alembic utilities for shared migration helpers."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.dialects import postgresql


def ensure_enum(
    bind,
    name: str,
    *values: str,
    schema: Optional[str] = None,
) -> postgresql.ENUM:
    """Create a PostgreSQL enum type if it does not already exist."""
    enum = postgresql.ENUM(*values, name=name, schema=schema, create_type=False)
    enum.create(bind, checkfirst=True)
    return enum
