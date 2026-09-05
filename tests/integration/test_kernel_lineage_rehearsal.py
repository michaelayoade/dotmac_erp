"""Execute the installed Kernel lineage against ERP's real migrated schema.

This is a PERMANENT NEGATIVE CANARY, not a forward ratchet. It proves the legacy
Kernel lineage must never run or be stamped in ERP, and that assertion comes from
PostgreSQL rather than from a migration-file inventory.

## Why the expected failure can never advance

An earlier version of this docstring said "each disposition moves the expected
failure forward until the lineage can be composed truthfully". **That is false
for ERP**, and believing it costs a slice.

Kernel `0001_initial_tenant_schema` creates `public.tenants` unconditionally, as
its FIRST table, before it reaches any identity/RBAC/audit work. ERP
intentionally owns that table — `20260813_tenant_projection` hosts the tenant
catalogue in ERP's own lineage as an Organization projection. So the collision is
structural and permanent: no disposition of `people`, `roles`, `user_credentials`,
`auth_sessions`, `person_roles` or `audit_events` can move a failure that happens
before any of them is reached.

Sub's equivalent rehearsal IS a forward ratchet, because Sub does not host
`tenants`. The two tests look identical and behave oppositely. Do not port
reasoning from one to the other.

## What this test is for

It stays red at `tenants`, forever, on purpose. It fails if the failure ever
CHANGES — which would mean someone dropped ERP's tenant catalogue, stamped the
kernel revision, or edited the kernel migration. Each of those is prohibited, and
this is the thing that notices.

## What replaced the thing it was blocking

`dotmac-files` needed a tenant foreign-key target and three database roles — not
the kernel's identity estate. Starter's ADR-0006 D1 amendment replaced the
physical `depends_on` with LOGICAL prerequisites an assembly binds to its own
truthful revisions, so ERP supplies `tenant_scope_catalog.v1` from
`20260813_tenant_projection` and never runs kernel `0001` at all. Full
Kernel-0001 convergence is explicitly NOT pursued: it would couple byte storage
to credentials, sessions, RBAC and audit for no domain reason.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from alembic import command
from app import config as app_config

pytestmark = pytest.mark.integration

EXPECTED_FIRST_FAILURE = "0001_initial_tenant_schema"
EXPECTED_FAILED_OBJECT = "tenants"
KERNEL_VERSION_TABLE = "dotmac_kernel_alembic_version"
ERP_PREDECESSOR = "20260812_merge_expand_withdrawal"


class KernelLineageFailure(RuntimeError):
    """A Kernel migration failure annotated with its active revision."""

    def __init__(self, revision: str, cause: Exception) -> None:
        super().__init__(f"Kernel revision {revision} failed: {cause}")
        self.revision = revision
        self.cause = cause


def _render(url: URL) -> str:
    return url.render_as_string(hide_password=False)


def _psycopg_url(url: URL) -> str:
    return _render(url.set(drivername="postgresql"))


def _kernel_versions_dir() -> Path:
    """Resolve migrations from the exact installed Kernel pin."""
    import dotmac_kernel

    directory = Path(dotmac_kernel.__file__).parent / "migrations" / "versions"
    if not directory.is_dir():
        raise pytest.UsageError(f"installed Kernel has no lineage at {directory}")
    return directory


@pytest.fixture()
def isolated_database_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[URL]:
    configured = os.getenv("TEST_DATABASE_URL")
    if not configured:
        raise pytest.UsageError("Kernel lineage rehearsal requires TEST_DATABASE_URL")
    base_url = make_url(configured)
    if not base_url.drivername.startswith("postgresql"):
        raise pytest.UsageError("Kernel lineage rehearsal requires PostgreSQL")

    name = f"erp_kernel_rehearsal_{uuid4().hex}"
    maintenance = base_url.set(database="postgres")
    with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
        admin.execute(
            sql.SQL("CREATE DATABASE {} OWNER app_admin").format(sql.Identifier(name))
        )
    try:
        database_url = base_url.set(
            database=name,
            username="app_admin",
            password=None,
        )
        monkeypatch.setenv("MIGRATION_DATABASE_URL", _render(database_url))
        # This fixture created `name` moments ago, so it can say which database
        # the upgrade is authorised for instead of leaving the executor to
        # report `database identity UNVERIFIED`. A rehearsal that cannot name
        # its own target is the weaker half of the same check it relies on.
        monkeypatch.setenv("MIGRATION_EXPECTED_DATABASE", name)
        monkeypatch.setattr(
            app_config.settings,
            "database_url",
            _render(database_url),
            raising=False,
        )
        yield database_url
    finally:
        with psycopg.connect(_psycopg_url(maintenance), autocommit=True) as admin:
            admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()",
                (name,),
            )
            admin.execute(
                sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name))
            )


def _erp_config(database_url: URL) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", _render(database_url))
    return config


def _kernel_config(database_url: URL) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("script_location", "alembic")
    config.set_main_option("version_locations", str(_kernel_versions_dir()))
    config.set_main_option("sqlalchemy.url", _render(database_url))
    return config


def _run_kernel_lineage(database_url: URL) -> None:
    """Run Kernel independently, without consuming ERP's Alembic head row."""
    config = _kernel_config(database_url)
    script = ScriptDirectory.from_config(config)
    active_revision: str | None = None

    def upgrade(revision, _context):
        steps = script._upgrade_revs("heads", revision)
        for step in steps:
            migration = step.migration_fn
            migration_revision = step.revision.revision

            def tracked_migration(
                *args,
                _migration=migration,
                _revision=migration_revision,
                **kwargs,
            ):
                nonlocal active_revision
                active_revision = _revision
                return _migration(*args, **kwargs)

            step.migration_fn = tracked_migration
        return steps

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            with EnvironmentContext(
                config,
                script,
                fn=upgrade,
                destination_rev="heads",
            ) as environment:
                environment.configure(
                    connection=connection,
                    version_table=KERNEL_VERSION_TABLE,
                    version_table_schema="public",
                )
                with environment.begin_transaction():
                    try:
                        environment.run_migrations()
                    except Exception as failure:
                        if active_revision is None:
                            raise
                        raise KernelLineageFailure(
                            active_revision,
                            failure,
                        ) from failure
    finally:
        engine.dispose()


