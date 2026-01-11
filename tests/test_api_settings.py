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

    def test_list_email_settings_v1(self, client, auth_headers):
        """Test listing email settings via v1 API."""
        response = client.get("/api/v1/settings/email", headers=auth_headers)
        assert response.status_code == 200

    def test_list_features_settings_v1(self, client, auth_headers):
        """Test listing features settings via v1 API."""
        response = client.get("/api/v1/settings/features", headers=auth_headers)
        assert response.status_code == 200

    def test_list_automation_settings_v1(self, client, auth_headers):
        """Test listing automation settings via v1 API."""
        response = client.get("/api/v1/settings/automation", headers=auth_headers)
        assert response.status_code == 200

    def test_list_reporting_settings_v1(self, client, auth_headers):
        """Test listing reporting settings via v1 API."""
        response = client.get("/api/v1/settings/reporting", headers=auth_headers)
        assert response.status_code == 200

    def test_upsert_email_setting_v1(self, client, auth_headers):
        """Test upserting an email setting via v1 API."""
        key = "smtp_from_name"
        payload = {"value_text": "Test App"}
        response = client.put(
            f"/api/v1/settings/email/{key}", json=payload, headers=auth_headers
        )
        assert response.status_code == 200

    def test_upsert_features_setting_v1(self, client, auth_headers):
        """Test upserting a features setting via v1 API."""
        key = "enable_leases"
        payload = {"value_json": True}
        response = client.put(
            f"/api/v1/settings/features/{key}", json=payload, headers=auth_headers
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


class TestEmailSettingsAPI:
    """Tests for the /settings/email endpoints."""

    def test_list_email_settings(self, client, auth_headers):
        """Test listing email settings."""
        response = client.get("/settings/email", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_email_settings_with_pagination(self, client, auth_headers):
        """Test listing email settings with pagination."""
        response = client.get(
            "/settings/email?limit=10&offset=0", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 10

    def test_list_email_settings_unauthorized(self, client):
        """Test listing email settings without auth."""
        response = client.get("/settings/email", follow_redirects=False)
        assert response.status_code in [401, 302]

    def test_get_email_setting(self, client, auth_headers):
        """Test getting a specific email setting."""
        # First create the setting
        client.put(
            "/settings/email/smtp_host",
            json={"value_text": "mail.example.com"},
            headers=auth_headers,
        )
        response = client.get("/settings/email/smtp_host", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "smtp_host"

    def test_get_email_setting_not_found(self, client, auth_headers):
        """Test getting a non-existent email setting."""
        response = client.get("/settings/email/nonexistent_key", headers=auth_headers)
        assert response.status_code == 400

    def test_upsert_email_setting(self, client, auth_headers):
        """Test creating an email setting via upsert."""
        key = "smtp_port"
        payload = {"value_text": "465"}
        response = client.put(f"/settings/email/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key
        assert data["value_text"] == "465"

    def test_upsert_email_setting_boolean(self, client, auth_headers):
        """Test upserting a boolean email setting."""
        key = "smtp_use_tls"
        payload = {"value_json": False}
        response = client.put(f"/settings/email/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key


class TestFeaturesSettingsAPI:
    """Tests for the /settings/features endpoints."""

    def test_list_features_settings(self, client, auth_headers):
        """Test listing features settings."""
        response = client.get("/settings/features", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_features_settings_with_pagination(self, client, auth_headers):
        """Test listing features settings with pagination."""
        response = client.get(
            "/settings/features?limit=10&offset=0", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 10

    def test_list_features_settings_unauthorized(self, client):
        """Test listing features settings without auth."""
        response = client.get("/settings/features", follow_redirects=False)
        assert response.status_code in [401, 302]

    def test_get_features_setting(self, client, auth_headers):
        """Test getting a specific features setting."""
        # First create the setting
        client.put(
            "/settings/features/enable_multi_currency",
            json={"value_json": True},
            headers=auth_headers,
        )
        response = client.get("/settings/features/enable_multi_currency", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "enable_multi_currency"

    def test_get_features_setting_not_found(self, client, auth_headers):
        """Test getting a non-existent features setting."""
        response = client.get("/settings/features/nonexistent_key", headers=auth_headers)
        assert response.status_code == 400

    def test_upsert_features_setting(self, client, auth_headers):
        """Test creating a features setting via upsert."""
        key = "enable_budgeting"
        payload = {"value_json": True}
        response = client.put(f"/settings/features/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key

    def test_upsert_features_setting_disable(self, client, auth_headers):
        """Test disabling a feature flag."""
        key = "enable_inventory"
        payload = {"value_json": False}
        response = client.put(f"/settings/features/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key


class TestAutomationSettingsAPI:
    """Tests for the /settings/automation endpoints."""

    def test_list_automation_settings(self, client, auth_headers):
        """Test listing automation settings."""
        response = client.get("/settings/automation", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_automation_settings_with_pagination(self, client, auth_headers):
        """Test listing automation settings with pagination."""
        response = client.get(
            "/settings/automation?limit=10&offset=0", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 10

    def test_list_automation_settings_unauthorized(self, client):
        """Test listing automation settings without auth."""
        response = client.get("/settings/automation", follow_redirects=False)
        assert response.status_code in [401, 302]

    def test_get_automation_setting(self, client, auth_headers):
        """Test getting a specific automation setting."""
        # First create the setting
        client.put(
            "/settings/automation/recurring_default_frequency",
            json={"value_text": "MONTHLY"},
            headers=auth_headers,
        )
        response = client.get("/settings/automation/recurring_default_frequency", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "recurring_default_frequency"

    def test_get_automation_setting_not_found(self, client, auth_headers):
        """Test getting a non-existent automation setting."""
        response = client.get("/settings/automation/nonexistent_key", headers=auth_headers)
        assert response.status_code == 400

    def test_upsert_automation_setting_integer(self, client, auth_headers):
        """Test creating an integer automation setting via upsert."""
        key = "recurring_max_occurrences"
        payload = {"value_text": "500"}
        response = client.put(f"/settings/automation/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key
        assert data["value_text"] == "500"

    def test_upsert_automation_setting_allowed_value(self, client, auth_headers):
        """Test upserting an automation setting with allowed values."""
        key = "recurring_default_frequency"
        payload = {"value_text": "WEEKLY"}
        response = client.put(f"/settings/automation/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key

    def test_upsert_automation_setting_invalid_value(self, client, auth_headers):
        """Test upserting an automation setting with invalid allowed value."""
        key = "recurring_default_frequency"
        payload = {"value_text": "INVALID"}
        response = client.put(f"/settings/automation/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 400


class TestReportingSettingsAPI:
    """Tests for the /settings/reporting endpoints."""

    def test_list_reporting_settings(self, client, auth_headers):
        """Test listing reporting settings."""
        response = client.get("/settings/reporting", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "count" in data

    def test_list_reporting_settings_with_pagination(self, client, auth_headers):
        """Test listing reporting settings with pagination."""
        response = client.get(
            "/settings/reporting?limit=10&offset=0", headers=auth_headers
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["items"]) <= 10

    def test_list_reporting_settings_unauthorized(self, client):
        """Test listing reporting settings without auth."""
        response = client.get("/settings/reporting", follow_redirects=False)
        assert response.status_code in [401, 302]

    def test_get_reporting_setting(self, client, auth_headers):
        """Test getting a specific reporting setting."""
        # First create the setting
        client.put(
            "/settings/reporting/default_export_format",
            json={"value_text": "PDF"},
            headers=auth_headers,
        )
        response = client.get("/settings/reporting/default_export_format", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == "default_export_format"

    def test_get_reporting_setting_not_found(self, client, auth_headers):
        """Test getting a non-existent reporting setting."""
        response = client.get("/settings/reporting/nonexistent_key", headers=auth_headers)
        assert response.status_code == 400

    def test_upsert_reporting_setting(self, client, auth_headers):
        """Test creating a reporting setting via upsert."""
        key = "report_page_size"
        payload = {"value_text": "LETTER"}
        response = client.put(f"/settings/reporting/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key

    def test_upsert_reporting_setting_boolean(self, client, auth_headers):
        """Test upserting a boolean reporting setting."""
        key = "include_logo_in_reports"
        payload = {"value_json": False}
        response = client.put(f"/settings/reporting/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert data["key"] == key

    def test_upsert_reporting_setting_invalid_allowed(self, client, auth_headers):
        """Test upserting a reporting setting with invalid allowed value."""
        key = "report_orientation"
        payload = {"value_text": "DIAGONAL"}
        response = client.put(f"/settings/reporting/{key}", json=payload, headers=auth_headers)
        assert response.status_code == 400
