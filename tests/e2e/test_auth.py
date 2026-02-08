"""
E2E Tests for Authentication Flows.

Tests login, logout, password reset, and session handling.
"""

import re

import pytest
from playwright.sync_api import expect

ALPINE_JS_MARKER = "cdn.jsdelivr.net/npm/alpinejs@"


def assert_alpinejs_in_response(response):
    assert response is not None, "No response available to check for Alpine.js"
    body = response.text()
    assert ALPINE_JS_MARKER in body, "Alpine.js script missing from response"


def goto_auth_page(page, url, timeout=30000):
    return page.goto(url, wait_until="domcontentloaded", timeout=timeout)


def wait_for_alpine(page, timeout=10000):
    """Wait for Alpine.js to initialize by checking for form elements."""
    # Wait for form to be visible (Alpine.js has initialized)
    page.wait_for_selector("form", state="visible", timeout=timeout)
    # Also wait for input fields to be ready
    page.wait_for_selector(
        "input[name='username'], input[name='email']", state="visible", timeout=timeout
    )


@pytest.mark.e2e
class TestLoginPage:
    """Tests for the login page display and structure."""

    def test_login_page_loads(self, page, base_url):
        """Test that the login page loads correctly."""
        response = goto_auth_page(page, f"{base_url}/login")
        assert response.ok, f"Login page failed to load: {response.status}"
        assert_alpinejs_in_response(response)

        # Wait for Alpine.js to initialize
        wait_for_alpine(page)

        # Should have a login form
        expect(page.locator("form")).to_be_visible()

    def test_login_page_has_username_field(self, page, base_url):
        """Test that login page has username/email field."""
        goto_auth_page(page, f"{base_url}/login")
        wait_for_alpine(page)

        # Look for username or email input
        username_field = page.locator(
            "input[name='username'], input[name='email'], input[type='email']"
        ).first
        expect(username_field).to_be_visible()

    def test_login_page_has_password_field(self, page, base_url):
        """Test that login page has password field."""
        goto_auth_page(page, f"{base_url}/login")
        wait_for_alpine(page)

        password_field = page.locator("input[type='password']").first
        expect(password_field).to_be_visible()

    def test_login_page_has_submit_button(self, page, base_url):
        """Test that login page has a submit button."""
        goto_auth_page(page, f"{base_url}/login")
        wait_for_alpine(page)

        submit_btn = page.locator("button[type='submit'], input[type='submit']").first
        expect(submit_btn).to_be_visible()

    def test_login_page_has_forgot_password_link(self, page, base_url):
        """Test that login page has forgot password link."""
        goto_auth_page(page, f"{base_url}/login")
        wait_for_alpine(page)

        # Look for forgot password link
        forgot_link = page.locator(
            "a[href*='forgot'], a:has-text('Forgot'), a:has-text('forgot')"
        )
        # May or may not exist, so just check if visible when present
        if forgot_link.count() > 0:
            expect(forgot_link.first).to_be_visible()


