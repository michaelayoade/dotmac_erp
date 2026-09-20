import inspect

import pytest
from fastapi import HTTPException

from app.web.deps import WebAuthContext
from app.web.finance.exp import (
    _require_report_export,
    expense_by_category_export,
    expense_by_employee_export,
    expense_summary_export,
    expense_trends_export,
)


@pytest.mark.parametrize(
    "endpoint",
    [
        expense_summary_export,
        expense_by_category_export,
        expense_by_employee_export,
        expense_trends_export,
    ],
)
def test_report_export_routes_use_export_permission_guard(endpoint) -> None:
    auth_dependency = inspect.signature(endpoint).parameters["auth"].default
    assert auth_dependency.dependency is _require_report_export


def test_report_read_permission_does_not_grant_export() -> None:
    read_only_auth = WebAuthContext(
        is_authenticated=True,
        scopes=["expense:reports:read"],
    )

    with pytest.raises(HTTPException) as exc_info:
        _require_report_export(read_only_auth)

    assert exc_info.value.status_code == 403


def test_report_export_permission_grants_export() -> None:
    export_auth = WebAuthContext(
        is_authenticated=True,
        scopes=["expense:reports:export"],
    )

    assert _require_report_export(export_auth) is export_auth
