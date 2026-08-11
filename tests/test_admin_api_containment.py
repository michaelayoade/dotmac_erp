from sqlalchemy import delete

from app.models.rbac import PersonRole
from tests.conftest import _create_access_token


def _headers(person, auth_session, scope: str) -> dict[str, str]:
    token = _create_access_token(
        str(person.id),
        str(auth_session.id),
        scopes=[scope],
    )
    return {"Authorization": f"Bearer {token}"}


def test_tenant_auth_manager_cannot_access_global_credentials(
    client, person, auth_session
):
    response = client.get(
        "/api/v1/user-credentials",
        headers=_headers(person, auth_session, "auth:manage"),
    )

    assert response.status_code == 403


def test_tenant_settings_manager_cannot_access_global_settings(client, auth_headers):
    response = client.get("/settings/auth", headers=auth_headers)

    assert response.status_code == 403


def test_removed_admin_role_revokes_cross_tenant_access_immediately(
    client,
    db_session,
    admin_person,
    admin_headers,
):
    db_session.execute(
        delete(PersonRole).where(PersonRole.person_id == admin_person.id)
    )
    db_session.commit()

    response = client.get("/api/v1/user-credentials", headers=admin_headers)

    assert response.status_code == 403
