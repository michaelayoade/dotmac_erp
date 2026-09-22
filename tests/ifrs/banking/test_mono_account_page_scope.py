"""Mono page settings must use the authenticated tenant, not ambient scope."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call
from uuid import UUID, uuid4

import pytest

from app.models.domain_settings import SettingDomain
from app.services.finance.banking.web_parts import accounts
from app.web.deps import WebAuthContext


@pytest.mark.parametrize(
    "organization_id",
    [
        UUID("00000000-0000-0000-0000-000000000011"),
        UUID("00000000-0000-0000-0000-000000000022"),
    ],
)
@pytest.mark.parametrize(
    ("enabled", "public_key", "has_person"),
    [(True, "public-test-key", True), (True, None, False), (False, None, False)],
)
def test_mono_page_uses_explicit_authenticated_scope(
    monkeypatch, organization_id, enabled, public_key, has_person
):
    db = MagicMock()
    # A stale ambient scope must not decide which tenant's settings to request.
    db.info = {"organization_id": uuid4()}
    auth = WebAuthContext(
        is_authenticated=True,
        organization_id=organization_id,
        person_id=uuid4() if has_person else None,
    )
    request = MagicMock()
    account_id = str(uuid4())
    service = accounts.BankingAccountWebService()
    detail = MagicMock(return_value={"account": None, "transactions": []})
    monkeypatch.setattr(service, "account_detail_context", detail)
    monkeypatch.setattr(accounts, "base_context", lambda *args, **kwargs: {})
    render = MagicMock(side_effect=lambda request, name, context: context)
    monkeypatch.setattr(accounts, "templates", SimpleNamespace(TemplateResponse=render))
    db.get.return_value = SimpleNamespace(email="employee@example.test")

    def resolve(db_arg, domain, key, *, organization_id):
        assert db_arg is db
        assert domain == SettingDomain.banking
        assert organization_id == auth.organization_id
        return enabled if key == "mono_enabled" else public_key

    resolver = MagicMock(side_effect=resolve)
    monkeypatch.setattr("app.services.settings_spec.resolve_value", resolver)

    context = service.account_detail_response(request, auth, db, account_id)

    expected_calls = [
        call(
            db,
            SettingDomain.banking,
            "mono_enabled",
            organization_id=organization_id,
        )
    ]
    if enabled:
        expected_calls.append(
            call(
                db,
                SettingDomain.banking,
                "mono_public_key",
                organization_id=organization_id,
            )
        )
        assert context["mono_public_key"] == (public_key or "")
        assert context["mono_user_email"] == (
            "employee@example.test" if has_person else ""
        )
    else:
        assert "mono_public_key" not in context
        assert "mono_user_email" not in context

    assert resolver.call_args_list == expected_calls
    assert context["mono_enabled"] is enabled
    detail.assert_called_once_with(db, str(organization_id), account_id)
    render.assert_called_once_with(
        request, "finance/banking/account_detail.html", context
    )
    db.commit.assert_not_called()
    db.flush.assert_not_called()
    if not has_person:
        db.get.assert_not_called()
