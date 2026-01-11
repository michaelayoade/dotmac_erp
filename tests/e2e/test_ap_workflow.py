"""
E2E Tests for AP Supplier and Invoice Workflows.

Tests the complete create/edit/submit/approve/post workflows for AP.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestAPSupplierCreateWorkflow:
    """Test AP supplier create workflow."""

    @pytest.mark.e2e
    def test_supplier_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that supplier form loads successfully."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/suppliers/new.*"))

    @pytest.mark.e2e
    def test_supplier_form_has_all_fields(self, authenticated_page: Page, base_url: str):
        """Test that supplier form has all required fields."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='supplier_code']")).to_be_visible()
        expect(form.locator("input[name='supplier_name']")).to_be_visible()
        expect(form.locator("select[name='currency_code']")).to_be_visible()

    @pytest.mark.e2e
    def test_supplier_form_has_payment_terms(self, authenticated_page: Page, base_url: str):
        """Test that supplier form has payment terms section."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='payment_terms_days']")).to_be_visible()
        expect(form.locator("select[name='payment_method']")).to_be_visible()

    @pytest.mark.e2e
    def test_supplier_form_has_gl_accounts(self, authenticated_page: Page, base_url: str):
        """Test that supplier form has GL account dropdowns."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("select[name='default_expense_account_id']")).to_be_visible()
        expect(form.locator("select[name='default_payable_account_id']")).to_be_visible()

    @pytest.mark.e2e
    def test_create_supplier_workflow(self, authenticated_page: Page, base_url: str):
        """Test full create supplier workflow."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        unique_id = str(uuid4())[:6].upper()
        supplier_code = f"SUP-{unique_id}"

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill required fields
        authenticated_page.fill("input[name='supplier_code']", supplier_code)
        authenticated_page.fill("input[name='supplier_name']", f"Test Supplier {unique_id}")

        # Select currency
        currency_select = authenticated_page.locator("select[name='currency_code']")
        expect(currency_select).to_be_visible()
        currency_select.select_option("USD")

        # Fill optional fields
        email_input = authenticated_page.locator("input[name='email']")
        expect(email_input).to_be_visible()
        email_input.fill(f"supplier_{unique_id}@test.com")

        payment_terms = authenticated_page.locator("input[name='payment_terms_days']")
        expect(payment_terms).to_be_visible()
        payment_terms.fill("30")

        # Submit
        submit_btn = authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        authenticated_page.wait_for_load_state("networkidle")

        # Check for success (redirect or success message)
        is_success = (
            "/ap/suppliers" in authenticated_page.url
            or authenticated_page.locator(".success, .alert-success, :text('successfully')").count() > 0
        )
        errors = authenticated_page.locator(".error, .alert-error, .alert-danger")
        expect(errors).to_have_count(0)
        assert is_success


class TestAPSupplierListWorkflow:
    """Test AP supplier list and search."""

    @pytest.mark.e2e
    def test_suppliers_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that suppliers list loads."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/suppliers.*"))

    @pytest.mark.e2e
    def test_suppliers_list_has_new_button(self, authenticated_page: Page, base_url: str):
        """Test that suppliers list has new button."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href='/ap/suppliers/new']").first
        expect(new_btn).to_be_visible()

    @pytest.mark.e2e
    def test_suppliers_search(self, authenticated_page: Page, base_url: str):
        """Test suppliers list search."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator("input[name='search']").first
        expect(search).to_be_visible()
        search.fill("Test")
        authenticated_page.wait_for_timeout(500)
        expect(authenticated_page.locator("table")).to_be_visible()


class TestAPInvoiceCreateWorkflow:
    """Test AP invoice create workflow."""

    @pytest.mark.e2e
    def test_invoice_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that invoice form loads."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/invoices/new.*"))

    @pytest.mark.e2e
    def test_invoice_form_has_supplier_select(self, authenticated_page: Page, base_url: str):
        """Test that invoice form has supplier selection."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        supplier_select = form.locator("select[name='supplier_id']")
        expect(supplier_select).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_form_has_dates(self, authenticated_page: Page, base_url: str):
        """Test that invoice form has date fields."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        invoice_date = form.locator("input[name='invoice_date']")
        expect(invoice_date).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_form_has_line_items(self, authenticated_page: Page, base_url: str):
        """Test that invoice form has line items section."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for line items section
        lines = authenticated_page.locator("table.lines-table").first
        expect(lines).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_add_line_button(self, authenticated_page: Page, base_url: str):
        """Test that invoice has add line button."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_btn = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Row'), "
            "[data-action='add-line'], button:has-text('+')"
        ).first
        expect(add_btn).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_totals_section(self, authenticated_page: Page, base_url: str):
        """Test that invoice shows totals section."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        totals = authenticated_page.locator(".totals-section").first
        expect(totals).to_be_visible()


