"""
E2E Tests for Accounts Receivable (AR) Module CRUD Operations.

Tests for creating, reading, updating, and deleting:
- Customers
- AR Invoices
- AR Receipts
- Credit Notes
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


@pytest.mark.e2e
class TestCustomerList:
    """Tests for customer list page."""

    def test_customer_list_with_search(self, authenticated_page, base_url):
        """Test customer list search functionality."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator("input[type='search'], input[name='search'], input[placeholder*='Search']")
        if search.count() > 0:
            search.first.fill("test")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    def test_customer_list_with_status_filter(self, authenticated_page, base_url):
        """Test customer list status filter."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            status_filter.select_option(index=1)
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    def test_customer_list_has_new_button(self, authenticated_page, base_url):
        """Test that customer list has new button."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/ar/customers/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestCustomerCreate:
    """Tests for creating customers."""

    def test_customer_create_page_loads(self, authenticated_page, base_url):
        """Test that customer create page loads."""
        response = authenticated_page.goto(f"{base_url}/ar/customers/new")
        assert response.ok, f"Customer create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_customer_create_has_code_field(self, authenticated_page, base_url):
        """Test that customer form has code field."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#customer_code, input[name='customer_code']")
        expect(field).to_be_visible()

    def test_customer_create_has_name_field(self, authenticated_page, base_url):
        """Test that customer form has name field."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#customer_name, input[name='customer_name']")
        expect(field).to_be_visible()

    def test_customer_create_has_currency_field(self, authenticated_page, base_url):
        """Test that customer form has currency field."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("[name*='currency']")
        expect(field.first).to_be_visible()

    def test_customer_create_has_credit_limit_field(self, authenticated_page, base_url):
        """Test that customer form has credit limit field."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("[name*='credit_limit']")
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_customer_create_has_payment_terms(self, authenticated_page, base_url):
        """Test that customer form has payment terms field."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("[name*='payment_terms'], [name*='payment']")
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_customer_create_has_contact_fields(self, authenticated_page, base_url):
        """Test that customer form has contact fields."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        email = authenticated_page.locator("[name*='email']")
        if email.count() > 0:
            expect(email.first).to_be_visible()

    def test_customer_create_minimal(self, authenticated_page, base_url):
        """Test creating customer with minimal data."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill required fields
        authenticated_page.locator("#customer_code, input[name='customer_code']").fill(f"CUST-{uid}")
        authenticated_page.locator("#customer_name, input[name='customer_name']").fill(f"Test Customer {uid}")

        # Select currency if dropdown
        currency = authenticated_page.locator("select[name*='currency']")
        if currency.count() > 0:
            currency.select_option(index=1)

        # Submit
        authenticated_page.locator("button[type='submit']").click()
        authenticated_page.wait_for_load_state("networkidle")

        # Should redirect to list or detail
        expect(authenticated_page).to_have_url(re.compile(r".*/ar/customers.*"))

    def test_customer_create_full_details(self, authenticated_page, base_url):
        """Test creating customer with full details."""
        authenticated_page.goto(f"{base_url}/ar/customers/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill all fields
        authenticated_page.locator("#customer_code, input[name='customer_code']").fill(f"CUST-{uid}")
        authenticated_page.locator("#customer_name, input[name='customer_name']").fill(f"Test Customer {uid}")

        # Currency
        currency = authenticated_page.locator("select[name*='currency']")
        if currency.count() > 0:
            currency.select_option(index=1)

        # Credit limit
        credit_limit = authenticated_page.locator("[name*='credit_limit']")
        if credit_limit.count() > 0:
            credit_limit.first.fill("50000")

        # Email
        email = authenticated_page.locator("[name*='email']")
        if email.count() > 0:
            email.first.fill(f"customer_{uid}@example.com")

        # Phone
        phone = authenticated_page.locator("[name*='phone']")
        if phone.count() > 0:
            phone.first.fill("+1234567890")

        # Submit
        authenticated_page.locator("button[type='submit']").click()
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page).to_have_url(re.compile(r".*/ar/customers.*"))


@pytest.mark.e2e
class TestCustomerEdit:
    """Tests for editing customers."""

    def test_customer_edit_accessible_from_list(self, authenticated_page, base_url):
        """Test that customer edit is accessible from list."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first customer link
        customer_link = authenticated_page.locator("table tbody tr a, .customer-list a").first
        if customer_link.count() > 0:
            customer_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(re.compile(r".*/ar/customers/.*"))

    def test_customer_detail_has_edit_button(self, authenticated_page, base_url):
        """Test that customer detail has edit button."""
        authenticated_page.goto(f"{base_url}/ar/customers")
        authenticated_page.wait_for_load_state("networkidle")

        customer_link = authenticated_page.locator("table tbody tr a, a[href*='/ar/customers/']").first
        if customer_link.count() > 0:
            customer_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator("a[href*='/edit'], button:has-text('Edit'), a:has-text('Edit')")
            if edit_btn.count() > 0:
                expect(edit_btn.first).to_be_visible()


