"""P5: cancelled/archived CRM entities must not appear in expense-claim pickers.

A canceled CRM project reaches ERP via the recently-updated delta (canceling
keeps is_active=True), which marks the CRMSyncMapping CANCELLED — but the pickers
weren't filtering it, so a dead project stayed selectable for new expense claims.

The CRMSyncMapping table lives in the ``sync`` schema, which the SQLite test
harness doesn't provision, so (like the other sync-service tests) these assert on
the generated query rather than round-tripping real rows.
"""

from __future__ import annotations

import uuid

import pytest

from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService


@pytest.fixture
def mock_db():
    from unittest.mock import MagicMock

    return MagicMock()


@pytest.fixture
def service(mock_db):
    return DotMacCRMSyncService(mock_db)


@pytest.fixture
def org_id():
    return uuid.uuid4()


def _executed_sql(mock_db) -> str:
    mock_db.scalars.return_value.all.return_value = []
    return str(mock_db.scalars.call_args[0][0]).lower()


def test_list_projects_excludes_cancelled_by_default(service, org_id, mock_db):
    mock_db.scalars.return_value.all.return_value = []
    service.list_projects(org_id)
    assert "crm_status not in" in _executed_sql(mock_db)


def test_list_projects_explicit_status_bypasses_default_exclusion(
    service, org_id, mock_db
):
    mock_db.scalars.return_value.all.return_value = []
    service.list_projects(org_id, status="cancelled")
    sql = _executed_sql(mock_db)
    # An explicit status uses equality, not the hide-cancelled default.
    assert "not in" not in sql
    assert "crm_status" in sql


def test_list_tickets_excludes_cancelled(service, org_id, mock_db):
    mock_db.scalars.return_value.all.return_value = []
    service.list_tickets(org_id)
    assert "crm_status not in" in _executed_sql(mock_db)


def test_list_work_orders_excludes_cancelled(service, org_id, mock_db):
    mock_db.scalars.return_value.all.return_value = []
    service.list_work_orders(org_id)
    assert "crm_status not in" in _executed_sql(mock_db)
