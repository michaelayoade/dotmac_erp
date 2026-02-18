from uuid import UUID

from app.web.deps import WebAuthContext


def test_hr_manager_has_people_module_access():
    auth = WebAuthContext(
        is_authenticated=True,
        person_id=UUID("00000000-0000-0000-0000-000000000002"),
        organization_id=UUID("00000000-0000-0000-0000-000000000001"),
        roles=["hr_manager"],
        scopes=[],
    )
    assert auth.has_module_access("people") is True
    assert auth.has_module_access("hr") is True  # alias
