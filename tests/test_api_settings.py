from app.models.domain_settings import DomainSetting, SettingDomain


class TestAuthSettingsAPI:
    """Tests for the /settings/auth endpoints."""

    def test_list_auth_settings(self, client, auth_headers):
        """Test listing auth settings."""
        response = client.get("/settings/auth", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_auth_settings_with_pagination(self, client, auth_headers):
        """Test listing auth settings with pagination."""
        response = client.get(
            "/settings/auth?limit=10&offset=0", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 10

    def test_list_auth_settings_unauthorized(self, client):
        """Test listing auth settings without auth."""
        response = client.get("/settings/auth", follow_redirects=False)
        # Returns 302 redirect to login when unauthorized
        assert response.status_code in [401, 302]

    def test_get_auth_setting(self, client, auth_headers, db_session):
        """Test getting a specific auth setting."""
        response = client.get("/settings/auth/jwt_algorithm", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "jwt_algorithm"
        assert data["value_text"] == "HS256"

    def test_get_auth_setting_not_found(self, client, auth_headers):
        """Test getting a non-existent auth setting."""
        response = client.get("/settings/auth/nonexistent_key", headers=auth_headers)
        assert response.status_code == 400

    def test_upsert_auth_setting_create(self, client, auth_headers):
        """Test creating an auth setting via upsert."""
        key = "jwt_access_ttl_minutes"
        payload = {"value_text": "45"}
        response = client.put(f"/settings/auth/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key
        assert data["value_text"] == "45"

    def test_upsert_auth_setting_update(self, client, auth_headers, db_session):
        """Test updating an auth setting via upsert."""
        payload = {"value_text": "strict"}
        response = client.put(
            "/settings/auth/refresh_cookie_samesite",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["value_text"] == "strict"

    def test_upsert_auth_setting_with_json(self, client, auth_headers):
        """Test creating an auth setting with JSON value."""
        key = "refresh_cookie_secure"
        payload = {"value_json": True}
        response = client.put(f"/settings/auth/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["value_json"] is True


class TestAuditSettingsAPI:
    """Tests for the /settings/audit endpoints."""

    def test_list_audit_settings(self, client, auth_headers):
        """Test listing audit settings."""
        response = client.get("/settings/audit", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_audit_settings_with_pagination(self, client, auth_headers):
        """Test listing audit settings with pagination."""
        response = client.get(
            "/settings/audit?limit=10&offset=0", headers=auth_headers
        )
        assert response.status_code == 200

    def test_get_audit_setting(self, client, auth_headers, db_session):
        """Test getting a specific audit setting."""
        response = client.get("/settings/audit/enabled", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "enabled"

    def test_get_audit_setting_not_found(self, client, auth_headers):
        """Test getting a non-existent audit setting."""
        response = client.get("/settings/audit/nonexistent_key", headers=auth_headers)
        assert response.status_code == 400

    def test_upsert_audit_setting(self, client, auth_headers):
        """Test creating an audit setting via upsert."""
        key = "methods"
        payload = {"value_json": ["POST", "GET"]}
        response = client.put(f"/settings/audit/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key


class TestSchedulerSettingsAPI:
    """Tests for the /settings/scheduler endpoints."""

    def test_list_scheduler_settings(self, client, auth_headers):
        """Test listing scheduler settings."""
        response = client.get("/settings/scheduler", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_scheduler_settings_with_pagination(self, client, auth_headers):
        """Test listing scheduler settings with pagination."""
        response = client.get(
            "/settings/scheduler?limit=10&offset=0", headers=auth_headers
        )
        assert response.status_code == 200

    def test_get_scheduler_setting(self, client, auth_headers, db_session):
        """Test getting a specific scheduler setting."""
        response = client.get("/settings/scheduler/timezone", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "timezone"

    def test_get_scheduler_setting_not_found(self, client, auth_headers):
        """Test getting a non-existent scheduler setting."""
        response = client.get(
            "/settings/scheduler/nonexistent_key", headers=auth_headers
        )
        assert response.status_code == 400

    def test_upsert_scheduler_setting(self, client, auth_headers):
        """Test creating a scheduler setting via upsert."""
        key = "beat_refresh_seconds"
        payload = {"value_text": "45"}
        response = client.put(
            f"/settings/scheduler/{key}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key


class TestSettingsAPIV1:
    """Tests for the /api/v1/settings endpoints."""

    def test_list_auth_settings_v1(self, client, auth_headers):
        """Test listing auth settings via v1 API."""
        response = client.get("/api/v1/settings/auth", headers=auth_headers)
        assert response.status_code == 200

    def test_list_audit_settings_v1(self, client, auth_headers):
        """Test listing audit settings via v1 API."""
        response = client.get("/api/v1/settings/audit", headers=auth_headers)
        assert response.status_code == 200

    def test_list_scheduler_settings_v1(self, client, auth_headers):
        """Test listing scheduler settings via v1 API."""
        response = client.get("/api/v1/settings/scheduler", headers=auth_headers)
        assert response.status_code == 200

    def test_upsert_auth_setting_v1(self, client, auth_headers):
        """Test upserting an auth setting via v1 API."""
        key = "jwt_refresh_ttl_days"
        payload = {"value_text": "10"}
        response = client.put(
            f"/api/v1/settings/auth/{key}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200


class TestSettingsFilters:
    """Tests for settings filters and ordering."""

    def test_list_settings_filter_by_active(self, client, auth_headers, db_session):
        """Test filtering settings by is_active."""
        setting = (
            db_session.query(DomainSetting)
            .filter(DomainSetting.domain == SettingDomain.auth)
            .first()
        )
        assert setting is not None
        setting.is_active = False
        db_session.commit()

        response = client.get("/settings/auth?is_active=true", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        for item in data["items"]:
            assert item["is_active"] is True

        response = client.get("/settings/auth?is_active=false", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert any(item["is_active"] is False for item in data["items"])

    def test_list_settings_with_ordering(self, client, auth_headers):
        """Test listing settings with custom ordering."""
        response = client.get(
            "/settings/auth?order_by=key&order_dir=asc", headers=auth_headers
        )
        assert response.status_code == 200
