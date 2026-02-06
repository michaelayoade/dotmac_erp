"""
E2E Tests for Accounts Receivable Module.

Tests the AR UI flows using Playwright.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestARCustomersNavigation:
    """Test AR customers page navigation."""

    @pytest.mark.e2e
    def test_customers_page_loads(self, ar_customers_page: Page):
        """Test that customers page loads successfully."""
        expect(ar_customers_page).to_have_url(re.compile(r".*/ar/customers.*"))
        expect(ar_customers_page.get_by_test_id("page-title")).to_contain_text(
            "Customers"
        )

    @pytest.mark.e2e
    def test_customers_page_has_content(self, ar_customers_page: Page):
        """Test that customers page has content."""
        expect(ar_customers_page.locator("table")).to_be_visible()

    @pytest.mark.e2e
    def test_new_customer_button_navigation(self, ar_customers_page: Page):
        """Test clicking new customer button navigates to form."""
        new_btn = ar_customers_page.locator("a[href='/ar/customers/new']").first
        expect(new_btn).to_be_visible()
        new_btn.click()
        ar_customers_page.wait_for_load_state("networkidle")
        expect(ar_customers_page.get_by_test_id("page-title")).to_contain_text(
            "New Customer"
        )


class TestARCustomersSearch:
    """Test AR customers search functionality."""

    @pytest.mark.e2e
    def test_customers_search_works(self, ar_customers_page: Page):
        """Test that search functionality works."""
        search = ar_customers_page.locator("input[name='search']").first
        expect(search).to_be_visible()
        search.fill("John")
        ar_customers_page.wait_for_timeout(500)
        expect(ar_customers_page.locator("table")).to_be_visible()


class TestARCustomerForm:
    """Test AR customer form interactions."""

    @pytest.mark.e2e
    def test_customer_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that customer form loads."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "New Customer"
        )

    @pytest.mark.e2e
    def test_customer_form_has_fields(self, authenticated_page: Page, base_url: str):
        """Test that customer form has expected fields."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='customer_code']")).to_be_visible()
        expect(form.locator("input[name='customer_name']")).to_be_visible()


class TestARInvoices:
    """Test AR invoices page."""

    @pytest.mark.e2e
    def test_invoices_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that invoices page loads."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/invoices.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "AR Invoices"
        )

    @pytest.mark.e2e
    def test_new_invoice_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new invoice form loads."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "New AR Invoice"
        )
        expect(authenticated_page.locator("form").first).to_be_visible()


class TestARReceipts:
    """Test AR receipts page."""

    @pytest.mark.e2e
    def test_receipts_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that receipts page loads."""
        authenticated_page.goto(f"{base_url}/ar/receipts")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/receipts.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "AR Receipts"
        )


class TestARAgingReport:
    """Test AR aging report page."""

    @pytest.mark.e2e
    def test_aging_report_loads(self, authenticated_page: Page, base_url: str):
        """Test that aging report loads."""
        authenticated_page.goto(f"{base_url}/ar/aging")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/aging.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "AR Aging Report"
        )

    @pytest.mark.e2e
    def test_aging_report_with_date(self, authenticated_page: Page, base_url: str):
        """Test aging report with date filter."""
        authenticated_page.goto(f"{base_url}/ar/aging?as_of_date=2024-06-30")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "AR Aging Report"
        )


class TestARInvoiceWorkflow:
    """Test AR invoice creation workflow."""

    @pytest.mark.e2e
    def test_invoice_form_has_required_fields(
        self, authenticated_page: Page, base_url: str
    ):
        """Test invoice form has all required fields."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        customer_field = form.locator("select[name='customer_id']")
        expect(customer_field).to_be_visible()
        date_field = form.locator("input[name='invoice_date']")
        expect(date_field).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_line_items_section(self, authenticated_page: Page, base_url: str):
        """Test invoice line items section exists."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for line items table or section
        lines_section = authenticated_page.locator("table.lines-table").first
        expect(lines_section).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_add_line_button(self, authenticated_page: Page, base_url: str):
        """Test add line button exists on invoice form."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_line_btn = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Row'), "
            "[data-action='add-line'], .add-line-btn, button:has-text('+')"
        ).first

        expect(add_line_btn).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_totals_display(self, authenticated_page: Page, base_url: str):
        """Test invoice totals section displays."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for totals section
        totals = authenticated_page.locator(".totals-section").first
        expect(totals).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_new_button_exists(self, authenticated_page: Page, base_url: str):
        """Test new invoice button exists on list page."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href='/ar/invoices/new']").first
        expect(new_btn).to_be_visible()


class TestARReceiptWorkflow:
    """Test AR payment receipt workflow."""

    @pytest.mark.e2e
    def test_receipt_form_loads(self, authenticated_page: Page, base_url: str):
        """Test receipt form loads."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "New AR Receipt"
        )
        expect(authenticated_page.locator("form").first).to_be_visible()

    @pytest.mark.e2e
    def test_receipt_has_customer_field(self, authenticated_page: Page, base_url: str):
        """Test receipt form has customer field."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("label", has_text="Customer")).to_be_visible()
        expect(form.locator("select").first).to_be_visible()

    @pytest.mark.e2e
    def test_receipt_has_amount_field(self, authenticated_page: Page, base_url: str):
        """Test receipt form has amount field."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        amount_field = authenticated_page.locator("input[placeholder='0.00']").first
        expect(amount_field).to_be_visible()

    @pytest.mark.e2e
    def test_receipt_invoice_allocation(self, authenticated_page: Page, base_url: str):
        """Test receipt can allocate to invoices."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        expect(
            authenticated_page.get_by_role("heading", name="Open Invoices")
        ).to_be_visible()
        expect(authenticated_page.locator("table").first).to_be_visible()

    @pytest.mark.e2e
    def test_receipt_payment_method_field(
        self, authenticated_page: Page, base_url: str
    ):
        """Test receipt has payment method selection."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("label", has_text="Payment Method")).to_be_visible()
        expect(form.locator("select").nth(1)).to_be_visible()


class TestARCreditNote:
    """Test AR credit note workflow."""

    @pytest.mark.e2e
    def test_credit_note_page_loads(self, authenticated_page: Page, base_url: str):
        """Test credit note page loads."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "AR Credit Notes"
        )

    @pytest.mark.e2e
    def test_create_credit_note_form(self, authenticated_page: Page, base_url: str):
        """Test create credit note form loads."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "New Credit Note"
        )

    @pytest.mark.e2e
    def test_credit_note_reason_field(self, authenticated_page: Page, base_url: str):
        """Test credit note has reason field."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes/new")
        authenticated_page.wait_for_load_state("networkidle")

        reason_field = authenticated_page.locator(
            "select[name='reason'], textarea[name='reason'], "
            "input[name='reason'], [data-field='reason']"
        ).first

        expect(reason_field).to_be_visible()


class TestARDetailPages:
    """Test AR detail pages handle missing records."""

    @pytest.mark.e2e
    def test_customer_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure customer detail shows not found state."""
        customer_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ar/customers/{customer_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Customer not found")).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure invoice detail shows not found state."""
        invoice_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ar/invoices/{invoice_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Invoice not found")).to_be_visible()

    @pytest.mark.e2e
    def test_receipt_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure receipt detail shows not found state."""
        receipt_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ar/receipts/{receipt_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Receipt not found")).to_be_visible()
