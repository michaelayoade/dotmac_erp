import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from starlette.requests import Request
from starlette.responses import StreamingResponse

from app.models.help.models import ArticleStatus
from app.services.sla_policies_web import (
    SLAPolicyDocumentNotFoundError,
    SLAPolicyDocumentStream,
    SLAPolicyReadService,
)
from app.templates import templates
from app.web.deps import get_db, require_web_auth
from app.web.sla_policies import router, sla_policies_page, sla_policy_document_view

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

    document_route = next(
        route for route in router.routes if route.name == "sla_policy_document_view"
    )
    document_dependencies = {
        dependency.call for dependency in document_route.dependant.dependencies
    }
    assert document_dependencies == {require_web_auth, get_db}


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
    template = (REPO_ROOT / "templates/base.html").read_text(encoding="utf-8")
    authenticated_menu = template.split(
        "{% if user and user.is_authenticated %}", maxsplit=1
    )[1].split("{% else %}", maxsplit=1)[0]
    before_first_inner_condition = authenticated_menu.split(
        "{% if user.is_admin %}", maxsplit=1
    )[0]

    assert 'href="/sla-policies"' in before_first_inner_condition
    assert "SLA Policies" in before_first_inner_condition


def test_page_template_has_safe_read_only_empty_state():
    template = (REPO_ROOT / "templates/sla_policies/index.html").read_text(
        encoding="utf-8"
    )

    assert "No SLA policies published yet" in template
    assert "policy.body_json" in template
    assert "| safe" not in template
    assert "<form" not in template
    assert "<input" not in template
    assert "download" not in template.lower()


def test_document_lookup_is_strictly_scoped_and_streams_from_s3():
    organization_id = uuid.uuid4()
    article_id = uuid.uuid4()
    key = f"sla_policies/{organization_id}/{article_id}/abc123.pdf"
    policy = SimpleNamespace(
        article_id=article_id,
        organization_id=organization_id,
        module_key="sla_policies",
        content_type="sla_policy",
        status=ArticleStatus.PUBLISHED,
        file_path=key,
        file_name="Policy.pdf",
        file_content_type="application/pdf",
    )
    db = MagicMock()
    db.scalar.return_value = policy
    storage = MagicMock()
    storage.exists.return_value = True
    storage.stream.return_value = (iter([b"%PDF"]), "text/plain", 4)

    with patch("app.services.sla_policies_web.get_storage", return_value=storage):
        document = SLAPolicyReadService(db).get_published_document_for_org(
            organization_id, article_id
        )

    stmt = db.scalar.call_args.args[0]
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
    assert f"help_article_override.article_id = '{article_id}'" in sql
    assert len(stmt._where_criteria) == 5
    assert document.content_type == "application/pdf"
    assert document.content_length == 4
    storage.exists.assert_called_once_with(key)
    storage.stream.assert_called_once_with(key)


def test_document_lookup_rejects_untrusted_storage_key():
    organization_id = uuid.uuid4()
    article_id = uuid.uuid4()
    db = MagicMock()
    db.scalar.return_value = SimpleNamespace(
        file_path="sla_policies/another-org/article/file.pdf",
        file_name="Policy.pdf",
        file_content_type="application/pdf",
    )

    with pytest.raises(SLAPolicyDocumentNotFoundError):
        SLAPolicyReadService(db).get_published_document_for_org(
            organization_id, article_id
        )


def test_document_route_is_inline_and_never_an_attachment(monkeypatch):
    organization_id = uuid.uuid4()
    article_id = uuid.uuid4()
    document = SLAPolicyDocumentStream(
        chunks=iter([b"%PDF"]),
        content_type="application/pdf",
        content_length=4,
        file_name='Policy "July".pdf',
    )

    class FakeReadService:
        def __init__(self, db):
            self.db = db

        def get_published_document_for_org(self, org_id, requested_article_id):
            assert org_id == organization_id
            assert requested_article_id == article_id
            return document

    request = _request()
    auth = SimpleNamespace(organization_id=organization_id)
    monkeypatch.setattr("app.web.sla_policies.SLAPolicyReadService", FakeReadService)

    response = sla_policy_document_view(request, article_id, auth, MagicMock())

    assert isinstance(response, StreamingResponse)
    assert response.media_type == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline;")
    assert "attachment" not in response.headers["content-disposition"]
    assert 'filename="Policy _July_.pdf"' in response.headers["content-disposition"]
    assert response.headers["content-length"] == "4"
    assert request.state.allow_sla_document_frame is True


def test_missing_document_does_not_set_frame_exception(monkeypatch):
    class FakeReadService:
        def __init__(self, db):
            self.db = db

        def get_published_document_for_org(self, org_id, article_id):
            raise SLAPolicyDocumentNotFoundError("not found")

    request = _request()
    monkeypatch.setattr("app.web.sla_policies.SLAPolicyReadService", FakeReadService)

    with pytest.raises(HTTPException) as exc:
        sla_policy_document_view(
            request,
            uuid.uuid4(),
            SimpleNamespace(organization_id=uuid.uuid4()),
            MagicMock(),
        )

    assert exc.value.status_code == 404
    assert not hasattr(request.state, "allow_sla_document_frame")


def test_public_template_embeds_documents_without_a_download_action():
    article_id = uuid.uuid4()
    common = {
        "article_id": article_id,
        "title": "Uploaded policy",
        "summary": "Summary",
        "body_json": None,
        "file_path": "sla_policies/key",
    }
    template = templates.env.get_template("sla_policies/index.html")
    request = _request()
    request.scope["router"] = router
    pdf_html = template.render(
        request=request,
        policies=[SimpleNamespace(**common, file_content_type="application/pdf")],
        user=None,
        page_title="SLA Policies",
    )
    image_html = template.render(
        request=request,
        policies=[SimpleNamespace(**common, file_content_type="image/png")],
        user=None,
        page_title="SLA Policies",
    )

    assert f'/sla-policies/{article_id}/document"' in pdf_html
    assert "<iframe" in pdf_html
    assert "<img" in image_html
    assert "download" not in pdf_html.lower()
    assert "download" not in image_html.lower()


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
