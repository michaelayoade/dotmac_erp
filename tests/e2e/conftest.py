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

import os
import socket
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
    return os.environ.get("E2E_BASE_URL", "http://localhost:8000")


def get_test_credentials():
    """Get test credentials from environment or use defaults."""
    return {
        "username": os.environ.get("E2E_TEST_USERNAME", "e2e_testuser"),
        "password": os.environ.get("E2E_TEST_PASSWORD", "e2e_testpassword123"),
    }


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
        pytest.skip(
            f"Server not running at {url}. "
            f"Start with: poetry run uvicorn app.main:app --port {port}"
        )

    return url


@pytest.fixture(scope="session")
def auth_tokens(base_url):
    """
    Get authentication tokens by logging in.

    This fixture logs in with test credentials and returns the access token.
    If login fails, it skips the tests with a helpful message.
    """
    creds = get_test_credentials()

    try:
        with httpx.Client(base_url=base_url, timeout=10.0) as client:
            response = client.post(
                "/api/v1/auth/login",
                json={
                    "username": creds["username"],
                    "password": creds["password"],
                },
            )

            if response.status_code == 200:
                data = response.json()
                return {
                    "access_token": data.get("access_token"),
                    "refresh_token": data.get("refresh_token"),
                }
            elif response.status_code == 401:
                pytest.skip(
                    f"E2E test user '{creds['username']}' not found or invalid password. "
                    f"Create the user or set E2E_TEST_USERNAME and E2E_TEST_PASSWORD env vars."
                )
            else:
                pytest.skip(
                    f"Login failed with status {response.status_code}: {response.text}"
                )
    except httpx.RequestError as e:
        pytest.skip(f"Could not connect to server for login: {e}")

    return None


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args, base_url, auth_tokens):
    """Configure browser context with authentication cookie."""
    # Parse the base URL to get the domain
    parsed = urlparse(base_url)
    domain = parsed.hostname or "localhost"

    # Create storage state with the access token cookie
    cookies = []
    if auth_tokens and auth_tokens.get("access_token"):
        cookies.append({
            "name": "access_token",
            "value": auth_tokens["access_token"],
            "domain": domain,
            "path": "/",
            "httpOnly": False,
            "secure": parsed.scheme == "https",
            "sameSite": "Lax",
        })

    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 720},
        "ignore_https_errors": True,
        "storage_state": {
            "cookies": cookies,
            "origins": [],
        } if cookies else None,
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
    if not auth_tokens or not auth_tokens.get("access_token"):
        pytest.skip("No authentication token available")

    # The cookie should already be set via browser_context_args,
    # but we can also set it directly if needed
    parsed = urlparse(base_url)
    page.context.add_cookies([{
        "name": "access_token",
        "value": auth_tokens["access_token"],
        "domain": parsed.hostname or "localhost",
        "path": "/",
    }])

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
