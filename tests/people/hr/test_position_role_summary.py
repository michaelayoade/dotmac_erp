from __future__ import annotations

import uuid
from datetime import date

from app.models.people.hr import (
    Department,
    Designation,
    Employee,
    EmployeeStatus,
    Position,
    PositionAssignment,
    PositionAssignmentType,
    PositionVacancyRoutingPolicy,
)
from app.models.person import Person
from app.services.common import PaginationParams
from app.services.people.hr.positions import (
    PositionAssignmentCreateData,
    PositionRoleSummary,
    PositionService,
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
    position_code: str | None = None,
    position_name: str | None = None,
    parent_position_id: uuid.UUID | None = None,
    is_active: bool = True,
    is_vacant: bool = True,
) -> Position:
    short_id = uuid.uuid4().hex[:8].upper()
    position = Position(
        position_id=uuid.uuid4(),
        organization_id=org_id,
        position_code=position_code or f"POS-{short_id}",
        position_name=position_name or f"Position {short_id}",
        parent_position_id=parent_position_id,
        vacancy_routing_policy=PositionVacancyRoutingPolicy.SKIP_UP,
        is_vacant=is_vacant,
        is_active=is_active,
    )
    db_session.add(position)
    db_session.flush()
    return position


def _assign(
    service: PositionService,
    position: Position,
    employee: Employee,
    *,
    assignment_type: PositionAssignmentType = PositionAssignmentType.PRIMARY,
) -> None:
    service.create_assignment(
        position.position_id,
        PositionAssignmentCreateData(
            employee_id=employee.employee_id,
            assignment_type=assignment_type,
            start_date=date(2026, 1, 1),
        ),
    )


