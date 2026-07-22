import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlalchemy.dialects import postgresql
from starlette.requests import Request

from app.services.sla_policies_web import SLAPolicyReadService
from app.templates import templates
from app.web.deps import get_db, require_web_auth
from app.web.sla_policies import router, sla_policies_page

REPO_ROOT = Path(__file__).resolve().parents[1]


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/sla-policies",
            "headers": [],
        }
    )


def test_read_service_query_is_strictly_scoped():
    organization_id = uuid.uuid4()
    db = MagicMock()
    db.scalars.return_value.all.return_value = []

    SLAPolicyReadService(db).list_published_for_org(organization_id)

    stmt = db.scalars.call_args.args[0]
    sql = str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert f"help_article_override.organization_id = '{organization_id}'" in sql
    assert "help_article_override.module_key = 'sla_policies'" in sql
    assert "help_article_override.content_type = 'sla_policy'" in sql
    assert "help_article_override.status = 'PUBLISHED'" in sql
    assert len(stmt._where_criteria) == 4


def test_route_uses_authentication_and_database_dependencies_only():
    route_entry = next(
        route for route in router.routes if route.path == "/sla-policies"
    )
    dependency_calls = {
        dependency.call for dependency in route_entry.dependant.dependencies
    }

    assert dependency_calls == {require_web_auth, get_db}


def test_page_visibility_does_not_vary_between_authenticated_users(monkeypatch):
    organization_id = uuid.uuid4()
    policy = SimpleNamespace(title="Published SLA Policy")
    calls: list[uuid.UUID] = []
    rendered_contexts: list[dict] = []

    class FakeReadService:
        def __init__(self, db):
            self.db = db

        def list_published_for_org(self, org_id):
            calls.append(org_id)
            return [policy]

    def fake_template_response(request, template_name, context):
        rendered_contexts.append(context)
        return SimpleNamespace(template_name=template_name, context=context)

    monkeypatch.setattr("app.web.sla_policies.SLAPolicyReadService", FakeReadService)
    monkeypatch.setattr("app.web.sla_policies.base_context", lambda *args, **kwargs: {})
    monkeypatch.setattr(
        "app.web.sla_policies.templates.TemplateResponse", fake_template_response
    )

    standard_user = SimpleNamespace(
        organization_id=organization_id,
        person_id=uuid.uuid4(),
        is_admin=False,
    )
    admin_user = SimpleNamespace(
        organization_id=organization_id,
        person_id=uuid.uuid4(),
        is_admin=True,
    )

    standard_response = sla_policies_page(_request(), standard_user, MagicMock())
    admin_response = sla_policies_page(_request(), admin_user, MagicMock())

    assert calls == [organization_id, organization_id]
    assert standard_response.template_name == "sla_policies/index.html"
    assert admin_response.template_name == "sla_policies/index.html"
    assert rendered_contexts[0]["policies"] == rendered_contexts[1]["policies"]


def test_menu_link_is_unconditional_within_authenticated_menu():
    template = (REPO_ROOT / "templates/base.html").read_text()
    authenticated_menu = template.split(
        "{% if user and user.is_authenticated %}", maxsplit=1
    )[1].split("{% else %}", maxsplit=1)[0]
    before_first_inner_condition = authenticated_menu.split(
        "{% if user.is_admin %}", maxsplit=1
    )[0]

    assert 'href="/sla-policies"' in before_first_inner_condition
    assert "SLA Policies" in before_first_inner_condition


def test_page_template_has_safe_read_only_empty_state():
    template = (REPO_ROOT / "templates/sla_policies/index.html").read_text()

    assert "No SLA policies published yet" in template
    assert "policy.body_json" in template
    assert "| safe" not in template
    assert "<form" not in template
    assert "<input" not in template


def test_manage_link_is_visible_only_to_admin_users():
    template = templates.env.get_template("sla_policies/index.html")
    common_context = {
        "request": _request(),
        "policies": [],
        "page_title": "SLA Policies",
    }
    standard_html = template.render(
        **common_context,
        user=SimpleNamespace(
            is_authenticated=True,
            is_admin=False,
            name="Standard User",
            initials="SU",
        ),
    )
    admin_html = template.render(
        **common_context,
        user=SimpleNamespace(
            is_authenticated=True,
            is_admin=True,
            name="Admin User",
            initials="AU",
        ),
    )

    manage_link = 'href="/admin/sla-policies"'
    assert manage_link not in standard_html
    assert "Manage SLA Policies" not in standard_html
    assert manage_link in admin_html
    assert "Manage SLA Policies" in admin_html
