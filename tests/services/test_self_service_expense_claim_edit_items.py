from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

from app.models.people.exp import ExpenseClaimStatus
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.position import Position
from app.models.people.hr.position_assignment import (
    PositionAssignment,
    PositionAssignmentType,
)
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services.people.self_service_web import SelfServiceWebService


def _ensure_employee_table(engine) -> None:
    tables = (
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


def _make_auth():
    auth = MagicMock()
    auth.organization_id = "00000000-0000-0000-0000-000000000001"
    auth.person_id = "00000000-0000-0000-0000-000000000002"
    return auth


def test_expense_claim_update_supports_existing_new_and_removed_items():
    svc = SelfServiceWebService()
    auth = _make_auth()
    db = MagicMock()
    org_id = UUID("00000000-0000-0000-0000-000000000001")
    claim_id = uuid4()
    existing_item_id = uuid4()
    removed_item_id = uuid4()
    requested_approver_id = uuid4()
    category_id = uuid4()

    claim = MagicMock()
    claim.employee_id = "00000000-0000-0000-0000-000000000003"
    claim.status = ExpenseClaimStatus.DRAFT

    with (
        patch.object(
            svc, "_get_employee_id", return_value="00000000-0000-0000-0000-000000000003"
        ),
        patch.object(svc, "_validate_expense_approver_selection"),
        patch("app.services.people.self_service_web.ExpenseService") as expense_service,
    ):
        expense_service.return_value.get_claim.return_value = claim

        response = svc.expense_claim_update_response(
            auth,
            db,
            claim_id=claim_id,
            requested_approver_id=requested_approver_id,
            items=[
                {
                    "item_id": str(existing_item_id),
                    "expense_date": date(2026, 3, 1),
                    "category_id": str(category_id),
                    "description": "Updated hotel",
                    "claimed_amount": Decimal("120.00"),
                    "receipt_number": "R-1",
                    "receipt_url": "https://example.com/r1",
                },
                {
                    "expense_date": date(2026, 3, 2),
                    "category_id": str(category_id),
                    "description": "New taxi",
                    "claimed_amount": Decimal("45.50"),
                    "receipt_number": "R-2",
                    "receipt_url": "https://example.com/r2",
                },
                {
                    "item_id": str(removed_item_id),
                    "remove": True,
                },
            ],
        )

    assert response.status_code == 302
    expense_service.return_value.update_claim.assert_called_once()
    expense_service.return_value.update_claim_item.assert_called_once_with(
        org_id,
        claim_id=claim_id,
        item_id=existing_item_id,
        expense_date=date(2026, 3, 1),
        category_id=category_id,
        description="Updated hotel",
        claimed_amount=Decimal("120.00"),
        receipt_number="R-1",
        receipt_url="https://example.com/r1",
    )
    expense_service.return_value.add_claim_item.assert_called_once_with(
        org_id,
        claim_id=claim_id,
        expense_date=date(2026, 3, 2),
        category_id=category_id,
        description="New taxi",
        claimed_amount=Decimal("45.50"),
        receipt_number="R-2",
        receipt_url="https://example.com/r2",
    )
    expense_service.return_value.remove_claim_item.assert_called_once_with(
        org_id,
        claim_id=claim_id,
        item_id=removed_item_id,
    )
    db.commit.assert_called_once()


def _wire_manager_via_position(
    db_session,
    org_id: UUID,
    *,
    employee: Employee,
    manager: Employee,
) -> None:
    """Establish a position chain so OrgResolver.get_manager returns the manager."""
    manager_position = Position(
        position_id=uuid4(),
        organization_id=org_id,
        is_vacant=False,
    )
    employee_position = Position(
        position_id=uuid4(),
        organization_id=org_id,
        parent_position_id=manager_position.position_id,
        is_vacant=False,
    )
    db_session.add_all([manager_position, employee_position])
    db_session.flush()
    db_session.add_all(
        [
            PositionAssignment(
                position_assignment_id=uuid4(),
                organization_id=org_id,
                employee_id=manager.employee_id,
                position_id=manager_position.position_id,
                assignment_type=PositionAssignmentType.PRIMARY,
                start_date=date.today(),
            ),
            PositionAssignment(
                position_assignment_id=uuid4(),
                organization_id=org_id,
                employee_id=employee.employee_id,
                position_id=employee_position.position_id,
                assignment_type=PositionAssignmentType.PRIMARY,
                start_date=date.today(),
            ),
        ]
    )
    db_session.flush()


def _person(org_id: UUID, first_name: str, last_name: str) -> Person:
    return Person(
        id=uuid4(),
        organization_id=org_id,
        first_name=first_name,
        last_name=last_name,
        email=f"{first_name.lower()}-{uuid4().hex}@example.com",
    )


def _employee(
    org_id: UUID,
    person: Person,
    code: str,
    *,
    reports_to_id: UUID | None = None,
) -> Employee:
    return Employee(
        employee_id=uuid4(),
        organization_id=org_id,
        person_id=person.id,
        employee_code=code,
        date_of_joining=date.today(),
        reports_to_id=reports_to_id,
        status=EmployeeStatus.ACTIVE,
    )


def _expense_approver_role(db_session) -> Role:
    role = db_session.query(Role).filter(Role.name == "expense_approver").first()
    if not role:
        role = Role(id=uuid4(), name="expense_approver", is_active=True)
        db_session.add(role)
        db_session.flush()

    permission = (
        db_session.query(Permission)
        .filter(Permission.key == "expense:claims:approve:tier1")
        .first()
    )
    if not permission:
        permission = Permission(
            id=uuid4(),
            key="expense:claims:approve:tier1",
            is_active=True,
        )
        db_session.add(permission)
        db_session.flush()

    existing = (
        db_session.query(RolePermission)
        .filter(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id,
        )
        .first()
    )
    if not existing:
        db_session.add(
            RolePermission(
                id=uuid4(),
                role_id=role.id,
                permission_id=permission.id,
            )
        )
    db_session.flush()
    return role


def test_expense_approver_options_include_all_active_expense_approvers_and_default_report_to(
    db_session,
    engine,
):
    _ensure_employee_table(engine)
    org_id = uuid4()
    role = _expense_approver_role(db_session)

    requester_person = _person(org_id, "Request", "User")
    manager_person = _person(org_id, "Manager", "Approver")
    other_person = _person(org_id, "Other", "Approver")
    inactive_person = _person(org_id, "Inactive", "Approver")
    non_approver_person = _person(org_id, "Plain", "Manager")

    manager = _employee(org_id, manager_person, "MGR")
    other = _employee(org_id, other_person, "APR")
    inactive = _employee(org_id, inactive_person, "INA")
    inactive.status = EmployeeStatus.SUSPENDED
    non_approver = _employee(org_id, non_approver_person, "PLN")
    requester = _employee(
        org_id,
        requester_person,
        "REQ",
        reports_to_id=manager.employee_id,
    )

    db_session.add_all(
        [
            requester_person,
            manager_person,
            other_person,
            inactive_person,
            non_approver_person,
            requester,
            manager,
            other,
            inactive,
            non_approver,
            PersonRole(id=uuid4(), person_id=manager.person_id, role_id=role.id),
            PersonRole(id=uuid4(), person_id=other.person_id, role_id=role.id),
            PersonRole(id=uuid4(), person_id=inactive.person_id, role_id=role.id),
        ]
    )
    db_session.flush()
    _wire_manager_via_position(db_session, org_id, employee=requester, manager=manager)
    db_session.commit()

    options = SelfServiceWebService._get_expense_approver_options(
        db_session,
        org_id,
        employee_id=requester.employee_id,
    )

    option_ids = {option["id"] for option in options}
    assert option_ids == {str(manager.employee_id), str(other.employee_id)}
    selected_ids = {option["id"] for option in options if option["selected"]}
    assert selected_ids == {str(manager.employee_id)}


def test_expense_approver_options_leave_default_empty_when_report_to_is_not_expense_approver(
    db_session,
    engine,
):
    _ensure_employee_table(engine)
    org_id = uuid4()
    role = _expense_approver_role(db_session)

    requester_person = _person(org_id, "Request", "NoDefault")
    manager_person = _person(org_id, "Manager", "Plain")
    approver_person = _person(org_id, "Active", "Approver")

    manager = _employee(org_id, manager_person, "MGR-NO")
    approver = _employee(org_id, approver_person, "APR-YES")
    requester = _employee(
        org_id,
        requester_person,
        "REQ-NO",
        reports_to_id=manager.employee_id,
    )

    db_session.add_all(
        [
            requester_person,
            manager_person,
            approver_person,
            requester,
            manager,
            approver,
            PersonRole(id=uuid4(), person_id=approver.person_id, role_id=role.id),
        ]
    )
    db_session.commit()

    options = SelfServiceWebService._get_expense_approver_options(
        db_session,
        org_id,
        employee_id=requester.employee_id,
    )

    assert {option["id"] for option in options} == {str(approver.employee_id)}
    assert [option for option in options if option["selected"]] == []


def test_expense_approver_options_include_active_admins_without_expense_permission(
    db_session,
    engine,
):
    _ensure_employee_table(engine)
    org_id = uuid4()
    admin_role = db_session.query(Role).filter(Role.name == "admin").first()
    if not admin_role:
        admin_role = Role(id=uuid4(), name="admin", is_active=True)
        db_session.add(admin_role)
        db_session.flush()

    requester_person = _person(org_id, "Request", "AdminOption")
    admin_person = _person(org_id, "Admin", "Approver")
    requester = _employee(org_id, requester_person, "REQ-ADM")
    admin = _employee(org_id, admin_person, "ADM")

    db_session.add_all(
        [
            requester_person,
            admin_person,
            requester,
            admin,
            PersonRole(id=uuid4(), person_id=admin.person_id, role_id=admin_role.id),
        ]
    )
    db_session.commit()

    options = SelfServiceWebService._get_expense_approver_options(
        db_session,
        org_id,
        employee_id=requester.employee_id,
    )

    assert {option["id"] for option in options} == {str(admin.employee_id)}
    assert SelfServiceWebService._is_active_expense_approver(
        db_session,
        org_id,
        admin.employee_id,
    )
