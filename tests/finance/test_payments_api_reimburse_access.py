"""Authorization tiers on the expense-reimbursement API.

``require_expense_reimburse_access`` used to guard all five reimburse routes
with one intersection over an admit list that contained ``payments:read``
alongside the execute permissions, so a read-only principal could reach
``POST /transfers/{intent_id}/initiate`` and move money. It is now three
guards: lookup, prepare, execute.

These are the guard-level tests. The route-level proof that a read-only
principal is refused BEFORE ``PaymentService.initiate_expense_transfer`` is
reached lives in ``tests/api/test_payments_payout_authorization.py``.
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.finance.payments import (
    EXPENSE_PAYOUT_EXECUTE_PERMISSION,
    EXPENSE_PAYOUT_PREPARE_PERMISSIONS,
    PAYMENTS_LOOKUP_PERMISSIONS,
    require_expense_payout_execute_access,
    require_expense_payout_prepare_access,
    require_payments_lookup_access,
)
from app.services.finance.platform.authorization import AuthorizationService


def _auth(scopes: list[str], roles: list[str] | None = None) -> dict:
    return {
        "roles": roles or [],
        "scopes": scopes,
        "person_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
    }


def _deny_db_permissions(monkeypatch: pytest.MonkeyPatch) -> None:
    """No permission is held in the database, only what the token carries."""
    monkeypatch.setattr(
        AuthorizationService,
        "check_any_permission",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        AuthorizationService,
        "check_permission",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "app.api.finance.payments.has_live_admin_grant",
        lambda *_args, **_kwargs: False,
    )


# ---------------------------------------------------------------------------
# The admit sets themselves
# ---------------------------------------------------------------------------


def test_read_permission_is_confined_to_the_lookup_tier() -> None:
    """``payments:read`` may appear on the lookup tier and nowhere else.

    This is the defect stated as an invariant. On the unfixed parent both
    the prepare set and the execute permission were the same list, and this
    assertion fails on the second and third clauses.
    """
    assert "payments:read" in PAYMENTS_LOOKUP_PERMISSIONS
    assert "payments:read" not in EXPENSE_PAYOUT_PREPARE_PERMISSIONS
    assert EXPENSE_PAYOUT_EXECUTE_PERMISSION != "payments:read"


def test_prepare_tier_is_the_historic_set_minus_the_read_permission() -> None:
    """Only ``payments:read`` was removed — nobody else lost access."""
    assert set(EXPENSE_PAYOUT_PREPARE_PERMISSIONS) == set(
        PAYMENTS_LOOKUP_PERMISSIONS
    ) - {"payments:read"}


def test_each_guard_declares_the_set_it_admits() -> None:
    """The architecture gate reads authorization off these declarations."""
    assert require_payments_lookup_access.authorized_permissions == frozenset(
        PAYMENTS_LOOKUP_PERMISSIONS
    )
    assert require_expense_payout_prepare_access.authorized_permissions == frozenset(
        EXPENSE_PAYOUT_PREPARE_PERMISSIONS
    )
    assert require_expense_payout_execute_access.authorized_permissions == frozenset(
        {EXPENSE_PAYOUT_EXECUTE_PERMISSION}
    )


# ---------------------------------------------------------------------------
# Lookup tier — behaviour preserved
# ---------------------------------------------------------------------------


def test_lookup_allows_the_read_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    _deny_db_permissions(monkeypatch)
    auth = _auth(["payments:read"])
    assert require_payments_lookup_access(auth=auth, db=MagicMock()) is auth


def test_lookup_allows_expense_reimburse_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_db_permissions(monkeypatch)
    auth = _auth(["expense:claims:reimburse"])
    assert require_payments_lookup_access(auth=auth, db=MagicMock()) is auth


def test_lookup_db_fallback_checks_reimburse_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, list[str]] = {}

    def _check_any_permission(db, pid, permissions, org_id):  # noqa: ANN001
        seen["permissions"] = permissions
        return True

    monkeypatch.setattr(
        AuthorizationService, "check_any_permission", _check_any_permission
    )

    auth = _auth([])
    assert require_payments_lookup_access(auth=auth, db=MagicMock()) is auth
    assert "expense:claims:reimburse" in seen["permissions"]


def test_lookup_denies_without_any_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_db_permissions(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        require_payments_lookup_access(auth=_auth([]), db=MagicMock())

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Forbidden"


# ---------------------------------------------------------------------------
# Prepare tier — the read permission no longer reaches a mutation
# ---------------------------------------------------------------------------


def test_prepare_denies_a_read_only_principal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the unfixed parent this returned ``auth``; now it is a 403."""
    _deny_db_permissions(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        require_expense_payout_prepare_access(
            auth=_auth(["payments:read"]), db=MagicMock()
        )

    assert excinfo.value.status_code == 403


def test_prepare_still_admits_the_reimburse_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny_db_permissions(monkeypatch)
    auth = _auth(["expense:claims:reimburse"])
    assert require_expense_payout_prepare_access(auth=auth, db=MagicMock()) is auth


# ---------------------------------------------------------------------------
# Execute tier — exactly one permission
# ---------------------------------------------------------------------------


def test_execute_admits_the_exact_transfer_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: the guard is not simply refusing everything."""
    _deny_db_permissions(monkeypatch)
    auth = _auth(["payments:transfer:initiate"])
    assert require_expense_payout_execute_access(auth=auth, db=MagicMock()) is auth


def test_execute_denies_the_read_permission(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE defect. On the unfixed parent this returned ``auth`` and the
    request went on to perform a real Paystack ``POST /transfer``."""
    _deny_db_permissions(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        require_expense_payout_execute_access(
            auth=_auth(["payments:read"]), db=MagicMock()
        )

    assert excinfo.value.status_code == 403


@pytest.mark.parametrize(
    "scope",
    [
        "payments:transfer",  # prefix of the required permission
        "payments:transfer:initiate:review",  # the required permission is a prefix
        "payments",  # ancestor namespace
        "payments:expense:initialize",  # a sibling execute permission
        "expense:claims:reimburse",  # admitted on the other two tiers
        "expense:claims:approve:tier3",  # highest approval tier
        "finance:access",  # blanket module scope
    ],
)
def test_execute_is_an_exact_match_not_a_prefix_or_a_blanket_scope(
    monkeypatch: pytest.MonkeyPatch, scope: str
) -> None:
    """Neither a prefix, an extension, a sibling, nor a module scope opens the
    payout. ``finance:access`` short-circuited on the unfixed parent."""
    _deny_db_permissions(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        require_expense_payout_execute_access(auth=_auth([scope]), db=MagicMock())

    assert excinfo.value.status_code == 403


def test_execute_ignores_the_admin_role_claim_on_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A token's ``roles`` claim is a login-time snapshot.

    A revoked administrator's un-expired token still asserts ``admin``; on the
    payout path that is a removed admin who can still move money. The guard
    re-asks the live grant tables instead, so a token claim alone is refused.
    """
    _deny_db_permissions(monkeypatch)

    with pytest.raises(HTTPException) as excinfo:
        require_expense_payout_execute_access(
            auth=_auth([], roles=["admin"]), db=MagicMock()
        )

    assert excinfo.value.status_code == 403


def test_execute_admits_a_live_admin_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    """The admin path is preserved — it is just re-asked of the grant tables."""
    _deny_db_permissions(monkeypatch)
    monkeypatch.setattr(
        "app.api.finance.payments.has_live_admin_grant",
        lambda *_args, **_kwargs: True,
    )

    auth = _auth([], roles=["admin"])
    assert require_expense_payout_execute_access(auth=auth, db=MagicMock()) is auth


def test_execute_db_fallback_asks_for_the_single_transfer_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The DB fallback is ``check_permission`` on one key, not
    ``check_any_permission`` over a list."""
    seen: dict[str, str] = {}

    def _check_permission(db, pid, key, org_id):  # noqa: ANN001
        seen["key"] = key
        return True

    monkeypatch.setattr(AuthorizationService, "check_permission", _check_permission)
    monkeypatch.setattr(
        AuthorizationService,
        "check_any_permission",
        lambda *_a, **_k: pytest.fail(
            "the execute guard must not authorize over a permission list"
        ),
    )

    auth = _auth([])
    assert require_expense_payout_execute_access(auth=auth, db=MagicMock()) is auth
    assert seen["key"] == "payments:transfer:initiate"
