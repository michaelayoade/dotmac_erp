"""The real RLS helper primes ERP and module scope in one statement."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

RLS_PATH = Path(__file__).resolve().parents[2] / "app" / "rls.py"


@pytest.fixture(scope="module")
def real_rls() -> ModuleType:
    """Load the implementation hidden by the SQLite suite's app.rls stub."""
    spec = importlib.util.spec_from_file_location("_real_app_rls", RLS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _statement_and_params(execute: MagicMock) -> tuple[str, dict[str, str]]:
    statement, params = execute.call_args.args
    return str(statement), params


def test_sync_primer_sets_organization_and_tenant_atomically(
    real_rls: ModuleType,
) -> None:
    session = MagicMock()
    organization_id = uuid4()

    real_rls.set_current_organization_sync(session, organization_id)

    statement, params = _statement_and_params(session.execute)
    assert "app.current_organization_id" in statement
    assert "app.current_tenant" in statement
    assert statement.upper().count("SET_CONFIG") == 2
    assert params == {
        "organization_id": str(organization_id),
        "tenant_id": str(organization_id),
    }


@pytest.mark.asyncio
async def test_async_primer_sets_the_same_two_scopes(real_rls: ModuleType) -> None:
    session = MagicMock()
    session.execute = AsyncMock()
    organization_id = uuid4()

    await real_rls.set_current_organization(session, organization_id)

    statement, params = _statement_and_params(session.execute)
    assert "app.current_organization_id" in statement
    assert "app.current_tenant" in statement
    assert params == {
        "organization_id": str(organization_id),
        "tenant_id": str(organization_id),
    }


def test_connection_rearming_sets_both_scopes(real_rls: ModuleType) -> None:
    connection = MagicMock()
    connection.dialect.name = "postgresql"
    organization_id = uuid4()

    real_rls.set_current_organization_on_connection(connection, organization_id)

    statement, params = _statement_and_params(connection.execute)
    assert "app.current_organization_id" in statement
    assert "app.current_tenant" in statement
    assert params == {
        "organization_id": str(organization_id),
        "tenant_id": str(organization_id),
    }
