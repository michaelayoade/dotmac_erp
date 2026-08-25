from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from app.api.sync import dotmac_sub
from app.models.pm.task import TaskStatus
from app.schemas.sync.dotmac_sub import BulkSyncRequest, SubProjectTaskPayload
from app.services.sync.sub_mappings import TASK_STATUS_MAP


def test_bulk_contract_accepts_project_tasks_without_ticket_fields() -> None:
    assert BulkSyncRequest().project_tasks == []
    payload = BulkSyncRequest(
        project_tasks=[
            SubProjectTaskPayload(
                source_id="task-1",
                project_source_id="project-1",
                title="Survey route",
                status="done",
                effort_hours=Decimal("2.5"),
            )
        ]
    )

    assert payload.project_tasks[0].project_source_id == "project-1"
    assert "ticket_source_id" not in SubProjectTaskPayload.model_fields
    assert TASK_STATUS_MAP[payload.project_tasks[0].status] == TaskStatus.COMPLETED


def test_sub_project_task_statuses_map_to_erp_lifecycle() -> None:
    assert TASK_STATUS_MAP["backlog"] == TaskStatus.OPEN
    assert TASK_STATUS_MAP["todo"] == TaskStatus.OPEN
    assert TASK_STATUS_MAP["blocked"] == TaskStatus.ON_HOLD
    assert TASK_STATUS_MAP["done"] == TaskStatus.COMPLETED


def test_bulk_route_delegates_the_complete_contract_to_its_owner(monkeypatch) -> None:
    calls: list[tuple] = []

    class Service:
        def __init__(self, _db) -> None:
            pass

        def bulk_sync(self, organization_id, payload):
            calls.append((organization_id, payload))
            return dotmac_sub.BulkSyncResponse(
                projects_synced=1,
                project_tasks_synced=1,
                work_orders_synced=1,
            )

    monkeypatch.setattr(dotmac_sub, "DotmacSubSyncService", Service)
    db = MagicMock()
    organization_id = uuid4()
    payload = BulkSyncRequest.model_validate(
        {
            "projects": [{"source_id": "p1", "name": "Build"}],
            "project_tasks": [
                {
                    "source_id": "pt1",
                    "project_source_id": "p1",
                    "title": "Survey",
                }
            ],
            "work_orders": [{"source_id": "wo1", "title": "Visit"}],
        }
    )

    result = dotmac_sub.sync_sub_operational_domains(
        payload,
        auth={"organization_id": organization_id},
        db=db,
    )

    assert calls == [(organization_id, payload)]
    assert result.project_tasks_synced == 1
    assert result.errors == []
    db.assert_not_called()
