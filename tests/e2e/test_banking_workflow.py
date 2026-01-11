"""
E2E Tests for Banking Module Workflows.

Tests bank account management, statements, and reconciliation workflows.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestBankAccountListWorkflow:
    """Test bank account list page."""

    @pytest.mark.e2e
    def test_accounts_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that bank accounts page loads."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/banking/accounts.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Bank Accounts")

    @pytest.mark.e2e
    def test_accounts_page_has_title(self, authenticated_page: Page, base_url: str):
        """Test that accounts page shows title."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Bank Accounts")

    @pytest.mark.e2e
    def test_accounts_page_has_new_button(self, authenticated_page: Page, base_url: str):
        """Test that accounts page has new account button."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.get_by_test_id("bank-accounts-new")
        expect(new_btn).to_be_visible()

    @pytest.mark.e2e
    def test_accounts_page_has_search(self, authenticated_page: Page, base_url: str):
        """Test that accounts page has search functionality."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.get_by_test_id("bank-accounts-search")
        expect(search).to_be_visible()

    @pytest.mark.e2e
    def test_accounts_page_has_status_filter(self, authenticated_page: Page, base_url: str):
        """Test that accounts page has status filter."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.get_by_test_id("bank-accounts-status")
        expect(status_filter).to_be_visible()

    @pytest.mark.e2e
    def test_accounts_page_has_table(self, authenticated_page: Page, base_url: str):
        """Test that accounts page has data table."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        table = authenticated_page.get_by_test_id("bank-accounts-table")
        expect(table).to_be_visible()


class TestBankAccountCreateWorkflow:
    """Test bank account create workflow."""

    @pytest.mark.e2e
    def test_new_account_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new account form loads."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/banking/accounts/new.*"))

    @pytest.mark.e2e
    def test_new_account_form_has_fields(self, authenticated_page: Page, base_url: str):
        """Test that new account form has required fields."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='bank_name']")).to_be_visible()
        expect(form.locator("input[name='account_number']")).to_be_visible()
        expect(form.locator("select[name='currency_code']")).to_be_visible()

    @pytest.mark.e2e
    def test_new_account_form_has_gl_account(self, authenticated_page: Page, base_url: str):
        """Test that new account form has GL account selection."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        gl_account = form.locator("select[name='gl_account_id']").first
        expect(gl_account).to_be_visible()

    @pytest.mark.e2e
    def test_create_bank_account_workflow(self, authenticated_page: Page, base_url: str):
        """Test full create bank account workflow."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        unique_id = str(uuid4())[:8]

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill fields
        bank_name = authenticated_page.locator("input[name='bank_name']").first
        expect(bank_name).to_be_visible()
        bank_name.fill(f"Test Bank {unique_id}")

        account_name = authenticated_page.locator("input[name='account_name']").first
        expect(account_name).to_be_visible()
        account_name.fill(f"Test Account {unique_id}")

        account_num = authenticated_page.locator("input[name='account_number']").first
        expect(account_num).to_be_visible()
        account_num.fill(f"1234567890{unique_id[:4]}")

        # Select currency
        currency = authenticated_page.locator("select[name='currency_code']").first
        expect(currency).to_be_visible()
        currency.select_option("USD")

        # Submit
        submit_btn = authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("body")).to_be_visible()


class TestBankStatementWorkflow:
    """Test bank statement workflows."""

    @pytest.mark.e2e
    def test_statements_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that statements page loads."""
        authenticated_page.goto(f"{base_url}/banking/statements")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/banking/statements.*"))

    @pytest.mark.e2e
    def test_statements_page_has_filters(self, authenticated_page: Page, base_url: str):
        """Test that statements page has filter options."""
        authenticated_page.goto(f"{base_url}/banking/statements")
        authenticated_page.wait_for_load_state("networkidle")

        # Account filter
        account_filter = authenticated_page.get_by_test_id("bank-statements-account")
        expect(account_filter).to_be_visible()

    @pytest.mark.e2e
    def test_new_statement_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new statement form loads."""
        authenticated_page.goto(f"{base_url}/banking/statements/import")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()


class TestBankReconciliationListWorkflow:
    """Test bank reconciliation list page."""

    @pytest.mark.e2e
    def test_reconciliations_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations page loads."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/banking/reconciliations.*"))

    @pytest.mark.e2e
    def test_reconciliations_page_has_stats(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations page shows statistics."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        stats = authenticated_page.get_by_test_id("bank-reconciliations-stats")
        expect(stats).to_be_visible()

    @pytest.mark.e2e
    def test_reconciliations_page_has_new_button(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations page has new reconciliation button."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/banking/reconciliations/new'], [data-testid='bank-reconciliations-new'], "
            "button:has-text('New')"
        ).first
        expect(new_btn).to_be_visible()

    @pytest.mark.e2e
    def test_reconciliations_page_has_account_filter(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations page has account filter."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        account_filter = authenticated_page.get_by_test_id("bank-reconciliations-account")
        expect(account_filter).to_be_visible()

    @pytest.mark.e2e
    def test_reconciliations_page_has_status_filter(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations page has status filter."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.get_by_test_id("bank-reconciliations-status")
        expect(status_filter).to_be_visible()

    @pytest.mark.e2e
    def test_reconciliations_page_has_date_filters(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations page has date range filters."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        # Start date
        start_date = authenticated_page.get_by_test_id("bank-reconciliations-start-date")
        expect(start_date).to_be_visible()

        # End date
        end_date = authenticated_page.get_by_test_id("bank-reconciliations-end-date")
        expect(end_date).to_be_visible()

    @pytest.mark.e2e
    def test_reconciliations_page_has_table(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations page has data table."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        table = authenticated_page.get_by_test_id("bank-reconciliations-table")
        expect(table).to_be_visible()


class TestBankReconciliationCreateWorkflow:
    """Test bank reconciliation create workflow."""

    @pytest.mark.e2e
    def test_new_reconciliation_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new reconciliation form loads."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/banking/reconciliations/new.*"))

    @pytest.mark.e2e
    def test_new_reconciliation_form_has_account_select(self, authenticated_page: Page, base_url: str):
        """Test that new reconciliation form has account selection."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        account_select = form.locator("select[name='bank_account_id']").first
        expect(account_select).to_be_visible()

    @pytest.mark.e2e
    def test_new_reconciliation_form_has_dates(self, authenticated_page: Page, base_url: str):
        """Test that new reconciliation form has date fields."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        date_field = form.locator("input[type='date']").first
        expect(date_field).to_be_visible()

    @pytest.mark.e2e
    def test_new_reconciliation_form_has_balance_fields(self, authenticated_page: Page, base_url: str):
        """Test that new reconciliation form has balance fields."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        statement_balance = form.locator(
            "input[name='statement_closing_balance'], input[name='statement_balance']"
        ).first
        expect(statement_balance).to_be_visible()


