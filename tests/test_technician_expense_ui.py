from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.web.deps import (
    WebAuthContext,
    require_expense_access,
    require_self_service_access,
    require_self_service_expense_approver,
    require_self_service_expense_ui_access,
)
from app.web.people.self_service import router as self_service_router
from scripts.seed_rbac import DEFAULT_ROLES, ROLE_PERMISSIONS


def _auth(*, roles: list[str]) -> WebAuthContext:
    return WebAuthContext(
        is_authenticated=True,
        person_id=uuid4(),
        organization_id=uuid4(),
        roles=roles,
        scopes=ROLE_PERMISSIONS["technician"],
    )


def test_technician_role_keeps_exact_employee_permissions() -> None:
    role_names = {name for name, _description in DEFAULT_ROLES}

    assert "technician" in role_names
    assert ROLE_PERMISSIONS["technician"] == ROLE_PERMISSIONS["employee"]
    assert "expense:access" in ROLE_PERMISSIONS["technician"]
    assert "expense:claims:create" in ROLE_PERMISSIONS["technician"]


def test_technician_keeps_self_service_but_loses_expense_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.is_module_enabled", lambda _module: True)
    auth = _auth(roles=["Technician"])

    assert require_self_service_access(auth) is auth
    assert "self_service" in auth.accessible_modules
    assert "expense" not in auth.accessible_modules


def test_technician_is_denied_all_erp_expense_pages_even_with_admin_role() -> None:
    auth = _auth(roles=["technician", "admin"])

    with pytest.raises(HTTPException) as module_error:
        require_expense_access(auth)
    assert module_error.value.status_code == 403

    with pytest.raises(HTTPException) as self_service_error:
        require_self_service_expense_ui_access(auth)
    assert self_service_error.value.status_code == 403

    with pytest.raises(HTTPException) as approval_error:
        require_self_service_expense_approver(auth)
    assert approval_error.value.status_code == 403


def test_employee_still_has_erp_expense_ui_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.main.is_module_enabled", lambda _module: True)
    auth = _auth(roles=["employee"])

    assert require_expense_access(auth) is auth
    assert require_self_service_expense_ui_access(auth) is auth


def test_every_personal_expense_route_uses_technician_ui_guard() -> None:
    expected_routes = {
        ("GET", "/self/expenses"),
        ("POST", "/self/expenses/claims"),
        ("GET", "/self/expenses/claims/{claim_id}/edit"),
        ("POST", "/self/expenses/claims/{claim_id}/edit"),
        ("POST", "/self/expenses/claims/{claim_id}/submit"),
        ("POST", "/self/expenses/claims/{claim_id}/delete"),
    }
    protected_routes = set()

    for route in self_service_router.routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        if require_self_service_expense_ui_access not in dependencies:
            continue
        protected_routes.update(
            (method, route.path)
            for method in route.methods
            if method in {"GET", "POST"}
        )

    assert protected_routes == expected_routes


def test_self_service_expense_card_is_hidden_for_technicians() -> None:
    template = (
        Path(__file__).parents[1] / "templates" / "people" / "self" / "index.html"
    ).read_text(encoding="utf-8")
    guard = "{% if not auth.is_technician %}"
    expense_link = 'href="/people/self/expenses"'

    assert guard in template
    assert template.index(guard) < template.index(expense_link)