class TestAPInvoiceListWorkflow:
    """Test AP invoice list and filters."""

    @pytest.mark.e2e
    def test_invoices_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that invoices list loads."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/invoices.*"))

    @pytest.mark.e2e
    def test_invoices_list_has_status_filter(self, authenticated_page: Page, base_url: str):
        """Test that invoices list has status filter."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status']").first
        expect(status_filter).to_be_visible()

    @pytest.mark.e2e
    def test_invoices_list_has_supplier_filter(self, authenticated_page: Page, base_url: str):
        """Test that invoices list has supplier filter."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        supplier_filter = authenticated_page.locator("select[name='supplier_id']").first
        expect(supplier_filter).to_be_visible()


class TestAPInvoiceDetailWorkflow:
    """Test AP invoice detail and status workflows."""

    @pytest.mark.e2e
    def test_invoice_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent invoice shows not found."""
        invoice_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ap/invoices/{invoice_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Invoice not found")).to_be_visible()

    @pytest.mark.e2e
    def test_supplier_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent supplier shows not found."""
        supplier_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ap/suppliers/{supplier_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Supplier not found")).to_be_visible()


class TestAPPaymentWorkflow:
    """Test AP payment workflows."""

    @pytest.mark.e2e
    def test_payments_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that payments list loads."""
        authenticated_page.goto(f"{base_url}/ap/payments")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/payments.*"))

    @pytest.mark.e2e
    def test_new_payment_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new payment form loads."""
        authenticated_page.goto(f"{base_url}/ap/payments/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

    @pytest.mark.e2e
    def test_payment_form_has_supplier_select(self, authenticated_page: Page, base_url: str):
        """Test that payment form has supplier selection."""
        authenticated_page.goto(f"{base_url}/ap/payments/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        supplier_select = form.locator("select[name='supplier_id']").first
        expect(supplier_select).to_be_visible()

    @pytest.mark.e2e
    def test_payment_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent payment shows not found."""
        payment_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ap/payments/{payment_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Payment not found")).to_be_visible()


class TestAPAgingReport:
    """Test AP aging report."""

    @pytest.mark.e2e
    def test_aging_report_loads(self, authenticated_page: Page, base_url: str):
        """Test that aging report loads."""
        authenticated_page.goto(f"{base_url}/ap/aging")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/aging.*"))

    @pytest.mark.e2e
    def test_aging_report_has_title(self, authenticated_page: Page, base_url: str):
        """Test that aging report has a title."""
        authenticated_page.goto(f"{base_url}/ap/aging")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("AP Aging Report")

    @pytest.mark.e2e
    def test_aging_report_shows_buckets(self, authenticated_page: Page, base_url: str):
        """Test that aging report shows aging buckets."""
        authenticated_page.goto(f"{base_url}/ap/aging")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("No aging data yet")).to_be_visible()


class TestAPPaymentBatchWorkflow:
    """Test AP payment batch workflow."""

    @pytest.mark.e2e
    def test_payment_batches_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that payment batches list loads."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Payment Batches")

    @pytest.mark.e2e
    def test_new_payment_batch_form(self, authenticated_page: Page, base_url: str):
        """Test that new payment batch form loads."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("New Payment Batch")

    @pytest.mark.e2e
    def test_payment_batch_has_bank_account(self, authenticated_page: Page, base_url: str):
        """Test that payment batch form has bank account selection."""
        authenticated_page.goto(f"{base_url}/ap/payment-batches/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        bank_account = form.locator("select[name='bank_account_id']").first
        expect(bank_account).to_be_visible()


class TestAPResponsiveDesign:
    """Test AP pages on different viewport sizes."""

    @pytest.mark.e2e
    def test_ap_suppliers_mobile(self, authenticated_page: Page, base_url: str):
        """Test AP suppliers on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_ap_invoices_mobile(self, authenticated_page: Page, base_url: str):
        """Test AP invoices on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("body")).to_be_visible()

    @pytest.mark.e2e
    def test_supplier_form_tablet(self, authenticated_page: Page, base_url: str):
        """Test supplier form on tablet viewport."""
        authenticated_page.set_viewport_size({"width": 768, "height": 1024})
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("body")).to_be_visible()
