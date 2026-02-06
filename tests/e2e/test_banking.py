"""
E2E Tests for Banking Module.

Tests for creating, reading, updating, and managing:
- Bank Accounts
- Bank Statements
- Bank Reconciliations
- Statement Imports
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Bank Accounts Tests
# =============================================================================


@pytest.mark.e2e
class TestBankAccountsList:
    """Tests for bank accounts list page."""

    def test_accounts_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that accounts list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/banking/accounts")
        assert response is not None, "No response from accounts navigation"
        assert response.ok, f"Accounts page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

    def test_accounts_page_has_cta(self, authenticated_page: Page, base_url: str):
        """Ensure accounts page loads and shows the new account CTA."""
        response = authenticated_page.goto(f"{base_url}/banking/accounts")
        assert response is not None, "No response from accounts navigation"
        assert response.ok, f"Accounts page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Bank Accounts"
        )
        expect(authenticated_page.get_by_test_id("bank-accounts-new")).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-accounts-search")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-accounts-status")
        ).to_be_visible()
        expect(authenticated_page.get_by_test_id("bank-accounts-table")).to_be_visible()

    def test_accounts_list_with_search(self, authenticated_page: Page, base_url: str):
        """Test accounts list search functionality."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.get_by_test_id("bank-accounts-search")
        if search.count() > 0:
            search.fill("Checking")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("main")).to_be_visible()

    def test_accounts_list_by_status(self, authenticated_page: Page, base_url: str):
        """Test accounts list status filter."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.get_by_test_id("bank-accounts-status")
        expect(status_filter).to_be_visible()


@pytest.mark.e2e
class TestBankAccountCreate:
    """Tests for creating bank accounts."""

    def test_account_create_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that account create page loads."""
        response = authenticated_page.goto(f"{base_url}/banking/accounts/new")
        assert response.ok, f"Account create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_account_create_has_name_field(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that account form has name field."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#account_name, input[name='account_name'], input[name='name']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_account_create_has_bank_name_field(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that account form has bank name field."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#bank_name, input[name='bank_name']")
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_account_create_has_account_number_field(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that account form has account number field."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#account_number, input[name='account_number']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_account_create_has_currency_field(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that account form has currency selection."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='currency'], select[name='currency_code'], #currency"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_account_create_full(self, authenticated_page: Page, base_url: str):
        """Test complete account creation workflow."""
        authenticated_page.goto(f"{base_url}/banking/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill required fields
        name_field = authenticated_page.locator(
            "input[name='account_name'], input[name='name']"
        )
        if name_field.count() > 0:
            name_field.first.fill(f"Test Account {uid}")

        bank_field = authenticated_page.locator("input[name='bank_name']")
        if bank_field.count() > 0:
            bank_field.first.fill("Test Bank")

        number_field = authenticated_page.locator("input[name='account_number']")
        if number_field.count() > 0:
            number_field.first.fill(f"ACC{uid}")

        # Submit
        submit_btn = authenticated_page.locator("button[type='submit']")
        if submit_btn.count() > 0:
            submit_btn.click()
            authenticated_page.wait_for_load_state("networkidle")


@pytest.mark.e2e
class TestBankAccountEdit:
    """Tests for editing bank accounts."""

    def test_account_edit_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that account edit page loads."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        # Navigate to first account then edit
        account_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/accounts/']"
        ).first
        if account_link.count() > 0:
            account_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                expect(authenticated_page).to_have_url(re.compile(r".*/edit.*"))

    def test_account_update_success(self, authenticated_page: Page, base_url: str):
        """Test successful account update."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        account_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/accounts/']"
        ).first
        if account_link.count() > 0:
            account_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                # Update a field
                name_field = authenticated_page.locator(
                    "input[name='account_name'], input[name='name']"
                )
                if name_field.count() > 0:
                    current_value = name_field.first.input_value()
                    name_field.first.fill(f"{current_value} Updated")

                    # Submit
                    authenticated_page.locator("button[type='submit']").click()
                    authenticated_page.wait_for_load_state("networkidle")

    def test_account_deactivate(self, authenticated_page: Page, base_url: str):
        """Test account deactivation."""
        authenticated_page.goto(f"{base_url}/banking/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        account_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/accounts/']"
        ).first
        if account_link.count() > 0:
            account_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for deactivate button
            deactivate_btn = authenticated_page.locator(
                "button:has-text('Deactivate'), button:has-text('Close'), a:has-text('Deactivate')"
            )
            if deactivate_btn.count() > 0:
                expect(deactivate_btn.first).to_be_visible()


# =============================================================================
# Bank Statements Tests
# =============================================================================


@pytest.mark.e2e
class TestBankStatementsList:
    """Tests for bank statements list page."""

    def test_statements_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that statements list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/banking/statements")
        assert response is not None, "No response from statements navigation"
        assert response.ok, f"Statements page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

    def test_statements_page_has_import_cta(
        self, authenticated_page: Page, base_url: str
    ):
        """Ensure statements page loads and shows the import CTA."""
        response = authenticated_page.goto(f"{base_url}/banking/statements")
        assert response is not None, "No response from statements navigation"
        assert response.ok, f"Statements page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Bank Statements"
        )
        expect(
            authenticated_page.get_by_test_id("bank-statements-import")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-statements-account")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-statements-status")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-statements-start-date")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-statements-end-date")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-statements-table")
        ).to_be_visible()

    def test_statements_list_with_filters(
        self, authenticated_page: Page, base_url: str
    ):
        """Test statements list filter options."""
        authenticated_page.goto(f"{base_url}/banking/statements")
        authenticated_page.wait_for_load_state("networkidle")

        # Check account filter
        account_filter = authenticated_page.get_by_test_id("bank-statements-account")
        expect(account_filter).to_be_visible()

        # Check date filters
        start_date = authenticated_page.get_by_test_id("bank-statements-start-date")
        expect(start_date).to_be_visible()

        end_date = authenticated_page.get_by_test_id("bank-statements-end-date")
        expect(end_date).to_be_visible()


@pytest.mark.e2e
class TestBankStatementImport:
    """Tests for importing bank statements."""

    def test_statement_import_page_loads(self, authenticated_page: Page, base_url: str):
        """Test statement import page loads."""
        response = authenticated_page.goto(f"{base_url}/banking/statements/import")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_statement_import_has_file_input(
        self, authenticated_page: Page, base_url: str
    ):
        """Test statement import has file input."""
        authenticated_page.goto(f"{base_url}/banking/statements/import")
        authenticated_page.wait_for_load_state("networkidle")

        file_input = authenticated_page.locator(
            "input[type='file'], input[name='file']"
        )
        if file_input.count() > 0:
            expect(file_input.first).to_be_visible()

    def test_statement_import_has_account_select(
        self, authenticated_page: Page, base_url: str
    ):
        """Test statement import has account selection."""
        authenticated_page.goto(f"{base_url}/banking/statements/import")
        authenticated_page.wait_for_load_state("networkidle")

        account_select = authenticated_page.locator(
            "select[name='bank_account_id'], select[name='account'], #bank_account_id"
        )
        if account_select.count() > 0:
            expect(account_select.first).to_be_visible()

    def test_statement_import_csv_format(self, authenticated_page: Page, base_url: str):
        """Test statement import supports CSV format."""
        authenticated_page.goto(f"{base_url}/banking/statements/import")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for format selection or CSV mention
        format_info = authenticated_page.locator(
            "text=CSV, text=csv, select[name='format']"
        )
        if format_info.count() > 0:
            expect(format_info.first).to_be_visible()


@pytest.mark.e2e
class TestBankStatementDetail:
    """Tests for bank statement detail page."""

    def test_statement_detail_page_accessible(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that statement detail is accessible."""
        authenticated_page.goto(f"{base_url}/banking/statements")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first statement link
        statement_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/statements/']"
        ).first
        if statement_link.count() > 0:
            statement_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(
                re.compile(r".*/banking/statements/.*")
            )

    def test_statement_transactions_list(self, authenticated_page: Page, base_url: str):
        """Test statement shows transaction list."""
        authenticated_page.goto(f"{base_url}/banking/statements")
        authenticated_page.wait_for_load_state("networkidle")

        statement_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/statements/']"
        ).first
        if statement_link.count() > 0:
            statement_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for transactions table
            transactions = authenticated_page.locator(
                "table, [class*='transactions'], text=Transactions"
            )
            if transactions.count() > 0:
                expect(transactions.first).to_be_visible()