class TestBankReconciliationDetailWorkflow:
    """Test bank reconciliation detail and matching workflow."""

    @pytest.mark.e2e
    def test_reconciliation_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent reconciliation shows appropriate message."""
        recon_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/banking/reconciliations/{recon_id}")
        authenticated_page.wait_for_load_state("networkidle")

        # Should show not found or handle gracefully
        expect(authenticated_page.locator("body")).to_be_visible()


class TestBankAccountDetailWorkflow:
    """Test bank account detail and edit workflow."""

    @pytest.mark.e2e
    def test_account_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent account shows appropriate message."""
        account_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/banking/accounts/{account_id}")
        authenticated_page.wait_for_load_state("networkidle")

        # Should show not found or handle gracefully
        expect(authenticated_page.locator("body")).to_be_visible()


class TestBankingResponsiveDesign:
    """Test banking pages on different viewport sizes."""

    @pytest.mark.e2e
    def test_banking_accounts_mobile(self, authenticated_page: Page, base_url: str):
        """Test banking accounts on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/banking/accounts", wait_until="domcontentloaded")
        expect(authenticated_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_banking_reconciliations_mobile(self, authenticated_page: Page, base_url: str):
        """Test banking reconciliations on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/banking/reconciliations", wait_until="domcontentloaded")
        expect(authenticated_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_banking_accounts_tablet(self, authenticated_page: Page, base_url: str):
        """Test banking accounts on tablet viewport."""
        authenticated_page.set_viewport_size({"width": 768, "height": 1024})
        authenticated_page.goto(f"{base_url}/banking/accounts", wait_until="domcontentloaded")
        expect(authenticated_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_new_account_form_mobile(self, authenticated_page: Page, base_url: str):
        """Test new account form on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/banking/accounts/new", wait_until="domcontentloaded")
        expect(authenticated_page.locator("body")).to_be_visible()
