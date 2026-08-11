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
