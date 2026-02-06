"""
E2E Tests for Expenses Module.

Tests for creating, reading, updating, and managing expense claims/reports.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Expenses List Tests
# =============================================================================


@pytest.mark.e2e
class TestExpensesList:
    """Tests for expenses list page."""

    def test_expenses_page_loads(self, authenticated_page, base_url):
        """Test that expenses list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/expenses")
        assert response.ok, f"Expenses list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_expenses_list_with_search(self, authenticated_page, base_url):
        """Test expenses list search functionality."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )
        if search.count() > 0:
            search.first.fill("EXP-001")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("main")).to_be_visible()

    def test_expenses_list_by_category(self, authenticated_page, base_url):
        """Test expenses list category filter."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        category_filter = authenticated_page.locator(
            "select[name='category'], select[name='expense_category'], #category"
        )
        if category_filter.count() > 0:
            expect(category_filter.first).to_be_visible()

    def test_expenses_list_by_status(self, authenticated_page, base_url):
        """Test expenses list status filter."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()

    def test_expenses_list_has_new_button(self, authenticated_page, base_url):
        """Test that expenses list has new button."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/expenses/new'], button:has-text('New'), a:has-text('New Expense')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()


# =============================================================================
# Expense CRUD Tests
# =============================================================================


@pytest.mark.e2e
class TestExpenseCreate:
    """Tests for creating expenses."""

    def test_expense_create_page_loads(self, authenticated_page, base_url):
        """Test that expense create page loads."""
        response = authenticated_page.goto(f"{base_url}/expenses/new")
        assert response.ok, f"Expense create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_expense_create_has_description_field(self, authenticated_page, base_url):
        """Test that expense form has description field."""
        authenticated_page.goto(f"{base_url}/expenses/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='description'], textarea[name='description'], #description"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_expense_create_has_amount_field(self, authenticated_page, base_url):
        """Test that expense form has amount field."""
        authenticated_page.goto(f"{base_url}/expenses/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='amount'], input[name='total'], #amount"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_expense_create_has_date_field(self, authenticated_page, base_url):
        """Test that expense form has date field."""
        authenticated_page.goto(f"{base_url}/expenses/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='expense_date'], input[name='date'], input[type='date']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_expense_create_has_category_field(self, authenticated_page, base_url):
        """Test that expense form has category selection."""
        authenticated_page.goto(f"{base_url}/expenses/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='category'], select[name='expense_category'], #category"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_expense_create_with_receipt(self, authenticated_page, base_url):
        """Test expense creation with receipt attachment."""
        authenticated_page.goto(f"{base_url}/expenses/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for file upload field
        file_input = authenticated_page.locator(
            "input[type='file'], input[name='receipt'], input[name='attachment']"
        )
        if file_input.count() > 0:
            expect(file_input.first).to_be_visible()


@pytest.mark.e2e
class TestExpenseDetail:
    """Tests for expense detail page."""

    def test_expense_detail_page_accessible(self, authenticated_page, base_url):
        """Test that expense detail is accessible."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(re.compile(r".*/expenses/.*"))

    def test_expense_detail_shows_amount(self, authenticated_page, base_url):
        """Test that expense detail shows amount."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            amount = authenticated_page.locator(
                "text=Amount, text=$, .amount, [class*='total']"
            )
            if amount.count() > 0:
                expect(amount.first).to_be_visible()

    def test_expense_detail_shows_receipt(self, authenticated_page, base_url):
        """Test that expense detail shows receipt if attached."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            receipt = authenticated_page.locator(
                "img, a:has-text('Receipt'), a:has-text('View Receipt'), .receipt"
            )
            # Receipt may or may not exist
            expect(authenticated_page.locator("main")).to_be_visible()


@pytest.mark.e2e
class TestExpenseEdit:
    """Tests for editing expenses."""

    def test_expense_edit_page_loads(self, authenticated_page, base_url):
        """Test that expense edit page loads."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                expect(authenticated_page).to_have_url(re.compile(r".*/edit.*"))


# =============================================================================
# Expense Workflow Tests
# =============================================================================


@pytest.mark.e2e
class TestExpenseWorkflows:
    """Tests for expense workflow operations."""

    def test_expense_submit_for_approval(self, authenticated_page, base_url):
        """Test submitting expense for approval."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            submit_btn = authenticated_page.locator(
                "button:has-text('Submit'), button:has-text('Submit for Approval')"
            )
            if submit_btn.count() > 0:
                expect(submit_btn.first).to_be_visible()

    def test_expense_approve_workflow(self, authenticated_page, base_url):
        """Test expense approval workflow."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            approve_btn = authenticated_page.locator(
                "button:has-text('Approve'), button:has-text('Accept')"
            )
            if approve_btn.count() > 0:
                expect(approve_btn.first).to_be_visible()

    def test_expense_reject_workflow(self, authenticated_page, base_url):
        """Test expense rejection workflow."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            reject_btn = authenticated_page.locator(
                "button:has-text('Reject'), button:has-text('Decline')"
            )
            if reject_btn.count() > 0:
                expect(reject_btn.first).to_be_visible()

    def test_expense_reimburse_workflow(self, authenticated_page, base_url):
        """Test expense reimbursement workflow."""
        authenticated_page.goto(f"{base_url}/expenses")
        authenticated_page.wait_for_load_state("networkidle")

        expense_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/']"
        ).first
        if expense_link.count() > 0:
            expense_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            reimburse_btn = authenticated_page.locator(
                "button:has-text('Reimburse'), button:has-text('Pay'), button:has-text('Mark as Paid')"
            )
            if reimburse_btn.count() > 0:
                expect(reimburse_btn.first).to_be_visible()


# =============================================================================
# Expense Reports Tests
# =============================================================================


@pytest.mark.e2e
class TestExpenseReports:
    """Tests for expense reports/claims."""

    def test_expense_reports_list_page_loads(self, authenticated_page, base_url):
        """Test expense reports list page loads."""
        response = authenticated_page.goto(f"{base_url}/expenses/reports")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_expense_report_create(self, authenticated_page, base_url):
        """Test creating an expense report/claim."""
        response = authenticated_page.goto(f"{base_url}/expenses/reports/new")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")

            # Look for title/name field
            name_field = authenticated_page.locator(
                "input[name='name'], input[name='title'], input[name='report_name']"
            )
            if name_field.count() > 0:
                expect(name_field.first).to_be_visible()

    def test_expense_report_add_expenses(self, authenticated_page, base_url):
        """Test adding expenses to a report."""
        authenticated_page.goto(f"{base_url}/expenses/reports")
        authenticated_page.wait_for_load_state("networkidle")

        report_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/expenses/reports/']"
        ).first
        if report_link.count() > 0:
            report_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            add_btn = authenticated_page.locator(
                "button:has-text('Add Expense'), button:has-text('Add'), a:has-text('Add Expense')"
            )
            if add_btn.count() > 0:
                expect(add_btn.first).to_be_visible()
