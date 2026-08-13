"""PostgreSQL proof for the ERP-hosted kernel tenant catalogue."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

pytestmark = pytest.mark.integration

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = PROJECT_ROOT / "alembic" / "versions" / "20260813_tenant_projection.py"


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


@pytest.fixture()
def isolated_database_url() -> Iterator[URL]:
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError(
            "tenant projection migration requires TEST_DATABASE_URL"
        )
    base_url = make_url(configured)
    if not base_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("tenant projection migration requires PostgreSQL")

    name = f"erp_tenant_projection_{uuid4().hex}"
    maintenance = base_url.set(database="postgres")
    with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield base_url.set(database=name)
    finally:
        with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "erp_tenant_projection_migration", MIGRATION
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade(database_url: URL) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                _load_migration().upgrade()
    finally:
        engine.dispose()


def _create_organization_source(database_url: URL) -> tuple[UUID, UUID]:
    first_id = uuid4()
    second_id = uuid4()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE SCHEMA core_org"))
            connection.execute(
                text(
                    """
                    CREATE TABLE core_org.organization (
                        organization_id uuid PRIMARY KEY,
                        legal_name varchar(255) NOT NULL,
                        is_active boolean NOT NULL,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO core_org.organization
                        (organization_id, legal_name, is_active)
                    VALUES (:first_id, '  Dotmac Abuja  ', true),
                           (:second_id, 'Dotmac Lagos', false)
                    """
                ),
                {"first_id": first_id, "second_id": second_id},
            )
    finally:
        engine.dispose()
    return first_id, second_id


def test_migration_projects_every_organization_and_is_rerunnable(
    isolated_database_url: URL,
) -> None:
    first_id, second_id = _create_organization_source(isolated_database_url)

    _run_upgrade(isolated_database_url)
    _run_upgrade(isolated_database_url)

    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT id, slug, name, is_active
                      FROM public.tenants
                     ORDER BY name
                    """
                )
            ).all()
            assert rows == [
                (first_id, f"erp-{first_id}", "Dotmac Abuja", True),
                (second_id, f"erp-{second_id}", "Dotmac Lagos", False),
            ]
            rls = connection.execute(
                text(
                    """
                    SELECT relname, relrowsecurity, relforcerowsecurity
                      FROM pg_class
                     WHERE oid IN ('public.tenants'::regclass,
                                   'public.tenant_domains'::regclass)
                     ORDER BY relname
                    """
                )
            ).all()
            assert rls == [
                ("tenant_domains", False, False),
                ("tenants", False, False),
            ]
            assert (
                connection.scalar(text("SELECT public.app_current_tenant_id()")) is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT has_table_privilege('public', 'public.tenants', 'INSERT')"
                    )
                )
                is False
            )
    finally:
        engine.dispose()


def test_migration_refuses_to_overwrite_existing_projection_drift(
    isolated_database_url: URL,
) -> None:
    first_id, _ = _create_organization_source(isolated_database_url)
    _run_upgrade(isolated_database_url)
    engine = create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE public.tenants SET name = 'Unknown writer' WHERE id = :id"
                ),
                {"id": first_id},
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        _run_upgrade(isolated_database_url)


def test_migration_refuses_an_incompatible_existing_catalog(
    isolated_database_url: URL,
) -> None:
    _create_organization_source(isolated_database_url)
    engine = create_engine(isolated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE public.tenants (
                        id uuid PRIMARY KEY,
                        slug varchar(64) NOT NULL UNIQUE,
                        name varchar(120) NOT NULL,
                        is_active boolean NOT NULL DEFAULT true,
                        suspended_at timestamptz,
                        deleted_at timestamptz,
                        created_at timestamptz NOT NULL DEFAULT now(),
                        updated_at timestamptz NOT NULL DEFAULT now()
                    )
                    """
                )
            )
            connection.execute(
                text("CREATE INDEX ix_tenants_slug ON public.tenants (slug)")
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match=r"tenants\.slug has length 64"):
        _run_upgrade(isolated_database_url)
