"""Regression proof for cached comparators during PostgreSQL type repair."""

from uuid import uuid4

import pytest
from sqlalchemy import Column, String
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.types import TypeDecorator

from tests.postgresql_types import native_postgresql_type, set_column_type


class SQLiteUUID(TypeDecorator):
    impl = String(36)
    cache_ok = True
    as_uuid = True


@pytest.mark.parametrize("operator", ["eq", "ne"])
def test_native_uuid_repair_discards_a_cached_decorator_comparator(operator):
    original = SQLiteUUID()
    column = Column("identifier", original)
    value = uuid4()
    getattr(column, f"__{operator}__")(value)
    old_comparator = column.comparator
    set_column_type(column, native_postgresql_type(original))
    assert column.comparator is not old_comparator
    expression = getattr(column, f"__{operator}__")(value)
    assert "::UUID" in str(expression.compile(dialect=postgresql.dialect()))
    assert "IS NULL" in str(column.is_(None))


def test_restoration_rebuilds_the_original_sqlite_comparator():
    original = SQLiteUUID()
    column = Column("identifier", original)
    value = uuid4()
    _ = column == value
    set_column_type(column, native_postgresql_type(original))
    _ = column == value
    native_comparator = column.comparator
    set_column_type(column, original)
    assert column.type is original
    assert column.comparator is not native_comparator
    assert str((column == value).compile(dialect=sqlite.dialect())) == "identifier = ?"
    assert "IS NULL" in str(column.is_(None))


def test_repeated_native_and_sqlite_restoration_remains_safe():
    original = SQLiteUUID()
    column = Column("identifier", original)
    for _iteration in range(3):
        _ = column == uuid4()
        set_column_type(column, native_postgresql_type(original))
        assert "::UUID" in str(
            (column == uuid4()).compile(dialect=postgresql.dialect())
        )
        set_column_type(column, original)
        assert (
            str((column == uuid4()).compile(dialect=sqlite.dialect()))
            == "identifier = ?"
        )
