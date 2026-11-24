from uuid import UUID

import pytest
from fastapi import HTTPException

from app.web.deps import (
    WebAuthContext,
    can_retry_hr_provisioning,
    can_view_hr_provisioning,
    require_hr_provisioning_retry,
    require_hr_provisioning_view,
)

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.mark.parametrize(
    ("role", "can_view", "can_retry"),
    [
        ("admin", True, True),
        ("hr_director", True, True),
        ("hr_manager", True, True),
        ("hr_officer", True, False),
        ("hr_assistant", True, False),
        ("hr_viewer", True, False),
        ("payroll_manager", False, False),
        ("auditor", False, False),
        (None, False, False),
    ],
)
def test_hr_provisioning_role_gate(
    role: str | None, can_view: bool, can_retry: bool
) -> None:
    auth = WebAuthContext(
        is_authenticated=True,
        organization_id=ORG_ID,
        roles=[role] if role else [],
        scopes=["hr:access"],
    )
    auth.has_module_access = lambda module: module == "people"

    assert can_view_hr_provisioning(auth) is can_view
    assert can_retry_hr_provisioning(auth) is can_retry

    if can_view:
        assert require_hr_provisioning_view(auth) is auth
    else:
        with pytest.raises(
            HTTPException, match="HR provisioning access required"
        ) as exc:
            require_hr_provisioning_view(auth)
        assert exc.value.status_code == 403

    if can_retry:
        assert require_hr_provisioning_retry(auth) is auth
    else:
        with pytest.raises(
            HTTPException, match="HR provisioning retry access required"
        ) as exc:
            require_hr_provisioning_retry(auth)
        assert exc.value.status_code == 403


def test_provisioning_gate_requires_people_module_access() -> None:
    auth = WebAuthContext(
        is_authenticated=True,
        organization_id=ORG_ID,
        roles=["admin"],
    )
    auth.has_module_access = lambda _module: False

    assert can_view_hr_provisioning(auth) is False
    assert can_retry_hr_provisioning(auth) is False
