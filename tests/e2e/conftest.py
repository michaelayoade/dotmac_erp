"""
Playwright E2E Test Configuration.

Provides fixtures for running E2E tests against the web application.

Usage:
    1. Start the server manually: poetry run uvicorn app.main:app --port 8000
    2. Set test credentials (or use defaults):
       export E2E_TEST_USERNAME=testuser
       export E2E_TEST_PASSWORD=testpassword123
    3. Run tests: poetry run pytest tests/e2e/ -v

Or set E2E_BASE_URL environment variable:
    export E2E_BASE_URL=http://localhost:8000
    poetry run pytest tests/e2e/ -v
"""

import base64
import json
import os
import socket
import time
from contextlib import closing
from urllib.parse import urlparse

import httpx
import pytest


def is_server_running(host: str, port: int) -> bool:
    """Check if server is running."""
    try:
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
            return sock.connect_ex((host, port)) == 0
    except Exception:
        return False


def get_base_url():
    """Get base URL from environment or pytest option."""
    return os.environ.get("E2E_BASE_URL", "http://localhost:8002")


def get_test_credentials():
    """Get test credentials from environment or use defaults."""
    return {
        "username": os.environ.get("E2E_TEST_USERNAME", "e2e_testuser"),
        "password": os.environ.get("E2E_TEST_PASSWORD", "e2e_testpassword123"),
    }


def get_admin_credentials():
    """Get admin test credentials from environment or fall back to test creds."""
    return {
        "username": os.environ.get(
            "E2E_ADMIN_USERNAME",
            os.environ.get("E2E_TEST_USERNAME", "e2e_testuser"),
        ),
        "password": os.environ.get(
            "E2E_ADMIN_PASSWORD",
            os.environ.get("E2E_TEST_PASSWORD", "e2e_testpassword123"),
        ),
    }


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _decode_jwt_payload(token: str) -> dict[str, object] | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1]
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        return json.loads(payload_bytes.decode("utf-8"))
    except Exception:
        return None


def _token_expired(token: str, leeway_seconds: int = 120) -> bool:
    payload = _decode_jwt_payload(token)
    if not payload:
        return True
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return True
    return exp <= time.time() + leeway_seconds


def _ensure_fresh_tokens(
    base_url: str,
    tokens: dict[str, str],
    creds: dict[str, str],
    user_label: str,
) -> dict[str, str]:
    access_token = tokens.get("access_token")
    if not access_token or _token_expired(access_token):
        refreshed = _login_for_tokens(base_url, creds, user_label)
        if refreshed:
            tokens.clear()
            tokens.update(refreshed)
    return tokens


def _should_fail_on_unavailable(kind: str) -> bool:
    if _env_flag("E2E_STRICT"):
        return True
    if kind == "server":
        return _env_flag("E2E_REQUIRE_SERVER")
    if kind == "login":
        return _env_flag("E2E_REQUIRE_LOGIN")
    return False


def _skip_or_fail(kind: str, message: str) -> None:
    if _should_fail_on_unavailable(kind):
        pytest.fail(message)
    pytest.skip(message)


def _login_for_tokens(base_url: str, creds: dict[str, str], user_label: str):
    try:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "username": creds["username"],
                    "password": creds["password"],
                },
            )

            if response.status_code == 200:
                cookies = response.cookies
                access_token = cookies.get("access_token")
                refresh_token = cookies.get("refresh_token")

                if not access_token:
                    try:
                        data = response.json()
                        access_token = data.get("access_token")
                        refresh_token = refresh_token or data.get("refresh_token")
                    except Exception:
                        pass

                if not access_token:
                    _skip_or_fail(
                        "login",
                        f"Login for {user_label} succeeded but no access_token found. "
                        f"Cookies: {list(cookies.keys())}",
                    )

                return {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                }
            if response.status_code == 401:
                _skip_or_fail(
                    "login",
                    f"E2E {user_label} '{creds['username']}' not found or invalid password. "
                    "Set credentials via environment variables.",
                )

            _skip_or_fail(
                "login",
                f"Login for {user_label} failed with status {response.status_code}: {response.text}",
            )
    except httpx.RequestError as e:
        _skip_or_fail(
            "login", f"Could not connect to server for {user_label} login: {e}"
        )

    return None


