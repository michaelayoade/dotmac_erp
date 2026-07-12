"""Group-targeted invitations for instructor-led training events.

Mirrors the LMS assignment tests: real rows drive the employee-expansion queries,
the pre-existing per-employee ``bulk_invite`` is stubbed, and each test asserts
which employees a group resolves to plus the not-found guards.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.models.people.hr import Department, Designation, Employee, EmployeeStatus
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.models.support.team import SupportTeam, SupportTeamMember
from app.services.people.training import TrainingService
from app.services.people.training.training_service import TrainingServiceError


def _ensure_tables(engine) -> None:
    models = (
        Department,
        Designation,
        Employee,
        SupportTeam,
        SupportTeamMember,
        Role,
        PersonRole,
    )
    for model in models:
        table = model.__table__
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
        table.create(engine, checkfirst=True)


def _designation(db, org_id, name, *, is_active=True) -> Designation:
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


def _employee(db, org_id, code, *, designation_id=None) -> Employee:
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


def _team(db, org_id, *, members=()) -> SupportTeam:
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


def _role_with(db, name, *, people=()) -> Role:
    role = Role(id=uuid.uuid4(), name=name, is_active=True)
    db.add(role)
    db.flush()
    for person_id in people:
        db.add(PersonRole(id=uuid.uuid4(), person_id=person_id, role_id=role.id))
    db.flush()
    return role


@pytest.fixture()
def svc(db_session, engine):
    _ensure_tables(engine)
    service = TrainingService(db_session)
    calls: list[dict] = []

    def _record(org_id, event_id, employee_ids):
        calls.append(
            {"org_id": org_id, "event_id": event_id, "employee_ids": list(employee_ids)}
        )
        return []

    service.bulk_invite = _record  # type: ignore[method-assign]
    service._calls = calls  # type: ignore[attr-defined]
    return service


def test_invite_department(svc, db_session):
    org, event = uuid.uuid4(), uuid.uuid4()
    dept = uuid.uuid4()
    a = _employee(db_session, org, "A")
    a.department_id = dept
    b = _employee(db_session, org, "B")
    b.department_id = dept
    _employee(db_session, org, "C")  # other dept
    db_session.flush()

    svc.invite_department(org, event, dept)

    assert set(svc._calls[0]["employee_ids"]) == {a.employee_id, b.employee_id}
    assert svc._calls[0]["event_id"] == event


def test_invite_designation_targets_title(svc, db_session):
    org, event = uuid.uuid4(), uuid.uuid4()
    tech = _designation(db_session, org, "Field Technician")
    other = _designation(db_session, org, "Accountant")
    a = _employee(db_session, org, "A", designation_id=tech.designation_id)
    _employee(db_session, org, "B", designation_id=other.designation_id)

    svc.invite_designation(org, event, tech.designation_id)

    assert set(svc._calls[0]["employee_ids"]) == {a.employee_id}


def test_invite_designation_unknown_raises(svc):
    with pytest.raises(TrainingServiceError):
        svc.invite_designation(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert svc._calls == []


def test_invite_role(svc, db_session):
    org, event = uuid.uuid4(), uuid.uuid4()
    a = _employee(db_session, org, "A")
    b = _employee(db_session, org, "B")
    _employee(db_session, org, "C")
    role = _role_with(db_session, "noc", people=[a.person_id, b.person_id])

    svc.invite_role(org, event, role.id)

    assert set(svc._calls[0]["employee_ids"]) == {a.employee_id, b.employee_id}


def test_invite_role_unknown_raises(svc):
    with pytest.raises(TrainingServiceError):
        svc.invite_role(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())


def test_invite_team_members(svc, db_session):
    org, event = uuid.uuid4(), uuid.uuid4()
    m1 = _employee(db_session, org, "M1")
    m2 = _employee(db_session, org, "M2")
    _employee(db_session, org, "OUT")
    team = _team(db_session, org, members=[m1, m2])

    svc.invite_team(org, event, team.team_id)

    assert set(svc._calls[0]["employee_ids"]) == {m1.employee_id, m2.employee_id}


def test_invite_team_unknown_raises(svc):
    with pytest.raises(TrainingServiceError):
        svc.invite_team(uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    assert svc._calls == []
