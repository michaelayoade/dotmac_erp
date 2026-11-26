from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.background import BackgroundTasks
from starlette.requests import Request

from app.api.people import hr as hr_api
from app.api.people.hr import create_employee
from app.schemas.people.hr import EmployeeCreate, EmployeeUpdate
from app.services.people.hr import EmployeeAlreadyExistsError


def test_create_employee_maps_duplicate_person_to_conflict(monkeypatch) -> None:
    person_id = uuid4()
    organization_id = uuid4()

    class _EmployeeService:
        def __init__(self, db, organization_id):
            assert db is object_db
            assert organization_id == organization_id_expected

        def create_employee(self, person_id, data):
            raise EmployeeAlreadyExistsError(
                str(person_id), "Person already has an employee record"
            )

    object_db = object()
    organization_id_expected = organization_id
    monkeypatch.setattr("app.api.people.hr.EmployeeService", _EmployeeService)

    payload = EmployeeCreate(date_of_joining=date.today(), person_id=person_id)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/people/hr/employees",
            "headers": [],
            "query_string": b"",
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        create_employee(
            payload,
            request,
            BackgroundTasks(),
            organization_id,
            object_db,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Person already has an employee record"


@pytest.mark.parametrize(
    ("payload_fields", "expected_fields"),
    [({"designation_id": None}, {"designation_id"}), ({}, set())],
)
def test_update_employee_passes_explicit_fields_to_service(
    monkeypatch, payload_fields, expected_fields
) -> None:
    employee_id = uuid4()
    organization_id = uuid4()
    captured = {}

    class _EmployeeService:
        def __init__(self, db, org_id):
            assert db is object_db
            assert org_id == organization_id

        def update_employee(self, updated_id, data):
            captured["employee_id"] = updated_id
            captured["data"] = data
            return object_employee

    object_db = object()
    object_employee = object()
    monkeypatch.setattr(hr_api, "EmployeeService", _EmployeeService)
    monkeypatch.setattr(
        hr_api.EmployeeRead,
        "model_validate",
        classmethod(lambda cls, employee: employee),
    )

    result = hr_api.update_employee(
        employee_id,
        EmployeeUpdate(**payload_fields),
        auth={},
        organization_id=organization_id,
        db=object_db,
    )

    assert result is object_employee
    assert captured["employee_id"] == employee_id
    assert captured["data"].provided_fields == expected_fields
