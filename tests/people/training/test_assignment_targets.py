"""Group-targeted training assignment: designation and support team.

Exercises the employee-expansion + validation added to ``AssignmentService``.
The pre-existing per-employee path (progress/audit/notification side effects) is
stubbed via ``assign_employees`` so these tests isolate *which* employees each
group resolves to, the assignment-source labelling, and org scoping.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models.people.hr import Department, Designation, Employee, EmployeeStatus
from app.models.person import Person
from app.models.support.team import SupportTeam, SupportTeamMember
from app.services.common import NotFoundError
from app.services.people.training.learning_assessment import AssignmentService


def _ensure_tables(engine) -> None:
    for model in (Department, Designation, Employee, SupportTeam, SupportTeamMember):
        table = model.__table__
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
        table.create(engine, checkfirst=True)


def _make_designation(db, org_id, name, *, is_active=True) -> Designation:
    d = Designation(
        designation_id=uuid.uuid4(),
        organization_id=org_id,
        designation_code=f"D-{uuid.uuid4().hex[:6]}",
        designation_name=name,
        is_active=is_active,
    )
    db.add(d)
    db.flush()
    return d


def _make_employee(db, org_id, code, *, designation_id=None) -> Employee:
    person = Person(
        id=uuid.uuid4(),
        organization_id=org_id,
        first_name=code,
        last_name="Employee",
        email=f"{uuid.uuid4().hex}@example.com",
    )
    employee = Employee(
        employee_id=uuid.uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=code,
        date_of_joining=date(2026, 1, 1),
        status=EmployeeStatus.ACTIVE,
        designation_id=designation_id,
    )
    db.add_all([person, employee])
    db.flush()
    return employee


def _make_team(db, org_id, *, members=()) -> SupportTeam:
    team = SupportTeam(
        team_id=uuid.uuid4(),
        organization_id=org_id,
        team_code=f"T-{uuid.uuid4().hex[:6]}",
        team_name="NOC Team",
    )
    db.add(team)
    db.flush()
    for emp in members:
        db.add(
            SupportTeamMember(
                member_id=uuid.uuid4(),
                team_id=team.team_id,
                employee_id=emp.employee_id,
            )
        )
    db.flush()
    return team


@pytest.fixture()
def svc(db_session, engine):
    _ensure_tables(engine)
    service = AssignmentService(db_session)
    calls: list[dict] = []

    def _record(org_id, course_id, employee_ids, **kwargs):
        calls.append(
            {
                "org_id": org_id,
                "course_id": course_id,
                "employee_ids": list(employee_ids),
                **kwargs,
            }
        )
        return []

    service.assign_employees = _record  # type: ignore[method-assign]
    service._calls = calls  # type: ignore[attr-defined]
    return service


# --- designation ---------------------------------------------------------


def test_assign_designation_targets_only_that_title(svc, db_session):
    org = uuid.uuid4()
    course = uuid.uuid4()
    field_tech = _make_designation(db_session, org, "Field Technician")
    other = _make_designation(db_session, org, "Accountant")
    a = _make_employee(db_session, org, "A", designation_id=field_tech.designation_id)
    b = _make_employee(db_session, org, "B", designation_id=field_tech.designation_id)
    _make_employee(db_session, org, "C", designation_id=other.designation_id)

    svc.assign_designation(org, course, field_tech.designation_id)

    assert len(svc._calls) == 1
    call = svc._calls[0]
    assert set(call["employee_ids"]) == {a.employee_id, b.employee_id}
    assert call["assignment_source"] == "designation"
    assert call["assignment_source_id"] == field_tech.designation_id


def test_assign_designation_is_org_scoped(svc, db_session):
    org, other_org = uuid.uuid4(), uuid.uuid4()
    course = uuid.uuid4()
    d_here = _make_designation(db_session, org, "Field Technician")
    # Same title, different org — must not be swept in.
    d_there = _make_designation(db_session, other_org, "Field Technician")
    mine = _make_employee(db_session, org, "A", designation_id=d_here.designation_id)
    _make_employee(db_session, other_org, "X", designation_id=d_there.designation_id)

    svc.assign_designation(org, course, d_here.designation_id)

    assert set(svc._calls[0]["employee_ids"]) == {mine.employee_id}


def test_assign_designation_unknown_raises(svc):
    with pytest.raises(NotFoundError):
        svc.assign_designation(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert svc._calls == []


def test_assign_designation_inactive_raises(svc, db_session):
    org = uuid.uuid4()
    retired = _make_designation(db_session, org, "Legacy Role", is_active=False)
    with pytest.raises(NotFoundError):
        svc.assign_designation(org, uuid.uuid4(), retired.designation_id)


# --- team ----------------------------------------------------------------


def test_assign_team_targets_all_members(svc, db_session):
    org = uuid.uuid4()
    course = uuid.uuid4()
    m1 = _make_employee(db_session, org, "M1")
    m2 = _make_employee(db_session, org, "M2")
    _make_employee(db_session, org, "NOT_A_MEMBER")
    team = _make_team(db_session, org, members=[m1, m2])

    svc.assign_team(org, course, team.team_id)

    call = svc._calls[0]
    assert set(call["employee_ids"]) == {m1.employee_id, m2.employee_id}
    assert call["assignment_source"] == "team"
    assert call["assignment_source_id"] == team.team_id


def test_assign_team_unknown_raises(svc):
    with pytest.raises(NotFoundError):
        svc.assign_team(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert svc._calls == []


def test_assign_team_is_org_scoped(svc, db_session):
    org, other_org = uuid.uuid4(), uuid.uuid4()
    team = _make_team(db_session, other_org)
    # Team belongs to a different org than the caller's org_id.
    with pytest.raises(NotFoundError):
        svc.assign_team(org, uuid.uuid4(), team.team_id)
