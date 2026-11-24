from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

from app.services.people.hr.provisioning_monitor import (
    STAGES,
    list_provisioning_employees,
    provisioning_status,
    record_stage,
    stage_rows,
)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


def test_record_stage_tracks_attempt_and_failure() -> None:
    employee = SimpleNamespace(
        workforce_provisioning_state={}, workforce_provisioning_status=None
    )

    record_stage(employee, "nextcloud", "running")
    record_stage(employee, "nextcloud", "failed", error="OCS request failed")

    stage = employee.workforce_provisioning_state["nextcloud"]
    assert stage["attempts"] == 1
    assert stage["error"] == "OCS request failed"
    assert employee.workforce_provisioning_status == "failed"


def test_stage_rows_returns_all_relay_stages() -> None:
    employee = SimpleNamespace(workforce_provisioning_state={})

    rows = stage_rows(employee)

    assert [row["key"] for row in rows] == [
        "mailcow",
        "activation_email",
        "nextcloud",
        "selfcare",
        "talk",
    ]
    assert {row["status"] for row in rows} == {"not_started"}


def test_partial_completed_stages_do_not_complete_provisioning() -> None:
    employee = SimpleNamespace(
        workforce_provisioning_state={}, workforce_provisioning_status=None
    )

    record_stage(employee, "mailcow", "completed")

    assert employee.workforce_provisioning_status == "pending"
    assert provisioning_status(employee) == "pending"
    assert [row["status"] for row in stage_rows(employee)] == [
        "completed",
        "not_started",
        "not_started",
        "not_started",
        "not_started",
    ]

    for stage in STAGES[1:]:
        record_stage(employee, stage, "skipped")

    assert employee.workforce_provisioning_status == "completed"


def test_status_filter_uses_current_stage_state_for_existing_employees() -> None:
    partial = SimpleNamespace(
        workforce_provisioning_state={"mailcow": {"status": "completed"}},
        workforce_provisioning_status="completed",
    )
    complete = SimpleNamespace(
        workforce_provisioning_state={
            stage: {"status": "completed"} for stage in STAGES
        },
        workforce_provisioning_status="completed",
    )
    db = MagicMock()
    result = db.execute.return_value
    result.__iter__.return_value = iter(
        [(partial, SimpleNamespace()), (complete, SimpleNamespace())]
    )

    rows = list_provisioning_employees(db, ORG_ID, status="completed", limit=1)

    assert [row["employee"] for row in rows] == [complete]
    result.close.assert_called_once()
