import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from starlette.datastructures import FormData
from starlette.requests import Request

from app.models.help.models import ArticleStatus
from app.services.sla_policies_admin_web import (
    SLAPolicyAdminService,
    SLAPolicyValidationError,
)
from app.services.sla_policies_web import SLAPolicyReadService
from app.templates import templates
from app.web.admin_sla_policies import router
from app.web.deps import WebAuthContext, require_admin_access

REPO_ROOT = Path(__file__).resolve().parents[1]


def _form(**overrides: str) -> FormData:
    values = [
        ("title", "Business Connectivity SLA"),
        ("summary", "Service targets for business connections."),
        ("section_title", "Availability"),
        ("section_body", "Monthly uptime target."),
        ("section_items", "Target: 99.9%\nMeasurement: monthly"),
    ]
    overridden = set(overrides)
    values = [(key, value) for key, value in values if key not in overridden]
    values.extend(overrides.items())
    return FormData(values)


def _compiled(statement) -> str:
    return str(
        statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/sla-policies",
            "headers": [],
        }
    )


def test_admin_router_uses_the_existing_admin_dependency():
    assert len(router.dependencies) == 1
    assert router.dependencies[0].dependency is require_admin_access


def test_non_admin_is_denied_and_admin_is_allowed():
    non_admin = WebAuthContext(
        is_authenticated=True,
        organization_id=uuid.uuid4(),
        roles=["employee"],
    )
    with pytest.raises(HTTPException) as exc:
        require_admin_access(non_admin)
    assert exc.value.status_code == 403

    admin = WebAuthContext(
        is_authenticated=True,
        organization_id=uuid.uuid4(),
        roles=["admin"],
    )
    assert require_admin_access(admin) is admin


def test_admin_queries_are_tenant_and_classification_scoped():
    organization_id = uuid.uuid4()
    article_id = uuid.uuid4()
    db = MagicMock()
    db.scalars.return_value.all.return_value = []

    service = SLAPolicyAdminService(db)
    service.list_for_org(organization_id)
    list_statement = db.scalars.call_args.args[0]
    list_sql = _compiled(list_statement)
    assert f"help_article_override.organization_id = '{organization_id}'" in list_sql
    assert "help_article_override.module_key = 'sla_policies'" in list_sql
    assert "help_article_override.content_type = 'sla_policy'" in list_sql
    assert len(list_statement._where_criteria) == 3

    db.scalar.return_value = SimpleNamespace(article_id=article_id)
    service.get_for_org(organization_id, article_id)
    item_statement = db.scalar.call_args.args[0]
    item_sql = _compiled(item_statement)
    assert f"help_article_override.organization_id = '{organization_id}'" in item_sql
    assert "help_article_override.module_key = 'sla_policies'" in item_sql
    assert "help_article_override.content_type = 'sla_policy'" in item_sql
    assert f"help_article_override.article_id = '{article_id}'" in item_sql
    assert len(item_statement._where_criteria) == 4


def test_create_is_always_a_classified_draft_with_expected_body_shape():
    organization_id = uuid.uuid4()
    db = MagicMock()
    service = SLAPolicyAdminService(db)

    policy_input = service.build_policy_input(_form())
    policy = service.create(organization_id, policy_input)

    assert policy.organization_id == organization_id
    assert policy.module_key == "sla_policies"
    assert policy.content_type == "sla_policy"
    assert policy.status is ArticleStatus.DRAFT
    assert policy.owner_id is None
    assert policy.body_json == {
        "sections": [
            {
                "title": "Availability",
                "body": "Monthly uptime target.",
                "items": ["Target: 99.9%", "Measurement: monthly"],
            }
        ]
    }
    db.add.assert_called_once_with(policy)
    db.flush.assert_called_once_with()


def test_publish_and_archive_use_existing_status_values():
    organization_id = uuid.uuid4()
    article_id = uuid.uuid4()
    policy = SimpleNamespace(
        article_id=article_id,
        status=ArticleStatus.DRAFT,
        published_at=None,
    )
    db = MagicMock()
    db.scalar.return_value = policy
    service = SLAPolicyAdminService(db)

    service.publish(organization_id, article_id)
    assert policy.status is ArticleStatus.PUBLISHED
    assert policy.published_at is not None

    service.archive(organization_id, article_id)
    assert policy.status is ArticleStatus.ARCHIVED
    assert db.flush.call_count == 2


def test_draft_is_excluded_until_publish_by_public_query():
    organization_id = uuid.uuid4()
    db = MagicMock()
    db.scalars.return_value.all.return_value = []

    SLAPolicyReadService(db).list_published_for_org(organization_id)

    public_sql = _compiled(db.scalars.call_args.args[0])
    assert "help_article_override.status = 'PUBLISHED'" in public_sql
    assert "help_article_override.module_key = 'sla_policies'" in public_sql
    assert "help_article_override.content_type = 'sla_policy'" in public_sql


def test_server_validation_rejects_invalid_or_incomplete_sections():
    with pytest.raises(SLAPolicyValidationError, match="body text or"):
        SLAPolicyAdminService.build_policy_input(
            _form(section_body="", section_items="")
        )

    with pytest.raises(SLAPolicyValidationError, match="invalid characters"):
        SLAPolicyAdminService.build_policy_input(_form(title="Invalid\x00Title"))


def test_admin_input_is_autoescaped_on_public_page():
    attack = '<script>alert("xss")</script>'
    policy = SimpleNamespace(
        title=attack,
        summary=attack,
        body_json={"sections": [{"title": attack, "body": attack, "items": [attack]}]},
    )

    rendered = templates.env.get_template("sla_policies/index.html").render(
        request=_request(),
        policies=[policy],
        user=None,
        page_title="SLA Policies",
    )

    assert attack not in rendered
    assert "&lt;script&gt;alert" in rendered


def test_admin_templates_protect_posts_and_do_not_mark_content_safe():
    list_template = (REPO_ROOT / "templates/admin/sla_policies/index.html").read_text()
    form_template = (REPO_ROOT / "templates/admin/sla_policies/form.html").read_text()

    assert list_template.count('method="POST"') == list_template.count(
        "request.state.csrf_form | safe"
    )
    assert form_template.count('method="POST"') == form_template.count(
        "request.state.csrf_form | safe"
    )
    assert "form_data.sections | tojson | safe" not in form_template
    assert "policy.title | safe" not in list_template
