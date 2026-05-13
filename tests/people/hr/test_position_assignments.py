from __future__ import annotations

import uuid
from datetime import date

import pytest
from sqlalchemy import select

from app.models.people.hr import (
    Department,
    Designation,
    Employee,
    EmployeeStatus,
    Position,
    PositionAssignment,
    PositionAssignmentType,
)
from app.models.person import Person
from app.services.common import ConflictError
from app.services.people.hr import EmployeeService, EmployeeUpdateData
from app.services.people.hr.errors import InvalidManagerError
from app.services.people.hr.org_resolver import OrgResolver
from app.services.people.hr.positions import (
    OrgChartNode,
    PositionAssignmentCreateData,
    PositionService,
    ReconcileResult,
)


def _ensure_hr_position_tables(engine) -> None:
    tables = (
        Department.__table__,
        Designation.__table__,
        Employee.__table__,
        Position.__table__,
        PositionAssignment.__table__,
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


def _make_employee(db_session, org_id: uuid.UUID, code: str) -> Employee:
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
    )
    db_session.add_all([person, employee])
    db_session.flush()
    return employee


def _make_position(
    db_session,
    org_id: uuid.UUID,
    *,
    parent_position_id: uuid.UUID | None = None,
    is_vacant: bool = True,
) -> Position:
    position = Position(
        position_id=uuid.uuid4(),
        organization_id=org_id,
        parent_position_id=parent_position_id,
        is_vacant=is_vacant,
    )
    db_session.add(position)
    db_session.flush()
    return position


def _assignment_data(
    employee: Employee,
    *,
    assignment_type: PositionAssignmentType = PositionAssignmentType.PRIMARY,
    start_date: date = date(2026, 5, 1),
    end_date: date | None = None,
) -> PositionAssignmentCreateData:
    return PositionAssignmentCreateData(
        employee_id=employee.employee_id,
        assignment_type=assignment_type,
        start_date=start_date,
        end_date=end_date,
    )


