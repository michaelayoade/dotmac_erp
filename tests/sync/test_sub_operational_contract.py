from decimal import Decimal
from unittest.mock import MagicMock

from app.api.sync import dotmac_sub
from app.models.pm.task import TaskStatus
from app.schemas.sync.sub_operational import BulkSyncRequest, SubProjectTaskPayload
from app.services.sync.sub_mappings import TASK_STATUS_MAP


def test_bulk_contract_accepts_project_tasks_without_breaking_older_payloads() -> None:
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
    assert TASK_STATUS_MAP[payload.project_tasks[0].status] == TaskStatus.COMPLETED


def test_sub_project_task_statuses_map_to_erp_lifecycle() -> None:
    assert TASK_STATUS_MAP["backlog"] == TaskStatus.OPEN
    assert TASK_STATUS_MAP["todo"] == TaskStatus.OPEN
    assert TASK_STATUS_MAP["blocked"] == TaskStatus.ON_HOLD
    assert TASK_STATUS_MAP["done"] == TaskStatus.COMPLETED


def test_bulk_sync_processes_dependencies_before_project_tasks(monkeypatch) -> None:
    calls: list[str] = []

    class Service:
        def __init__(self, _db) -> None:
            pass

        def sync_project(self, _org_id, _payload) -> None:
            calls.append("project")

        def sync_ticket(self, _org_id, _payload, item_errors) -> None:
            calls.append("ticket")

        def sync_project_task(self, _org_id, _payload) -> None:
            calls.append("project_task")

        def sync_work_order(self, _org_id, _payload) -> None:
            calls.append("work_order")

    monkeypatch.setattr(dotmac_sub, "DotMacSubSyncService", Service)
    db = MagicMock()
    db.begin_nested.return_value = MagicMock()
    payload = dotmac_sub.BulkSyncRequest.model_validate(
        {
            "projects": [{"source_id": "p1", "name": "Build"}],
            "tickets": [{"source_id": "t1", "subject": "Install"}],
            "project_tasks": [
                {
                    "source_id": "pt1",
                    "project_source_id": "p1",
                    "ticket_source_id": "t1",
                    "title": "Survey",
                }
            ],
            "work_orders": [{"source_id": "wo1", "title": "Visit"}],
        }
    )

    result = dotmac_sub.sync_sub_operational_domains(
        payload,
        auth={"organization_id": "00000000-0000-0000-0000-000000000001"},
        db=db,
    )

    assert calls == ["project", "ticket", "project_task", "work_order"]
    assert result.project_tasks_synced == 1
    assert result.errors == []