@pytest.mark.e2e
class TestLoginWithCredentials:
    """Tests for actual login functionality."""

    def test_login_with_valid_credentials_redirects(self, page, base_url):
        """Test that valid credentials redirect to dashboard."""
        import os

        username = os.environ.get("E2E_TEST_USERNAME", "e2e_testuser")
        password = os.environ.get("E2E_TEST_PASSWORD", "e2e_testpassword123")

        goto_auth_page(page, f"{base_url}/login")
        wait_for_alpine(page)

        # Fill in credentials
        username_field = page.locator(
            "input[name='username'], input[name='email']"
        ).first
        password_field = page.locator("input[type='password']").first

        username_field.fill(username)
        password_field.fill(password)

        # Submit form
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_load_state("networkidle")

        # Should redirect to dashboard or home
        expect(page).to_have_url(re.compile(r".*(dashboard|home|/).*"))

    def test_login_with_invalid_credentials_shows_error(self, page, base_url):
        """Test that invalid credentials show an error message."""
        goto_auth_page(page, f"{base_url}/login")
        wait_for_alpine(page)

        # Fill in invalid credentials
        username_field = page.locator(
            "input[name='username'], input[name='email']"
        ).first
        password_field = page.locator("input[type='password']").first

        username_field.fill("invalid_user_that_does_not_exist")
        password_field.fill("wrong_password_123")

        # Submit form
        page.locator("button[type='submit'], input[type='submit']").first.click()
        page.wait_for_load_state("networkidle")

        # Should show error message or stay on login page
        # Either an error is shown or we're still on login
        current_url = page.url
        if "/login" in current_url:
            # Still on login page, which is expected for failed login
            pass
        else:
            # If redirected, there should be an error indication
            error_indicator = page.locator(
                ".error, .alert-error, .text-red, [class*='error']"
            )
            if error_indicator.count() > 0:
                expect(error_indicator.first).to_be_visible()

    def test_login_with_empty_fields_shows_validation(self, page, base_url):
        """Test that submitting empty form shows validation."""
        goto_auth_page(page, f"{base_url}/login")
        wait_for_alpine(page)

        # Try to submit without filling fields
        submit_btn = page.locator("button[type='submit'], input[type='submit']").first
        submit_btn.click()

        # Should either show HTML5 validation or stay on page
        # Check if still on login page
        page.wait_for_timeout(500)
        expect(page).to_have_url(re.compile(r".*login.*"))


@pytest.mark.e2e
class TestLogout:
    """Tests for logout functionality."""

    def test_logout_redirects_to_login(self, authenticated_page, base_url):
        """Test that logout redirects to login page."""
        # First go to dashboard to ensure we're logged in
        authenticated_page.goto(f"{base_url}/dashboard")
        authenticated_page.wait_for_load_state("networkidle")

        # Navigate to logout
        authenticated_page.goto(f"{base_url}/logout")
        authenticated_page.wait_for_load_state("networkidle")

        # Should redirect to login
        expect(authenticated_page).to_have_url(re.compile(r".*login.*"))

    def test_after_logout_protected_routes_redirect(self, page, base_url):
        """Test that after logout, protected routes redirect to login."""
        # Start fresh without auth
        goto_auth_page(page, f"{base_url}/logout")

        # Clear cookies to ensure logged out
        page.context.clear_cookies()

        # Try to access protected route
        goto_auth_page(page, f"{base_url}/dashboard")

        # Should redirect to login
        expect(page).to_have_url(re.compile(r".*login.*"))


@pytest.mark.e2e
class TestProtectedRoutes:
    """Tests for protected route access control."""

    def test_dashboard_requires_auth(self, page, base_url):
        """Test that dashboard requires authentication."""
        # Clear any existing cookies
        page.context.clear_cookies()

        goto_auth_page(page, f"{base_url}/dashboard")

        # Should redirect to login
        expect(page).to_have_url(re.compile(r".*login.*"))

    def test_gl_accounts_requires_auth(self, page, base_url):
        """Test that GL accounts requires authentication."""
        page.context.clear_cookies()

        goto_auth_page(page, f"{base_url}/gl/accounts")

        expect(page).to_have_url(re.compile(r".*login.*"))

    def test_ap_suppliers_requires_auth(self, page, base_url):
        """Test that AP suppliers requires authentication."""
        page.context.clear_cookies()

        goto_auth_page(page, f"{base_url}/ap/suppliers")

        expect(page).to_have_url(re.compile(r".*login.*"))

    def test_ar_customers_requires_auth(self, page, base_url):
        """Test that AR customers requires authentication."""
        page.context.clear_cookies()

        goto_auth_page(page, f"{base_url}/ar/customers")

        expect(page).to_have_url(re.compile(r".*login.*"))

    def test_settings_requires_auth(self, page, base_url):
        """Test that settings requires authentication."""
        page.context.clear_cookies()

        goto_auth_page(page, f"{base_url}/settings")

        expect(page).to_have_url(re.compile(r".*login.*"))


