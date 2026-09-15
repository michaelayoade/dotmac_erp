"""Regression coverage for native PostgreSQL DDL and ORM JSON bindings."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import (
    JSON,
    Column,
    MetaData,
    String,
    Table,
    Text,
    TypeDecorator,
    insert,
)
from sqlalchemy.dialects.postgresql.base import PGDialect
from sqlalchemy.dialects.postgresql.json import JSONB
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.sql.sqltypes import UUID

from tests.postgresql_types import native_postgresql_type


class PatchedJSONB(Text):
    """The root conftest's SQLite-only type shape."""


class PatchedUUID(TypeDecorator):
    impl = String(36)
    cache_ok = True

    def __init__(self, as_uuid=True):
        super().__init__()
        self.as_uuid = as_uuid


def test_repairs_jsonb_nested_in_a_dialect_variant_without_mutating_original():
    original = JSON(none_as_null=True).with_variant(PatchedJSONB(), "postgresql")
    repaired = native_postgresql_type(original)
    assert repaired is not original
    assert isinstance(repaired, JSON)
    assert repaired.none_as_null is True
    assert isinstance(original._variant_mapping["postgresql"], PatchedJSONB)
    assert isinstance(repaired._variant_mapping["postgresql"], JSONB)
    assert repaired.dialect_impl(PGDialect()).compile(dialect=PGDialect()) == "JSONB"
    table = Table("proof", MetaData(), Column("payload", repaired))
    sql = str(
        insert(table)
        .values(payload={"attempts": 1})
        .compile(dialect=PGDialect_psycopg())
    )
    assert "::JSONB" in sql
    processor = repaired.dialect_impl(PGDialect()).bind_processor(PGDialect())
    assert processor is not None
    assert json.loads(processor({"attempts": 1})) == {"attempts": 1}


def test_unrelated_dialect_variants_are_preserved():
    sqlite_type = Text()
    original = (
        JSON()
        .with_variant(PatchedJSONB(), "postgresql")
        .with_variant(sqlite_type, "sqlite")
    )
    repaired = native_postgresql_type(original)
    assert repaired._variant_mapping["sqlite"] is sqlite_type
    assert original._variant_mapping["sqlite"] is sqlite_type


def test_handles_separately_loaded_patched_jsonb_classes():
    alternate_class = type("PatchedJSONB", (Text,), {})
    assert isinstance(native_postgresql_type(PatchedJSONB()), JSONB)
    assert isinstance(native_postgresql_type(alternate_class()), JSONB)


@pytest.mark.parametrize("as_uuid", [True, False])
def test_uuid_repairs_preserve_python_value_mode(as_uuid):
    repaired = native_postgresql_type(PatchedUUID(as_uuid=as_uuid))
    assert isinstance(repaired, UUID)
    assert repaired.as_uuid is as_uuid


@pytest.mark.parametrize("column_type", [JSON(), Text(), String(32), JSONB()])
def test_native_and_unrelated_types_are_not_changed(column_type):
    assert native_postgresql_type(column_type) is column_type