def test_create_primary_assignment_marks_position_not_vacant(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    employee = _make_employee(db_session, org_id, "EMP-001")
    position = _make_position(db_session, org_id)

    assignment = PositionService(db_session, org_id).create_assignment(
        position.position_id,
        _assignment_data(employee),
    )

    assert assignment.position_id == position.position_id
    assert assignment.employee_id == employee.employee_id
    assert position.is_vacant is False


def test_primary_assignment_rejects_active_primary_for_same_position(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    first_employee = _make_employee(db_session, org_id, "EMP-001")
    second_employee = _make_employee(db_session, org_id, "EMP-002")
    position = _make_position(db_session, org_id)
    service = PositionService(db_session, org_id)
    service.create_assignment(position.position_id, _assignment_data(first_employee))

    with pytest.raises(
        ConflictError,
        match="position already has an active primary assignment",
    ):
        service.create_assignment(
            position.position_id, _assignment_data(second_employee)
        )


def test_primary_assignment_rejects_active_primary_for_same_employee(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    employee = _make_employee(db_session, org_id, "EMP-001")
    first_position = _make_position(db_session, org_id)
    second_position = _make_position(db_session, org_id)
    service = PositionService(db_session, org_id)
    service.create_assignment(first_position.position_id, _assignment_data(employee))

    with pytest.raises(
        ConflictError,
        match="employee already has an active primary position",
    ):
        service.create_assignment(
            second_position.position_id, _assignment_data(employee)
        )


def test_acting_assignment_allowed_alongside_primary(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    primary_employee = _make_employee(db_session, org_id, "EMP-001")
    acting_employee = _make_employee(db_session, org_id, "EMP-002")
    position = _make_position(db_session, org_id)
    service = PositionService(db_session, org_id)
    service.create_assignment(position.position_id, _assignment_data(primary_employee))

    acting = service.create_assignment(
        position.position_id,
        _assignment_data(
            acting_employee,
            assignment_type=PositionAssignmentType.ACTING,
        ),
    )

    assert acting.assignment_type == PositionAssignmentType.ACTING
    assert position.is_vacant is False


def test_end_last_active_assignment_marks_position_vacant(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    employee = _make_employee(db_session, org_id, "EMP-001")
    position = _make_position(db_session, org_id)
    service = PositionService(db_session, org_id)
    assignment = service.create_assignment(
        position.position_id, _assignment_data(employee)
    )

    service.end_assignment(
        position.position_id,
        assignment.position_assignment_id,
        end_date=date(2026, 5, 2),
    )

    assert assignment.end_date == date(2026, 5, 2)
    assert position.is_vacant is True


def test_org_resolver_direct_reports_rolls_up_vacant_positions(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    manager_position = _make_position(db_session, org_id, is_vacant=False)
    vacant_position = _make_position(
        db_session,
        org_id,
        parent_position_id=manager_position.position_id,
        is_vacant=True,
    )
    employee_position = _make_position(
        db_session,
        org_id,
        parent_position_id=vacant_position.position_id,
        is_vacant=False,
    )
    service = PositionService(db_session, org_id)
    service.create_assignment(
        manager_position.position_id,
        _assignment_data(manager),
    )
    service.create_assignment(
        employee_position.position_id,
        _assignment_data(employee),
    )

    reports = OrgResolver(db_session).get_direct_reports(
        manager.employee_id,
        org_id,
    )

    assert [report.employee_id for report in reports] == [employee.employee_id]


def test_employee_service_direct_reports_uses_positions(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    legacy_report = _make_employee(db_session, org_id, "EMP-LEGACY")
    legacy_report.reports_to_id = manager.employee_id
    manager_position = _make_position(db_session, org_id, is_vacant=False)
    employee_position = _make_position(
        db_session,
        org_id,
        parent_position_id=manager_position.position_id,
        is_vacant=False,
    )
    service = PositionService(db_session, org_id)
    service.create_assignment(manager_position.position_id, _assignment_data(manager))
    service.create_assignment(employee_position.position_id, _assignment_data(employee))

    reports = EmployeeService(db_session, org_id).get_direct_reports(
        manager.employee_id
    )

    assert [report.employee_id for report in reports] == [employee.employee_id]
    assert legacy_report.employee_id not in {report.employee_id for report in reports}


def test_employee_reports_to_update_syncs_position_parent(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    manager_position = _make_position(db_session, org_id, is_vacant=False)
    employee_position = _make_position(db_session, org_id, is_vacant=False)
    position_service = PositionService(db_session, org_id)
    position_service.create_assignment(
        manager_position.position_id,
        _assignment_data(manager),
    )
    position_service.create_assignment(
        employee_position.position_id,
        _assignment_data(employee),
    )

    EmployeeService(db_session, org_id).update_employee(
        employee.employee_id,
        EmployeeUpdateData(reports_to_id=manager.employee_id),
    )

    assert employee_position.parent_position_id == manager_position.position_id
    resolved = OrgResolver(db_session).get_manager(employee.employee_id, org_id)
    assert resolved is not None and resolved.employee_id == manager.employee_id


def test_primary_assignment_syncs_existing_legacy_manager_to_position_parent(
    db_session,
):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    employee.reports_to_id = manager.employee_id
    manager_position = _make_position(db_session, org_id, is_vacant=False)
    employee_position = _make_position(db_session, org_id, is_vacant=True)
    position_service = PositionService(db_session, org_id)
    position_service.create_assignment(
        manager_position.position_id,
        _assignment_data(manager),
    )

    position_service.create_assignment(
        employee_position.position_id,
        _assignment_data(employee),
    )

    assert employee_position.parent_position_id == manager_position.position_id


def test_reconcile_provisions_positions_for_employees_missing_assignments(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    employee.reports_to_id = manager.employee_id
    db_session.flush()

    result = PositionService(db_session, org_id).reconcile_from_reports_to_id()

    assert isinstance(result, ReconcileResult)
    assert result.positions_created == 2
    assert result.assignments_created == 2
    manager_assignment = db_session.scalar(
        select(PositionAssignment).where(
            PositionAssignment.employee_id == manager.employee_id,
            PositionAssignment.end_date.is_(None),
        )
    )
    employee_assignment = db_session.scalar(
        select(PositionAssignment).where(
            PositionAssignment.employee_id == employee.employee_id,
            PositionAssignment.end_date.is_(None),
        )
    )
    assert manager_assignment is not None
    assert employee_assignment is not None
    employee_position = db_session.get(Position, employee_assignment.position_id)
    assert employee_position.parent_position_id == manager_assignment.position_id


def test_reconcile_syncs_parents_for_existing_positions(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    manager_position = _make_position(db_session, org_id, is_vacant=False)
    employee_position = _make_position(db_session, org_id, is_vacant=False)
    service = PositionService(db_session, org_id)
    service.create_assignment(manager_position.position_id, _assignment_data(manager))
    service.create_assignment(employee_position.position_id, _assignment_data(employee))

    employee.reports_to_id = manager.employee_id
    db_session.flush()
    employee_position.parent_position_id = None
    db_session.flush()

    result = service.reconcile_from_reports_to_id()

    assert result.positions_created == 0
    assert result.assignments_created == 0
    assert result.parents_synced == 1
    assert employee_position.parent_position_id == manager_position.position_id


def test_reconcile_is_idempotent(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    employee.reports_to_id = manager.employee_id
    db_session.flush()
    service = PositionService(db_session, org_id)
    service.reconcile_from_reports_to_id()

    second = service.reconcile_from_reports_to_id()

    assert second.positions_created == 0
    assert second.assignments_created == 0
    assert second.parents_synced == 0


def test_reconcile_scopes_to_employee_id_subset(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    in_scope = _make_employee(db_session, org_id, "IN-001")
    out_of_scope = _make_employee(db_session, org_id, "OUT-001")
    db_session.flush()
    service = PositionService(db_session, org_id)

    result = service.reconcile_from_reports_to_id(
        employee_ids=[in_scope.employee_id],
    )

    assert result.positions_created == 1
    assert result.assignments_created == 1
    out_assignment = db_session.scalar(
        select(PositionAssignment).where(
            PositionAssignment.employee_id == out_of_scope.employee_id,
        )
    )
    assert out_assignment is None


def test_reconcile_preserves_parent_when_reports_to_id_is_none(db_session):
    """
    A position parent set via the EmployeeService.set_manager chokepoint
    must NOT be overwritten by a bulk reconcile when the employee's
    reports_to_id is None. Bulk callers that need to clear a manager should
    use set_manager(employee, None) explicitly.
    """
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    manager_position = _make_position(db_session, org_id, is_vacant=False)
    employee_position = _make_position(
        db_session,
        org_id,
        parent_position_id=manager_position.position_id,
        is_vacant=False,
    )
    service = PositionService(db_session, org_id)
    service.create_assignment(manager_position.position_id, _assignment_data(manager))
    service.create_assignment(employee_position.position_id, _assignment_data(employee))
    employee.reports_to_id = None
    db_session.flush()

    result = service.reconcile_from_reports_to_id(
        employee_ids=[employee.employee_id],
    )

    assert result.parents_synced == 0
    assert employee_position.parent_position_id == manager_position.position_id


def test_position_chain_starts_with_employee_and_walks_up(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    director = _make_employee(db_session, org_id, "DIR-001")
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    director_position = _make_position(db_session, org_id, is_vacant=False)
    manager_position = _make_position(
        db_session,
        org_id,
        parent_position_id=director_position.position_id,
        is_vacant=False,
    )
    employee_position = _make_position(
        db_session,
        org_id,
        parent_position_id=manager_position.position_id,
        is_vacant=False,
    )
    service = PositionService(db_session, org_id)
    service.create_assignment(director_position.position_id, _assignment_data(director))
    service.create_assignment(manager_position.position_id, _assignment_data(manager))
    service.create_assignment(employee_position.position_id, _assignment_data(employee))

    chain = OrgResolver(db_session).get_position_chain(employee.employee_id, org_id)

    assert [position.position_id for position, _ in chain] == [
        employee_position.position_id,
        manager_position.position_id,
        director_position.position_id,
    ]
    assert [incumbent.employee_id if incumbent else None for _, incumbent in chain] == [
        employee.employee_id,
        manager.employee_id,
        director.employee_id,
    ]


def test_position_chain_includes_vacant_ancestor(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    director = _make_employee(db_session, org_id, "DIR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    director_position = _make_position(db_session, org_id, is_vacant=False)
    vacant_position = _make_position(
        db_session,
        org_id,
        parent_position_id=director_position.position_id,
        is_vacant=True,
    )
    employee_position = _make_position(
        db_session,
        org_id,
        parent_position_id=vacant_position.position_id,
        is_vacant=False,
    )
    service = PositionService(db_session, org_id)
    service.create_assignment(director_position.position_id, _assignment_data(director))
    service.create_assignment(employee_position.position_id, _assignment_data(employee))

    chain = OrgResolver(db_session).get_position_chain(employee.employee_id, org_id)

    assert [position.position_id for position, _ in chain] == [
        employee_position.position_id,
        vacant_position.position_id,
        director_position.position_id,
    ]
    incumbents = [incumbent for _, incumbent in chain]
    assert incumbents[0].employee_id == employee.employee_id
    assert incumbents[1] is None
    assert incumbents[2].employee_id == director.employee_id


def test_position_chain_empty_for_employee_without_assignment(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    employee = _make_employee(db_session, org_id, "EMP-001")

    chain = OrgResolver(db_session).get_position_chain(employee.employee_id, org_id)

    assert chain == []


def test_build_org_chart_returns_roots_with_children(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    ceo = _make_employee(db_session, org_id, "CEO-001")
    director = _make_employee(db_session, org_id, "DIR-001")
    engineer = _make_employee(db_session, org_id, "ENG-001")
    ceo_position = _make_position(db_session, org_id, is_vacant=False)
    director_position = _make_position(
        db_session,
        org_id,
        parent_position_id=ceo_position.position_id,
        is_vacant=False,
    )
    engineer_position = _make_position(
        db_session,
        org_id,
        parent_position_id=director_position.position_id,
        is_vacant=False,
    )
    service = PositionService(db_session, org_id)
    service.create_assignment(ceo_position.position_id, _assignment_data(ceo))
    service.create_assignment(director_position.position_id, _assignment_data(director))
    service.create_assignment(engineer_position.position_id, _assignment_data(engineer))

    roots = service.build_org_chart()

    assert len(roots) == 1
    assert isinstance(roots[0], OrgChartNode)
    assert roots[0].position_id == ceo_position.position_id
    assert roots[0].incumbent_employee_id == ceo.employee_id
    assert len(roots[0].children) == 1
    director_node = roots[0].children[0]
    assert director_node.position_id == director_position.position_id
    assert len(director_node.children) == 1
    assert director_node.children[0].position_id == engineer_position.position_id


def test_build_org_chart_marks_vacant_positions(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    ceo = _make_employee(db_session, org_id, "CEO-001")
    ceo_position = _make_position(db_session, org_id, is_vacant=False)
    vacant_position = _make_position(
        db_session,
        org_id,
        parent_position_id=ceo_position.position_id,
        is_vacant=True,
    )
    service = PositionService(db_session, org_id)
    service.create_assignment(ceo_position.position_id, _assignment_data(ceo))

    roots = service.build_org_chart()

    assert len(roots) == 1
    assert roots[0].is_vacant is False
    vacant_node = roots[0].children[0]
    assert vacant_node.position_id == vacant_position.position_id
    assert vacant_node.is_vacant is True
    assert vacant_node.incumbent_employee_id is None
    assert vacant_node.incumbent_name == ""


def test_build_org_chart_empty_org_returns_empty(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()

    roots = PositionService(db_session, org_id).build_org_chart()

    assert roots == []


def test_employee_reports_to_update_rejects_position_cycle(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    manager = _make_employee(db_session, org_id, "MGR-001")
    employee = _make_employee(db_session, org_id, "EMP-001")
    manager_position = _make_position(db_session, org_id, is_vacant=False)
    employee_position = _make_position(
        db_session,
        org_id,
        parent_position_id=manager_position.position_id,
        is_vacant=False,
    )
    position_service = PositionService(db_session, org_id)
    position_service.create_assignment(
        manager_position.position_id,
        _assignment_data(manager),
    )
    position_service.create_assignment(
        employee_position.position_id,
        _assignment_data(employee),
    )

    with pytest.raises(InvalidManagerError):
        EmployeeService(db_session, org_id).update_employee(
            manager.employee_id,
            EmployeeUpdateData(reports_to_id=employee.employee_id),
        )
