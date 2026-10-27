from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

from sqlalchemy import select

from app.models.auth import AuthProvider, UserCredential
from app.models.rbac import PersonRole, Role
from app.services.admin import web as admin_web_module
from app.services.admin.web import AdminWebService
from app.services.auth_flow import verify_password
from app.services.people.hr import EmployeeService
from app.services.people.hr import employees as employee_module
from app.services.people.hr.invite_attachment import (
    set_default_invite_attachment_metadata,
)
from app.services.people.hr.invite_email import set_employee_invite_email_template


def _ensure_employee_role(db_session):
    role = db_session.scalar(select(Role).where(Role.name == "employee"))
    if role is None:
        role = Role(
            name="employee", description="Default employee role", is_active=True
        )
        db_session.add(role)
        db_session.commit()
        db_session.refresh(role)
    return role


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
    role = _ensure_employee_role(db_session)

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


def test_ensure_local_user_credentials_for_employee_provisions_login_defaults(
    db_session, person, monkeypatch
):
    service = EmployeeService(db_session, person.organization_id)
    monkeypatch.setattr(
        service,
        "get_employee",
        lambda _employee_id: SimpleNamespace(person_id=person.id),
    )

    credential = service.ensure_local_user_credentials_for_employee(uuid4())

    stored = db_session.scalar(
        select(UserCredential).where(
            UserCredential.id == credential.id,
        )
    )

    assert stored is not None
    assert stored.username == person.email.lower()
    assert verify_password("Dotmac@123", stored.password_hash)
    assert stored.must_change_password is True


def test_send_employee_access_invite_uses_password_reset_flow(
    db_session, person, monkeypatch
):
    captured: dict[str, object] = {}

    def _fake_send_password_reset_email(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        employee_module,
        "send_password_reset_email",
        _fake_send_password_reset_email,
    )

    service = EmployeeService(db_session, person.organization_id)
    set_employee_invite_email_template(
        db_session,
        person.organization_id,
        subject="Welcome to Dotmac",
        body_html="<p>Hello {name}</p><p>{reset_link}</p>",
        body_text="Hello {name}: {reset_link}",
    )
    db_session.commit()
    monkeypatch.setattr(
        service,
        "get_employee",
        lambda _employee_id: SimpleNamespace(person_id=person.id),
    )
    service.ensure_local_user_credentials_for_employee(uuid4())

    result = service.send_employee_access_invite(
        uuid4(),
        app_url="https://erp.example.com",
        attachments=[("welcome.pdf", b"welcome", "application/pdf")],
    )

    assert result.sent is True
    assert result.recipient_email == person.email
    assert result.recipient_kind == "work"
    assert result.attempted_recipients == (person.email,)
    assert captured["to_email"] == person.email
    assert captured["person_name"] == (person.display_name or person.first_name)
    assert captured["app_url"] == "https://erp.example.com"
    assert captured["organization_id"] == person.organization_id
    assert captured["next_url"] == "/people/self/tax-info"
    assert captured["attachments"] == [("welcome.pdf", b"welcome", "application/pdf")]
    assert captured["email_template"] == {
        "subject": "Welcome to Dotmac",
        "body_html": "<p>Hello {name}</p><p>{reset_link}</p>",
        "body_text": "Hello {name}: {reset_link}",
    }
    assert isinstance(captured["reset_token"], str)
    assert captured["reset_token"]


def test_send_employee_access_invite_sends_work_email_before_personal_email(
    db_session, person, monkeypatch
):
    captured: list[dict[str, object]] = []

    def _fake_send_password_reset_email(**kwargs):
        captured.append(kwargs)
        return True

    monkeypatch.setattr(
        employee_module,
        "send_password_reset_email",
        _fake_send_password_reset_email,
    )

    service = EmployeeService(db_session, person.organization_id)
    monkeypatch.setattr(
        service,
        "get_employee",
        lambda _employee_id: SimpleNamespace(
            person_id=person.id,
            personal_email="Personal.User@Example.com",
        ),
    )
    service.ensure_local_user_credentials_for_employee(uuid4())

    result = service.send_employee_access_invite(
        uuid4(),
        app_url="https://erp.example.com",
    )

    assert result.sent is True
    assert result.recipient_email == person.email
    assert result.recipient_kind == "work"
    assert result.attempted_recipients == (
        person.email,
        "personal.user@example.com",
    )
    assert [item["to_email"] for item in captured] == [
        person.email,
        "personal.user@example.com",
    ]
    assert all(item["reset_token"] for item in captured)


def test_send_employee_access_invite_attaches_default_welcome_pack(
    db_session, person, monkeypatch
):
    captured: dict[str, object] = {}

    set_default_invite_attachment_metadata(
        db_session,
        person.organization_id,
        {
            "s3_key": f"tenants/{person.organization_id}/files/{uuid4()}",
            "filename": "welcome.pdf",
            "content_type": "application/pdf",
            "file_size": 12,
        },
    )
    db_session.commit()

    readable = MagicMock()
    readable.__enter__.return_value = readable
    readable.__exit__.return_value = False
    readable.read.return_value = b"welcome pack"

    def _fake_send_password_reset_email(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        "app.services.storage.get_dotmac_files_provider",
        lambda: MagicMock(),
    )
    monkeypatch.setattr("dotmac_files.download_target", lambda *a, **k: MagicMock())
    monkeypatch.setattr("dotmac_files.open_object", lambda *a, **k: readable)
    monkeypatch.setattr(
        employee_module,
        "send_password_reset_email",
        _fake_send_password_reset_email,
    )

    service = EmployeeService(db_session, person.organization_id)
    monkeypatch.setattr(
        service,
        "get_employee",
        lambda _employee_id: SimpleNamespace(person_id=person.id),
    )
    service.ensure_local_user_credentials_for_employee(uuid4())

    result = service.send_employee_access_invite(
        uuid4(),
        app_url="https://erp.example.com",
    )

    assert result.sent is True
    assert captured["attachments"] == [
        ("welcome.pdf", b"welcome pack", "application/pdf")
    ]


def test_send_employee_access_invite_continues_when_default_attachment_missing(
    db_session, person, monkeypatch
):
    captured: dict[str, object] = {}

    set_default_invite_attachment_metadata(
        db_session,
        person.organization_id,
        {
            "s3_key": "hr_invites/org/missing.pdf",
            "filename": "welcome.pdf",
            "content_type": "application/pdf",
        },
    )
    db_session.commit()

    def _fake_send_password_reset_email(**kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(
        employee_module,
        "send_password_reset_email",
        _fake_send_password_reset_email,
    )

    service = EmployeeService(db_session, person.organization_id)
    monkeypatch.setattr(
        service,
        "get_employee",
        lambda _employee_id: SimpleNamespace(person_id=person.id),
    )
    service.ensure_local_user_credentials_for_employee(uuid4())

    result = service.send_employee_access_invite(
        uuid4(),
        app_url="https://erp.example.com",
    )

    assert result.sent is True
    assert captured["attachments"] is None
