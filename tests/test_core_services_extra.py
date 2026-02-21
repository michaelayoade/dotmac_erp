import uuid

from starlette.requests import Request
from starlette.responses import Response

from app.schemas.rbac import PermissionCreate, PersonRoleCreate, RoleCreate
from app.services import audit as audit_service
from app.services import rbac as rbac_service
from app.services import scheduler as scheduler_service
from app.services.admin.web import admin_web_service


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


def test_admin_audit_logs_context_resolves_actor_name(db_session, person):
    event = audit_service.AuditEvent(
        actor_type=audit_service.AuditActorType.user,
        organization_id=person.organization_id,
        actor_person_id=person.id,
        actor_id=str(person.id),
        action="POST",
        entity_type="/test",
        entity_id=None,
        status_code=200,
        is_success=True,
        is_active=True,
        ip_address="127.0.0.1",
        user_agent="pytest",
        request_id="req-1",
        metadata_={"path": "/test", "query": {}},
    )
    db_session.add(event)
    db_session.commit()

    context = admin_web_service.audit_logs_context(
        db=db_session,
        organization_id=person.organization_id,
        search="",
        actor_type="",
        status="",
        page=1,
    )
    matching = [
        event for event in context["events"] if event.get("actor_id") == str(person.id)
    ]
    assert matching
    assert matching[0]["actor_name"] == person.name
    assert matching[0]["actor_type_label"] == "User"
    assert matching[0]["action_label"] == "Create (POST)"
    assert matching[0]["entity_label"] == "Test"
    assert matching[0]["request_summary"] == "POST /test"