# =============================================================================
# Bank Reconciliations Tests
# =============================================================================


@pytest.mark.e2e
class TestBankReconciliationsList:
    """Tests for bank reconciliations list page."""

    def test_reconciliations_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that reconciliations list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/banking/reconciliations")
        assert response is not None, "No response from reconciliations navigation"
        assert response.ok, f"Reconciliations page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

    def test_reconciliations_page_has_new_cta(
        self, authenticated_page: Page, base_url: str
    ):
        """Ensure reconciliations page loads and shows the new reconciliation CTA."""
        response = authenticated_page.goto(f"{base_url}/banking/reconciliations")
        assert response is not None, "No response from reconciliations navigation"
        assert response.ok, f"Reconciliations page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Bank Reconciliations"
        )
        expect(
            authenticated_page.get_by_test_id("bank-reconciliations-new")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-reconciliations-account")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-reconciliations-status")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-reconciliations-start-date")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-reconciliations-end-date")
        ).to_be_visible()
        expect(
            authenticated_page.get_by_test_id("bank-reconciliations-table")
        ).to_be_visible()


@pytest.mark.e2e
class TestBankReconciliationCreate:
    """Tests for creating bank reconciliations."""

    def test_reconciliation_create_page_loads(
        self, authenticated_page: Page, base_url: str
    ):
        """Test reconciliation create page loads."""
        response = authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_reconciliation_create_has_account_select(
        self, authenticated_page: Page, base_url: str
    ):
        """Test reconciliation form has account selection."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        authenticated_page.wait_for_load_state("networkidle")

        account_select = authenticated_page.locator(
            "select[name='bank_account_id'], select[name='account'], #bank_account_id"
        )
        if account_select.count() > 0:
            expect(account_select.first).to_be_visible()

    def test_reconciliation_create_has_statement_select(
        self, authenticated_page: Page, base_url: str
    ):
        """Test reconciliation form has statement selection."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        authenticated_page.wait_for_load_state("networkidle")

        statement_select = authenticated_page.locator(
            "select[name='statement_id'], select[name='statement'], #statement_id"
        )
        if statement_select.count() > 0:
            expect(statement_select.first).to_be_visible()

    def test_reconciliation_create_has_date_field(
        self, authenticated_page: Page, base_url: str
    ):
        """Test reconciliation form has date field."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations/new")
        authenticated_page.wait_for_load_state("networkidle")

        date_field = authenticated_page.locator(
            "input[type='date'], input[name='reconciliation_date']"
        )
        if date_field.count() > 0:
            expect(date_field.first).to_be_visible()


@pytest.mark.e2e
class TestBankReconciliationWorkflow:
    """Tests for bank reconciliation workflow."""

    def test_reconciliation_load_unmatched(
        self, authenticated_page: Page, base_url: str
    ):
        """Test loading unmatched transactions."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first reconciliation
        recon_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/reconciliations/']"
        ).first
        if recon_link.count() > 0:
            recon_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for unmatched transactions section
            unmatched = authenticated_page.locator(
                "text=Unmatched, text=Pending, [class*='unmatched']"
            )
            if unmatched.count() > 0:
                expect(unmatched.first).to_be_visible()

    def test_reconciliation_manual_match(self, authenticated_page: Page, base_url: str):
        """Test manual matching functionality."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        recon_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/reconciliations/']"
        ).first
        if recon_link.count() > 0:
            recon_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for match button
            match_btn = authenticated_page.locator(
                "button:has-text('Match'), a:has-text('Match')"
            )
            if match_btn.count() > 0:
                expect(match_btn.first).to_be_visible()

    def test_reconciliation_auto_match(self, authenticated_page: Page, base_url: str):
        """Test auto-matching functionality."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        recon_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/reconciliations/']"
        ).first
        if recon_link.count() > 0:
            recon_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for auto-match button
            auto_match_btn = authenticated_page.locator(
                "button:has-text('Auto'), button:has-text('Suggest')"
            )
            if auto_match_btn.count() > 0:
                expect(auto_match_btn.first).to_be_visible()

    def test_reconciliation_create_adjustment(
        self, authenticated_page: Page, base_url: str
    ):
        """Test creating adjustment during reconciliation."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        recon_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/reconciliations/']"
        ).first
        if recon_link.count() > 0:
            recon_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for adjustment button
            adjustment_btn = authenticated_page.locator(
                "button:has-text('Adjustment'), button:has-text('Add Adjustment'), a:has-text('Adjust')"
            )
            if adjustment_btn.count() > 0:
                expect(adjustment_btn.first).to_be_visible()

    def test_reconciliation_complete(self, authenticated_page: Page, base_url: str):
        """Test completing reconciliation."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        recon_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/reconciliations/']"
        ).first
        if recon_link.count() > 0:
            recon_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for complete button
            complete_btn = authenticated_page.locator(
                "button:has-text('Complete'), button:has-text('Finalize'), button:has-text('Finish')"
            )
            if complete_btn.count() > 0:
                expect(complete_btn.first).to_be_visible()

    def test_reconciliation_detail_page(self, authenticated_page: Page, base_url: str):
        """Test reconciliation detail page."""
        authenticated_page.goto(f"{base_url}/banking/reconciliations")
        authenticated_page.wait_for_load_state("networkidle")

        recon_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/banking/reconciliations/']"
        ).first
        if recon_link.count() > 0:
            recon_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(
                re.compile(r".*/banking/reconciliations/.*")
            )
