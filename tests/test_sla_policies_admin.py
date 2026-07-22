import uuid
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql
from starlette.datastructures import FormData
from starlette.requests import Request

from app.models.help.models import ArticleStatus, HelpArticleOverride
from app.services.file_upload import (
    InvalidMagicBytesError,
    get_sla_policy_document_upload,
)
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


def test_document_upload_uses_s3_uuid_name_magic_validation_and_checksum():
    service = get_sla_policy_document_upload()
    storage = MagicMock()
    pdf = b"%PDF-1.7\npolicy"

    assert service.config.allowed_content_types == {
        "application/pdf",
        "image/jpeg",
        "image/png",
    }
    assert service.config.allowed_extensions == {".pdf", ".jpg", ".jpeg", ".png"}
    assert service.config.max_size_bytes == 10 * 1024 * 1024
    assert service.config.require_magic_bytes is True
    assert service.config.compute_checksum is True

    with patch.object(service, "_get_storage", return_value=storage):
        result = service.save(
            pdf,
            content_type="application/pdf",
            subdirs=("org-id", "article-id"),
            original_filename="Policy.pdf",
        )

    assert re.fullmatch(
        r"sla_policies/org-id/article-id/[0-9a-f]{12}\.pdf", result.s3_key
    )
    assert result.file_size == len(pdf)
    assert result.checksum == hashlib.sha256(pdf).hexdigest()
    storage.upload.assert_called_once_with(result.s3_key, pdf, "application/pdf")

    with pytest.raises(InvalidMagicBytesError):
        service.validate("application/pdf", "fake.pdf", 8, b"not-pdf")


def test_document_input_restricts_extension_mime_and_empty_files():
    form = FormData({"title": "Uploaded policy", "summary": "Summary"})

    document = SLAPolicyAdminService.build_document_input(
        form,
        file_name=r"C:\\fakepath\\Policy.pdf",
        file_content_type="application/pdf",
        file_data=b"%PDF-1.7",
    )
    assert document.file_name == "Policy.pdf"

    with pytest.raises(SLAPolicyValidationError, match="PDF, JPEG, or PNG"):
        SLAPolicyAdminService.build_document_input(
            form,
            file_name="Policy.docx",
            file_content_type=(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            ),
            file_data=b"PK\x03\x04",
        )
    with pytest.raises(SLAPolicyValidationError, match="matches its file type"):
        SLAPolicyAdminService.build_document_input(
            form,
            file_name="Policy.pdf",
            file_content_type="image/png",
            file_data=b"%PDF-1.7",
        )
    with pytest.raises(SLAPolicyValidationError, match="empty"):
        SLAPolicyAdminService.build_document_input(
            form,
            file_name="Policy.pdf",
            file_content_type="application/pdf",
            file_data=b"",
        )


def test_create_document_persists_only_approved_metadata_as_a_draft():
    organization_id = uuid.uuid4()
    db = MagicMock()
    upload_service = MagicMock()
    upload_service.save.return_value = SimpleNamespace(
        s3_key="sla_policies/org/article/abc123.pdf",
        file_size=12,
        checksum="a" * 64,
    )
    service = SLAPolicyAdminService(db)
    document_input = service.build_document_input(
        FormData({"title": "Uploaded policy", "summary": "Summary"}),
        file_name="Policy.pdf",
        file_content_type="application/pdf",
        file_data=b"%PDF-1.7\n",
    )

    with patch(
        "app.services.sla_policies_admin_web.get_sla_policy_document_upload",
        return_value=upload_service,
    ):
        policy = service.create_document(organization_id, document_input)

    assert policy.organization_id == organization_id
    assert policy.module_key == "sla_policies"
    assert policy.content_type == "sla_policy"
    assert policy.status is ArticleStatus.DRAFT
    assert policy.body_json is None
    assert policy.file_path == "sla_policies/org/article/abc123.pdf"
    assert policy.file_name == "Policy.pdf"
    assert policy.file_content_type == "application/pdf"
    assert policy.file_size_bytes == 12
    assert policy.content_hash == "a" * 64
    upload_service.save.assert_called_once()
    assert upload_service.save.call_args.kwargs["subdirs"][0] == str(organization_id)
    assert uuid.UUID(upload_service.save.call_args.kwargs["subdirs"][1])
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
    upload_template = (
        REPO_ROOT / "templates/admin/sla_policies/upload.html"
    ).read_text()

    assert list_template.count('method="POST"') == list_template.count(
        "request.state.csrf_form | safe"
    )
    assert form_template.count('method="POST"') == form_template.count(
        "request.state.csrf_form | safe"
    )
    assert "form_data.sections | tojson | safe" not in form_template
    assert "policy.title | safe" not in list_template
    assert 'enctype="multipart/form-data"' in upload_template
    assert "request.state.csrf_form | safe" in upload_template
    assert "application/pdf,image/jpeg,image/png" in upload_template
    assert "max_size_mb=10" in upload_template


def test_help_article_override_has_only_the_approved_nullable_file_columns():
    expected = {
        "file_path",
        "file_name",
        "file_content_type",
        "file_size_bytes",
        "content_hash",
    }
    table = HelpArticleOverride.__table__

    assert expected.issubset(table.columns.keys())
    assert all(table.columns[name].nullable for name in expected)

    migration = (
        REPO_ROOT / "alembic/versions/20260722_add_sla_policy_document_columns.py"
    ).read_text()
    assert migration.count('op.add_column(\n        "help_article_override"') == 5
    assert (
        'down_revision: str | tuple[str, ...] = "20260721_extended_info_changes"'
        in migration
    )


def test_admin_sidebar_places_sla_policies_under_service_management():
    sidebar = (REPO_ROOT / "templates/admin/base_admin.html").read_text()

    service_management_position = sidebar.index(">Service Management</p>")
    sla_link_position = sidebar.index("<!-- SLA Policies -->")
    system_position = sidebar.index(">System</p>")
    sla_link = sidebar[sla_link_position:system_position]

    assert service_management_position < sla_link_position < system_position
    assert "url_for('admin_sla_policy_list')" in sla_link
    assert "SLA Policies</span>" in sla_link
    assert "flex items-center gap-3 px-3 py-2.5 rounded-xl" in sla_link