@pytest.fixture(scope="session")
def base_url():
    """Provide the base URL for all tests.

    Checks if server is running and skips tests if not.
    """
    url = get_base_url()

    # Parse host and port from URL
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 8000

    if not is_server_running(host, port):
        _skip_or_fail(
            "server",
            f"Server not running at {url}. "
            f"Start with: poetry run uvicorn app.main:app --port {port}",
        )

    return url


@pytest.fixture(scope="session")
def auth_tokens(base_url):
    """
    Get authentication tokens by logging in.

    This fixture logs in with test credentials and returns the tokens from cookies.
    If login fails, it skips the tests with a helpful message.
    """
    return _login_for_tokens(base_url, get_test_credentials(), "test user")


@pytest.fixture
def fresh_auth_tokens(base_url, auth_tokens):
    """Ensure access tokens are refreshed for long-running E2E suites."""
    if not auth_tokens:
        pytest.skip("No authentication token available")

    _ensure_fresh_tokens(base_url, auth_tokens, get_test_credentials(), "test user")
    if not auth_tokens.get("access_token"):
        pytest.skip("No authentication token available")

    return auth_tokens


@pytest.fixture(scope="session")
def admin_auth_tokens(base_url):
    """Get admin authentication tokens for admin-only tests."""
    tokens = _login_for_tokens(base_url, get_admin_credentials(), "admin user")
    if not tokens or not tokens.get("access_token"):
        pytest.skip("No admin authentication token available")

    try:
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            response = client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {tokens['access_token']}"},
            )
            if response.status_code != 200:
                pytest.skip(
                    "Admin auth check failed; ensure admin user exists and credentials are correct."
                )
            roles = response.json().get("roles", [])
            if "admin" not in roles:
                pytest.skip(
                    "Admin role missing for E2E admin credentials. "
                    "Set E2E_ADMIN_USERNAME/E2E_ADMIN_PASSWORD to an admin user."
                )
    except httpx.RequestError as e:
        pytest.skip(f"Could not verify admin role: {e}")

    return tokens


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context defaults for E2E tests."""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 720},
        "ignore_https_errors": True,
    }


# =============================================================================
# Authenticated Page Fixtures
# =============================================================================


@pytest.fixture
def authenticated_page(page, base_url, auth_tokens):
    """
    Provide a page with authentication cookie set.

    Use this for pages that require authentication.
    """
    if not auth_tokens:
        pytest.skip("No authentication token available")

    _ensure_fresh_tokens(base_url, auth_tokens, get_test_credentials(), "test user")
    if not auth_tokens.get("access_token"):
        pytest.skip("No authentication token available")

    # The cookie should already be set via browser_context_args,
    # but we can also set it directly if needed
    parsed = urlparse(base_url)
    page.context.add_cookies(
        [
            {
                "name": "access_token",
                "value": auth_tokens["access_token"],
                "domain": parsed.hostname or "localhost",
                "path": "/",
            }
        ]
    )

    return page


@pytest.fixture
def admin_authenticated_page(page, base_url, admin_auth_tokens):
    """
    Provide a page with admin authentication cookie set.

    Use this for admin-only pages.
    """
    if not admin_auth_tokens:
        pytest.skip("No admin authentication token available")

    _ensure_fresh_tokens(
        base_url, admin_auth_tokens, get_admin_credentials(), "admin user"
    )
    if not admin_auth_tokens.get("access_token"):
        pytest.skip("No admin authentication token available")

    parsed = urlparse(base_url)
    page.context.add_cookies(
        [
            {
                "name": "access_token",
                "value": admin_auth_tokens["access_token"],
                "domain": parsed.hostname or "localhost",
                "path": "/",
            }
        ]
    )

    return page


@pytest.fixture
def dashboard_page(authenticated_page, base_url):
    """Navigate to the dashboard and return the page."""
    authenticated_page.goto(f"{base_url}/dashboard")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def gl_accounts_page(authenticated_page, base_url):
    """Navigate to GL accounts page and return the page."""
    authenticated_page.goto(f"{base_url}/gl/accounts")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ap_suppliers_page(authenticated_page, base_url):
    """Navigate to AP suppliers page and return the page."""
    authenticated_page.goto(f"{base_url}/ap/suppliers")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ar_customers_page(authenticated_page, base_url):
    """Navigate to AR customers page and return the page."""
    authenticated_page.goto(f"{base_url}/ar/customers")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Unauthenticated Page Fixtures (for testing login flows, etc.)
# =============================================================================


@pytest.fixture
def unauthenticated_page(page):
    """Provide a page without authentication for testing login flows."""
    return page


# =============================================================================
# Settings Page Fixtures
# =============================================================================


@pytest.fixture
def settings_page(authenticated_page, base_url):
    """Navigate to settings index and return the page."""
    authenticated_page.goto(f"{base_url}/settings")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def organization_settings_page(authenticated_page, base_url):
    """Navigate to organization settings and return the page."""
    authenticated_page.goto(f"{base_url}/settings/organization")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def email_settings_page(authenticated_page, base_url):
    """Navigate to email settings and return the page."""
    authenticated_page.goto(f"{base_url}/settings/email")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def automation_settings_page(authenticated_page, base_url):
    """Navigate to automation settings and return the page."""
    authenticated_page.goto(f"{base_url}/settings/automation-settings")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def report_settings_page(authenticated_page, base_url):
    """Navigate to report settings and return the page."""
    authenticated_page.goto(f"{base_url}/settings/reports")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def features_page(authenticated_page, base_url):
    """Navigate to feature flags and return the page."""
    authenticated_page.goto(f"{base_url}/settings/features")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def numbering_page(authenticated_page, base_url):
    """Navigate to numbering sequences and return the page."""
    authenticated_page.goto(f"{base_url}/settings/numbering")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# AP Module Fixtures
# =============================================================================


@pytest.fixture
def ap_invoices_page(authenticated_page, base_url):
    """Navigate to AP invoices list and return the page."""
    authenticated_page.goto(f"{base_url}/ap/invoices")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ap_payments_page(authenticated_page, base_url):
    """Navigate to AP payments list and return the page."""
    authenticated_page.goto(f"{base_url}/ap/payments")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ap_purchase_orders_page(authenticated_page, base_url):
    """Navigate to purchase orders list and return the page."""
    authenticated_page.goto(f"{base_url}/ap/purchase-orders")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ap_goods_receipts_page(authenticated_page, base_url):
    """Navigate to goods receipts list and return the page."""
    authenticated_page.goto(f"{base_url}/ap/goods-receipts")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# AR Module Fixtures
# =============================================================================


@pytest.fixture
def ar_invoices_page(authenticated_page, base_url):
    """Navigate to AR invoices list and return the page."""
    authenticated_page.goto(f"{base_url}/ar/invoices")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ar_receipts_page(authenticated_page, base_url):
    """Navigate to AR receipts list and return the page."""
    authenticated_page.goto(f"{base_url}/ar/receipts")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ar_credit_notes_page(authenticated_page, base_url):
    """Navigate to credit notes list and return the page."""
    authenticated_page.goto(f"{base_url}/ar/credit-notes")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# GL Module Fixtures
# =============================================================================


@pytest.fixture
def gl_journals_page(authenticated_page, base_url):
    """Navigate to GL journals list and return the page."""
    authenticated_page.goto(f"{base_url}/gl/journals")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def gl_periods_page(authenticated_page, base_url):
    """Navigate to fiscal periods list and return the page."""
    authenticated_page.goto(f"{base_url}/gl/periods")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def gl_trial_balance_page(authenticated_page, base_url):
    """Navigate to trial balance and return the page."""
    authenticated_page.goto(f"{base_url}/gl/trial-balance")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def gl_period_close_page(authenticated_page, base_url):
    """Navigate to period close and return the page."""
    authenticated_page.goto(f"{base_url}/gl/period-close")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Admin Module Fixtures
# =============================================================================


@pytest.fixture
def admin_users_page(admin_authenticated_page, base_url):
    """Navigate to admin users list and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin/users")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


