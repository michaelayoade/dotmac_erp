from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select

from app.models.people.hr import (
    Employee,
    EmployeeStatus,
    Position,
    PositionAssignment,
    PositionAssignmentType,
)
from app.models.people.perf.appraisal import Appraisal
from app.models.people.perf.appraisal_cycle import AppraisalCycle, AppraisalCycleStatus
from app.models.person import Person
from app.services.performance_automation import PerformanceAutomationService


def _ensure_position_appraisal_tables(engine) -> None:
    tables = (
        Employee.__table__,
        Position.__table__,
        PositionAssignment.__table__,
        AppraisalCycle.__table__,
        Appraisal.__table__,
    )
    for table in tables:
        for column in table.columns:
            default = column.server_default
            if default is None:
                continue
            default_text = str(getattr(default, "arg", default)).lower()
            if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
                column.server_default = None
        table.create(engine, checkfirst=True)


def _make_employee(
    db_session,
    org_id: uuid.UUID,
    code: str,
    *,
    date_of_joining: date,
) -> Employee:
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
        status=EmployeeStatus.ACTIVE,
        date_of_joining=date_of_joining,
        reports_to_id=None,
    )
    db_session.add_all([person, employee])
    db_session.flush()
    return employee


def test_generate_appraisals_uses_position_manager_when_legacy_manager_is_empty(
    db_session,
):
    _ensure_position_appraisal_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(
        db_session,
        org_id,
        "MGR-001",
        date_of_joining=date(2025, 1, 1),
    )
    employee = _make_employee(
        db_session,
        org_id,
        "EMP-001",
        date_of_joining=date(2025, 1, 1),
    )
    manager_position = Position(
        position_id=uuid.uuid4(),
        organization_id=org_id,
        is_vacant=False,
        is_active=True,
    )
    employee_position = Position(
        position_id=uuid.uuid4(),
        organization_id=org_id,
        parent_position_id=manager_position.position_id,
        is_vacant=False,
        is_active=True,
    )
    db_session.add_all(
        [
            manager_position,
            employee_position,
            PositionAssignment(
                position_assignment_id=uuid.uuid4(),
                organization_id=org_id,
                employee_id=manager.employee_id,
                position_id=manager_position.position_id,
                start_date=date(2025, 1, 1),
                assignment_type=PositionAssignmentType.PRIMARY,
            ),
            PositionAssignment(
                position_assignment_id=uuid.uuid4(),
                organization_id=org_id,
                employee_id=employee.employee_id,
                position_id=employee_position.position_id,
                start_date=date(2025, 1, 1),
                assignment_type=PositionAssignmentType.PRIMARY,
            ),
        ]
    )
    cycle = AppraisalCycle(
        cycle_id=uuid.uuid4(),
        organization_id=org_id,
        cycle_code=f"CY-{uuid.uuid4().hex[:8]}",
        cycle_name="Position Manager Cycle",
        review_period_start=date(2026, 1, 1),
        review_period_end=date(2026, 12, 31),
        start_date=date(2026, 1, 1),
        end_date=date(2026, 12, 31),
        status=AppraisalCycleStatus.ACTIVE,
        min_tenure_months=0,
    )
    db_session.add(cycle)
    db_session.flush()

    created = PerformanceAutomationService(db_session).generate_appraisals_for_cycle(
        cycle
    )

    assert len(created) == 1
    appraisal = db_session.scalar(
        select(Appraisal).where(Appraisal.employee_id == employee.employee_id)
    )
    assert appraisal is not None
    assert appraisal.manager_id == manager.employee_id
