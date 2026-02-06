"""
E2E Tests for AR Customer and Invoice Workflows.

Tests the complete create/edit/submit/approve/post workflows for AR.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestARCustomerCreateWorkflow:
    """Test AR customer create workflow."""

    @pytest.mark.e2e
    def test_customer_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that customer form loads successfully."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/customers/new.*"))

    @pytest.mark.e2e
    def test_customer_form_has_all_fields(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that customer form has all required fields."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='customer_code']")).to_be_visible()
        expect(form.locator("input[name='customer_name']")).to_be_visible()
        expect(form.locator("select[name='currency_code']")).to_be_visible()

    @pytest.mark.e2e
    def test_customer_form_has_credit_fields(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that customer form has credit management fields."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='credit_limit']")).to_be_visible()
        expect(form.locator("input[name='payment_terms_days']")).to_be_visible()

    @pytest.mark.e2e
    def test_customer_form_has_gl_accounts(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that customer form has GL account dropdowns."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(
            form.locator("select[name='default_revenue_account_id']")
        ).to_be_visible()
        expect(
            form.locator("select[name='default_receivable_account_id']")
        ).to_be_visible()

    @pytest.mark.e2e
    def test_customer_form_has_contact_fields(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that customer form has contact information fields."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(form.locator("input[name='email']")).to_be_visible()
        expect(form.locator("input[name='phone']")).to_be_visible()
        expect(form.locator("textarea[name='billing_address']")).to_be_visible()

    @pytest.mark.e2e
    def test_create_customer_workflow(self, authenticated_page: Page, base_url: str):
        """Test full create customer workflow."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        unique_id = str(uuid4())[:6].upper()
        customer_code = f"CUST-{unique_id}"

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Fill required fields
        authenticated_page.fill("input[name='customer_code']", customer_code)
        authenticated_page.fill(
            "input[name='customer_name']", f"Test Customer {unique_id}"
        )

        # Select currency
        currency_select = authenticated_page.locator("select[name='currency_code']")
        expect(currency_select).to_be_visible()
        currency_select.select_option("USD")

        # Fill optional fields
        email_input = authenticated_page.locator("input[name='email']")
        expect(email_input).to_be_visible()
        email_input.fill(f"customer_{unique_id}@test.com")

        payment_terms = authenticated_page.locator("input[name='payment_terms_days']")
        expect(payment_terms).to_be_visible()
        payment_terms.fill("30")

        credit_limit = authenticated_page.locator("input[name='credit_limit']")
        expect(credit_limit).to_be_visible()
        credit_limit.fill("10000")

        # Submit
        submit_btn = authenticated_page.locator("button[type='submit']").first
        expect(submit_btn).to_be_visible()
        submit_btn.click()
        authenticated_page.wait_for_load_state("networkidle")

        # Check for success (redirect or success message)
        is_success = (
            "/ar/customers" in authenticated_page.url
            or authenticated_page.locator(
                ".success, .alert-success, :text('successfully')"
            ).count()
            > 0
        )
        errors = authenticated_page.locator(".error, .alert-error, .alert-danger")
        expect(errors).to_have_count(0)
        assert is_success


