"""Regression tests for extended-profile request persistence."""

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.models.people.hr.employee import EmployeeStatus
from app.models.people.hr.info_change_request import (
    InfoChangeOperation,
    InfoChangeType,
)
from app.services.people.hr.info_change_service import (
    InfoChangeService,
    _json_safe_value,
)
from app.services.people.hr import info_change_service


class ExampleValue(str, Enum):
    VALUE = "VALUE"


def test_json_safe_value_converts_nested_workflow_types() -> None:
    identifier = uuid4()
    submitted_at = datetime(2026, 7, 29, 12, 30, tzinfo=timezone.utc)

    result = _json_safe_value(
        {
            "qualification_type": ExampleValue.VALUE,
            "start_date": date(2020, 9, 1),
            "submitted_at": submitted_at,
            "score": Decimal("4.50"),
            "document_id": identifier,
            "nested": [date(2024, 6, 30), (ExampleValue.VALUE,)],
        }
    )

    assert result == {
        "qualification_type": "VALUE",
        "start_date": "2020-09-01",
        "submitted_at": "2026-07-29T12:30:00+00:00",
        "score": "4.50",
        "document_id": str(identifier),
        "nested": ["2024-06-30", ["VALUE"]],
    }


def test_append_batch_request_stores_json_safe_snapshots(monkeypatch) -> None:
    class RequestStub:
        def __init__(self, **values) -> None:
            self.__dict__.update(values)

    monkeypatch.setattr(
        info_change_service,
        "EmployeeInfoChangeRequest",
        RequestStub,
    )
    db = MagicMock()
    service = InfoChangeService(db)
    batch = SimpleNamespace(
        organization_id=uuid4(),
        expires_at=datetime(2026, 8, 28, tzinfo=timezone.utc),
    )

    request = service._append_batch_request(
        batch=batch,
        employee_id=uuid4(),
        change_type=InfoChangeType.QUALIFICATION,
        operation=InfoChangeOperation.UPDATE,
        proposed_changes={
            "qualification_type": ExampleValue.VALUE,
            "start_date": date(2020, 9, 1),
        },
        previous_values={"end_date": date(2024, 6, 30)},
        requester_notes=None,
        target_record_id=uuid4(),
        batch_item_order=1,
        pending_evidence=None,
    )

    assert request.proposed_changes == {
        "qualification_type": "VALUE",
        "start_date": "2020-09-01",
    }
    assert request.previous_values == {"end_date": "2024-06-30"}
    db.add.assert_called_once_with(request)
    db.flush.assert_called_once_with()


def test_terminated_employee_gets_accurate_submission_error() -> None:
    employee = SimpleNamespace(status=EmployeeStatus.TERMINATED)
    db = MagicMock()
    db.scalar.return_value = employee
    service = InfoChangeService(db)

    with pytest.raises(
        ValueError,
        match="Terminated employees cannot submit profile change requests",
    ):
        service._get_employee_or_raise(uuid4(), uuid4())


def test_missing_employee_still_reports_not_found() -> None:
    db = MagicMock()
    db.scalar.return_value = None
    service = InfoChangeService(db)
    employee_id = uuid4()

    with pytest.raises(ValueError, match=f"Employee {employee_id} not found"):
        service._get_employee_or_raise(uuid4(), employee_id)


def test_non_terminated_employee_is_eligible() -> None:
    employee_id = UUID("22222222-2222-2222-2222-222222222222")
    employee = SimpleNamespace(
        employee_id=employee_id,
        status=EmployeeStatus.ACTIVE,
    )
    db = MagicMock()
    db.scalar.return_value = employee

    assert (
        InfoChangeService(db)._get_employee_or_raise(uuid4(), employee_id) is employee
    )
