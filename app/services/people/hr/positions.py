"""Position service for position-based HR reporting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

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
from app.services.common import (
    ConflictError,
    NotFoundError,
    PaginatedResult,
    PaginationParams,
    ValidationError,
    paginate,
)
from app.services.people.hr.org_resolver import OrgResolver


@dataclass
class PositionCreateData:
    """Data for creating a position."""

    designation_id: UUID | None = None
    department_id: UUID | None = None
    parent_position_id: UUID | None = None
    is_vacant: bool = True


@dataclass
class PositionUpdateData:
    """Data for updating a position."""

    designation_id: UUID | None = None
    department_id: UUID | None = None
    parent_position_id: UUID | None = None
    is_vacant: bool | None = None


@dataclass
class PositionAssignmentCreateData:
    """Data for assigning an employee to a position."""

    employee_id: UUID
    assignment_type: PositionAssignmentType
    start_date: date
    end_date: date | None = None


@dataclass
class PositionSummary:
    """Display summary for a position row."""

    position: Position
    incumbent: Employee | None


@dataclass
class ReconcileResult:
    """Counts returned from ``PositionService.reconcile_from_reports_to_id``."""

    positions_created: int = 0
    assignments_created: int = 0
    parents_synced: int = 0


@dataclass
class OrgChartNode:
    """A single node in the position org chart with display data and children."""

    position_id: UUID
    designation_name: str
    department_name: str
    incumbent_employee_id: UUID | None
    incumbent_name: str
    is_vacant: bool
    children: list[OrgChartNode]


class PositionService:
    """Service for managing first-class HR positions."""

    def __init__(self, db: Session, organization_id: UUID) -> None:
        self.db = db
        self.organization_id = organization_id
        self.resolver = OrgResolver(db)

    def list_positions(
        self,
        *,
        search: str | None = None,
        include_deleted: bool = False,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[Position]:
        """List positions for the organization."""
        stmt = (
            select(Position)
            .where(Position.organization_id == self.organization_id)
            .options(
                selectinload(Position.designation),
                selectinload(Position.department),
                selectinload(Position.parent_position).selectinload(
                    Position.designation
                ),
                selectinload(Position.parent_position).selectinload(
                    Position.department
                ),
            )
            .order_by(Position.created_at.desc())
        )
        if not include_deleted:
            stmt = stmt.where(Position.is_active.is_(True))

        search_text = (search or "").strip()
        if search_text:
            like = f"%{search_text}%"
            stmt = (
                stmt.join(
                    Designation,
                    Position.designation_id == Designation.designation_id,
                    isouter=True,
                )
                .join(
                    Department,
                    Position.department_id == Department.department_id,
                    isouter=True,
                )
                .where(
                    or_(
                        Designation.designation_name.ilike(like),
                        Designation.designation_code.ilike(like),
                        Department.department_name.ilike(like),
                        Department.department_code.ilike(like),
                    )
                )
            )

        return paginate(self.db, stmt, pagination or PaginationParams())

    def list_position_summaries(
        self,
        *,
        search: str | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[PositionSummary]:
        """List positions with current incumbent display data."""
        positions = self.list_positions(search=search, pagination=pagination)
        summaries = [
            PositionSummary(
                position=position,
                incumbent=self.resolver.get_position_incumbent(
                    position.position_id,
                    self.organization_id,
                ),
            )
            for position in positions.items
        ]
        return PaginatedResult(
            items=summaries,
            total=positions.total,
            offset=positions.offset,
            limit=positions.limit,
        )

    def build_org_chart(self) -> list[OrgChartNode]:
        """
        Build the full position tree for the organization.

        Returns root-level nodes (positions with no parent). Each node holds
        its child nodes, so callers can render the tree by recursion. Two
        queries total regardless of tree size — one for positions with their
        designations/departments, one for active PRIMARY incumbents.

        Positions are sorted by designation_name within each level so the
        output is stable across renders.
        """
        positions = list(
            self.db.scalars(
                select(Position)
                .where(
                    Position.organization_id == self.organization_id,
                    Position.is_active.is_(True),
                )
                .options(
                    selectinload(Position.designation),
                    selectinload(Position.department),
                )
            ).all()
        )
        if not positions:
            return []

        position_ids = [p.position_id for p in positions]
        incumbent_rows = self.db.execute(
            select(
                PositionAssignment.position_id,
                Employee.employee_id,
                Person.first_name,
                Person.last_name,
                Person.display_name,
            )
            .join(Employee, Employee.employee_id == PositionAssignment.employee_id)
            .join(Person, Person.id == Employee.person_id, isouter=True)
            .where(
                PositionAssignment.organization_id == self.organization_id,
                PositionAssignment.position_id.in_(position_ids),
                PositionAssignment.assignment_type == PositionAssignmentType.PRIMARY,
                PositionAssignment.end_date.is_(None),
                Employee.status == EmployeeStatus.ACTIVE,
            )
        ).all()
        incumbents_by_position: dict[UUID, tuple[UUID, str]] = {}
        for row in incumbent_rows:
            name = (
                row.display_name
                or f"{row.first_name or ''} {row.last_name or ''}".strip()
                or ""
            )
            incumbents_by_position[row.position_id] = (row.employee_id, name)

        nodes_by_id: dict[UUID, OrgChartNode] = {
            p.position_id: OrgChartNode(
                position_id=p.position_id,
                designation_name=(
                    p.designation.designation_name if p.designation else ""
                ),
                department_name=(p.department.department_name if p.department else ""),
                incumbent_employee_id=incumbents_by_position.get(
                    p.position_id, (None, "")
                )[0],
                incumbent_name=incumbents_by_position.get(p.position_id, (None, ""))[1],
                is_vacant=p.position_id not in incumbents_by_position,
                children=[],
            )
            for p in positions
        }

        roots: list[OrgChartNode] = []
        for position in positions:
            node = nodes_by_id[position.position_id]
            parent_id = position.parent_position_id
            if parent_id and parent_id in nodes_by_id:
                nodes_by_id[parent_id].children.append(node)
            else:
                roots.append(node)

        def _sort_children(node: OrgChartNode) -> None:
            node.children.sort(
                key=lambda n: (n.designation_name or "", str(n.position_id))
            )
            for child in node.children:
                _sort_children(child)

        roots.sort(key=lambda n: (n.designation_name or "", str(n.position_id)))
        for root in roots:
            _sort_children(root)
        return roots

    def get_position(self, position_id: UUID) -> Position:
        """Get a position by ID or raise."""
        position = self.db.scalar(
            select(Position)
            .where(
                Position.position_id == position_id,
                Position.organization_id == self.organization_id,
                Position.is_active.is_(True),
            )
            .options(
                selectinload(Position.designation),
                selectinload(Position.department),
                selectinload(Position.parent_position),
            )
        )
        if not position:
            raise NotFoundError("Position not found")
        return position

    def create_position(self, data: PositionCreateData) -> Position:
        """Create a new position."""
        self._validate_references(data)
        position = Position(
            organization_id=self.organization_id,
            designation_id=data.designation_id,
            department_id=data.department_id,
            parent_position_id=data.parent_position_id,
            is_vacant=data.is_vacant,
        )
        self.db.add(position)
        self.db.flush()
        return position

    def update_position(self, position_id: UUID, data: PositionUpdateData) -> Position:
        """Update a position."""
        position = self.get_position(position_id)
        self._validate_references(data, position_id=position.position_id)

        position.designation_id = data.designation_id
        position.department_id = data.department_id
        position.parent_position_id = data.parent_position_id
        if data.is_vacant is not None:
            position.is_vacant = data.is_vacant

        self.db.flush()
        return position

    def list_assignments(self, position_id: UUID) -> list[PositionAssignment]:
        """List assignment history for a position."""
        self.get_position(position_id)
        stmt = (
            select(PositionAssignment)
            .where(
                PositionAssignment.organization_id == self.organization_id,
                PositionAssignment.position_id == position_id,
            )
            .options(
                selectinload(PositionAssignment.employee).selectinload(Employee.person)
            )
            .order_by(
                PositionAssignment.end_date.is_not(None),
                PositionAssignment.start_date.desc(),
            )
        )
        return list(self.db.scalars(stmt).all())

    def list_employee_options(self, *, limit: int = 500) -> list[Employee]:
        """List active employees for assignment dropdowns."""
        stmt = (
            select(Employee)
            .where(
                Employee.organization_id == self.organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
            .options(selectinload(Employee.person))
            .order_by(Employee.employee_code.asc())
            .limit(limit)
        )
        return list(self.db.scalars(stmt).all())

    def create_assignment(
        self,
        position_id: UUID,
        data: PositionAssignmentCreateData,
    ) -> PositionAssignment:
        """Assign an employee to a position."""
        position = self.get_position(position_id)
        self._validate_assignment(data)

        if data.end_date and data.end_date < data.start_date:
            raise ValidationError("End date cannot be before start date")

        if data.assignment_type == PositionAssignmentType.PRIMARY:
            self._validate_primary_assignment_availability(
                employee_id=data.employee_id,
                position_id=position_id,
                start_date=data.start_date,
                end_date=data.end_date,
            )

        assignment = PositionAssignment(
            organization_id=self.organization_id,
            employee_id=data.employee_id,
            position_id=position_id,
            assignment_type=data.assignment_type,
            start_date=data.start_date,
            end_date=data.end_date,
        )
        self.db.add(assignment)

        if not data.end_date or data.end_date >= date.today():
            position.is_vacant = False

        self.db.flush()
        if data.assignment_type == PositionAssignmentType.PRIMARY:
            employee = self.db.get(Employee, data.employee_id)
            if employee and employee.reports_to_id:
                self.sync_employee_manager_position(
                    employee.employee_id,
                    employee.reports_to_id,
                )
        return assignment

    def end_assignment(
        self,
        position_id: UUID,
        assignment_id: UUID,
        *,
        end_date: date,
    ) -> PositionAssignment:
        """End an active position assignment."""
        position = self.get_position(position_id)
        assignment = self.db.scalar(
            select(PositionAssignment).where(
                PositionAssignment.position_assignment_id == assignment_id,
                PositionAssignment.position_id == position_id,
                PositionAssignment.organization_id == self.organization_id,
            )
        )
        if not assignment:
            raise NotFoundError("Assignment not found")
        if end_date < assignment.start_date:
            raise ValidationError("End date cannot be before start date")

        assignment.end_date = end_date
        self.db.flush()

        if not self._has_active_assignment(position.position_id):
            position.is_vacant = True
            self.db.flush()

        return assignment

    def sync_employee_manager_position(
        self,
        employee_id: UUID,
        manager_employee_id: UUID | None,
    ) -> Position | None:
        """
        Mirror a legacy employee manager selection into the position tree.

        If the employee has no active position assignment yet, there is no
        position to update, so the legacy field remains as a compatibility
        value until an assignment exists.
        """
        employee_assignment = self.resolver.get_active_assignment(
            employee_id,
            self.organization_id,
        )
        if not employee_assignment:
            return None

        employee_position = self.get_position(employee_assignment.position_id)
        parent_position_id = None
        if manager_employee_id:
            manager_assignment = self.resolver.get_active_assignment(
                manager_employee_id,
                self.organization_id,
            )
            if not manager_assignment:
                raise ValidationError(
                    "Selected manager does not have an active position assignment"
                )
            parent_position_id = manager_assignment.position_id

        update = PositionUpdateData(
            designation_id=employee_position.designation_id,
            department_id=employee_position.department_id,
            parent_position_id=parent_position_id,
            is_vacant=employee_position.is_vacant,
        )
        return self.update_position(employee_position.position_id, update)

    def reconcile_from_reports_to_id(
        self,
        *,
        employee_ids: list[UUID] | None = None,
    ) -> ReconcileResult:
        """
        Bulk-provision positions and sync parent links from ``reports_to_id``.

        Use this after batch writers (CSV import, ERPNext sync, staging
        import) that set ``Employee.reports_to_id`` directly without going
        through the per-row ``EmployeeService.set_manager`` chokepoint.

        Pass 1 — provision: any target employee lacking an active PRIMARY
        position assignment gets a freshly created ``Position`` (mirroring
        their designation and department) and a PRIMARY ``PositionAssignment``
        starting on ``date_of_joining`` (or today, if missing).

        Pass 2 — parent sync: for every target employee with
        ``reports_to_id`` set, the position's ``parent_position_id`` is
        synced to the active PRIMARY position of that manager. Employees
        whose ``reports_to_id`` is ``None`` are SKIPPED — their
        ``parent_position_id`` may have been set deliberately via the
        ``EmployeeService.set_manager`` chokepoint, and a bulk
        reconcile must not overwrite it. To explicitly clear a manager,
        callers must go through ``set_manager(employee, None)``.

        Idempotent. Pass ``None`` to reconcile the entire organization, or a
        list of employee IDs to limit scope to a recently-imported batch.
        """
        result = ReconcileResult()
        today = date.today()

        target_stmt = select(Employee).where(
            Employee.organization_id == self.organization_id,
            Employee.status != EmployeeStatus.TERMINATED,
        )
        if employee_ids is not None:
            if not employee_ids:
                return result
            target_stmt = target_stmt.where(Employee.employee_id.in_(employee_ids))
        employees = list(self.db.scalars(target_stmt).all())
        if not employees:
            return result

        provisioned = self._provision_missing_positions(employees, today=today)
        result.positions_created += provisioned
        result.assignments_created += provisioned

        active_primary_rows = self.db.execute(
            select(
                PositionAssignment.employee_id,
                PositionAssignment.position_id,
            ).where(
                PositionAssignment.organization_id == self.organization_id,
                PositionAssignment.assignment_type == PositionAssignmentType.PRIMARY,
                PositionAssignment.end_date.is_(None),
            )
        ).all()
        emp_to_position: dict[UUID, UUID] = {
            row.employee_id: row.position_id for row in active_primary_rows
        }

        target_position_ids = {
            emp_to_position[e.employee_id]
            for e in employees
            if e.employee_id in emp_to_position
        }
        if not target_position_ids:
            return result

        positions_by_id: dict[UUID, Position] = {
            p.position_id: p
            for p in self.db.scalars(
                select(Position).where(Position.position_id.in_(target_position_ids))
            ).all()
        }

        for emp in employees:
            # Skip employees whose reports_to_id is None — they may have a
            # manager set via the position chokepoint (set_manager) that this
            # bulk reconcile must not overwrite. Bulk callers that need to
            # explicitly clear a manager should use EmployeeService.set_manager
            # with manager_employee_id=None.
            if emp.reports_to_id is None:
                continue
            position_id = emp_to_position.get(emp.employee_id)
            if not position_id:
                continue
            position = positions_by_id.get(position_id)
            if not position:
                continue
            new_parent_id = emp_to_position.get(emp.reports_to_id)
            if position.parent_position_id != new_parent_id:
                position.parent_position_id = new_parent_id
                result.parents_synced += 1

        if result.parents_synced:
            self.db.flush()
        return result

    def provision_positions_for_employees(
        self,
        employee_ids: list[UUID],
    ) -> int:
        """
        Ensure each given employee has an active PRIMARY position assignment.

        For any employee in the input list that lacks an active PRIMARY
        assignment, creates a ``Position`` (copying their designation and
        department) and a PRIMARY ``PositionAssignment`` starting on the
        employee's date_of_joining (or today). Idempotent — returns the
        number of positions newly provisioned. Use after creating an
        employee, before calling ``set_manager`` so the chokepoint has
        something to link to.
        """
        if not employee_ids:
            return 0
        employees = list(
            self.db.scalars(
                select(Employee).where(
                    Employee.organization_id == self.organization_id,
                    Employee.status != EmployeeStatus.TERMINATED,
                    Employee.employee_id.in_(employee_ids),
                )
            ).all()
        )
        if not employees:
            return 0
        return self._provision_missing_positions(employees, today=date.today())

    def _provision_missing_positions(
        self,
        employees: list[Employee],
        *,
        today: date,
    ) -> int:
        """Internal: provision Position + PRIMARY assignment for employees missing one."""
        target_employee_ids = [e.employee_id for e in employees]
        existing_primary_emp_ids = set(
            self.db.scalars(
                select(PositionAssignment.employee_id).where(
                    PositionAssignment.organization_id == self.organization_id,
                    PositionAssignment.employee_id.in_(target_employee_ids),
                    PositionAssignment.assignment_type
                    == PositionAssignmentType.PRIMARY,
                    PositionAssignment.end_date.is_(None),
                )
            ).all()
        )

        missing = [
            e for e in employees if e.employee_id not in existing_primary_emp_ids
        ]
        if not missing:
            return 0

        new_positions_by_employee: dict[UUID, Position] = {}
        for emp in missing:
            position = Position(
                organization_id=self.organization_id,
                designation_id=emp.designation_id,
                department_id=emp.department_id,
                is_vacant=False,
            )
            new_positions_by_employee[emp.employee_id] = position
            self.db.add(position)
        self.db.flush()

        for emp in missing:
            position = new_positions_by_employee[emp.employee_id]
            assignment = PositionAssignment(
                organization_id=self.organization_id,
                employee_id=emp.employee_id,
                position_id=position.position_id,
                assignment_type=PositionAssignmentType.PRIMARY,
                start_date=emp.date_of_joining or today,
            )
            self.db.add(assignment)
        self.db.flush()
        return len(missing)

    def list_parent_options(
        self,
        *,
        exclude_position_id: UUID | None = None,
        limit: int = 500,
    ) -> list[Position]:
        """List positions that may be selected as parent positions."""
        stmt = (
            select(Position)
            .where(
                Position.organization_id == self.organization_id,
                Position.is_active.is_(True),
            )
            .options(
                selectinload(Position.designation), selectinload(Position.department)
            )
            .order_by(Position.created_at.desc())
            .limit(limit)
        )
        if exclude_position_id:
            stmt = stmt.where(Position.position_id != exclude_position_id)
        return list(self.db.scalars(stmt).all())

    def _validate_references(
        self,
        data: PositionCreateData | PositionUpdateData,
        *,
        position_id: UUID | None = None,
    ) -> None:
        if data.designation_id:
            designation = self.db.scalar(
                select(Designation).where(
                    Designation.designation_id == data.designation_id,
                    Designation.organization_id == self.organization_id,
                    Designation.is_active.is_(True),
                )
            )
            if not designation:
                raise ValidationError("Designation not found")

        if data.department_id:
            department = self.db.scalar(
                select(Department).where(
                    Department.department_id == data.department_id,
                    Department.organization_id == self.organization_id,
                    Department.is_active.is_(True),
                )
            )
            if not department:
                raise ValidationError("Department not found")

        if data.parent_position_id:
            if position_id and data.parent_position_id == position_id:
                raise ValidationError("A position cannot report to itself")

            parent = self.db.scalar(
                select(Position).where(
                    Position.position_id == data.parent_position_id,
                    Position.organization_id == self.organization_id,
                    Position.is_active.is_(True),
                )
            )
            if not parent:
                raise ValidationError("Parent position not found")

            if position_id and self._would_create_cycle(position_id, parent):
                raise ValidationError("Parent position would create a reporting cycle")

    def _would_create_cycle(self, position_id: UUID, parent: Position) -> bool:
        visited = {position_id}
        current: Position | None = parent
        while current and current.parent_position_id:
            if current.parent_position_id in visited:
                return True
            visited.add(current.parent_position_id)
            current = self.db.scalar(
                select(Position).where(
                    Position.position_id == current.parent_position_id,
                    Position.organization_id == self.organization_id,
                    Position.is_active.is_(True),
                )
            )
            if not current:
                return False
        return False

    def _validate_assignment(self, data: PositionAssignmentCreateData) -> None:
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == data.employee_id,
                Employee.organization_id == self.organization_id,
                Employee.status == EmployeeStatus.ACTIVE,
            )
        )
        if not employee:
            raise ValidationError("Employee not found")

    def _validate_primary_assignment_availability(
        self,
        *,
        employee_id: UUID,
        position_id: UUID,
        start_date: date,
        end_date: date | None,
    ) -> None:
        overlap_filter = self._assignment_overlap_filter(start_date, end_date)

        position_conflict = self.db.scalar(
            select(PositionAssignment.position_assignment_id)
            .where(
                PositionAssignment.organization_id == self.organization_id,
                PositionAssignment.position_id == position_id,
                PositionAssignment.assignment_type == PositionAssignmentType.PRIMARY,
                overlap_filter,
            )
            .limit(1)
        )
        if position_conflict:
            raise ConflictError(
                "This position already has an active primary assignment"
            )

        employee_conflict = self.db.scalar(
            select(PositionAssignment.position_assignment_id)
            .where(
                PositionAssignment.organization_id == self.organization_id,
                PositionAssignment.employee_id == employee_id,
                PositionAssignment.assignment_type == PositionAssignmentType.PRIMARY,
                overlap_filter,
            )
            .limit(1)
        )
        if employee_conflict:
            raise ConflictError("This employee already has an active primary position")

    @staticmethod
    def _assignment_overlap_filter(start_date: date, end_date: date | None):
        assignment_end = PositionAssignment.end_date
        new_end = end_date or date.max
        return (PositionAssignment.start_date <= new_end) & (
            assignment_end.is_(None) | (assignment_end >= start_date)
        )

    def _has_active_assignment(self, position_id: UUID) -> bool:
        today = date.today()
        return bool(
            self.db.scalar(
                select(PositionAssignment.position_assignment_id)
                .where(
                    PositionAssignment.organization_id == self.organization_id,
                    PositionAssignment.position_id == position_id,
                    PositionAssignment.start_date <= today,
                    (
                        PositionAssignment.end_date.is_(None)
                        | (PositionAssignment.end_date >= today)
                    ),
                )
                .limit(1)
            )
        )
