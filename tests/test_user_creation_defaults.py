from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select

from app.models.auth import AuthProvider, UserCredential
from app.models.rbac import PersonRole, Role
from app.services.admin import web as admin_web_module
from app.services.admin.web import AdminWebService
from app.services.auth_flow import verify_password
from app.services.people.hr import EmployeeService


def test_admin_create_user_applies_default_username_password_and_reset_flag(
    db_session, monkeypatch
):
    monkeypatch.setattr(
        admin_web_module, "fire_audit_event", lambda *args, **kwargs: None
    )

    email = f"new.user.{uuid4().hex[:8]}@Example.COM"
    created, error = AdminWebService.create_user(
        db=db_session,
        first_name="New",
        last_name="User",
        email=email,
        username="",
        organization_id="00000000-0000-0000-0000-000000000001",
        password="",
        password_confirm="",
        must_change_password=False,
        role_ids=[],
    )

    assert error is None
    assert created is not None

    credential = db_session.scalar(
        select(UserCredential).where(
            UserCredential.person_id == created.id,
            UserCredential.provider == AuthProvider.local,
        )
    )
    assert credential is not None
    assert credential.username == email.lower()
    assert verify_password("Dotmac@123", credential.password_hash)
    assert credential.must_change_password is True


def test_employee_credential_creation_applies_defaults(db_session, person, monkeypatch):
    service = EmployeeService(db_session, person.organization_id)
    monkeypatch.setattr(
        service,
        "get_employee",
        lambda _employee_id: SimpleNamespace(person_id=person.id),
    )

    credential = service.create_user_credentials_for_employee(
        employee_id=uuid4(),
        username=None,
        password=None,
        must_change_password=False,
    )

    assert credential.username == person.email.lower()
    assert verify_password("Dotmac@123", credential.password_hash)
    assert credential.must_change_password is True


def test_create_employee_assigns_default_employee_role(db_session, person):
    role = Role(name="employee", description="Default employee role", is_active=True)
    db_session.add(role)
    db_session.commit()
    db_session.refresh(role)

    service = EmployeeService(db_session, person.organization_id)
    service._ensure_default_employee_role(person.id)
    db_session.commit()

    assignments = list(
        db_session.scalars(
            select(PersonRole).where(
                PersonRole.person_id == person.id,
                PersonRole.role_id == role.id,
            )
        ).all()
    )

    assert len(assignments) == 1

    # Idempotent: re-applying should not create duplicate person-role links.
    service._ensure_default_employee_role(person.id)
    db_session.commit()
    assignments_after_second_call = list(
        db_session.scalars(
            select(PersonRole).where(
                PersonRole.person_id == person.id,
                PersonRole.role_id == role.id,
            )
        ).all()
    )
    assert len(assignments_after_second_call) == 1
