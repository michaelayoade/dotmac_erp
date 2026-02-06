"""
E2E Tests for General Ledger Module.

Tests the GL UI flows using Playwright.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestGLAccountsNavigation:
    """Test GL accounts page navigation."""

    @pytest.mark.e2e
    def test_accounts_page_loads(self, gl_accounts_page: Page):
        """Test that accounts page loads successfully."""
        expect(gl_accounts_page).to_have_url(re.compile(r".*/gl/accounts.*"))
        expect(gl_accounts_page.get_by_test_id("page-title")).to_contain_text(
            "Chart of Accounts"
        )

    @pytest.mark.e2e
    def test_accounts_page_has_content(self, gl_accounts_page: Page):
        """Test that accounts page has content."""
        expect(gl_accounts_page.locator("table")).to_be_visible()

    @pytest.mark.e2e
    def test_new_account_button_navigation(self, gl_accounts_page: Page, base_url: str):
        """Test clicking new account button navigates to form."""
        # Look for a "new" or "add" button
        new_btn = gl_accounts_page.locator("a[href='/gl/accounts/new']").first
        expect(new_btn).to_be_visible()
        new_btn.click()
        gl_accounts_page.wait_for_load_state("networkidle")
        expect(gl_accounts_page.get_by_test_id("page-title")).to_contain_text(
            "New Account"
        )


class TestGLAccountsSearch:
    """Test GL accounts search functionality."""

    @pytest.mark.e2e
    def test_accounts_search_input_exists(self, gl_accounts_page: Page):
        """Test that search input exists."""
        search = gl_accounts_page.locator("input[name='search']").first
        expect(search).to_be_visible()

    @pytest.mark.e2e
    def test_accounts_search_works(self, gl_accounts_page: Page):
        """Test that search functionality works."""
        search = gl_accounts_page.locator("input[name='search']").first
        expect(search).to_be_visible()
        search.fill("Cash")
        gl_accounts_page.wait_for_timeout(500)
        expect(gl_accounts_page.locator("table")).to_be_visible()


class TestGLJournals:
    """Test GL journal entries page."""

    @pytest.mark.e2e
    def test_journals_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that journals page loads."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/journals.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Journal Entries"
        )

    @pytest.mark.e2e
    def test_journals_page_has_content(self, authenticated_page: Page, base_url: str):
        """Test that journals page has content."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("table")).to_be_visible()


class TestGLPeriods:
    """Test GL fiscal periods page."""

    @pytest.mark.e2e
    def test_periods_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that periods page loads."""
        authenticated_page.goto(f"{base_url}/gl/periods")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/periods.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Fiscal Periods"
        )


class TestGLTrialBalance:
    """Test GL trial balance page."""

    @pytest.mark.e2e
    def test_trial_balance_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that trial balance page loads."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/trial-balance.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Trial Balance"
        )

    @pytest.mark.e2e
    def test_trial_balance_has_date_filter(
        self, authenticated_page: Page, base_url: str
    ):
        """Test trial balance page has date filter."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        date_filter = authenticated_page.locator("input[name='as_of_date']").first
        expect(date_filter).to_be_visible()

    @pytest.mark.e2e
    def test_trial_balance_shows_totals(self, authenticated_page: Page, base_url: str):
        """Test trial balance displays debit/credit totals."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for total row or summary
        totals = authenticated_page.locator("tfoot").first
        expect(totals).to_be_visible()


class TestGLJournalWorkflow:
    """Test GL journal entry workflow."""

    @pytest.mark.e2e
    def test_journal_form_has_required_fields(
        self, authenticated_page: Page, base_url: str
    ):
        """Test journal form has all required fields."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        desc = form.locator("input[name='description']")
        expect(desc).to_be_visible()
        date_field = form.locator("input[name='entry_date']")
        expect(date_field).to_be_visible()

    @pytest.mark.e2e
    def test_journal_line_entry(self, authenticated_page: Page, base_url: str):
        """Test journal line entry interface."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for add line button
        add_line = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Row'), "
            "[data-action='add-line'], .add-line-btn"
        ).first

        expect(add_line).to_be_visible()

    @pytest.mark.e2e
    def test_journal_balance_validation(self, authenticated_page: Page, base_url: str):
        """Test journal shows balance validation."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for balance indicator
        balance = authenticated_page.locator("tfoot").first
        expect(balance).to_be_visible()


class TestGLPeriodClose:
    """Test GL period close workflow."""

    @pytest.mark.e2e
    def test_period_close_page_loads(self, authenticated_page: Page, base_url: str):
        """Test period close page loads."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    @pytest.mark.e2e
    def test_period_close_shows_checklist(
        self, authenticated_page: Page, base_url: str
    ):
        """Test period close shows close checklist."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for checklist or steps
        checklist = authenticated_page.locator(
            ".checklist, .close-steps, [data-checklist], ol, ul.steps"
        ).first
        expect(checklist).to_be_visible()

    @pytest.mark.e2e
    def test_period_close_button_exists(self, authenticated_page: Page, base_url: str):
        """Test close period button exists."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")

        close_btn = authenticated_page.locator(
            "button:has-text('Close Period'), button:has-text('Close'), "
            "[data-action='close-period']"
        ).first
        expect(close_btn).to_be_visible()


class TestGLDetailPages:
    """Test GL detail pages handle missing records."""

    @pytest.mark.e2e
    def test_account_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure account detail shows not found state."""
        account_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/gl/accounts/{account_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Account not found")).to_be_visible()

    @pytest.mark.e2e
    def test_journal_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure journal detail shows not found state."""
        entry_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/gl/journals/{entry_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(
            authenticated_page.get_by_text("Journal entry not found")
        ).to_be_visible()