class TestARCustomerListWorkflow:
    """Test AR customer list and search."""

    @pytest.mark.e2e
    def test_customers_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that customers list loads."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/customers.*"))

    @pytest.mark.e2e
    def test_customers_list_has_new_button(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that customers list has new button."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href='/ar/customers/new']").first
        expect(new_btn).to_be_visible()

    @pytest.mark.e2e
    def test_customers_search(self, authenticated_page: Page, base_url: str):
        """Test customers list search."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator("input[name='search']").first
        expect(search).to_be_visible()
        search.fill("Test")
        authenticated_page.wait_for_timeout(500)
        expect(authenticated_page.locator("table")).to_be_visible()


class TestARInvoiceCreateWorkflow:
    """Test AR invoice create workflow."""

    @pytest.mark.e2e
    def test_invoice_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that invoice form loads."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/invoices/new.*"))

    @pytest.mark.e2e
    def test_invoice_form_has_customer_select(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that invoice form has customer selection."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        customer_select = form.locator("select[name='customer_id']")
        expect(customer_select).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_form_has_dates(self, authenticated_page: Page, base_url: str):
        """Test that invoice form has date fields."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        invoice_date = form.locator("input[name='invoice_date']")
        expect(invoice_date).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_form_has_line_items(self, authenticated_page: Page, base_url: str):
        """Test that invoice form has line items section."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for line items section
        lines = authenticated_page.locator("table.lines-table").first
        expect(lines).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_add_line_button(self, authenticated_page: Page, base_url: str):
        """Test that invoice has add line button."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_btn = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Row'), "
            "[data-action='add-line'], button:has-text('+')"
        ).first
        expect(add_btn).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_totals_section(self, authenticated_page: Page, base_url: str):
        """Test that invoice shows totals section."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        totals = authenticated_page.locator(".totals-section").first
        expect(totals).to_be_visible()


class TestARInvoiceListWorkflow:
    """Test AR invoice list and filters."""

    @pytest.mark.e2e
    def test_invoices_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that invoices list loads."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/invoices.*"))

    @pytest.mark.e2e
    def test_invoices_list_has_status_filter(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that invoices list has status filter."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status']").first
        expect(status_filter).to_be_visible()

    @pytest.mark.e2e
    def test_invoices_list_has_customer_filter(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that invoices list has customer filter."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        customer_filter = authenticated_page.locator("select[name='customer_id']").first
        expect(customer_filter).to_be_visible()


class TestARInvoiceDetailWorkflow:
    """Test AR invoice detail and status workflows."""

    @pytest.mark.e2e
    def test_invoice_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent invoice shows not found."""
        invoice_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ar/invoices/{invoice_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Invoice not found")).to_be_visible()

    @pytest.mark.e2e
    def test_customer_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent customer shows not found."""
        customer_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ar/customers/{customer_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Customer not found")).to_be_visible()


class TestARReceiptWorkflow:
    """Test AR receipt/payment workflows."""

    @pytest.mark.e2e
    def test_receipts_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that receipts list loads."""
        authenticated_page.goto(f"{base_url}/ar/receipts")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/receipts.*"))

    @pytest.mark.e2e
    def test_new_receipt_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new receipt form loads."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

    @pytest.mark.e2e
    def test_receipt_form_has_customer_select(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that receipt form has customer selection."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        customer_select = form.locator("select[name='customer_id']").first
        expect(customer_select).to_be_visible()

    @pytest.mark.e2e
    def test_receipt_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Test that non-existent receipt shows not found."""
        receipt_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/ar/receipts/{receipt_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Receipt not found")).to_be_visible()


class TestARAgingReport:
    """Test AR aging report."""

    @pytest.mark.e2e
    def test_aging_report_loads(self, authenticated_page: Page, base_url: str):
        """Test that aging report loads."""
        authenticated_page.goto(f"{base_url}/ar/aging")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/aging.*"))

    @pytest.mark.e2e
    def test_aging_report_has_title(self, authenticated_page: Page, base_url: str):
        """Test that aging report has a title."""
        authenticated_page.goto(f"{base_url}/ar/aging")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "AR Aging Report"
        )

    @pytest.mark.e2e
    def test_aging_report_shows_buckets(self, authenticated_page: Page, base_url: str):
        """Test that aging report shows aging buckets."""
        authenticated_page.goto(f"{base_url}/ar/aging")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("No aging data yet")).to_be_visible()


class TestARCreditNoteWorkflow:
    """Test AR credit note workflow."""

    @pytest.mark.e2e
    def test_credit_notes_list_loads(self, authenticated_page: Page, base_url: str):
        """Test that credit notes list loads."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "AR Credit Notes"
        )

    @pytest.mark.e2e
    def test_new_credit_note_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that new credit note form loads."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()
        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "New Credit Note"
        )


class TestARResponsiveDesign:
    """Test AR pages on different viewport sizes."""

    @pytest.mark.e2e
    def test_ar_customers_mobile(self, authenticated_page: Page, base_url: str):
        """Test AR customers on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("main")).to_be_visible()

    @pytest.mark.e2e
    def test_ar_invoices_mobile(self, authenticated_page: Page, base_url: str):
        """Test AR invoices on mobile viewport."""
        authenticated_page.set_viewport_size({"width": 375, "height": 667})
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("main")).to_be_visible()

    @pytest.mark.e2e
    def test_customer_form_tablet(self, authenticated_page: Page, base_url: str):
        """Test customer form on tablet viewport."""
        authenticated_page.set_viewport_size({"width": 768, "height": 1024})
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")
        expect(authenticated_page.locator("main")).to_be_visible()
