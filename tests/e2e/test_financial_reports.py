"""
E2E Tests for Financial Reports Module.

Tests the financial reporting UI flows using Playwright.
"""

import pytest
from playwright.sync_api import Page, expect


class TestReportsNavigation:
    """Test reports page navigation."""

    @pytest.mark.e2e
    def test_reports_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that reports page loads successfully."""
        authenticated_page.goto(f"{base_url}/reports")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Reports")
        expect(authenticated_page.locator("input[name='start_date']")).to_be_visible()
        expect(authenticated_page.locator("input[name='end_date']")).to_be_visible()

    @pytest.mark.e2e
    def test_reports_list_displays(self, authenticated_page: Page, base_url: str):
        """Test that reports list displays."""
        authenticated_page.goto(f"{base_url}/reports")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.locator("a[href='/reports/income-statement']").first).to_be_visible()
        expect(authenticated_page.locator("a[href='/reports/balance-sheet']").first).to_be_visible()
        expect(authenticated_page.locator("a[href='/reports/tax-summary']").first).to_be_visible()


class TestTrialBalanceReport:
    """Test trial balance report generation."""

    @pytest.mark.e2e
    def test_trial_balance_page_loads(self, authenticated_page: Page, base_url: str):
        """Test trial balance report page loads."""
        authenticated_page.goto(f"{base_url}/reports/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Trial Balance")

    @pytest.mark.e2e
    def test_trial_balance_has_as_of_date(self, authenticated_page: Page, base_url: str):
        """Test trial balance has as-of date selector."""
        authenticated_page.goto(f"{base_url}/reports/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        as_of_date = authenticated_page.locator("input[name='as_of_date']").first
        expect(as_of_date).to_be_visible()

    @pytest.mark.e2e
    def test_trial_balance_refresh_button(self, authenticated_page: Page, base_url: str):
        """Test trial balance has refresh button."""
        authenticated_page.goto(f"{base_url}/reports/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        refresh_btn = authenticated_page.locator("button:has-text('Refresh')").first
        expect(refresh_btn).to_be_visible()


class TestIncomeStatementReport:
    """Test income statement report generation."""

    @pytest.mark.e2e
    def test_income_statement_page_loads(self, authenticated_page: Page, base_url: str):
        """Test income statement report page loads."""
        authenticated_page.goto(f"{base_url}/reports/income-statement")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Statement of Profit or Loss")

    @pytest.mark.e2e
    def test_income_statement_date_range(self, authenticated_page: Page, base_url: str):
        """Test income statement has date range selector."""
        authenticated_page.goto(f"{base_url}/reports/income-statement")
        authenticated_page.wait_for_load_state("networkidle")

        date_from = authenticated_page.locator("input[name='start_date']").first
        date_to = authenticated_page.locator("input[name='end_date']").first
        expect(date_from).to_be_visible()
        expect(date_to).to_be_visible()


class TestBalanceSheetReport:
    """Test balance sheet report generation."""

    @pytest.mark.e2e
    def test_balance_sheet_page_loads(self, authenticated_page: Page, base_url: str):
        """Test balance sheet report page loads."""
        authenticated_page.goto(f"{base_url}/reports/balance-sheet")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Statement of Financial Position")

    @pytest.mark.e2e
    def test_balance_sheet_as_of_date(self, authenticated_page: Page, base_url: str):
        """Test balance sheet has as-of date selector."""
        authenticated_page.goto(f"{base_url}/reports/balance-sheet")
        authenticated_page.wait_for_load_state("networkidle")

        as_of_date = authenticated_page.locator("input[name='as_of_date']").first
        expect(as_of_date).to_be_visible()


class TestGeneralLedgerReport:
    """Test general ledger report."""

    @pytest.mark.e2e
    def test_general_ledger_page_loads(self, authenticated_page: Page, base_url: str):
        """Test general ledger report page loads."""
        authenticated_page.goto(f"{base_url}/reports/general-ledger")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("General Ledger")
        expect(authenticated_page.locator("select[name='account_id']")).to_be_visible()
        expect(authenticated_page.locator("input[name='start_date']")).to_be_visible()
        expect(authenticated_page.locator("input[name='end_date']")).to_be_visible()


class TestReportActions:
    """Test report action buttons."""

    @pytest.mark.e2e
    def test_trial_balance_print_button_exists(self, authenticated_page: Page, base_url: str):
        """Test trial balance print button exists."""
        authenticated_page.goto(f"{base_url}/reports/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        print_btn = authenticated_page.locator("button:has-text('Print')").first
        expect(print_btn).to_be_visible()

    @pytest.mark.e2e
    def test_income_statement_print_button_exists(self, authenticated_page: Page, base_url: str):
        """Test income statement print button exists."""
        authenticated_page.goto(f"{base_url}/reports/income-statement")
        authenticated_page.wait_for_load_state("networkidle")

        print_btn = authenticated_page.locator("button:has-text('Print')").first
        expect(print_btn).to_be_visible()

    @pytest.mark.e2e
    def test_balance_sheet_print_button_exists(self, authenticated_page: Page, base_url: str):
        """Test balance sheet print button exists."""
        authenticated_page.goto(f"{base_url}/reports/balance-sheet")
        authenticated_page.wait_for_load_state("networkidle")

        print_btn = authenticated_page.locator("button:has-text('Print')").first
        expect(print_btn).to_be_visible()


class TestAdditionalReports:
    """Test additional report pages."""

    @pytest.mark.e2e
    def test_tax_summary_page_loads(self, authenticated_page: Page, base_url: str):
        """Test tax summary page loads."""
        authenticated_page.goto(f"{base_url}/reports/tax-summary")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Tax Summary")
        expect(authenticated_page.locator("input[name='start_date']")).to_be_visible()
        expect(authenticated_page.locator("input[name='end_date']")).to_be_visible()

    @pytest.mark.e2e
    def test_expense_summary_page_loads(self, authenticated_page: Page, base_url: str):
        """Test expense summary page loads."""
        authenticated_page.goto(f"{base_url}/reports/expense-summary")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Expense Summary")
        expect(authenticated_page.locator("input[name='start_date']")).to_be_visible()
        expect(authenticated_page.locator("input[name='end_date']")).to_be_visible()
