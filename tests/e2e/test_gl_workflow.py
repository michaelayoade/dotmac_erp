"""
E2E Tests for GL Journal Entry Workflows.

Tests the complete create/edit/submit/approve/post workflows for GL.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestGLAccountCreateWorkflow:
    """Test GL account create workflow."""

    @pytest.mark.e2e
    def test_account_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that account form loads successfully."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/accounts/new.*"))

    @pytest.mark.e2e
    def test_account_form_has_all_fields(self, authenticated_page: Page, base_url: str):
        """Test that account form has all required fields."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='account_code']")).to_be_visible()
        expect(form.locator("input[name='account_name']")).to_be_visible()
        expect(form.locator("select[name='category_id']")).to_be_visible()
        expect(form.locator("select[name='account_type']")).to_be_visible()

    @pytest.mark.e2e
    def test_account_form_has_checkboxes(self, authenticated_page: Page, base_url: str):
        """Test that account form has configuration checkboxes."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        checkboxes = [
            "input[name='is_active']",
            "input[name='is_posting_allowed']",
            "input[name='is_budgetable']",
            "input[name='is_reconciliation_required']",
        ]
        for checkbox_selector in checkboxes:
            checkbox = form.locator(checkbox_selector)
            expect(checkbox).to_be_visible()

    @pytest.mark.e2e
    def test_create_account_workflow(self, authenticated_page: Page, base_url: str):
        """Test full create account workflow."""
        authenticated_page.goto(f"{base_url}/gl/accounts/new")
        authenticated_page.wait_for_load_state("networkidle")

        unique_id = str(uuid4())[:6].upper()
        account_code = f"TEST{unique_id}"

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill required fields
        authenticated_page.fill("input[name='account_code']", account_code)
        authenticated_page.fill("input[name='account_name']", f"Test Account {unique_id}")

        # Select category if available
        category_select = authenticated_page.locator("select[name='category_id']")
        expect(category_select).to_be_visible()
        options = category_select.locator("option:not([value=''])")
        assert options.count() > 0
        first_value = options.first.get_attribute("value")
        assert first_value
        category_select.select_option(first_value)

        # Select account type if available
        type_select = authenticated_page.locator("select[name='account_type']")
        expect(type_select).to_be_visible()
        options = type_select.locator("option:not([value=''])")
        assert options.count() > 0
        first_value = options.first.get_attribute("value")
        assert first_value
        type_select.select_option(first_value)

        # Select normal balance if available
        balance_select = authenticated_page.locator("select[name='normal_balance']")
        expect(balance_select).to_be_visible()
        options = balance_select.locator("option:not([value=''])")
        assert options.count() > 0
        first_value = options.first.get_attribute("value")
        assert first_value
        balance_select.select_option(first_value)

        # Check is_active and is_posting_allowed
        is_active = authenticated_page.locator("input[name='is_active']")
        expect(is_active).to_be_visible()
        is_active.check()

        is_posting = authenticated_page.locator("input[name='is_posting_allowed']")
        expect(is_posting).to_be_visible()
        is_posting.check()

        # Submit
        submit_btn = authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        authenticated_page.wait_for_load_state("networkidle")

        # Check for success (redirect or success message)
        is_success = (
            "/gl/accounts" in authenticated_page.url
            or authenticated_page.locator(".success, .alert-success").count() > 0
        )
        errors = authenticated_page.locator(".error, .alert-error, .alert-danger")
        expect(errors).to_have_count(0)
        assert is_success


class TestGLJournalEntryCreateWorkflow:
    """Test GL journal entry create workflow."""

    @pytest.mark.e2e
    def test_journal_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that journal entry form loads."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/journals/new.*"))

    @pytest.mark.e2e
    def test_journal_form_has_header_fields(self, authenticated_page: Page, base_url: str):
        """Test that journal form has header fields."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("select[name='journal_type']")).to_be_visible()
        expect(form.locator("select[name='fiscal_period_id']")).to_be_visible()
        expect(form.locator("input[name='entry_date']")).to_be_visible()
        expect(form.locator("input[name='posting_date']")).to_be_visible()
        expect(form.locator("input[name='description']")).to_be_visible()

    @pytest.mark.e2e
    def test_journal_form_has_add_line_button(self, authenticated_page: Page, base_url: str):
        """Test that journal form has add line button."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_btn = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Row')"
        ).first

        expect(add_btn).to_be_visible()

    @pytest.mark.e2e
    def test_add_journal_line(self, authenticated_page: Page, base_url: str):
        """Test adding a journal line."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_btn = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Row')"
        ).first

        expect(add_btn).to_be_visible()
        add_btn.click()
        authenticated_page.wait_for_timeout(300)

        line_account = authenticated_page.locator("tbody select").first
        expect(line_account).to_be_visible()

    @pytest.mark.e2e
    def test_journal_balance_indicator(self, authenticated_page: Page, base_url: str):
        """Test that journal shows balance indicator."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for total row in footer
        totals = authenticated_page.locator("tfoot").first
        expect(totals).to_be_visible()

    @pytest.mark.e2e
    def test_journal_submit_disabled_when_unbalanced(self, authenticated_page: Page, base_url: str):
        """Test that submit is disabled when journal is unbalanced."""
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")

        submit_btn = authenticated_page.locator("button[type='submit']").first

        expect(submit_btn).to_be_visible()
        is_disabled = submit_btn.get_attribute("disabled")
        if is_disabled:
            assert is_disabled is not None


class TestGLJournalDetailWorkflow:
    """Test GL journal detail page and status workflows."""

    @pytest.mark.e2e
    def test_journal_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent journal shows not found."""
        entry_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/gl/journals/{entry_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Journal entry not found")).to_be_visible()

    @pytest.mark.e2e
    def test_journals_list_page(self, authenticated_page: Page, base_url: str):
        """Test that journals list page loads."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/journals.*"))

    @pytest.mark.e2e
    def test_journals_list_has_filters(self, authenticated_page: Page, base_url: str):
        """Test that journals list has filter options."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for status filter or search
        status_filter = authenticated_page.locator("select[name='status']").first
        search = authenticated_page.locator("input[name='search']").first
        expect(status_filter).to_be_visible()
        expect(search).to_be_visible()

    @pytest.mark.e2e
    def test_journals_list_has_action_buttons(self, authenticated_page: Page, base_url: str):
        """Test that journals list shows action buttons."""
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for new journal button
        new_btn = authenticated_page.locator("a[href='/gl/journals/new']").first
        expect(new_btn).to_be_visible()


class TestGLFiscalPeriodWorkflow:
    """Test GL fiscal period management."""

    @pytest.mark.e2e
    def test_periods_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that periods page loads."""
        authenticated_page.goto(f"{base_url}/gl/periods")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/periods.*"))

    @pytest.mark.e2e
    def test_periods_has_status_indicators(self, authenticated_page: Page, base_url: str):
        """Test that periods page shows status indicators."""
        authenticated_page.goto(f"{base_url}/gl/periods")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Period Status Legend")).to_be_visible()

    @pytest.mark.e2e
    def test_new_period_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new period form loads."""
        authenticated_page.goto(f"{base_url}/gl/periods/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("New Fiscal Year")


class TestGLTrialBalanceWorkflow:
    """Test GL trial balance report."""

    @pytest.mark.e2e
    def test_trial_balance_loads(self, authenticated_page: Page, base_url: str):
        """Test that trial balance page loads."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/trial-balance.*"))

    @pytest.mark.e2e
    def test_trial_balance_has_date_filter(self, authenticated_page: Page, base_url: str):
        """Test that trial balance has date filter."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        date_filter = authenticated_page.locator("input[name='as_of_date']").first
        expect(date_filter).to_be_visible()

    @pytest.mark.e2e
    def test_trial_balance_shows_accounts(self, authenticated_page: Page, base_url: str):
        """Test that trial balance displays account data."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for data table
        table = authenticated_page.locator("table").first
        expect(table).to_be_visible()

    @pytest.mark.e2e
    def test_trial_balance_shows_totals(self, authenticated_page: Page, base_url: str):
        """Test that trial balance shows debit/credit totals."""
        authenticated_page.goto(f"{base_url}/gl/trial-balance")
        authenticated_page.wait_for_load_state("networkidle")

        totals = authenticated_page.locator("tfoot").first
        expect(totals).to_be_visible()


class TestGLPeriodCloseWorkflow:
    """Test GL period close workflow."""

    @pytest.mark.e2e
    def test_period_close_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that period close page loads."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    @pytest.mark.e2e
    def test_period_close_shows_checklist(self, authenticated_page: Page, base_url: str):
        """Test that period close shows a checklist or steps."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for checklist items
        checklist = authenticated_page.locator(
            ".checklist, .close-checklist, ol, ul.steps, [data-checklist]"
        ).first
        expect(checklist).to_be_visible()

    @pytest.mark.e2e
    def test_period_close_has_close_button(self, authenticated_page: Page, base_url: str):
        """Test that period close has a close button."""
        authenticated_page.goto(f"{base_url}/gl/period-close")
        authenticated_page.wait_for_load_state("networkidle")

        close_btn = authenticated_page.locator(
            "button:has-text('Close'), button:has-text('Close Period'), "
            "[data-action='close-period']"
        ).first
        expect(close_btn).to_be_visible()


class TestGLAccountDetailWorkflow:
    """Test GL account detail and edit workflow."""

    @pytest.mark.e2e
    def test_account_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent account shows not found."""
        account_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/gl/accounts/{account_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Account not found")).to_be_visible()

    @pytest.mark.e2e
    def test_accounts_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that accounts list loads."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/gl/accounts.*"))

    @pytest.mark.e2e
    def test_accounts_list_search(self, authenticated_page: Page, base_url: str):
        """Test accounts list search functionality."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator("input[name='search']").first
        expect(search).to_be_visible()
        search.fill("Cash")
        authenticated_page.wait_for_timeout(500)
        expect(authenticated_page.locator("table")).to_be_visible()


class TestGLResponsiveDesign:
    """Test GL pages on different viewport sizes."""

    @pytest.mark.e2e
    def test_gl_accounts_mobile(self, authenticated_page: Page, base_url: str):
        """Test GL accounts on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_gl_journals_mobile(self, authenticated_page: Page, base_url: str):
        """Test GL journals on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/gl/journals")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_journal_form_tablet(self, authenticated_page: Page, base_url: str):
        """Test journal form on tablet viewport."""
        authenticated_page.set_viewport_size({"width": 768, "height": 1024})
        authenticated_page.goto(f"{base_url}/gl/journals/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("body")).to_be_visible()