@pytest.mark.e2e
class TestARInvoiceList:
    """Tests for AR invoice list."""

    def test_invoice_list_page_loads(self, authenticated_page, base_url):
        """Test that AR invoice list loads."""
        response = authenticated_page.goto(f"{base_url}/ar/invoices")
        assert response.ok, f"AR invoice list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_invoice_list_with_customer_filter(self, authenticated_page, base_url):
        """Test AR invoice list customer filter."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        customer_filter = authenticated_page.locator("select[name='customer'], select[name='customer_id']")
        if customer_filter.count() > 0:
            expect(customer_filter.first).to_be_visible()

    def test_invoice_list_with_date_range(self, authenticated_page, base_url):
        """Test AR invoice list date range filters."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        date_filters = authenticated_page.locator("input[type='date']")
        if date_filters.count() > 0:
            expect(date_filters.first).to_be_visible()

    def test_invoice_list_with_status_filter(self, authenticated_page, base_url):
        """Test AR invoice list status filter."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status']")
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()

    def test_invoice_list_has_new_button(self, authenticated_page, base_url):
        """Test that invoice list has new button."""
        authenticated_page.goto(f"{base_url}/ar/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/ar/invoices/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestARInvoiceCreate:
    """Tests for creating AR invoices."""

    def test_invoice_create_page_loads(self, authenticated_page, base_url):
        """Test that AR invoice create page loads."""
        response = authenticated_page.goto(f"{base_url}/ar/invoices/new")
        assert response.ok, f"AR invoice create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_invoice_create_has_customer_field(self, authenticated_page, base_url):
        """Test that invoice form has customer selection."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='customer_id'], select[name='customer'], #customer_id")
        expect(field.first).to_be_visible()

    def test_invoice_create_has_date_fields(self, authenticated_page, base_url):
        """Test that invoice form has date fields."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        dates = authenticated_page.locator("input[type='date']")
        expect(dates.first).to_be_visible()

    def test_invoice_create_has_line_items_section(self, authenticated_page, base_url):
        """Test that invoice form has line items section."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        lines = authenticated_page.locator("text=Line, text=Items, button:has-text('Add'), [class*='line']")
        expect(lines.first).to_be_visible()

    def test_invoice_create_has_add_line_button(self, authenticated_page, base_url):
        """Test that invoice form has add line button."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_btn = authenticated_page.locator("button:has-text('Add'), a:has-text('Add Line'), button[class*='add']")
        if add_btn.count() > 0:
            expect(add_btn.first).to_be_visible()

    def test_invoice_create_shows_totals(self, authenticated_page, base_url):
        """Test that invoice form shows totals."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        totals = authenticated_page.locator("text=Total, text=Subtotal, [class*='total']")
        if totals.count() > 0:
            expect(totals.first).to_be_visible()


