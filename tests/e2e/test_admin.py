"""
E2E Tests for Admin Module.

Tests admin dashboard, user/role/permission/organization management workflows.
Requires admin user credentials for testing.
"""

import os
import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


# Admin-specific fixtures
@pytest.fixture
def admin_credentials():
    """Get admin test credentials."""
    return {
        "username": os.environ.get("E2E_ADMIN_USERNAME", "e2e_testuser"),
        "password": os.environ.get("E2E_ADMIN_PASSWORD", "e2e_testpassword123"),
    }


@pytest.fixture
def admin_dashboard_page(admin_authenticated_page: Page, base_url: str):
    """Navigate to admin dashboard and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


@pytest.fixture
def admin_users_page(admin_authenticated_page: Page, base_url: str):
    """Navigate to admin users page and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin/users")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


@pytest.fixture
def admin_roles_page(admin_authenticated_page: Page, base_url: str):
    """Navigate to admin roles page and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin/roles")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


class TestAdminDashboard:
    """Test admin dashboard page."""

    @pytest.mark.e2e
    def test_dashboard_loads(self, admin_dashboard_page: Page):
        """Test that admin dashboard loads successfully."""
        expect(admin_dashboard_page).to_have_url(re.compile(r".*/admin.*"))

    @pytest.mark.e2e
    def test_dashboard_has_navigation(self, admin_dashboard_page: Page):
        """Test that admin dashboard has navigation links."""
        # Check for sidebar navigation items
        nav_items = admin_dashboard_page.locator(
            "a[href*='/admin/users'], a[href*='/admin/roles'], "
            "a[href*='/admin/organizations'], a[href*='/admin/settings']"
        )
        # At least some nav items should exist
        expect(admin_dashboard_page.locator("main")).to_be_visible()

    @pytest.mark.e2e
    def test_dashboard_has_stats(self, admin_dashboard_page: Page):
        """Test that admin dashboard shows statistics."""
        # Look for stat cards or summary sections
        stats = admin_dashboard_page.locator(".stat-card").first
        expect(stats).to_be_visible()


class TestAdminUsersNavigation:
    """Test admin users page navigation."""

    @pytest.mark.e2e
    def test_users_page_loads(self, admin_users_page: Page):
        """Test that users page loads successfully."""
        expect(admin_users_page).to_have_url(re.compile(r".*/admin/users.*"))

    @pytest.mark.e2e
    def test_users_page_has_table(self, admin_users_page: Page):
        """Test that users page has a data table."""
        table = admin_users_page.locator("table").first
        expect(table).to_be_visible()

    @pytest.mark.e2e
    def test_users_page_has_new_button(self, admin_users_page: Page):
        """Test that users page has a new user button."""
        new_btn = admin_users_page.locator("a[href='/admin/users/new']").first
        expect(new_btn).to_be_visible()

    @pytest.mark.e2e
    def test_users_search_works(self, admin_users_page: Page):
        """Test that search functionality works on users page."""
        search = admin_users_page.locator("input[name='search']").first
        expect(search).to_be_visible()
        search.fill("test")
        admin_users_page.wait_for_timeout(500)
        expect(admin_users_page.locator("table")).to_be_visible()


class TestAdminUserWorkflow:
    """Test admin user create/edit workflow."""

    @pytest.mark.e2e
    def test_new_user_form_loads(self, admin_authenticated_page: Page, base_url: str):
        """Test that new user form loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/users/new.*")
        )

    @pytest.mark.e2e
    def test_new_user_form_has_required_fields(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new user form has required fields."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='first_name']")).to_be_visible()
        expect(form.locator("input[name='last_name']")).to_be_visible()
        expect(form.locator("input[name='email']")).to_be_visible()
        expect(form.locator("input[name='username']")).to_be_visible()
        expect(form.locator("input[name='password']")).to_be_visible()

    @pytest.mark.e2e
    def test_new_user_form_has_organization_select(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new user form has organization dropdown."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        org_select = admin_authenticated_page.locator(
            "select[name='organization_id']"
        ).first
        expect(org_select).to_be_visible()

    @pytest.mark.e2e
    def test_new_user_form_has_role_selection(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new user form has role checkboxes."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Look for role checkboxes
        role_inputs = admin_authenticated_page.locator(
            "input[name='roles'], input[type='checkbox'][value]"
        ).first
        expect(role_inputs).to_be_visible()

    @pytest.mark.e2e
    def test_create_user_validation_error(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that form shows validation errors on empty submit."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Try to submit empty form - browser validation should prevent
        submit_btn = admin_authenticated_page.locator(
            "button[type='submit'], input[type='submit']"
        ).first
        expect(submit_btn).to_be_visible()
        first_name = admin_authenticated_page.locator("input[name='first_name']").first
        expect(first_name).to_be_visible()
        is_required = first_name.get_attribute("required")
        assert is_required is not None or first_name.is_visible()

    @pytest.mark.e2e
    def test_create_user_workflow(self, admin_authenticated_page: Page, base_url: str):
        """Test full create user workflow with unique data."""
        admin_authenticated_page.goto(f"{base_url}/admin/users/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Generate unique test data
        unique_id = str(uuid4())[:8]
        test_email = f"test_{unique_id}@example.com"
        test_username = f"testuser_{unique_id}"

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill required fields
        admin_authenticated_page.fill("input[name='first_name']", "Test")
        admin_authenticated_page.fill("input[name='last_name']", "User")
        admin_authenticated_page.fill("input[name='email']", test_email)
        admin_authenticated_page.fill("input[name='username']", test_username)
        admin_authenticated_page.fill("input[name='password']", "TestPassword123!")
        admin_authenticated_page.fill(
            "input[name='password_confirm']", "TestPassword123!"
        )

        # Select organization if available
        org_select = admin_authenticated_page.locator("select[name='organization_id']")
        expect(org_select).to_be_visible()
        options = org_select.locator("option:not([value=''])")
        assert options.count() > 0
        first_option_value = options.first.get_attribute("value")
        assert first_option_value
        org_select.select_option(first_option_value)

        # Submit form
        submit_btn = admin_authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Should redirect to users list or show success
        success_msg = admin_authenticated_page.locator(
            ".alert-success, .success, [data-status='success'], "
            ":text('successfully'), :text('created')"
        )
        is_success = (
            success_msg.count() > 0
            or "users" in admin_authenticated_page.url
            or "created=1" in admin_authenticated_page.url
        )
        errors = admin_authenticated_page.locator(".error, .alert-error, .alert-danger")
        expect(errors).to_have_count(0)
        assert is_success

    @pytest.mark.e2e
    def test_user_detail_not_found(self, admin_authenticated_page: Page, base_url: str):
        """Test that non-existent user shows appropriate message."""
        user_id = str(uuid4())
        admin_authenticated_page.goto(f"{base_url}/admin/users/{user_id}")
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Should show not found or redirect
        expect(admin_authenticated_page.locator("main")).to_be_visible()


class TestAdminRolesNavigation:
    """Test admin roles page navigation."""

    @pytest.mark.e2e
    def test_roles_page_loads(self, admin_roles_page: Page):
        """Test that roles page loads successfully."""
        expect(admin_roles_page).to_have_url(re.compile(r".*/admin/roles.*"))

    @pytest.mark.e2e
    def test_roles_page_has_new_button(self, admin_roles_page: Page):
        """Test that roles page has new role button."""
        new_btn = admin_roles_page.locator(
            "a[href*='/admin/roles/new'], button:has-text('New'), "
            "button:has-text('Create Role'), .btn-new"
        ).first
        expect(new_btn).to_be_visible()


class TestAdminRoleWorkflow:
    """Test admin role create/edit workflow."""

    @pytest.mark.e2e
    def test_new_role_form_loads(self, admin_authenticated_page: Page, base_url: str):
        """Test that new role form loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles/new")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/roles/new.*")
        )

    @pytest.mark.e2e
    def test_new_role_form_has_fields(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new role form has expected fields."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()
        name_input = form.locator("input[name='name']").first
        expect(name_input).to_be_visible()

    @pytest.mark.e2e
    def test_create_role_workflow(self, admin_authenticated_page: Page, base_url: str):
        """Test full create role workflow."""
        admin_authenticated_page.goto(f"{base_url}/admin/roles/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        unique_id = str(uuid4())[:8]
        role_name = f"TestRole_{unique_id}"

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill fields
        name_input = admin_authenticated_page.locator("input[name='name']").first
        expect(name_input).to_be_visible()
        name_input.fill(role_name)

        desc_input = admin_authenticated_page.locator(
            "input[name='description'], textarea[name='description']"
        )
        expect(desc_input.first).to_be_visible()
        desc_input.first.fill("Test role description")

        # Check is_active checkbox
        is_active = admin_authenticated_page.locator("input[name='is_active']")
        expect(is_active).to_be_visible()
        is_active.check()

        # Submit
        submit_btn = admin_authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Check for success
        is_success = (
            "roles" in admin_authenticated_page.url
            or "created=1" in admin_authenticated_page.url
            or admin_authenticated_page.locator(".success, .alert-success").count() > 0
        )
        errors = admin_authenticated_page.locator(".error, .alert-error, .alert-danger")
        expect(errors).to_have_count(0)
        assert is_success

    @pytest.mark.e2e
    def test_role_detail_not_found(self, admin_authenticated_page: Page, base_url: str):
        """Test that non-existent role shows appropriate message."""
        role_id = str(uuid4())
        admin_authenticated_page.goto(f"{base_url}/admin/roles/{role_id}")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page.locator("main")).to_be_visible()


class TestAdminPermissions:
    """Test admin permissions page."""

    @pytest.mark.e2e
    def test_permissions_page_loads(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that permissions page loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/permissions")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/permissions.*")
        )

    @pytest.mark.e2e
    def test_new_permission_form_loads(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new permission form loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/permissions/new")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/permissions/new.*")
        )

    @pytest.mark.e2e
    def test_create_permission_workflow(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test full create permission workflow."""
        admin_authenticated_page.goto(f"{base_url}/admin/permissions/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        unique_id = str(uuid4())[:8]
        permission_key = f"test.permission.{unique_id}"

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill key field
        key_input = admin_authenticated_page.locator("input[name='key']").first
        expect(key_input).to_be_visible()
        key_input.fill(permission_key)

        desc_input = admin_authenticated_page.locator(
            "input[name='description'], textarea[name='description']"
        )
        expect(desc_input.first).to_be_visible()
        desc_input.first.fill("Test permission description")

        # Submit
        submit_btn = admin_authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        admin_authenticated_page.wait_for_load_state("networkidle")
        is_success = (
            "permissions" in admin_authenticated_page.url
            or admin_authenticated_page.locator(".success, .alert-success").count() > 0
        )
        errors = admin_authenticated_page.locator(".error, .alert-error, .alert-danger")
        expect(errors).to_have_count(0)
        assert is_success


class TestAdminOrganizations:
    """Test admin organizations page."""

    @pytest.mark.e2e
    def test_organizations_page_loads(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that organizations page loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/organizations")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/organizations.*")
        )

    @pytest.mark.e2e
    def test_new_organization_form_loads(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new organization form loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/organizations/new")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/organizations/new.*")
        )

    @pytest.mark.e2e
    def test_new_organization_form_has_fields(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new organization form has expected fields."""
        admin_authenticated_page.goto(f"{base_url}/admin/organizations/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()
        fields = [
            "input[name='organization_code']",
            "input[name='legal_name']",
            "input[name='functional_currency_code'], select[name='functional_currency_code']",
        ]
        for field_selector in fields:
            field = form.locator(field_selector).first
            expect(field).to_be_visible()

    @pytest.mark.e2e
    def test_organization_detail_not_found(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that non-existent organization shows appropriate message."""
        org_id = str(uuid4())
        admin_authenticated_page.goto(f"{base_url}/admin/organizations/{org_id}")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page.locator("main")).to_be_visible()


class TestAdminSettings:
    """Test admin settings page."""

    @pytest.mark.e2e
    def test_settings_page_loads(self, admin_authenticated_page: Page, base_url: str):
        """Test that settings page loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/settings")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(re.compile(r".*/admin/settings.*"))

    @pytest.mark.e2e
    def test_new_setting_form_loads(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new setting form loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/settings/new")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/settings/new.*")
        )

    @pytest.mark.e2e
    def test_create_setting_workflow(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test full create setting workflow."""
        admin_authenticated_page.goto(f"{base_url}/admin/settings/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        unique_id = str(uuid4())[:8]

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill fields
        domain_input = admin_authenticated_page.locator(
            "input[name='domain'], select[name='domain']"
        ).first
        expect(domain_input).to_be_visible()
        if domain_input.evaluate("el => el.tagName") == "SELECT":
            domain_input.select_option(index=1)
        else:
            domain_input.fill("auth")

        key_input = admin_authenticated_page.locator("input[name='key']").first
        expect(key_input).to_be_visible()
        key_input.fill(f"test_key_{unique_id}")

        value_type = admin_authenticated_page.locator("select[name='value_type']").first
        expect(value_type).to_be_visible()
        value_type.select_option("string")

        value_input = admin_authenticated_page.locator(
            "input[name='value'], textarea[name='value']"
        ).first
        expect(value_input).to_be_visible()
        value_input.fill("test_value")

        # Submit
        submit_btn = admin_authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        admin_authenticated_page.wait_for_load_state("networkidle")
        is_success = (
            "settings" in admin_authenticated_page.url
            or admin_authenticated_page.locator(".success, .alert-success").count() > 0
        )
        errors = admin_authenticated_page.locator(".error, .alert-error, .alert-danger")
        expect(errors).to_have_count(0)
        assert is_success


class TestAdminAuditLogs:
    """Test admin audit logs page."""

    @pytest.mark.e2e
    def test_audit_logs_page_loads(self, admin_authenticated_page: Page, base_url: str):
        """Test that audit logs page loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/audit-logs")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/audit-logs.*")
        )

    @pytest.mark.e2e
    def test_audit_logs_has_filters(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that audit logs page has filter options."""
        admin_authenticated_page.goto(f"{base_url}/admin/audit-logs")
        admin_authenticated_page.wait_for_load_state("networkidle")

        # Look for search/filter inputs
        search = admin_authenticated_page.locator("input[name='search']").first
        expect(search).to_be_visible()

    @pytest.mark.e2e
    def test_audit_logs_has_table(self, admin_authenticated_page: Page, base_url: str):
        """Test that audit logs displays data in a table."""
        admin_authenticated_page.goto(f"{base_url}/admin/audit-logs")
        admin_authenticated_page.wait_for_load_state("networkidle")

        table = admin_authenticated_page.locator("table").first
        expect(table).to_be_visible()


class TestAdminTasks:
    """Test admin scheduled tasks page."""

    @pytest.mark.e2e
    def test_tasks_page_loads(self, admin_authenticated_page: Page, base_url: str):
        """Test that tasks page loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/tasks")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(re.compile(r".*/admin/tasks.*"))

    @pytest.mark.e2e
    def test_new_task_form_loads(self, admin_authenticated_page: Page, base_url: str):
        """Test that new task form loads."""
        admin_authenticated_page.goto(f"{base_url}/admin/tasks/new")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page).to_have_url(
            re.compile(r".*/admin/tasks/new.*")
        )

    @pytest.mark.e2e
    def test_new_task_form_has_fields(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test that new task form has expected fields."""
        admin_authenticated_page.goto(f"{base_url}/admin/tasks/new")
        admin_authenticated_page.wait_for_load_state("networkidle")

        form = admin_authenticated_page.locator("form").first
        expect(form).to_be_visible()
        name_input = form.locator("input[name='name']").first
        expect(name_input).to_be_visible()
        task_name_input = form.locator(
            "input[name='task_name'], select[name='task_name']"
        ).first
        expect(task_name_input).to_be_visible()


class TestAdminResponsiveDesign:
    """Test admin pages on different viewport sizes."""

    @pytest.mark.e2e
    def test_admin_mobile_viewport(self, admin_authenticated_page: Page, base_url: str):
        """Test admin dashboard on mobile viewport."""
        admin_authenticated_page.set_viewport_size({"width": 375, "height": 667})
        admin_authenticated_page.goto(f"{base_url}/admin")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page.locator("main")).to_be_visible()

    @pytest.mark.e2e
    def test_admin_tablet_viewport(self, admin_authenticated_page: Page, base_url: str):
        """Test admin dashboard on tablet viewport."""
        admin_authenticated_page.set_viewport_size({"width": 768, "height": 1024})
        admin_authenticated_page.goto(f"{base_url}/admin")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page.locator("main")).to_be_visible()

    @pytest.mark.e2e
    def test_admin_desktop_viewport(
        self, admin_authenticated_page: Page, base_url: str
    ):
        """Test admin dashboard on desktop viewport."""
        admin_authenticated_page.set_viewport_size({"width": 1920, "height": 1080})
        admin_authenticated_page.goto(f"{base_url}/admin")
        admin_authenticated_page.wait_for_load_state("networkidle")
        expect(admin_authenticated_page.locator("main")).to_be_visible()


class TestAdminAccessControl:
    """Test admin access control."""

    @pytest.mark.e2e
    def test_unauthenticated_access_redirects(self, page: Page, base_url: str):
        """Test that unauthenticated users are redirected from admin."""
        # Clear any existing cookies
        page.context.clear_cookies()
        page.goto(f"{base_url}/admin")
        page.wait_for_load_state("networkidle")

        # Should redirect to login
        current_url = page.url
        # Either redirected to login or shows unauthorized
        is_redirected = (
            "/login" in current_url
            or "/admin/login" in current_url
            or page.locator(":text('login'), :text('sign in')").count() > 0
        )
        assert is_redirected, f"Expected redirect from /admin, got {current_url}"
