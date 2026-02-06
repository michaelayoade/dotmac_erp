import uuid

from starlette.requests import Request
from starlette.responses import Response

from app.schemas.rbac import PermissionCreate, PersonRoleCreate, RoleCreate
from app.services import audit as audit_service
from app.services import rbac as rbac_service
from app.services import scheduler as scheduler_service


def test_rbac_role_permission_link(db_session, person):
    role = rbac_service.roles.create(
        db_session, RoleCreate(name=f"test_role_{uuid.uuid4().hex[:8]}")
    )
    permission_key = f"people:read:{uuid.uuid4().hex[:8]}"
    permission = rbac_service.permissions.create(
        db_session, PermissionCreate(key=permission_key, description="People Read")
    )
    link = rbac_service.person_roles.create(
        db_session, PersonRoleCreate(person_id=person.id, role_id=role.id)
    )
    assert link.person_id == person.id
    assert permission.key == permission_key


def test_audit_log_request(db_session):
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/test",
        "headers": [],
        "client": ("127.0.0.1", 12345),
    }
    request = Request(scope)
    response = Response(status_code=200)
    audit_service.audit_events.log_request(db_session, request, response)
    events = audit_service.audit_events.list(
        db_session,
        actor_id=None,
        actor_type=None,
        action="POST",
        entity_type="/test",
        request_id=None,
        is_success=True,
        status_code=200,
        is_active=None,
        order_by="occurred_at",
        order_dir="desc",
        limit=5,
        offset=0,
    )
    assert len(events) == 1


def test_scheduler_refresh_response():
    result = scheduler_service.refresh_schedule()
    assert "detail" in result
