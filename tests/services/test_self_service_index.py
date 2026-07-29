import uuid
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request

from app.services.people.self_service_web import SelfServiceWebService
from app.web.deps import WebAuthContext

REPO_ROOT = Path(__file__).resolve().parents[2]


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/people/self"})


def _auth() -> WebAuthContext:
    return WebAuthContext(
        is_authenticated=True,
        person_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        roles=[],
        scopes=["self:access"],
    )


def test_index_response_allows_team_discipline_for_manager_reports(monkeypatch):
    employee_id = uuid.uuid4()
    captured: dict = {}

    monkeypatch.setattr(
        "app.services.people.self_service_web.base_context",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        SelfServiceWebService,
        "_get_employee_id",
        staticmethod(lambda db, org_id, person_id: employee_id),
    )
    monkeypatch.setattr(
        SelfServiceWebService,
        "_get_direct_reports",
        staticmethod(lambda db, org_id, manager_employee_id: [SimpleNamespace()]),
    )
    monkeypatch.setattr(
        SelfServiceWebService,
        "_has_team_approvals",
        staticmethod(lambda *args, **kwargs: False),
    )
    monkeypatch.setattr(
        SelfServiceWebService,
        "_has_team_expense_approvals",
        staticmethod(lambda *args, **kwargs: False),
    )

    def fake_template_response(request, template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return SimpleNamespace()

    monkeypatch.setattr(
        "app.services.people.self_service_web.templates.TemplateResponse",
        fake_template_response,
    )

    SelfServiceWebService().index_response(_request(), _auth(), SimpleNamespace())

    assert captured["template_name"] == "people/self/index.html"
    assert captured["context"]["can_team_discipline"] is True
    assert captured["context"]["can_team_leave"] is False
    assert captured["context"]["can_team_expenses"] is False


def test_self_service_index_template_uses_team_discipline_capability():
    template = (REPO_ROOT / "templates/people/self/index.html").read_text()

    assert "{% if can_team_discipline %}" in template
    assert "{% if 'discipline' in modules %}" not in template