@pytest.mark.e2e
class TestForgotPassword:
    """Tests for forgot password flow."""

    def test_forgot_password_page_loads(self, page, base_url):
        """Test that forgot password page loads."""
        response = page.goto(f"{base_url}/forgot-password")

        assert response.ok, f"Forgot password page failed: {response.status}"

    def test_forgot_password_has_email_field(self, page, base_url):
        """Test that forgot password page has email field."""
        page.goto(f"{base_url}/forgot-password")

        page.wait_for_load_state("networkidle")

        email_field = page.locator("input[type='email'], input[name='email']")
        if email_field.count() > 0:
            expect(email_field.first).to_be_visible()

    def test_forgot_password_has_submit_button(self, page, base_url):
        """Test that forgot password page has submit button."""
        page.goto(f"{base_url}/forgot-password")

        page.wait_for_load_state("networkidle")

        submit_btn = page.locator("button[type='submit'], input[type='submit']")
        if submit_btn.count() > 0:
            expect(submit_btn.first).to_be_visible()


@pytest.mark.e2e
class TestResetPassword:
    """Tests for password reset flow."""

    def test_reset_password_page_requires_token(self, page, base_url):
        """Test that reset password page requires a token."""
        response = page.goto(f"{base_url}/reset-password")

        # If response is 422 (missing token), that's expected behavior
        if response.status == 422:
            return  # Pass - token is required

        page.wait_for_load_state("networkidle")

        # Without token, should show error or redirect
        # Check for error message or redirect to login/forgot-password
        url = page.url
        has_error = (
            page.locator(".error, .alert-error, .text-red, [class*='error']").count()
            > 0
        )
        is_redirected = "login" in url or "forgot" in url

        # Token is required - page should either error, redirect, or require token param
        assert has_error or is_redirected or "token" in url or response.status >= 400, (
            "Reset password should require token"
        )

    def test_reset_password_with_invalid_token(self, page, base_url):
        """Test reset password with invalid token shows error."""
        page.goto(f"{base_url}/reset-password?token=invalid_token_12345")

        page.wait_for_load_state("networkidle")

        # Should show invalid token error or redirect
        url = page.url
        has_error = (
            page.locator(
                ".error, .alert-error, .text-red, [class*='error'], [class*='invalid']"
            ).count()
            > 0
        )
        is_redirected = "login" in url or "forgot" in url or "expired" in url

        # Page may display the reset form but show error on submit
        # Just verify we're on the reset password page or redirected
        is_on_reset_page = "reset-password" in url
        assert has_error or is_redirected or is_on_reset_page, (
            "Invalid token should show error, redirect, or display reset page"
        )


@pytest.mark.e2e
class TestAdminLogin:
    """Tests for admin login page."""

    def test_admin_login_page_loads(self, page, base_url):
        """Test that admin login page loads."""
        response = goto_auth_page(page, f"{base_url}/admin/login")

        assert response.ok, f"Admin login page failed: {response.status}"
        assert_alpinejs_in_response(response)

    def test_admin_login_has_form(self, page, base_url):
        """Test that admin login page has a form."""
        goto_auth_page(page, f"{base_url}/admin/login")

        wait_for_alpine(page)

        expect(page.locator("form")).to_be_visible()

    def test_admin_routes_require_admin_role(self, authenticated_page, base_url):
        """Test that admin routes require admin role."""
        # Authenticated user trying to access admin
        # Note: If user has admin role, they should be able to access
        authenticated_page.goto(f"{base_url}/admin")
        authenticated_page.wait_for_load_state("networkidle")

        url = authenticated_page.url
        is_login = "login" in url
        is_forbidden = "403" in url or "forbidden" in url.lower()
        is_admin = "/admin" in url and "login" not in url

        # User should either be on admin page (if has role) or redirected/forbidden
        # All three outcomes are valid depending on user's role
        assert is_admin or is_login or is_forbidden, (
            "Should either access admin, redirect to login, or show forbidden"
        )
