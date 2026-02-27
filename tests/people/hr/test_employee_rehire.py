from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.people.hr.employee import EmployeeStatus
from app.services.people.hr.employees import EmployeeService
from app.services.people.hr.errors import EmployeeStatusError, ValidationError


def _employee(status: EmployeeStatus, *, leaving: date | None = None):
    return SimpleNamespace(
        employee_id=uuid4(),
        organization_id=uuid4(),
        status=status,
        date_of_joining=date(2020, 1, 1),
        date_of_leaving=leaving,
        department_id=None,
        designation_id=None,
        updated_at=None,
        updated_by_id=None,
    )


def test_rehire_employee_sets_active_and_dates(monkeypatch):
    db = SimpleNamespace()
    svc = EmployeeService(db, uuid4())
    emp = _employee(EmployeeStatus.RESIGNED, leaving=date(2026, 1, 31))
    svc.get_employee = lambda _employee_id: emp

    monkeypatch.setattr(
        "app.services.people.hr.employees.fire_audit_event",
        lambda **_kwargs: None,
    )

    class _LifecycleService:
        def __init__(self, _db):
            pass

        def create_onboarding(self, _org_id, **_kwargs):
            return SimpleNamespace(status=None)

    monkeypatch.setattr(
        "app.services.people.hr.lifecycle.LifecycleService",
        _LifecycleService,
    )

    result = svc.rehire_employee(emp.employee_id, date(2026, 2, 1), notes="rehired")

    assert result.status == EmployeeStatus.ACTIVE
    assert result.date_of_joining == date(2026, 2, 1)
    assert result.date_of_leaving is None


def test_rehire_employee_rejects_non_separated_status():
    db = SimpleNamespace()
    svc = EmployeeService(db, uuid4())
    emp = _employee(EmployeeStatus.ACTIVE)
    svc.get_employee = lambda _employee_id: emp

    with pytest.raises(EmployeeStatusError):
        svc.rehire_employee(emp.employee_id, date(2026, 2, 1))


def test_rehire_employee_rejects_date_before_leaving():
    db = SimpleNamespace()
    svc = EmployeeService(db, uuid4())
    emp = _employee(EmployeeStatus.TERMINATED, leaving=date(2026, 2, 10))
    svc.get_employee = lambda _employee_id: emp

    with pytest.raises(ValidationError, match="Rehire date cannot be before"):
        svc.rehire_employee(emp.employee_id, date(2026, 2, 1))
