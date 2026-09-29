from datetime import date
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.background import BackgroundTasks
from starlette.requests import Request

from app.api.people.hr import create_employee
from app.schemas.people.hr import EmployeeCreate
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
