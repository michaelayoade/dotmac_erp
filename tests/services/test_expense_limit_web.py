from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.datastructures import FormData

from app.services.expense.limit_web import ExpenseLimitWebService


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_get_approver_scope_id_prefers_employee_scope_id():
    form = FormData(
        [
            ("scope_type", "EMPLOYEE"),
            ("scope_id", "employee-uuid"),
            ("scope_id", ""),
        ]
    )

    scope_id = ExpenseLimitWebService._get_approver_scope_id(form, "EMPLOYEE")

    assert scope_id == "employee-uuid"


def test_get_approver_scope_id_uses_scope_option_id_for_non_employee():
    form = FormData(
        [
            ("scope_type", "GRADE"),
            ("scope_id", ""),
            ("scope_option_id", "grade-uuid"),
        ]
    )

    scope_id = ExpenseLimitWebService._get_approver_scope_id(form, "GRADE")

    assert scope_id == "grade-uuid"


@pytest.mark.asyncio
async def test_create_validation_response_keeps_new_form_action(monkeypatch):
    service = ExpenseLimitWebService()
    request = SimpleNamespace(
        state=SimpleNamespace(
            csrf_form=FormData(
                [
                    ("scope_type", ""),
                    ("max_approval_amount", "25000"),
                    ("weekly_approval_budget", "100000"),
                    ("is_active", "1"),
                ]
            )
        )
    )
    auth = SimpleNamespace(organization_id=uuid4())
    captured = {}

    monkeypatch.setattr(service, "_get_scope_options", lambda _db, _org_id: {})
    monkeypatch.setattr(
        "app.services.expense.limit_web.base_context",
        lambda *_args, **_kwargs: {},
    )

    def capture_template_response(_request, template_name, context):
        captured["template_name"] = template_name
        captured["context"] = context
        return SimpleNamespace()

    monkeypatch.setattr(
        "app.services.expense.limit_web.templates.TemplateResponse",
        capture_template_response,
    )

    await service.create_approver_limit_response(request, auth, SimpleNamespace())

    context = captured["context"]

    assert captured["template_name"] == "expense/limits/approver_form.html"
    assert context["errors"] == {"scope_type": "Required"}
    assert context["approver_limit"]["max_approval_amount"] == "25000"
    assert context["is_edit"] is False
    assert context["form_action"] == "/expense/limits/approvers/new"


def test_edit_approver_limit_form_action_includes_record_id():
    approver_limit_id = uuid4()

    context = ExpenseLimitWebService._approver_limit_form_context(
        approver_limit={"approver_limit_id": approver_limit_id},
        approver_limit_id=approver_limit_id,
        scope_options={},
        errors={},
    )

    assert context["is_edit"] is True
    assert context["form_action"] == (
        f"/expense/limits/approvers/{approver_limit_id}/edit"
    )


def test_approver_limit_template_uses_explicit_form_mode_and_action():
    template = (REPO_ROOT / "templates/expense/limits/approver_form.html").read_text(
        encoding="utf-8"
    )

    assert 'action="{{ form_action }}"' in template
    assert 'if is_edit else "New Approver Limit"' in template
    assert "approver_limit.approver_limit_id }}/edit" not in template
