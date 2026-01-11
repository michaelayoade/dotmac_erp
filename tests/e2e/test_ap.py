"""
E2E Tests for Accounts Payable Module.

Tests the AP UI flows using Playwright.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestAPSuppliersNavigation:
    """Test AP suppliers page navigation."""

    @pytest.mark.e2e
    def test_suppliers_page_loads(self, ap_suppliers_page: Page):
        """Test that suppliers page loads successfully."""
        expect(ap_suppliers_page).to_have_url(re.compile(r".*/ap/suppliers.*"))
        expect(ap_suppliers_page.get_by_test_id("page-title")).to_contain_text("Suppliers")

    @pytest.mark.e2e
    def test_suppliers_page_has_content(self, ap_suppliers_page: Page):
        """Test that suppliers page has content."""
        expect(ap_suppliers_page.locator("table")).to_be_visible()

    @pytest.mark.e2e
    def test_new_supplier_button_navigation(self, ap_suppliers_page: Page):
        """Test clicking new supplier button navigates to form."""
        new_btn = ap_suppliers_page.locator("a[href='/ap/suppliers/new']").first
        expect(new_btn).to_be_visible()
        new_btn.click()
        ap_suppliers_page.wait_for_load_state("networkidle")
        expect(ap_suppliers_page.get_by_test_id("page-title")).to_contain_text("New Supplier")


class TestAPSuppliersSearch:
    """Test AP suppliers search functionality."""

    @pytest.mark.e2e
    def test_suppliers_search_works(self, ap_suppliers_page: Page):
        """Test that search functionality works."""
        search = ap_suppliers_page.locator("input[name='search']").first
        expect(search).to_be_visible()
        search.fill("Acme")
        ap_suppliers_page.wait_for_timeout(500)
        expect(ap_suppliers_page.locator("table")).to_be_visible()


class TestAPSupplierForm:
    """Test AP supplier form interactions."""

    @pytest.mark.e2e
    def test_supplier_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that supplier form loads."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("New Supplier")

    @pytest.mark.e2e
    def test_supplier_form_has_fields(self, authenticated_page: Page, base_url: str):
        """Test that supplier form has expected fields."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='supplier_code']")).to_be_visible()
        expect(form.locator("input[name='supplier_name']")).to_be_visible()


class TestAPInvoices:
    """Test AP invoices page."""

    @pytest.mark.e2e
    def test_invoices_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that invoices page loads."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/invoices.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("AP Invoices")

    @pytest.mark.e2e
    def test_new_invoice_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new invoice form loads."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("New AP Invoice")
        expect(authenticated_page.locator("form").first).to_be_visible()


class TestAPPayments:
    """Test AP payments page."""

    @pytest.mark.e2e
    def test_payments_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that payments page loads."""
        authenticated_page.goto(f"{base_url}/ap/payments")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/payments.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("AP Payments")


class TestAPAgingReport:
    """Test AP aging report page."""

    @pytest.mark.e2e
    def test_aging_report_loads(self, authenticated_page: Page, base_url: str):
        """Test that aging report loads."""
        authenticated_page.goto(f"{base_url}/ap/aging")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/aging.*"))
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("AP Aging Report")

    @pytest.mark.e2e
    def test_aging_report_with_date(self, authenticated_page: Page, base_url: str):
        """Test aging report with date filter."""
        authenticated_page.goto(f"{base_url}/ap/aging?as_of_date=2024-06-30")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("AP Aging Report")


class TestAPInvoiceWorkflow:
    """Test AP invoice approval workflow."""

    @pytest.mark.e2e
    def test_invoice_form_has_required_fields(self, authenticated_page: Page, base_url: str):
        """Test invoice form has all required fields."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        supplier_field = form.locator("select").first
        expect(supplier_field).to_be_visible()
        invoice_num = form.locator("#invoice_number").first
        expect(invoice_num).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_line_items_section(self, authenticated_page: Page, base_url: str):
        """Test invoice line items section exists."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for line items table or section
        lines_section = authenticated_page.locator("table.lines-table").first
        expect(lines_section).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_add_line_button(self, authenticated_page: Page, base_url: str):
        """Test add line button exists on invoice form."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_line_btn = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Row'), "
            "[data-action='add-line'], .add-line-btn, button:has-text('+')"
        ).first

        expect(add_line_btn).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_totals_display(self, authenticated_page: Page, base_url: str):
        """Test invoice totals section displays."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for totals section
        totals = authenticated_page.locator(".totals-section").first
        expect(totals).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_new_button_exists(self, authenticated_page: Page, base_url: str):
        """Test new invoice button exists on invoice page."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href='/ap/invoices/new']").first
        expect(new_btn).to_be_visible()


class TestAPPaymentBatchWorkflow:
    """Test AP payment batch workflow."""

    @pytest.mark.e2e
    def test_payment_batch_page_loads(self, authenticated_page: Page, base_url: str):
        """Test payment batch page loads."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Payment Batches")

    @pytest.mark.e2e
    def test_create_payment_batch_form(self, authenticated_page: Page, base_url: str):
        """Test create payment batch form loads."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("New Payment Batch")

    @pytest.mark.e2e
    def test_payment_batch_has_bank_account_field(self, authenticated_page: Page, base_url: str):
        """Test payment batch has bank account selection."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches/new")
        authenticated_page.wait_for_load_state("networkidle")

        bank_account = authenticated_page.locator("select[name='bank_account_id']").first
        expect(bank_account).to_be_visible()

    @pytest.mark.e2e
    def test_payment_batch_invoice_selection(self, authenticated_page: Page, base_url: str):
        """Test payment batch has invoice selection."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for invoice selection table or checkboxes
        invoice_selection = authenticated_page.locator("table.invoice-selection").first
        expect(invoice_selection).to_be_visible()

    @pytest.mark.e2e
    def test_payment_batch_new_button(self, authenticated_page: Page, base_url: str):
        """Test payment batch list has new button."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href='/ap/payment-batches/new']").first
        expect(new_btn).to_be_visible()


class TestAPDetailPages:
    """Test AP detail pages handle missing records."""

    @pytest.mark.e2e
    def test_supplier_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure supplier detail shows not found state."""
        supplier_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ap/suppliers/{supplier_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Supplier not found")).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure invoice detail shows not found state."""
        invoice_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ap/invoices/{invoice_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Invoice not found")).to_be_visible()

    @pytest.mark.e2e
    def test_payment_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure payment detail shows not found state."""
        payment_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ap/payments/{payment_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Payment not found")).to_be_visible()
