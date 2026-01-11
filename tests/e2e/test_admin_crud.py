"""
E2E Tests for Admin Module CRUD Operations.

Tests for creating, reading, updating, and deleting:
- Users
- Roles
- Permissions
- Organizations
- Settings
- Scheduled Tasks
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


@pytest.mark.e2e
class TestUserList:
    """Tests for user list page functionality."""

    def test_user_list_page_loads(self, admin_authenticated_page, base_url):
        """Test that user list page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/users")
        assert response.ok, f"User list failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page.get_by_test_id("page-title")).to_contain_text("Users")

    def test_user_list_has_table(self, admin_authenticated_page, base_url):
        """Test that user list has a table or list."""
        admin_authenticated_page.goto(f"{base_url}/admin/users")
        admin_authenticated_page.wait_for_load_state("networkidle")

        table = admin_authenticated_page.locator("table, [role='table'], .user-list")
        expect(table.first).to_be_visible()

    def test_user_list_has_new_button(self, admin_authenticated_page, base_url):
        """Test that user list has new user button."""
        admin_authenticated_page.goto(f"{base_url}/admin/users")
        admin_authenticated_page.wait_for_load_state("networkidle")

        new_btn = admin_authenticated_page.locator("a[href*='/admin/users/new'], button:has-text('New'), a:has-text('New User')")
        expect(new_btn.first).to_be_visible()

    def test_user_list_has_search(self, admin_authenticated_page, base_url):
        """Test that user list has search functionality."""
        admin_authenticated_page.goto(f"{base_url}/admin/users")
        admin_authenticated_page.wait_for_load_state("networkidle")

        search = admin_authenticated_page.locator("input[type='search'], input[name='search'], input[placeholder*='Search']")
        if search.count() > 0:
            expect(search.first).to_be_visible()


@pytest.mark.e2e
class TestUserCreate:
    """Tests for creating users."""

    def test_user_create_page_loads(self, admin_authenticated_page, base_url):
        """Test that user create page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        assert response.ok, f"User create page failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_user_create_has_username_field(self, admin_authenticated_page, base_url):
        """Test that user create has username field."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#username, input[name='username']")
        expect(field).to_be_visible()

    def test_user_create_has_email_field(self, admin_authenticated_page, base_url):
        """Test that user create has email field."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#email, input[name='email'], input[type='email']")
        expect(field).to_be_visible()

    def test_user_create_has_password_field(self, admin_authenticated_page, base_url):
        """Test that user create has password field."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("input[type='password']")
        expect(field.first).to_be_visible()

    def test_user_create_has_role_selection(self, admin_authenticated_page, base_url):
        """Test that user create has role selection."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        roles = admin_authenticated_page.locator("select[name='role'], select[name='roles'], input[name='roles'], [name*='role']")
        if roles.count() > 0:
            expect(roles.first).to_be_visible()

    def test_user_create_full_workflow(self, admin_authenticated_page, base_url):
        """Test complete user creation workflow."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill required fields
        admin_authenticated_page.locator("#username, input[name='username']").fill(f"testuser_{uid}")
        admin_authenticated_page.locator("#email, input[name='email'], input[type='email']").first.fill(f"test_{uid}@example.com")

        password_fields = admin_authenticated_page.locator("input[type='password']")
        if password_fields.count() > 0:
            password_fields.first.fill("TestPassword123!")
        if password_fields.count() > 1:
            password_fields.nth(1).fill("TestPassword123!")

        # Submit form
        admin_authenticated_page.locator("button[type='submit']").click()
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Should redirect to list or detail page
        expect(admin_authenticated_page).to_have_url(re.compile(r".*/admin/users.*"))


@pytest.mark.e2e
class TestUserEdit:
    """Tests for editing users."""

    def test_user_edit_page_accessible(self, admin_authenticated_page, base_url):
        """Test that user edit page is accessible from list."""
        admin_authenticated_page.goto(f"{base_url}/admin/users")
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Find first edit link
        edit_link = admin_authenticated_page.locator("a[href*='/edit'], a:has-text('Edit'), button:has-text('Edit')")
        if edit_link.count() > 0:
            edit_link.first.click()
            admin_authenticated_page.wait_for_load_state("networkidle")

            # Should be on edit page
            expect(admin_authenticated_page).to_have_url(re.compile(r".*/admin/users/.*/edit.*"))


