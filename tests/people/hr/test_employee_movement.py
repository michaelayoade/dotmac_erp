from __future__ import annotations

import uuid
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.person import Person
from app.services.people.hr.employees import EmployeeService
from app.services.people.hr.web.employee_web import HRWebService


def _ensure_employee_table(engine) -> None:
    for column in Employee.__table__.columns:
        default = column.server_default
        if default is None:
            continue
        default_text = str(getattr(default, "arg", default)).lower()
        if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
            column.server_default = None
    Employee.__table__.create(engine, checkfirst=True)


def _add_employee(
    db_session,
    *,
    organization_id: uuid.UUID,
    joined: date,
    left: date | None = None,
    status: EmployeeStatus = EmployeeStatus.ACTIVE,
) -> Employee:
    person = Person(
        id=uuid.uuid4(),
        organization_id=organization_id,
        first_name="Movement",
        last_name=uuid.uuid4().hex[:8],
        email=f"{uuid.uuid4().hex}@example.com",
    )
    employee = Employee(
        employee_id=uuid.uuid4(),
        organization_id=organization_id,
        person_id=person.id,
        employee_code=f"EMP-{uuid.uuid4().hex[:8]}",
        date_of_joining=joined,
        date_of_leaving=left,
        status=status,
    )
    db_session.add_all([person, employee])
    db_session.flush()
    return employee


def test_employee_movement_counts_join_and_exit_in_their_actual_months(db_session):
    _ensure_employee_table(db_session.bind)
    org_id = uuid.uuid4()
    _add_employee(
        db_session,
        organization_id=org_id,
        joined=date(2026, 1, 10),
        left=date(2026, 8, 20),
        status=EmployeeStatus.RESIGNED,
    )
    _add_employee(
        db_session,
        organization_id=org_id,
        joined=date(2025, 11, 2),
        left=date(2026, 3, 5),
        status=EmployeeStatus.TERMINATED,
    )
    _add_employee(
        db_session,
        organization_id=org_id,
        joined=date(2026, 1, 31),
        left=None,
    )

    movement = EmployeeService(db_session, org_id).get_employee_movement(
        date(2026, 1, 1),
        date(2026, 8, 31),
    )
    by_month = {row["month_start"]: row for row in movement}

    assert by_month[date(2026, 1, 1)]["onboarded"] == 2
    assert by_month[date(2026, 3, 1)]["offboarded"] == 1
    assert by_month[date(2026, 8, 1)]["offboarded"] == 1
    assert by_month[date(2026, 2, 1)]["onboarded"] == 0
    assert by_month[date(2026, 2, 1)]["offboarded"] == 0


def test_employee_movement_keeps_years_distinct_and_sorted(db_session):
    _ensure_employee_table(db_session.bind)
    org_id = uuid.uuid4()
    _add_employee(
        db_session,
        organization_id=org_id,
        joined=date(2025, 1, 15),
        left=date(2026, 1, 15),
        status=EmployeeStatus.RESIGNED,
    )
    _add_employee(
        db_session,
        organization_id=org_id,
        joined=date(2026, 1, 15),
    )

    movement = EmployeeService(db_session, org_id).get_employee_movement(
        date(2025, 1, 1),
        date(2026, 1, 31),
    )

    assert [row["month_start"] for row in movement] == sorted(
        row["month_start"] for row in movement
    )
    assert movement[0] == {
        "month_start": date(2025, 1, 1),
        "month_label": "Jan 2025",
        "onboarded": 1,
        "offboarded": 0,
    }
    assert movement[-1] == {
        "month_start": date(2026, 1, 1),
        "month_label": "Jan 2026",
        "onboarded": 1,
        "offboarded": 1,
    }
    assert len(movement) == 13


