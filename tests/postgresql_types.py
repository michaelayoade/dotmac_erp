"""Undo SQLite-only SQLAlchemy types, including dialect-specific variants.

A copied table's DDL and its ORM mapper must use the same native PostgreSQL
bindings. Fixing only a table copy leaves dict values bound as VARCHAR by the
original mapper, which psycopg cannot adapt. Keep this helper independent of
fixtures so its restoration behavior can be tested without a database.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text
from sqlalchemy.dialects.postgresql.json import JSONB
from sqlalchemy.sql.sqltypes import UUID
from sqlalchemy.sql.type_api import TypeEngine


def native_postgresql_type(column_type: TypeEngine[Any]) -> TypeEngine[Any]:
    """Return a repaired copy only when a SQLite stand-in is present.

    SQLAlchemy 2 stores ``with_variant`` choices on the outer type rather than
    wrapping them in a Variant object. Repair those choices recursively without
    changing the generic/SQLite type or any unrelated dialect variant. The
    caller retains the original type object to restore it after its fixture.
    """
    if (
        hasattr(column_type, "impl")
        and hasattr(column_type, "as_uuid")
        and hasattr(column_type.impl, "length")
    ):
        return UUID(as_uuid=column_type.as_uuid)
    if isinstance(column_type, Text) and any(
        cls.__name__ == "PatchedJSONB" for cls in type(column_type).__mro__
    ):
        return JSONB()

    variants = column_type._variant_mapping
    repaired_variants = {
        dialect: native_postgresql_type(variant)
        for dialect, variant in variants.items()
    }
    if any(repaired_variants[key] is not value for key, value in variants.items()):
        repaired = column_type.copy()
        repaired._variant_mapping = variants.union(repaired_variants)
        return repaired
    return column_type
