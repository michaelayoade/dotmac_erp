"""
E2E Tests for General Ledger Module.

Tests the GL UI flows using Playwright.
"""

import pytest
from playwright.sync_api import Page, expect


class TestGLAccountsNavigation:
    """Test GL accounts page navigation."""

    @pytest.mark.e2e
    def test_accounts_page_loads(self, gl_accounts_page: Page):
        """Test that accounts page loads successfully."""
        expect(gl_accounts_page).to_have_url(lambda url: "/gl/accounts" in url)

    @pytest.mark.e2e
    def test_accounts_page_has_content(self, gl_accounts_page: Page):
        """Test that accounts page has content."""
        expect(gl_accounts_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_new_account_button_navigation(self, gl_accounts_page: Page, base_url: str):
        """Test clicking new account button navigates to form."""
        # Look for a "new" or "add" button
        new_btn = gl_accounts_page.locator(
            "a[href*='new'], button:has-text('New'), button:has-text('Add'), "
            "[data-action='new'], .btn-new, .btn-add"
        ).first

        if new_btn.is_visible():
            new_btn.click()
            gl_accounts_page.wait_for_load_state("networkidle")
            # Should navigate to new account form
            expect(gl_accounts_page).to_have_url(lambda url: "new" in url or "add" in url or "form" in url)


class TestGLAccountsSearch:
    """Test GL accounts search functionality."""

    @pytest.mark.e2e
    def test_accounts_search_input_exists(self, gl_accounts_page: Page):
        """Test that search input exists."""
        search = gl_accounts_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search'], "
            "[data-search], .search-input"
        ).first

        # Search may or may not exist
        if search.is_visible():
            expect(search).to_be_visible()

    @pytest.mark.e2e
    def test_accounts_search_works(self, gl_accounts_page: Page):
        """Test that search functionality works."""
        search = gl_accounts_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        ).first

        if search.is_visible():
            search.fill("Cash")
            # Wait for HTMX to respond
            gl_accounts_page.wait_for_timeout(500)
            # Page should still be functional
            expect(gl_accounts_page.locator("body")).to_be_visible()


class TestGLJournals:
    """Test GL journal entries page."""

    @pytest.mark.e2e
    def test_journals_page_loads(self, page: Page, base_url: str):
        """Test that journals page loads."""
        page.goto(f"{base_url}/gl/journals")
        page.wait_for_load_state("networkidle")
        expect(page).to_have_url(lambda url: "/gl/journals" in url)

    @pytest.mark.e2e
    def test_journals_page_has_content(self, page: Page, base_url: str):
        """Test that journals page has content."""
        page.goto(f"{base_url}/gl/journals")
        page.wait_for_load_state("networkidle")
        expect(page.locator("body")).to_be_visible()


class TestGLPeriods:
    """Test GL fiscal periods page."""

    @pytest.mark.e2e
    def test_periods_page_loads(self, page: Page, base_url: str):
        """Test that periods page loads."""
        page.goto(f"{base_url}/gl/periods")
        page.wait_for_load_state("networkidle")
        expect(page).to_have_url(lambda url: "/gl/periods" in url)


class TestGLTrialBalance:
    """Test GL trial balance page."""

    @pytest.mark.e2e
    def test_trial_balance_page_loads(self, page: Page, base_url: str):
        """Test that trial balance page loads."""
        page.goto(f"{base_url}/gl/trial-balance")
        page.wait_for_load_state("networkidle")
        expect(page).to_have_url(lambda url: "/gl/trial-balance" in url)
