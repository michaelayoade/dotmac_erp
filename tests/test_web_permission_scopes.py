import uuid
from datetime import UTC, datetime, timedelta

from starlette.requests import Request

from app.models.auth import Session as AuthSession
from app.models.auth import SessionStatus
from app.models.people.hr.employee import Employee
from app.models.person import Person
from app.models.rbac import Permission, PersonRole, Role, RolePermission
from app.services.auth_flow import _issue_access_token
from app.web.deps import optional_web_auth, require_web_auth


def _ensure_employee_table(engine) -> None:
    for column in Employee.__table__.columns:
        default = column.server_default
        if default is None:
            continue
        default_text = str(getattr(default, "arg", default)).lower()
        if "gen_random_uuid" in default_text or "uuid_generate" in default_text:
            column.server_default = None
    Employee.__table__.create(engine, checkfirst=True)


def test_require_web_auth_loads_db_backed_permission_scopes(db_session):
    org_id = uuid.uuid4()
    person_id = uuid.uuid4()
    role_id = uuid.uuid4()
    permission_id = uuid.uuid4()
    session_id = uuid.uuid4()

    person = Person(
        id=person_id,
        organization_id=org_id,
        first_name="Finance",
        last_name="Manager",
        email="finance.manager@example.com",
    )
    role = Role(id=role_id, name="finance_manager", is_active=True)
    permission = Permission(
        id=permission_id,
        key="ap:invoices:read",
        description="Read AP invoices",
        is_active=True,
    )
    db_session.add_all(
        [
            person,
            role,
            permission,
            PersonRole(person_id=person_id, role_id=role_id),
            RolePermission(role_id=role_id, permission_id=permission_id),
            AuthSession(
                id=session_id,
                person_id=person_id,
                status=SessionStatus.active,
                token_hash=f"unused-for-access-token-validation-{session_id}",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                last_seen_at=None,
            ),
        ]
    )
    db_session.commit()

    access_token = _issue_access_token(
        db_session,
        str(person_id),
        str(session_id),
        roles=["finance_manager"],
        permissions=["finance:access"],
    )
    request = Request({"type": "http", "method": "GET", "path": "/finance/ap/invoices"})

    auth = require_web_auth(
        request=request,
        authorization=f"Bearer {access_token}",
        db=db_session,
    )

    assert "finance:access" in auth.scopes
    assert "ap:invoices:read" in auth.scopes
    assert auth.has_permission("ap:invoices:read") is True


def test_optional_web_auth_loads_db_backed_permission_scopes(db_session):
    _ensure_employee_table(db_session.bind)
    org_id = uuid.uuid4()
    person_id = uuid.uuid4()
    role_id = uuid.uuid4()
    permission_id = uuid.uuid4()
    session_id = uuid.uuid4()

    person = Person(
        id=person_id,
        organization_id=org_id,
        first_name="Ops",
        last_name="User",
        email="ops.user@example.com",
    )
    role = Role(id=role_id, name="ops_manager", is_active=True)
    permission = Permission(
        id=permission_id,
        key="expense:claims:reimburse",
        description="Reimburse expense claims",
        is_active=True,
    )
    db_session.add_all(
        [
            person,
            role,
            permission,
            PersonRole(person_id=person_id, role_id=role_id),
            RolePermission(role_id=role_id, permission_id=permission_id),
            AuthSession(
                id=session_id,
                person_id=person_id,
                status=SessionStatus.active,
                token_hash=f"unused-for-access-token-validation-{session_id}",
                expires_at=datetime.now(UTC) + timedelta(hours=1),
                last_seen_at=None,
            ),
        ]
    )
    db_session.commit()

    access_token = _issue_access_token(
        db_session,
        str(person_id),
        str(session_id),
        roles=["ops_manager"],
        permissions=["finance:access"],
    )
    request = Request({"type": "http", "method": "GET", "path": "/finance/payments"})

    auth = optional_web_auth(
        request=request,
        authorization=f"Bearer {access_token}",
        db=db_session,
    )

    assert auth.is_authenticated is True
    assert "expense:claims:reimburse" in auth.scopes
    assert auth.has_permission("expense:claims:reimburse") is True