def _database_roles(database_url: URL) -> set[str]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return set(
                connection.execute(
                    text(
                        "SELECT rolname FROM pg_roles "
                        "WHERE rolname IN ('app_admin', 'app_user', 'platform_api')"
                    )
                ).scalars()
            )
    finally:
        engine.dispose()


def _assert_no_kernel_stamp(database_url: URL) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            exists = connection.scalar(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": f"public.{KERNEL_VERSION_TABLE}"},
            )
            if exists is not None:
                assert (
                    connection.scalar(
                        text(
                            "SELECT count(*) FROM public.dotmac_kernel_alembic_version"
                        )
                    )
                    == 0
                )
    finally:
        engine.dispose()


def _insert_upgrade_organization(database_url: URL) -> UUID:
    organization_id = uuid4()
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO core_org.organization (
                        organization_id,
                        organization_code,
                        legal_name,
                        functional_currency_code,
                        presentation_currency_code,
                        fiscal_year_end_month,
                        fiscal_year_end_day,
                        is_active
                    ) VALUES (
                        :organization_id,
                        'LINEAGE',
                        'Lineage Upgrade Organization',
                        'NGN',
                        'NGN',
                        12,
                        31,
                        true
                    )
                    """
                ),
                {"organization_id": organization_id},
            )
    finally:
        engine.dispose()
    return organization_id


def _assert_expected_failure(database_url: URL) -> None:
    roles_before = _database_roles(database_url)

    with pytest.raises(KernelLineageFailure) as captured:
        _run_kernel_lineage(database_url)

    failure = captured.value
    assert failure.revision == EXPECTED_FIRST_FAILURE
    database_error = getattr(failure.cause, "orig", None)
    assert isinstance(database_error, psycopg.errors.DuplicateTable)
    assert EXPECTED_FAILED_OBJECT in str(database_error)

    _assert_no_kernel_stamp(database_url)
    assert _database_roles(database_url) == roles_before


def test_fresh_erp_chain_pins_the_exact_kernel_lineage_failure(
    isolated_database_url: URL,
) -> None:
    command.upgrade(_erp_config(isolated_database_url), "heads")

    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM information_schema.tables")
                )
                > 100
            )
            assert connection.scalar(text("SELECT to_regclass('public.tenants')"))
    finally:
        engine.dispose()

    _assert_expected_failure(isolated_database_url)


def test_real_predecessor_upgrade_pins_the_same_kernel_lineage_failure(
    isolated_database_url: URL,
) -> None:
    config = _erp_config(isolated_database_url)
    command.upgrade(config, ERP_PREDECESSOR)
    organization_id = _insert_upgrade_organization(isolated_database_url)
    command.upgrade(config, "heads")

    engine = create_engine(isolated_database_url)
    try:
        with engine.connect() as connection:
            tenant_ids = set(
                connection.execute(text("SELECT id FROM public.tenants")).scalars()
            )
            organization_ids = set(
                connection.execute(
                    text("SELECT organization_id FROM core_org.organization")
                ).scalars()
            )
            assert tenant_ids == organization_ids
            assert organization_id in tenant_ids
    finally:
        engine.dispose()

    _assert_expected_failure(isolated_database_url)