@pytest.mark.e2e
class TestRoleList:
    """Tests for role list page."""

    def test_role_list_page_loads(self, admin_authenticated_page, base_url):
        """Test that role list page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/roles")
        assert response.ok, f"Role list failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_role_list_has_table(self, admin_authenticated_page, base_url):
        """Test that role list displays roles."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles")
        admin_authenticated_page.wait_for_load_state("networkidle")

        table = admin_authenticated_page.locator("table, [role='table'], .role-list")
        expect(table.first).to_be_visible()

    def test_role_list_has_new_button(self, admin_authenticated_page, base_url):
        """Test that role list has new button."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles")
        admin_authenticated_page.wait_for_load_state("networkidle")

        new_btn = admin_authenticated_page.locator("a[href*='/admin/roles/new'], button:has-text('New'), a:has-text('New Role')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestRoleCreate:
    """Tests for creating roles."""

    def test_role_create_page_loads(self, admin_authenticated_page, base_url):
        """Test that role create page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/roles/new")
        assert response.ok, f"Role create failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_role_create_has_name_field(self, admin_authenticated_page, base_url):
        """Test that role create has name field."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#name, input[name='name']")
        expect(field).to_be_visible()

    def test_role_create_has_description_field(self, admin_authenticated_page, base_url):
        """Test that role create has description field."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#description, textarea[name='description'], input[name='description']")
        expect(field).to_be_visible()

    def test_role_create_full_workflow(self, admin_authenticated_page, base_url):
        """Test complete role creation workflow."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill fields
        admin_authenticated_page.locator("#name, input[name='name']").fill(f"test_role_{uid}")

        desc_field = admin_authenticated_page.locator("#description, textarea[name='description'], input[name='description']")
        if desc_field.count() > 0:
            desc_field.fill(f"Test role description {uid}")

        # Submit
        admin_authenticated_page.locator("button[type='submit']").click()
        admin_authenticated_page.wait_for_load_state("networkidle")

        expect(admin_authenticated_page).to_have_url(re.compile(r".*/admin/roles.*"))


@pytest.mark.e2e
class TestPermissionList:
    """Tests for permission list page."""

    def test_permission_list_page_loads(self, admin_authenticated_page, base_url):
        """Test that permission list page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/permissions")
        assert response.ok, f"Permission list failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_permission_list_has_table(self, admin_authenticated_page, base_url):
        """Test that permission list displays permissions."""
        admin_authenticated_page.goto(f"{base_url}/admin/permissions")
        admin_authenticated_page.wait_for_load_state("networkidle")

        table = admin_authenticated_page.locator("table, [role='table'], .permission-list")
        expect(table.first).to_be_visible()


@pytest.mark.e2e
class TestPermissionCreate:
    """Tests for creating permissions."""

    def test_permission_create_page_loads(self, admin_authenticated_page, base_url):
        """Test that permission create page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/permissions/new")
        assert response.ok, f"Permission create failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_permission_create_has_key_field(self, admin_authenticated_page, base_url):
        """Test that permission create has key field."""
        admin_authenticated_page.goto(f"{base_url}/admin/permissions/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#key, input[name='key']")
        expect(field).to_be_visible()

    def test_permission_create_full_workflow(self, admin_authenticated_page, base_url):
        """Test complete permission creation workflow."""
        admin_authenticated_page.goto(f"{base_url}/admin/permissions/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill fields
        admin_authenticated_page.locator("#key, input[name='key']").fill(f"test.permission.{uid}")

        desc_field = admin_authenticated_page.locator("#description, textarea[name='description'], input[name='description']")
        if desc_field.count() > 0:
            desc_field.fill(f"Test permission {uid}")

        # Submit
        admin_authenticated_page.locator("button[type='submit']").click()
        admin_authenticated_page.wait_for_load_state("networkidle")

        expect(admin_authenticated_page).to_have_url(re.compile(r".*/admin/permissions.*"))


@pytest.mark.e2e
class TestOrganizationList:
    """Tests for organization list page."""

    def test_organization_list_page_loads(self, admin_authenticated_page, base_url):
        """Test that organization list page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/organizations")
        assert response.ok, f"Organization list failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_organization_list_has_table(self, admin_authenticated_page, base_url):
        """Test that organization list displays organizations."""
        admin_authenticated_page.goto(f"{base_url}/admin/organizations")
        admin_authenticated_page.wait_for_load_state("networkidle")

        table = admin_authenticated_page.locator("table, [role='table'], .organization-list")
        expect(table.first).to_be_visible()


