"""Exercise the real sales-index repair and concurrent refresh on PostgreSQL.

Only a randomly named disposable database is modified. TEST_DATABASE_URL must
point to a test server whose role can create databases (as in integration CI).
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import psycopg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import Connection, create_engine, make_url, text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def migration() -> ModuleType:
    path = ROOT / "alembic/versions/20260905_repair_sales_mv_index.py"
    spec = importlib.util.spec_from_file_location("sales_index_repair", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def database() -> Iterator[Connection]:
    configured = os.environ.get("TEST_DATABASE_URL")
    if not configured:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL migration tests")
    base = make_url(configured)
    if not base.drivername.startswith("postgresql"):
        raise pytest.UsageError("sales-index repair tests require PostgreSQL")
    name = f"erp_sales_index_{uuid4().hex}"
    maintenance = base.set(drivername="postgresql", database="postgres")
    with psycopg.connect(
        maintenance.render_as_string(hide_password=False), autocommit=True
    ) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    engine = create_engine(base.set(database=name))
    try:
        with engine.connect() as connection:
            with connection.begin():
                connection.execute(text("CREATE SCHEMA rpt"))
                connection.execute(
                    text(
                        "CREATE TABLE public.sales (invoice_id integer, total integer)"
                    )
                )
                connection.execute(text("INSERT INTO public.sales VALUES (1, 100)"))
            yield connection
    finally:
        engine.dispose()
        with psycopg.connect(
            maintenance.render_as_string(hide_password=False), autocommit=True
        ) as admin:
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def _view(connection: Connection, *, populated: bool = True) -> None:
    statement = (
        "CREATE MATERIALIZED VIEW rpt.sales_analysis_mv AS "
        "SELECT invoice_id, total FROM public.sales WITH DATA"
        if populated
        else "CREATE MATERIALIZED VIEW rpt.sales_analysis_mv AS "
        "SELECT invoice_id, total FROM public.sales WITH NO DATA"
    )
    connection.execute(text(statement))


def _upgrade(connection: Connection, migration: ModuleType) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()


def _refresh(connection: Connection) -> None:
    connection.execute(
        text("REFRESH MATERIALIZED VIEW CONCURRENTLY rpt.sales_analysis_mv")
    )


def test_missing_index_reproduces_failure_then_refreshes_new_sales(
    database: Connection, migration: ModuleType
) -> None:
    _view(database)
    with pytest.raises(DBAPIError, match="cannot refresh materialized view"):
        with database.begin_nested():
            _refresh(database)
    _upgrade(database, migration)
    repaired_oid = database.scalar(
        text("SELECT 'rpt.uq_sales_analysis_mv_invoice_id'::regclass::oid")
    )
    _upgrade(database, migration)
    with Operations.context(MigrationContext.configure(database)):
        migration.downgrade()
    assert (
        database.scalar(
            text("SELECT 'rpt.uq_sales_analysis_mv_invoice_id'::regclass::oid")
        )
        == repaired_oid
    )
    database.execute(text("INSERT INTO public.sales VALUES (2, 200)"))
    _refresh(database)
    assert database.execute(
        text("SELECT invoice_id, total FROM rpt.sales_analysis_mv ORDER BY invoice_id")
    ).all() == [(1, 100), (2, 200)]


def test_healthy_index_is_preserved_on_repeated_upgrade_and_downgrade(
    database: Connection, migration: ModuleType
) -> None:
    _view(database)
    database.execute(
        text(
            "CREATE UNIQUE INDEX original_sales_key ON rpt.sales_analysis_mv(invoice_id)"
        )
    )
    before = database.scalar(text("SELECT 'rpt.original_sales_key'::regclass::oid"))
    _upgrade(database, migration)
    _upgrade(database, migration)
    with Operations.context(MigrationContext.configure(database)):
        migration.downgrade()
    assert (
        database.scalar(text("SELECT 'rpt.original_sales_key'::regclass::oid"))
        == before
    )
    assert (
        database.scalar(
            text(
                "SELECT count(*) FROM pg_index WHERE indrelid='rpt.sales_analysis_mv'::regclass"
            )
        )
        == 1
    )
    _refresh(database)


@pytest.mark.parametrize(
    "definition",
    [
        "CREATE INDEX uq_sales_analysis_mv_invoice_id ON rpt.sales_analysis_mv(invoice_id)",
        "CREATE UNIQUE INDEX uq_sales_analysis_mv_invoice_id "
        "ON rpt.sales_analysis_mv(invoice_id) WHERE invoice_id > 0",
        "CREATE UNIQUE INDEX uq_sales_analysis_mv_invoice_id "
        "ON rpt.sales_analysis_mv((invoice_id + 0))",
    ],
)
def test_unusable_named_index_is_replaced(
    database: Connection, migration: ModuleType, definition: str
) -> None:
    _view(database)
    database.execute(text(definition))
    _upgrade(database, migration)
    _refresh(database)
    assert database.scalar(text("SELECT count(*) FROM rpt.sales_analysis_mv")) == 1


def test_duplicates_refuse_repair_without_dropping_existing_index(
    database: Connection, migration: ModuleType
) -> None:
    database.execute(text("INSERT INTO public.sales VALUES (1, 200)"))
    _view(database)
    database.execute(
        text(
            "CREATE INDEX uq_sales_analysis_mv_invoice_id ON rpt.sales_analysis_mv(invoice_id)"
        )
    )
    before = database.scalar(
        text("SELECT 'rpt.uq_sales_analysis_mv_invoice_id'::regclass::oid")
    )
    with pytest.raises(RuntimeError, match="duplicate invoice_id"):
        _upgrade(database, migration)
    assert (
        database.scalar(
            text("SELECT 'rpt.uq_sales_analysis_mv_invoice_id'::regclass::oid")
        )
        == before
    )
    assert database.scalar(text("SELECT count(*) FROM rpt.sales_analysis_mv")) == 2


def test_unpopulated_view_gets_index_before_first_plain_refresh(
    database: Connection, migration: ModuleType
) -> None:
    _view(database, populated=False)
    _upgrade(database, migration)
    database.execute(text("REFRESH MATERIALIZED VIEW rpt.sales_analysis_mv"))
    _refresh(database)
    assert database.scalar(text("SELECT total FROM rpt.sales_analysis_mv")) == 100


def test_missing_view_fails_clearly(
    database: Connection, migration: ModuleType
) -> None:
    with pytest.raises(RuntimeError, match="must exist"):
        _upgrade(database, migration)


def test_name_collision_does_not_drop_another_relations_index(
    database: Connection, migration: ModuleType
) -> None:
    _view(database)
    database.execute(text("CREATE TABLE rpt.other_report (invoice_id integer)"))
    database.execute(
        text(
            "CREATE INDEX uq_sales_analysis_mv_invoice_id ON rpt.other_report(invoice_id)"
        )
    )
    with pytest.raises(RuntimeError, match="another relation"):
        _upgrade(database, migration)
    assert database.scalar(
        text(
            "SELECT indrelid = 'rpt.other_report'::regclass FROM pg_index "
            "WHERE indexrelid = 'rpt.uq_sales_analysis_mv_invoice_id'::regclass"
        )
    )


def test_failed_concurrent_index_build_is_repaired(
    database: Connection, migration: ModuleType
) -> None:
    _view(database)
    database.commit()
    database.execution_options(isolation_level="AUTOCOMMIT")
    try:
        # A real failed concurrent build leaves an invalid index in pg_index.
        with pytest.raises(DBAPIError, match="division by zero"):
            database.execute(
                text(
                    "CREATE UNIQUE INDEX CONCURRENTLY uq_sales_analysis_mv_invoice_id "
                    "ON rpt.sales_analysis_mv ((invoice_id / (invoice_id - 1)))"
                )
            )
    finally:
        database.rollback()
        database.execution_options(isolation_level="READ COMMITTED")
    assert (
        database.scalar(
            text(
                "SELECT indisvalid FROM pg_index "
                "WHERE indexrelid='rpt.uq_sales_analysis_mv_invoice_id'::regclass"
            )
        )
        is False
    )
    _upgrade(database, migration)
    _refresh(database)
    assert database.scalar(text("SELECT total FROM rpt.sales_analysis_mv")) == 100
