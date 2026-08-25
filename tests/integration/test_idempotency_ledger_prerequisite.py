"""PostgreSQL proofs for ERP's ``idempotency_ledger.v1`` provider."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

pytestmark = pytest.mark.integration

PREREQUISITE = "idempotency_ledger.v1"


@pytest.fixture(autouse=True)
def _install_erp_bindings() -> Iterator[None]:
    """Install this assembly's claims and restore process state afterwards."""
    from dotmac_kernel.prerequisites import (
        install_prerequisite_bindings,
        installed_bindings,
    )

    from app.migration_bindings import ASSEMBLY_PREREQUISITE_BINDINGS

    previous = tuple(installed_bindings())
    install_prerequisite_bindings(ASSEMBLY_PREREQUISITE_BINDINGS)
    try:
        yield
    finally:
        install_prerequisite_bindings(previous)


@contextlib.contextmanager
def _broken(engine: Engine, statement: str) -> Iterator[Connection]:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.execute(text(statement))
        yield connection
    finally:
        transaction.rollback()
        connection.close()


def test_migrated_erp_satisfies_the_kernel_ledger_contract(engine: Engine) -> None:
    from dotmac_kernel.migrations.verify import require_prerequisites

    with engine.connect() as connection:
        require_prerequisites(connection, (PREREQUISITE,))


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        pytest.param(
            "DROP TABLE public.idempotency_records",
            "does not exist",
            id="tenant-ledger-absent",
        ),
        pytest.param(
            "DROP TABLE public.platform_idempotency_records",
            "does not exist",
            id="platform-ledger-absent",
        ),
        pytest.param(
            "ALTER TABLE public.idempotency_records DROP CONSTRAINT "
            "uq_idempotency_records_tenant_scope_key",
            "no unique constraint",
            id="tenant-key-widened",
        ),
        pytest.param(
            "ALTER TABLE public.platform_idempotency_records DROP CONSTRAINT "
            "uq_platform_idempotency_records_scope_key",
            "no unique constraint",
            id="platform-key-widened",
        ),
        pytest.param(
            "ALTER TABLE public.idempotency_records DROP COLUMN fingerprint",
            "columns differ",
            id="fingerprint-overloaded-away",
        ),
        pytest.param(
            "ALTER TABLE public.idempotency_records NO FORCE ROW LEVEL SECURITY",
            "FORCEd row-level security",
            id="tenant-ledger-unforced",
        ),
        pytest.param(
            "ALTER TABLE public.platform_idempotency_records ENABLE ROW LEVEL SECURITY",
            "must carry no",
            id="platform-ledger-policied",
        ),
        pytest.param(
            "DROP INDEX public.ix_idempotency_records_expires_at",
            "no index on",
            id="retention-unindexed",
        ),
    ],
)
def test_each_broken_observable_is_refused_specifically(
    engine: Engine, statement: str, expected: str
) -> None:
    from dotmac_kernel.migrations.verify import (
        PrerequisiteNotSatisfiedError,
        require_prerequisites,
    )

    with _broken(engine, statement) as connection:
        with pytest.raises(PrerequisiteNotSatisfiedError, match=expected):
            require_prerequisites(connection, (PREREQUISITE,))


def test_platform_grants_are_reachable_and_tenant_role_is_revoked(
    engine: Engine,
) -> None:
    privileges = ("SELECT", "INSERT", "UPDATE", "DELETE")
    with engine.connect() as connection:
        for privilege in privileges:
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege("
                    ":role, 'public.platform_idempotency_records', :privilege)"
                ),
                {"role": "platform_api", "privilege": privilege},
            )
        for privilege in (
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "TRUNCATE",
            "REFERENCES",
            "TRIGGER",
        ):
            assert not connection.scalar(
                text(
                    "SELECT has_table_privilege("
                    ":role, 'public.platform_idempotency_records', :privilege)"
                ),
                {"role": "app_user", "privilege": privilege},
            )
        for privilege in ("SELECT", "INSERT", "UPDATE", "REFERENCES"):
            assert not connection.scalar(
                text(
                    "SELECT has_any_column_privilege("
                    ":role, 'public.platform_idempotency_records', :privilege)"
                ),
                {"role": "app_user", "privilege": privilege},
            )
