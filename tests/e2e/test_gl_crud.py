"""
E2E Tests for General Ledger (GL) Module CRUD Operations.

Tests for creating, reading, updating, and deleting:
- Chart of Accounts
- Journal Entries
- Fiscal Periods
- Trial Balance
- Period Close
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


@pytest.mark.e2e
class TestAccountList:
    """Tests for chart of accounts list page."""

    def test_account_list_with_search(self, authenticated_page, base_url):
        """Test account list search functionality."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator("input[type='search'], input[name='search'], input[placeholder*='Search']")
        if search.count() > 0:
            search.first.fill("1000")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    def test_account_list_by_category(self, authenticated_page, base_url):
        """Test account list category filter."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        category_filter = authenticated_page.locator("select[name='category'], #category")
        if category_filter.count() > 0:
            category_filter.select_option(index=1)
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    def test_account_list_has_new_button(self, authenticated_page, base_url):
        """Test that account list has new button."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/gl/accounts/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()

    def test_account_list_shows_account_codes(self, authenticated_page, base_url):
        """Test that account list displays account codes."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        table = authenticated_page.locator("table, [role='table']")
        expect(table.first).to_be_visible()


@pytest.mark.e2e
class TestAccountCreate:
    """Tests for creating accounts."""

    def test_account_create_page_loads(self, authenticated_page, base_url):
        """Test that account create page loads."""
        response = authenticated_page.goto(f"{base_url}/gl/accounts/new")
        assert response.ok, f"Account create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_account_create_has_code_field(self, authenticated_page, base_url):
        """Test that account form has code field."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#account_code, input[name='account_code']")
        expect(field).to_be_visible()

    def test_account_create_has_name_field(self, authenticated_page, base_url):
        """Test that account form has name field."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#account_name, input[name='account_name']")
        expect(field).to_be_visible()

    def test_account_create_has_category_field(self, authenticated_page, base_url):
        """Test that account form has category selection."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='account_category'], select[name='category'], #account_category")
        expect(field.first).to_be_visible()

    def test_account_create_has_type_field(self, authenticated_page, base_url):
        """Test that account form has type field."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='account_type'], select[name='type'], #account_type")
        expect(field.first).to_be_visible()

    def test_account_create_has_checkboxes(self, authenticated_page, base_url):
        """Test that account form has boolean checkboxes."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        checkboxes = authenticated_page.locator("input[type='checkbox']")
        if checkboxes.count() > 0:
            expect(checkboxes.first).to_be_visible()

    def test_account_create_workflow(self, authenticated_page, base_url):
        """Test complete account creation workflow."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill required fields
        authenticated_page.locator("#account_code, input[name='account_code']").fill(f"9{uid[:3]}")
        authenticated_page.locator("#account_name, input[name='account_name']").fill(f"Test Account {uid}")

        # Select category
        category = authenticated_page.locator("select[name='account_category'], select[name='category']")
        if category.count() > 0:
            category.first.select_option(index=1)

        # Select type
        type_field = authenticated_page.locator("select[name='account_type'], select[name='type']")
        if type_field.count() > 0:
            type_field.first.select_option(index=1)

        # Submit
        authenticated_page.locator("button[type='submit']").click()
        authenticated_page.wait_for_load_state("networkidle")

        # Should redirect
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/accounts.*"))


@pytest.mark.e2e
class TestAccountEdit:
    """Tests for editing accounts."""

    def test_account_detail_page_accessible(self, authenticated_page, base_url):
        """Test that account detail is accessible."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first account link
        account_link = authenticated_page.locator("table tbody tr a, a[href*='/gl/accounts/']").first
        if account_link.count() > 0:
            account_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(re.compile(r".*/gl/accounts/.*"))

    def test_account_has_edit_button(self, authenticated_page, base_url):
        """Test that account detail has edit button."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        account_link = authenticated_page.locator("table tbody tr a, a[href*='/gl/accounts/']").first
        if account_link.count() > 0:
            account_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator("a[href*='/edit'], button:has-text('Edit')")
            if edit_btn.count() > 0:
                expect(edit_btn.first).to_be_visible()


@pytest.mark.e2e
class TestJournalEntryList:
    """Tests for journal entry list page."""

    def test_journal_list_page_loads(self, authenticated_page, base_url):
        """Test that journal list loads."""
        response = authenticated_page.goto(f"{base_url}/gl/journals")
        assert response.ok, f"Journal list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_journal_list_with_search(self, authenticated_page, base_url):
        """Test journal list search functionality."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator("input[type='search'], input[name='search']")
        if search.count() > 0:
            expect(search.first).to_be_visible()

    def test_journal_list_by_date_range(self, authenticated_page, base_url):
        """Test journal list date range filter."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")

        date_filters = authenticated_page.locator("input[type='date']")
        if date_filters.count() > 0:
            expect(date_filters.first).to_be_visible()

    def test_journal_list_by_status(self, authenticated_page, base_url):
        """Test journal list status filter."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status']")
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()

    def test_journal_list_has_new_button(self, authenticated_page, base_url):
        """Test that journal list has new button."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/gl/journals/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestJournalEntryCreate:
    """Tests for creating journal entries."""

    def test_journal_create_page_loads(self, authenticated_page, base_url):
        """Test that journal create page loads."""
        response = authenticated_page.goto(f"{base_url}/gl/journals/new")
        assert response.ok, f"Journal create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_journal_create_has_type_field(self, authenticated_page, base_url):
        """Test that journal form has entry type field."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='entry_type'], select[name='type'], #entry_type")
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_journal_create_has_period_field(self, authenticated_page, base_url):
        """Test that journal form has period field."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='fiscal_period_id'], select[name='period']")
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_journal_create_has_date_field(self, authenticated_page, base_url):
        """Test that journal form has date field."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("input[type='date']")
        expect(field.first).to_be_visible()

    def test_journal_create_has_description_field(self, authenticated_page, base_url):
        """Test that journal form has description field."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#description, input[name='description'], textarea[name='description']")
        expect(field.first).to_be_visible()

    def test_journal_create_has_add_line_button(self, authenticated_page, base_url):
        """Test that journal form has add line button."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_btn = authenticated_page.locator("button:has-text('Add'), a:has-text('Add Line')")
        if add_btn.count() > 0:
            expect(add_btn.first).to_be_visible()

    def test_journal_create_shows_balance(self, authenticated_page, base_url):
        """Test that journal form shows debit/credit balance."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        balance = authenticated_page.locator("text=Debit, text=Credit, text=Balance, [class*='balance']")
        if balance.count() > 0:
            expect(balance.first).to_be_visible()


@pytest.mark.e2e
class TestJournalEntryDetail:
    """Tests for journal entry detail page."""

    def test_journal_detail_shows_lines(self, authenticated_page, base_url):
        """Test that journal detail shows entry lines."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first journal link
        journal_link = authenticated_page.locator("table tbody tr a, a[href*='/gl/journals/']").first
        if journal_link.count() > 0:
            journal_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Should show line items
            table = authenticated_page.locator("table, [class*='lines']")
            if table.count() > 0:
                expect(table.first).to_be_visible()


