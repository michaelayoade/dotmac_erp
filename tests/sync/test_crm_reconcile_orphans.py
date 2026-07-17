"""P5: erp bulk-sync orphan reconcile.

CRM's ``sync_all_active`` push is upsert-only and silently drops canceled /
soft-deleted entities, so their ERP copies lived forever. After a clean FULL
run CRM now POSTs the complete seen-id set per entity type to
``/sync/crm/reconcile-orphans``; ACTIVE mappings not in the set are soft-closed
behind the reference safety rails (min-fetch ratio, max-terminate ratio+floor
mirrored from dotmac_crm selfcare ``_reconcile_selfcare_orphans``).
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.sync.dotmac_crm import require_any_service_scope
from app.models.finance.core_org.project import ProjectStatus
from app.models.pm.task import TaskStatus
from app.models.support.ticket import TicketStatus
from app.models.sync.dotmac_crm_sync import CRMSyncStatus
from app.services.sync.crm.projects import (
    _ORPHAN_MAX_TERMINATE_FLOOR,
    _ORPHAN_MAX_TERMINATE_RATIO,
    _ORPHAN_MIN_FETCH_RATIO,
)
from app.services.sync.dotmac_crm_sync_service import DotMacCRMSyncService


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def service(mock_db):
    return DotMacCRMSyncService(mock_db)


@pytest.fixture
def org_id():
    return uuid.UUID("00000000-0000-0000-0000-000000000001")


def _mapping(crm_id: str, local_entity_type: str = "project"):
    m = MagicMock()
    m.crm_id = crm_id
    m.local_entity_type = local_entity_type
    m.local_entity_id = uuid.uuid4()
    m.crm_status = CRMSyncStatus.ACTIVE
    return m


def _prime(mock_db, mappings):
    mock_db.scalars.return_value.all.return_value = mappings


class TestScopeGate:
    """Mirrors tests/sync/test_service_auth_scopes.py for the any-of gate."""

    def test_unscoped_key_grandfathered(self):
        dep = require_any_service_scope("crm:sync:write", "crm:write")
        auth = {"scopes": []}
        assert dep(auth=auth) is auth

    def test_crm_sync_write_allowed(self):
        dep = require_any_service_scope("crm:sync:write", "crm:write")
        auth = {"scopes": ["crm:sync:write"]}
        assert dep(auth=auth) is auth

    def test_crm_write_allowed(self):
        dep = require_any_service_scope("crm:sync:write", "crm:write")
        auth = {"scopes": ["crm:write", "crm:ncc:read"]}
        assert dep(auth=auth) is auth

    def test_missing_scope_rejected(self):
        dep = require_any_service_scope("crm:sync:write", "crm:write")
        auth = {"scopes": ["crm:inventory:read"]}
        with pytest.raises(HTTPException) as exc:
            dep(auth=auth)
        assert exc.value.status_code == 403
        assert "crm:sync:write" in exc.value.detail


class TestRailConstants:
    def test_constants_match_reference(self):
        """Guard constants must stay verbatim with the selfcare reference."""
        assert _ORPHAN_MIN_FETCH_RATIO == 0.5
        assert _ORPHAN_MAX_TERMINATE_RATIO == 0.2
        assert _ORPHAN_MAX_TERMINATE_FLOOR == 3


class TestReconcileRails:
    def test_small_fetch_skipped(self, service, org_id, mock_db):
        """seen < examined * 0.5 -> skip with a logged reason, close nothing."""
        mappings = [_mapping(f"crm-{i}") for i in range(10)]
        _prime(mock_db, mappings)

        result = service.reconcile_orphans(
            org_id,
            entity_type="project",
            seen_crm_ids=["crm-0", "crm-1", "crm-2", "crm-3"],  # 4 < 10*0.5
            active_count=4,
        )

        assert result["skipped_reason"] == "small_fetch"
        assert result["examined"] == 10
        assert result["orphaned"] == 6
        assert result["closed"] == 0
        mock_db.begin_nested.assert_not_called()

    def test_too_many_orphans_skipped(self, service, org_id, mock_db):
        """orphans > max(3, int(examined*0.2)) -> skip, close nothing."""
        mappings = [_mapping(f"crm-{i}") for i in range(10)]
        _prime(mock_db, mappings)
        seen = [f"crm-{i}" for i in range(4, 10)]  # 6 seen (>= 5), 4 orphans > 3

        result = service.reconcile_orphans(
            org_id, entity_type="project", seen_crm_ids=seen, active_count=6
        )

        assert result["skipped_reason"] == "too_many"
        assert result["orphaned"] == 4
        assert result["closed"] == 0
        mock_db.begin_nested.assert_not_called()

    def test_no_orphans_noop(self, service, org_id, mock_db):
        mappings = [_mapping(f"crm-{i}") for i in range(3)]
        _prime(mock_db, mappings)

        result = service.reconcile_orphans(
            org_id,
            entity_type="project",
            seen_crm_ids=[f"crm-{i}" for i in range(3)],
            active_count=3,
        )

        assert result["skipped_reason"] is None
        assert result["orphaned"] == 0
        assert result["closed"] == 0

    def test_unknown_entity_type_raises(self, service, org_id):
        with pytest.raises(ValueError):
            service.reconcile_orphans(
                org_id, entity_type="invoice", seen_crm_ids=[], active_count=0
            )


class TestReconcileSoftClose:
    def test_happy_path_project_soft_closed(self, service, org_id, mock_db):
        """Orphaned project mapping -> local Project CANCELLED, mapping CANCELLED."""
        mappings = [_mapping(f"crm-{i}") for i in range(10)]
        _prime(mock_db, mappings)
        seen = [f"crm-{i}" for i in range(2, 10)]  # crm-0, crm-1 orphaned

        local_project = MagicMock()
        mock_db.get.return_value = local_project

        result = service.reconcile_orphans(
            org_id, entity_type="project", seen_crm_ids=seen, active_count=8
        )

        assert result["skipped_reason"] is None
        assert result["orphaned"] == 2
        assert result["closed"] == 2
        assert result["errors"] == []
        assert local_project.status == ProjectStatus.CANCELLED
        assert mappings[0].crm_status == CRMSyncStatus.CANCELLED
        assert mappings[1].crm_status == CRMSyncStatus.CANCELLED
        # Seen mappings untouched
        assert mappings[2].crm_status == CRMSyncStatus.ACTIVE
        # Per-row savepoints committed
        assert mock_db.begin_nested.call_count == 2

    def test_ticket_closes_with_closed_status(self, service, org_id, mock_db):
        mappings = [_mapping(f"crm-{i}", "ticket") for i in range(4)]
        _prime(mock_db, mappings)
        local_ticket = MagicMock()
        mock_db.get.return_value = local_ticket

        result = service.reconcile_orphans(
            org_id,
            entity_type="ticket",
            seen_crm_ids=["crm-1", "crm-2", "crm-3"],
            active_count=3,
        )

        assert result["closed"] == 1
        assert local_ticket.status == TicketStatus.CLOSED

    def test_work_order_task_cancelled(self, service, org_id, mock_db):
        mappings = [_mapping(f"crm-{i}", "task") for i in range(4)]
        _prime(mock_db, mappings)
        local_task = MagicMock()
        mock_db.get.return_value = local_task

        result = service.reconcile_orphans(
            org_id,
            entity_type="work_order",
            seen_crm_ids=["crm-1", "crm-2", "crm-3"],
            active_count=3,
        )

        assert result["closed"] == 1
        assert local_task.status == TaskStatus.CANCELLED

    def test_missing_local_entity_still_closes_mapping(self, service, org_id, mock_db):
        mappings = [_mapping(f"crm-{i}") for i in range(4)]
        _prime(mock_db, mappings)
        mock_db.get.return_value = None  # local Project row is gone

        result = service.reconcile_orphans(
            org_id,
            entity_type="project",
            seen_crm_ids=["crm-1", "crm-2", "crm-3"],
            active_count=3,
        )

        assert result["closed"] == 1
        assert mappings[0].crm_status == CRMSyncStatus.CANCELLED

    def test_per_row_error_rolls_back_and_continues(self, service, org_id, mock_db):
        """One failing row is rolled back to its savepoint; the rest close."""
        mappings = [_mapping(f"crm-{i}") for i in range(10)]
        _prime(mock_db, mappings)
        seen = [f"crm-{i}" for i in range(2, 10)]  # crm-0, crm-1 orphaned

        good_project = MagicMock()
        mock_db.get.side_effect = [RuntimeError("db exploded"), good_project]
        savepoints = [MagicMock(), MagicMock()]
        mock_db.begin_nested.side_effect = savepoints

        result = service.reconcile_orphans(
            org_id, entity_type="project", seen_crm_ids=seen, active_count=8
        )

        assert result["closed"] == 1
        assert len(result["errors"]) == 1
        assert "crm-0" in result["errors"][0]
        savepoints[0].rollback.assert_called_once()
        savepoints[0].commit.assert_not_called()
        savepoints[1].commit.assert_called_once()
        assert good_project.status == ProjectStatus.CANCELLED
        assert mappings[1].crm_status == CRMSyncStatus.CANCELLED