def test_list_role_summaries_empty_org_returns_empty(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()

    result = PositionService(db_session, org_id).list_role_summaries()

    assert result.items == []
    assert result.total == 0


def test_list_role_summaries_groups_seats_sharing_role_name(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    _make_position(db_session, org_id, position_name="Engineer", position_code="ENG-01")
    _make_position(db_session, org_id, position_name="Engineer", position_code="ENG-02")
    _make_position(db_session, org_id, position_name="Manager", position_code="MGR-01")

    result = PositionService(db_session, org_id).list_role_summaries()

    by_role = {row.role_name: row for row in result.items}
    assert by_role["Engineer"].total_seats == 2
    assert by_role["Manager"].total_seats == 1
    assert result.total == 2


def test_list_role_summaries_splits_primary_and_coverage(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    primary_holder = _make_employee(db_session, org_id, "EMP-P")
    acting_holder = _make_employee(db_session, org_id, "EMP-A")
    seat = _make_position(db_session, org_id, position_name="Lead", is_vacant=False)
    service = PositionService(db_session, org_id)
    _assign(
        service, seat, primary_holder, assignment_type=PositionAssignmentType.PRIMARY
    )
    _assign(service, seat, acting_holder, assignment_type=PositionAssignmentType.ACTING)

    result = service.list_role_summaries()

    [row] = result.items
    assert row.total_seats == 1
    assert row.assigned_seats == 1
    assert row.vacant_seats == 0
    assert row.primary_assignments == 1
    assert row.coverage_assignments == 1


def test_list_role_summaries_marks_vacant_seats(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    holder = _make_employee(db_session, org_id, "EMP-001")
    assigned = _make_position(
        db_session, org_id, position_name="Analyst", position_code="ANL-01"
    )
    _make_position(db_session, org_id, position_name="Analyst", position_code="ANL-02")
    service = PositionService(db_session, org_id)
    _assign(service, assigned, holder)

    [row] = service.list_role_summaries().items

    assert row.total_seats == 2
    assert row.assigned_seats == 1
    assert row.vacant_seats == 1


def test_list_role_summaries_ignores_ended_assignments(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    holder = _make_employee(db_session, org_id, "EMP-001")
    seat = _make_position(db_session, org_id, position_name="Analyst", is_vacant=False)
    service = PositionService(db_session, org_id)
    assignment = service.create_assignment(
        seat.position_id,
        PositionAssignmentCreateData(
            employee_id=holder.employee_id,
            assignment_type=PositionAssignmentType.PRIMARY,
            start_date=date(2026, 1, 1),
        ),
    )
    service.end_assignment(
        seat.position_id,
        assignment.position_assignment_id,
        end_date=date(2026, 2, 1),
    )

    [row] = service.list_role_summaries().items

    assert row.assigned_seats == 0
    assert row.vacant_seats == 1
    assert row.primary_assignments == 0


def test_list_role_summaries_excludes_inactive_positions(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    _make_position(db_session, org_id, position_name="Engineer", is_active=True)
    _make_position(db_session, org_id, position_name="Retired Role", is_active=False)

    result = PositionService(db_session, org_id).list_role_summaries()

    role_names = {row.role_name for row in result.items}
    assert "Engineer" in role_names
    assert "Retired Role" not in role_names


def test_list_role_summaries_search_filters_by_role(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    _make_position(db_session, org_id, position_name="Engineer")
    _make_position(db_session, org_id, position_name="Accountant")

    result = PositionService(db_session, org_id).list_role_summaries(search="engin")

    [row] = result.items
    assert row.role_name == "Engineer"


def test_list_role_summaries_paginates(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    for name in ("Alpha", "Bravo", "Charlie"):
        _make_position(db_session, org_id, position_name=name)

    page_one = PositionService(db_session, org_id).list_role_summaries(
        pagination=PaginationParams(offset=0, limit=2)
    )
    page_two = PositionService(db_session, org_id).list_role_summaries(
        pagination=PaginationParams(offset=2, limit=2)
    )

    assert page_one.total == 3
    assert [row.role_name for row in page_one.items] == ["Alpha", "Bravo"]
    assert [row.role_name for row in page_two.items] == ["Charlie"]


def test_list_role_summaries_returns_typed_dataclass(db_session):
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    _make_position(db_session, org_id, position_name="Engineer")

    [row] = PositionService(db_session, org_id).list_role_summaries().items

    assert isinstance(row, PositionRoleSummary)
    assert row.role_name == "Engineer"
    assert row.designation_name == ""
    assert row.department_name == ""


def test_list_positions_orders_top_down_by_hierarchy_depth(db_session):
    # Names are intentionally chosen so depth-order (Zenith→Yak→Xenon)
    # disagrees with alphabetical order (Xenon→Yak→Zenith).  If the
    # CTE join were silently dropped, every row would land at
    # coalesce(NULL, 999) = 999 and tie-break by name, producing the
    # alphabetical order — which would fail this assertion.
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    root = _make_position(
        db_session, org_id, position_name="Zenith", position_code="ROOT-1"
    )
    middle = _make_position(
        db_session,
        org_id,
        position_name="Yak",
        position_code="MID-1",
        parent_position_id=root.position_id,
    )
    leaf = _make_position(
        db_session,
        org_id,
        position_name="Xenon",
        position_code="LEAF-1",
        parent_position_id=middle.position_id,
    )

    items = PositionService(db_session, org_id).list_positions().items

    ordered_ids = [position.position_id for position in items]
    assert ordered_ids == [root.position_id, middle.position_id, leaf.position_id]


def test_list_positions_demotes_cycled_positions_to_bottom(db_session):
    # The rooted position is named "Zenith" so it sorts alphabetically
    # AFTER the cycled positions.  Under a working CTE it still comes
    # first (depth=0 beats the cycled positions' depth=NULL→999); under
    # a broken CTE all three would tie at 999 and "Zenith" would land
    # last, failing the assertion.  Also locks in non-termination: if
    # the recursive CTE's depth guard regresses, this test hangs rather
    # than fails fast — both are observable.
    _ensure_hr_position_tables(db_session.bind)
    org_id = uuid.uuid4()
    rooted = _make_position(
        db_session, org_id, position_name="Zenith", position_code="ROOT-1"
    )
    cycle_a = _make_position(
        db_session, org_id, position_name="CycleA", position_code="CYA-1"
    )
    cycle_b = _make_position(
        db_session,
        org_id,
        position_name="CycleB",
        position_code="CYB-1",
        parent_position_id=cycle_a.position_id,
    )
    cycle_a.parent_position_id = cycle_b.position_id
    db_session.flush()

    items = PositionService(db_session, org_id).list_positions().items

    ordered_ids = [position.position_id for position in items]
    assert ordered_ids[0] == rooted.position_id
    assert set(ordered_ids[1:]) == {cycle_a.position_id, cycle_b.position_id}