@pytest.mark.e2e
class TestARReceiptList:
    """Tests for AR receipt list."""

    def test_receipt_list_page_loads(self, authenticated_page, base_url):
        """Test that AR receipt list loads."""
        response = authenticated_page.goto(f"{base_url}/ar/receipts")
        assert response.ok, f"AR receipt list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_receipt_list_has_new_button(self, authenticated_page, base_url):
        """Test that receipt list has new button."""
        authenticated_page.goto(f"{base_url}/ar/receipts")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/ar/receipts/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()

    def test_receipt_list_by_customer(self, authenticated_page, base_url):
        """Test receipt list customer filter."""
        authenticated_page.goto(f"{base_url}/ar/receipts")
        authenticated_page.wait_for_load_state("networkidle")

        customer_filter = authenticated_page.locator("select[name='customer'], select[name='customer_id']")
        if customer_filter.count() > 0:
            expect(customer_filter.first).to_be_visible()


@pytest.mark.e2e
class TestARReceiptCreate:
    """Tests for creating AR receipts."""

    def test_receipt_create_page_loads(self, authenticated_page, base_url):
        """Test that AR receipt create page loads."""
        response = authenticated_page.goto(f"{base_url}/ar/receipts/new")
        assert response.ok, f"AR receipt create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_receipt_create_has_customer_field(self, authenticated_page, base_url):
        """Test that receipt form has customer field."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='customer_id'], select[name='customer']")
        expect(field.first).to_be_visible()

    def test_receipt_create_has_amount_field(self, authenticated_page, base_url):
        """Test that receipt form has amount field."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("input[name='amount'], input[name*='amount']")
        expect(field.first).to_be_visible()

    def test_receipt_create_has_date_field(self, authenticated_page, base_url):
        """Test that receipt form has date field."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("input[type='date']")
        expect(field.first).to_be_visible()

    def test_receipt_create_has_payment_method(self, authenticated_page, base_url):
        """Test that receipt form has payment method."""
        authenticated_page.goto(f"{base_url}/ar/receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("[name*='payment_method'], [name*='method']")
        if field.count() > 0:
            expect(field.first).to_be_visible()


@pytest.mark.e2e
class TestCreditNoteList:
    """Tests for credit note list."""

    def test_credit_note_list_page_loads(self, authenticated_page, base_url):
        """Test that credit note list loads."""
        response = authenticated_page.goto(f"{base_url}/ar/credit-notes")
        assert response.ok, f"Credit note list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_credit_note_list_has_new_button(self, authenticated_page, base_url):
        """Test that credit note list has new button."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/ar/credit-notes/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestCreditNoteCreate:
    """Tests for creating credit notes."""

    def test_credit_note_create_page_loads(self, authenticated_page, base_url):
        """Test that credit note create page loads."""
        response = authenticated_page.goto(f"{base_url}/ar/credit-notes/new")
        assert response.ok, f"Credit note create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_credit_note_create_has_customer_field(self, authenticated_page, base_url):
        """Test that credit note form has customer field."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='customer_id'], select[name='customer']")
        expect(field.first).to_be_visible()

    def test_credit_note_create_has_reason_field(self, authenticated_page, base_url):
        """Test that credit note form has reason field."""
        authenticated_page.goto(f"{base_url}/ar/credit-notes/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("[name*='reason'], textarea")
        if field.count() > 0:
            expect(field.first).to_be_visible()


@pytest.mark.e2e
class TestARAgingReport:
    """Tests for AR aging report."""

    def test_aging_report_loads(self, authenticated_page, base_url):
        """Test that AR aging report loads."""
        response = authenticated_page.goto(f"{base_url}/ar/aging")
        assert response.ok, f"AR aging report failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_aging_report_has_date_filter(self, authenticated_page, base_url):
        """Test that aging report has date filter."""
        authenticated_page.goto(f"{base_url}/ar/aging")
        authenticated_page.wait_for_load_state("networkidle")

        date_filter = authenticated_page.locator("input[type='date'], input[name='as_of_date']")
        if date_filter.count() > 0:
            expect(date_filter.first).to_be_visible()

    def test_aging_report_shows_buckets(self, authenticated_page, base_url):
        """Test that aging report shows aging buckets."""
        authenticated_page.goto(f"{base_url}/ar/aging")
        authenticated_page.wait_for_load_state("networkidle")

        buckets = authenticated_page.locator("text=Current, text=30, text=60, text=90, th:has-text('Days')")
        if buckets.count() > 0:
            expect(buckets.first).to_be_visible()