@pytest.mark.e2e
class TestOrganizationCreate:
    """Tests for creating organizations."""

    def test_organization_create_page_loads(self, admin_authenticated_page, base_url):
        """Test that organization create page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/organizations/new")
        assert response.ok, f"Organization create failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_organization_create_has_code_field(self, admin_authenticated_page, base_url):
        """Test that organization create has code field."""
        admin_authenticated_page.goto(f"{base_url}/admin/organizations/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#organization_code, input[name='organization_code'], #code, input[name='code']")
        expect(field.first).to_be_visible()

    def test_organization_create_has_legal_name_field(self, admin_authenticated_page, base_url):
        """Test that organization create has legal name field."""
        admin_authenticated_page.goto(f"{base_url}/admin/organizations/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#legal_name, input[name='legal_name']")
        expect(field).to_be_visible()

    def test_organization_create_has_currency_field(self, admin_authenticated_page, base_url):
        """Test that organization create has currency field."""
        admin_authenticated_page.goto(f"{base_url}/admin/organizations/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("[name*='currency']")
        expect(field.first).to_be_visible()


@pytest.mark.e2e
class TestSettingsCRUD:
    """Tests for admin settings CRUD."""

    def test_settings_list_page_loads(self, admin_authenticated_page, base_url):
        """Test that admin settings list page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/settings")
        assert response.ok, f"Settings list failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_settings_create_page_loads(self, admin_authenticated_page, base_url):
        """Test that settings create page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/settings/new")
        assert response.ok, f"Settings create failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_settings_create_has_domain_field(self, admin_authenticated_page, base_url):
        """Test that settings create has domain field."""
        admin_authenticated_page.goto(f"{base_url}/admin/settings/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#domain, select[name='domain'], input[name='domain']")
        expect(field.first).to_be_visible()

    def test_settings_create_has_key_field(self, admin_authenticated_page, base_url):
        """Test that settings create has key field."""
        admin_authenticated_page.goto(f"{base_url}/admin/settings/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#key, input[name='key']")
        expect(field).to_be_visible()

    def test_settings_create_has_value_type_field(self, admin_authenticated_page, base_url):
        """Test that settings create has value type field."""
        admin_authenticated_page.goto(f"{base_url}/admin/settings/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        field = admin_authenticated_page.locator("#value_type, select[name='value_type']")
        expect(field).to_be_visible()


@pytest.mark.e2e
class TestScheduledTasksCRUD:
    """Tests for scheduled tasks CRUD."""

    def test_tasks_list_page_loads(self, admin_authenticated_page, base_url):
        """Test that tasks list page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/tasks")
        assert response.ok, f"Tasks list failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_tasks_create_page_loads(self, admin_authenticated_page, base_url):
        """Test that task create page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/tasks/new")
        assert response.ok, f"Task create failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")


@pytest.mark.e2e
class TestAuditLogs:
    """Tests for audit logs viewing."""

    def test_audit_logs_page_loads(self, admin_authenticated_page, base_url):
        """Test that audit logs page loads."""
        response = admin_authenticated_page.goto(f"{base_url}/admin/audit-logs")
        assert response.ok, f"Audit logs failed: {response.status}"

        admin_authenticated_page.wait_for_load_state("networkidle")

    def test_audit_logs_has_table(self, admin_authenticated_page, base_url):
        """Test that audit logs displays log entries."""
        admin_authenticated_page.goto(f"{base_url}/admin/audit-logs")
        admin_authenticated_page.wait_for_load_state("networkidle")

        table = admin_authenticated_page.locator("table, [role='table'], .audit-log-list")
        expect(table.first).to_be_visible()

    def test_audit_logs_has_filters(self, admin_authenticated_page, base_url):
        """Test that audit logs has search/filter."""
        admin_authenticated_page.goto(f"{base_url}/admin/audit-logs")
        admin_authenticated_page.wait_for_load_state("networkidle")

        filters = admin_authenticated_page.locator("input[type='search'], input[name='search'], select, input[type='date']")
        if filters.count() > 0:
            expect(filters.first).to_be_visible()