@pytest.fixture
def admin_roles_page(admin_authenticated_page, base_url):
    """Navigate to admin roles list and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin/roles")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


@pytest.fixture
def admin_permissions_page(admin_authenticated_page, base_url):
    """Navigate to admin permissions list and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin/permissions")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


@pytest.fixture
def admin_organizations_page(admin_authenticated_page, base_url):
    """Navigate to admin organizations list and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin/organizations")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


@pytest.fixture
def admin_audit_logs_page(admin_authenticated_page, base_url):
    """Navigate to admin audit logs and return the page."""
    admin_authenticated_page.goto(f"{base_url}/admin/audit-logs")
    admin_authenticated_page.wait_for_load_state("networkidle")
    return admin_authenticated_page


# =============================================================================
# Fixed Assets Module Fixtures
# =============================================================================


@pytest.fixture
def fixed_assets_page(authenticated_page, base_url):
    """Navigate to fixed assets list and return the page."""
    authenticated_page.goto(f"{base_url}/fa/assets")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def fixed_asset_categories_page(authenticated_page, base_url):
    """Navigate to asset categories list and return the page."""
    authenticated_page.goto(f"{base_url}/fa/categories")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def depreciation_page(authenticated_page, base_url):
    """Navigate to depreciation schedule and return the page."""
    authenticated_page.goto(f"{base_url}/fa/depreciation")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Inventory Module Fixtures
# =============================================================================


@pytest.fixture
def inventory_items_page(authenticated_page, base_url):
    """Navigate to inventory items list and return the page."""
    authenticated_page.goto(f"{base_url}/inv/items")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def inventory_transactions_page(authenticated_page, base_url):
    """Navigate to inventory transactions list and return the page."""
    authenticated_page.goto(f"{base_url}/inv/transactions")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def stock_levels_page(authenticated_page, base_url):
    """Navigate to stock levels page and return the page."""
    authenticated_page.goto(f"{base_url}/inv/stock-levels")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Banking Module Fixtures
# =============================================================================


@pytest.fixture
def bank_accounts_page(authenticated_page, base_url):
    """Navigate to bank accounts list and return the page."""
    authenticated_page.goto(f"{base_url}/banking/accounts")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def bank_statements_page(authenticated_page, base_url):
    """Navigate to bank statements list and return the page."""
    authenticated_page.goto(f"{base_url}/banking/statements")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def bank_reconciliations_page(authenticated_page, base_url):
    """Navigate to bank reconciliations list and return the page."""
    authenticated_page.goto(f"{base_url}/banking/reconciliations")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def statement_import_page(authenticated_page, base_url):
    """Navigate to statement import page and return the page."""
    authenticated_page.goto(f"{base_url}/banking/statements/import")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Quotes Module Fixtures
# =============================================================================


@pytest.fixture
def quotes_page(authenticated_page, base_url):
    """Navigate to quotes list and return the page."""
    authenticated_page.goto(f"{base_url}/quotes")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def quote_create_page(authenticated_page, base_url):
    """Navigate to quote create page and return the page."""
    authenticated_page.goto(f"{base_url}/quotes/new")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Sales Orders Module Fixtures
# =============================================================================


@pytest.fixture
def sales_orders_page(authenticated_page, base_url):
    """Navigate to sales orders list and return the page."""
    authenticated_page.goto(f"{base_url}/sales-orders")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def sales_order_create_page(authenticated_page, base_url):
    """Navigate to sales order create page and return the page."""
    authenticated_page.goto(f"{base_url}/sales-orders/new")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Expenses Module Fixtures
# =============================================================================


@pytest.fixture
def expenses_page(authenticated_page, base_url):
    """Navigate to expenses list and return the page."""
    authenticated_page.goto(f"{base_url}/expenses")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def expense_create_page(authenticated_page, base_url):
    """Navigate to expense create page and return the page."""
    authenticated_page.goto(f"{base_url}/expenses/new")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def expense_reports_page(authenticated_page, base_url):
    """Navigate to expense reports list and return the page."""
    authenticated_page.goto(f"{base_url}/expenses/reports")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Automation Module Fixtures
# =============================================================================


@pytest.fixture
def recurring_templates_page(authenticated_page, base_url):
    """Navigate to recurring templates list and return the page."""
    authenticated_page.goto(f"{base_url}/automation/recurring")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def workflow_rules_page(authenticated_page, base_url):
    """Navigate to workflow rules list and return the page."""
    authenticated_page.goto(f"{base_url}/automation/workflows")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def custom_fields_page(authenticated_page, base_url):
    """Navigate to custom fields list and return the page."""
    authenticated_page.goto(f"{base_url}/automation/custom-fields")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def document_templates_page(authenticated_page, base_url):
    """Navigate to document templates list and return the page."""
    authenticated_page.goto(f"{base_url}/automation/templates")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Business Workflow Fixtures
# =============================================================================


@pytest.fixture
def period_close_page(authenticated_page, base_url):
    """Navigate to period close page and return the page."""
    authenticated_page.goto(f"{base_url}/gl/period-close")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ap_aging_page(authenticated_page, base_url):
    """Navigate to AP aging report and return the page."""
    authenticated_page.goto(f"{base_url}/ap/aging")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def ar_aging_page(authenticated_page, base_url):
    """Navigate to AR aging report and return the page."""
    authenticated_page.goto(f"{base_url}/ar/aging")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Viewport Size Fixtures (for responsive tests)
# =============================================================================

MOBILE_VIEWPORT = {"width": 375, "height": 667}
TABLET_VIEWPORT = {"width": 768, "height": 1024}
DESKTOP_VIEWPORT = {"width": 1280, "height": 720}


@pytest.fixture
def mobile_page(page, base_url, fresh_auth_tokens):
    """Provide a page with mobile viewport and authentication."""
    from urllib.parse import urlparse

    page.set_viewport_size(MOBILE_VIEWPORT)

    parsed = urlparse(base_url)
    page.context.add_cookies(
        [
            {
                "name": "access_token",
                "value": fresh_auth_tokens["access_token"],
                "domain": parsed.hostname or "localhost",
                "path": "/",
            }
        ]
    )

    return page


@pytest.fixture
def tablet_page(page, base_url, fresh_auth_tokens):
    """Provide a page with tablet viewport and authentication."""
    from urllib.parse import urlparse

    page.set_viewport_size(TABLET_VIEWPORT)

    parsed = urlparse(base_url)
    page.context.add_cookies(
        [
            {
                "name": "access_token",
                "value": fresh_auth_tokens["access_token"],
                "domain": parsed.hostname or "localhost",
                "path": "/",
            }
        ]
    )

    return page


@pytest.fixture
def desktop_page(page, base_url, fresh_auth_tokens):
    """Provide a page with desktop viewport and authentication."""
    from urllib.parse import urlparse

    page.set_viewport_size(DESKTOP_VIEWPORT)

    parsed = urlparse(base_url)
    page.context.add_cookies(
        [
            {
                "name": "access_token",
                "value": fresh_auth_tokens["access_token"],
                "domain": parsed.hostname or "localhost",
                "path": "/",
            }
        ]
    )

    return page


# =============================================================================
# Error Testing Fixtures
# =============================================================================


@pytest.fixture
def invalid_uuid():
    """Provide an invalid UUID for 404 testing."""
    return "00000000-0000-0000-0000-000000000000"


@pytest.fixture
def nonexistent_page_url(base_url):
    """Provide a URL that should return 404."""
    return f"{base_url}/nonexistent-page-xyz123"


# =============================================================================
# Pagination Testing Fixtures
# =============================================================================


@pytest.fixture
def paginated_suppliers_page(authenticated_page, base_url):
    """Navigate to suppliers list with pagination params."""
    authenticated_page.goto(f"{base_url}/ap/suppliers?page=1&limit=10")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def paginated_invoices_page(authenticated_page, base_url):
    """Navigate to invoices list with pagination params."""
    authenticated_page.goto(f"{base_url}/ap/invoices?page=1&limit=10")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def paginated_journals_page(authenticated_page, base_url):
    """Navigate to journals list with pagination params."""
    authenticated_page.goto(f"{base_url}/gl/journals?page=1&limit=10")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


# =============================================================================
# Search and Filter Testing Fixtures
# =============================================================================


@pytest.fixture
def filtered_suppliers_page(authenticated_page, base_url):
    """Navigate to suppliers list with active status filter."""
    authenticated_page.goto(f"{base_url}/ap/suppliers?status=active")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def searched_suppliers_page(authenticated_page, base_url):
    """Navigate to suppliers list with search term."""
    authenticated_page.goto(f"{base_url}/ap/suppliers?search=test")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page


@pytest.fixture
def empty_search_results_page(authenticated_page, base_url):
    """Navigate to suppliers list with search that returns no results."""
    authenticated_page.goto(f"{base_url}/ap/suppliers?search=xyznonexistent123456")
    authenticated_page.wait_for_load_state("networkidle")
    return authenticated_page
