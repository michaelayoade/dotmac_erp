from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.authz.profile import EXPENSE_ROLE_GRANTS
from app.services.expense.limit_web import ExpenseLimitWebService


def test_reset_route_requires_dedicated_permission():
    import ast

    tree = ast.parse(
        (Path(__file__).parents[2] / "app/web/finance/exp_limits.py").read_text(
            encoding="utf-8"
        )
    )
    endpoint = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "reviewer_reset_approver_budget"
    )
    dependencies = [
        node.args[0].id
        for node in ast.walk(endpoint.args)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Depends"
    ]
    assert "_require_limits_reset" in dependencies
    assert "_require_limits_review" not in dependencies


def test_review_only_user_cannot_reset_even_through_service():
    auth = SimpleNamespace(
        has_permission=lambda permission: permission == "expense:limits:review"
    )
    db = MagicMock()
    with pytest.raises(HTTPException) as error:
        ExpenseLimitWebService().reviewer_reset_approver_budget_response(
            auth,
            db,
            uuid4(),
            {"reset_reason": "Emergency"},
        )
    assert error.value.status_code == 403
    db.add.assert_not_called()
    db.flush.assert_not_called()


def test_approvers_and_processors_do_not_inherit_reset_authority():
    for role in ("employee", "expense_approver", "expense_processor", "auditor"):
        assert "expense:limits:reset" not in EXPENSE_ROLE_GRANTS[role]
    assert "expense:limits:reset" in EXPENSE_ROLE_GRANTS["admin"]
    assert "expense:limits:reset" in EXPENSE_ROLE_GRANTS["expense_reviewer"]


def test_reset_form_is_conditioned_on_permission():
    template = (
        Path(__file__).parents[2]
        / "templates/expense/limits/reviewer_approver_detail.html"
    ).read_text(encoding="utf-8")
    assert "weekly_budget is not none and can_reset_budget" in template