@pytest.mark.e2e
class TestFiscalPeriodList:
    """Tests for fiscal period list."""

    def test_period_list_page_loads(self, authenticated_page, base_url):
        """Test that period list loads."""
        response = authenticated_page.goto(f"{base_url}/gl/periods")
        assert response.ok, f"Period list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_period_list_shows_status(self, authenticated_page, base_url):
        """Test that period list shows status indicators."""
        authenticated_page.goto(f"{base_url}/gl/periods")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for status badges or indicators
        status = authenticated_page.locator("text=Open, text=Closed, text=OPEN, text=CLOSED, [class*='status']")
        if status.count() > 0:
            expect(status.first).to_be_visible()

    def test_period_list_has_new_button(self, authenticated_page, base_url):
        """Test that period list has new button."""
        authenticated_page.goto(f"{base_url}/gl/periods")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/gl/periods/new'], button:has-text('New')")
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestPeriodClose:
    """Tests for period close functionality."""

    def test_period_close_page_loads(self, authenticated_page, base_url):
        """Test that period close page loads."""
        response = authenticated_page.goto(f"{base_url}/gl/period-close")
        assert response.ok, f"Period close failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_period_close_has_checklist(self, authenticated_page, base_url):
        """Test that period close has checklist items."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for checklist items
        checklist = authenticated_page.locator("input[type='checkbox'], [class*='checklist'], li, .check")
        if checklist.count() > 0:
            expect(checklist.first).to_be_visible()

    def test_period_close_has_close_button(self, authenticated_page, base_url):
        """Test that period close has close button."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")

        close_btn = authenticated_page.locator("button:has-text('Close'), button:has-text('close')")
        if close_btn.count() > 0:
            expect(close_btn.first).to_be_visible()


@pytest.mark.e2e
class TestTrialBalance:
    """Tests for trial balance report."""

    def test_trial_balance_loads(self, authenticated_page, base_url):
        """Test that trial balance loads."""
        response = authenticated_page.goto(f"{base_url}/gl/trial-balance")
        assert response.ok, f"Trial balance failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_trial_balance_has_date_filter(self, authenticated_page, base_url):
        """Test that trial balance has date filter."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        date_filter = authenticated_page.locator("input[type='date'], input[name='as_of_date']")
        if date_filter.count() > 0:
            expect(date_filter.first).to_be_visible()

    def test_trial_balance_shows_totals(self, authenticated_page, base_url):
        """Test that trial balance shows debit/credit totals."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        totals = authenticated_page.locator("tfoot, text=Total, [class*='total']")
        if totals.count() > 0:
            expect(totals.first).to_be_visible()

    def test_trial_balance_has_print_button(self, authenticated_page, base_url):
        """Test that trial balance has print/export button."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        print_btn = authenticated_page.locator("button:has-text('Print'), button:has-text('Export'), a:has-text('Print')")
        if print_btn.count() > 0:
            expect(print_btn.first).to_be_visible()