def test_employee_movement_is_tenant_scoped(db_session):
    _ensure_employee_table(db_session.bind)
    org_id = uuid.uuid4()
    other_org_id = uuid.uuid4()
    _add_employee(
        db_session,
        organization_id=org_id,
        joined=date(2026, 4, 1),
    )
    _add_employee(
        db_session,
        organization_id=other_org_id,
        joined=date(2026, 4, 2),
        left=date(2026, 4, 20),
        status=EmployeeStatus.RESIGNED,
    )

    movement = EmployeeService(db_session, org_id).get_employee_movement(
        date(2026, 4, 1),
        date(2026, 4, 30),
    )

    assert movement[0]["onboarded"] == 1
    assert movement[0]["offboarded"] == 0


def test_employee_movement_ignores_a_missing_leaving_date(db_session):
    _ensure_employee_table(db_session.bind)
    org_id = uuid.uuid4()
    _add_employee(
        db_session,
        organization_id=org_id,
        joined=date(2026, 5, 12),
        left=None,
    )

    movement = EmployeeService(db_session, org_id).get_employee_movement(
        date(2026, 5, 1),
        date(2026, 5, 31),
    )

    assert movement[0]["onboarded"] == 1
    assert movement[0]["offboarded"] == 0


def test_employee_movement_rejects_an_invalid_range(db_session):
    service = EmployeeService(db_session, uuid.uuid4())

    with pytest.raises(ValueError, match="end_date must be on or after start_date"):
        service.get_employee_movement(date(2026, 2, 1), date(2026, 1, 31))


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("last_6_months", (date(2026, 4, 1), date(2026, 9, 8))),
        ("last_12_months", (date(2025, 10, 1), date(2026, 9, 8))),
        ("this_year", (date(2026, 1, 1), date(2026, 9, 8))),
        ("previous_year", (date(2025, 1, 1), date(2025, 12, 31))),
    ],
)
def test_employee_movement_periods_use_calendar_months(period, expected):
    assert (
        HRWebService._employee_movement_period_bounds(
            period,
            date(2026, 9, 8),
        )
        == expected
    )


def test_employee_movement_period_rejects_unknown_value():
    with pytest.raises(ValueError, match="Unsupported employee movement period"):
        HRWebService._employee_movement_period_bounds("all_time", date(2026, 9, 8))


def test_employee_movement_chart_response_rejects_unknown_period(db_session):
    with pytest.raises(HTTPException) as exc_info:
        HRWebService().employee_movement_chart_response(
            request=SimpleNamespace(),
            auth=SimpleNamespace(organization_id=uuid.uuid4()),
            db=db_session,
            period="all_time",
        )

    assert exc_info.value.status_code == 400


def test_employee_movement_chart_response_uses_org_local_period(
    db_session,
    monkeypatch,
):
    org_id = uuid.uuid4()
    movement = [
        {
            "month_start": date(2026, 9, 1),
            "month_label": "Sep 2026",
            "onboarded": 2,
            "offboarded": 1,
        }
    ]
    captured = {}

    monkeypatch.setattr(
        "app.services.people.hr.web.employee_web.AttendanceService.get_org_today",
        lambda self, organization_id: date(2026, 9, 8),
    )

    def _movement(self, start_date, end_date):
        captured["organization_id"] = self.organization_id
        captured["dates"] = (start_date, end_date)
        return movement

    monkeypatch.setattr(
        "app.services.people.hr.web.employee_web.EmployeeService.get_employee_movement",
        _movement,
    )
    monkeypatch.setattr(
        "app.services.people.hr.web.employee_web.templates.TemplateResponse",
        lambda request, template_name, context: SimpleNamespace(
            status_code=200,
            template_name=template_name,
            context=context,
        ),
    )

    response = HRWebService().employee_movement_chart_response(
        request=SimpleNamespace(),
        auth=SimpleNamespace(organization_id=org_id),
        db=db_session,
        period="last_6_months",
    )

    assert response.status_code == 200
    assert response.template_name == "people/hr/_employee_movement_chart.html"
    assert captured == {
        "organization_id": org_id,
        "dates": (date(2026, 4, 1), date(2026, 9, 8)),
    }
    assert response.context["movement"] == movement
    assert response.context["has_activity"] is True
