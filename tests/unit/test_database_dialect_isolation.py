"""The PostgreSQL lane must never construct SQLite-typed ORM expressions."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "database_url",
    [
        "postgresql://localhost/dialect_probe",
        "postgresql+psycopg://localhost/dialect_probe",
    ],
)
def test_postgresql_url_preserves_native_types_before_model_import(database_url):
    script = textwrap.dedent("""        import tests.conftest
        from uuid import uuid4
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.dialects.postgresql.json import JSONB
        from sqlalchemy.sql.sqltypes import UUID
        from app.models.person import Person
        from app.models.finance.ar.customer import Customer
        assert postgresql.UUID is UUID
        assert postgresql.JSONB is JSONB
        expression = Person.id == uuid4()
        assert '::UUID' in str(expression.compile(dialect=postgresql.dialect()))
        email = Customer.primary_contact['email'].astext
        assert '->>' in str(email.compile(dialect=postgresql.dialect()))
    """)
    environment = dict(os.environ, TEST_DATABASE_URL=database_url)
    # Fixed interpreter and inline test source; no shell or user command.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("database_url", [None, "sqlite+pysqlite:///:memory:"])
def test_unit_lane_keeps_sqlite_type_adapters(database_url):
    script = textwrap.dedent("""        import tests.conftest as fixtures
        from sqlalchemy.dialects import postgresql
        from sqlalchemy.dialects.sqlite import dialect
        from sqlalchemy import create_engine
        from app.models.person import Person
        assert postgresql.UUID is fixtures.PatchedUUID
        assert postgresql.JSONB is fixtures.PatchedJSONB
        assert str(Person.__table__.c.id.type.compile(dialect=dialect())) == 'VARCHAR(36)'
        engine = create_engine('sqlite://')
        value = {'email': 'fixture@example.invalid'}
        adapter = fixtures.PatchedJSONB()
        encoded = adapter.bind_processor(engine.dialect)(value)
        assert adapter.result_processor(engine.dialect, None)(encoded) == value
        engine.dispose()
    """)
    environment = dict(os.environ)
    environment.pop("TEST_DATABASE_URL", None)
    if database_url is not None:
        environment["TEST_DATABASE_URL"] = database_url
    # Fixed interpreter and inline test source; no shell or user command.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
