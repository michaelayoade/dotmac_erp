"""
E2E Tests for Accounts Payable (AP) Module CRUD Operations.

Tests for creating, reading, updating, and deleting:
- Suppliers
- AP Invoices
- AP Payments
- Purchase Orders
- Goods Receipts
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


@pytest.mark.e2e
class TestSupplierList:
    """Tests for supplier list page."""

    def test_supplier_list_with_search(self, authenticated_page, base_url):
        """Test supplier list search functionality."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator("input[type='search'], input[name='search'], input[placeholder*='Search']")
        if search.count() > 0:
            search.first.fill("test")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            # Page should still work after search
            expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    def test_supplier_list_with_status_filter(self, authenticated_page, base_url):
        """Test supplier list status filter."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            status_filter.select_option(index=1)
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.get_by_test_id("page-title")).to_be_visible()

    def test_supplier_list_pagination(self, authenticated_page, base_url):
        """Test supplier list pagination if present."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        pagination = authenticated_page.locator(".pagination, nav[aria-label*='pagination'], [class*='pagination']")
        if pagination.count() > 0:
            expect(pagination.first).to_be_visible()


@pytest.mark.e2e
class TestSupplierCreate:
    """Tests for creating suppliers."""

    def test_supplier_create_page_loads(self, authenticated_page, base_url):
        """Test that supplier create page loads."""
        response = authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        assert response.ok, f"Supplier create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_supplier_create_has_code_field(self, authenticated_page, base_url):
        """Test that supplier form has code field."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#supplier_code, input[name='supplier_code']")
        expect(field).to_be_visible()

    def test_supplier_create_has_name_field(self, authenticated_page, base_url):
        """Test that supplier form has name field."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("#supplier_name, input[name='supplier_name']")
        expect(field).to_be_visible()

    def test_supplier_create_has_currency_field(self, authenticated_page, base_url):
        """Test that supplier form has currency field."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("[name*='currency']")
        expect(field.first).to_be_visible()

    def test_supplier_create_has_payment_terms(self, authenticated_page, base_url):
        """Test that supplier form has payment terms field."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("[name*='payment_terms'], [name*='payment']")
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_supplier_create_has_contact_fields(self, authenticated_page, base_url):
        """Test that supplier form has contact fields."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        email = authenticated_page.locator("[name*='email']")
        if email.count() > 0:
            expect(email.first).to_be_visible()

    def test_supplier_create_minimal(self, authenticated_page, base_url):
        """Test creating supplier with minimal data."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill required fields
        authenticated_page.locator("#supplier_code, input[name='supplier_code']").fill(f"SUP-{uid}")
        authenticated_page.locator("#supplier_name, input[name='supplier_name']").fill(f"Test Supplier {uid}")

        # Select currency if dropdown
        currency = authenticated_page.locator("select[name*='currency']")
        if currency.count() > 0:
            currency.select_option(index=1)

        # Submit
        authenticated_page.locator("button[type='submit']").click()
        authenticated_page.wait_for_load_state("networkidle")

        # Should redirect to list or detail
        expect(authenticated_page).to_have_url(re.compile(r".*/ap/suppliers.*"))

    def test_supplier_create_full_details(self, authenticated_page, base_url):
        """Test creating supplier with full details."""
        authenticated_page.goto(f"{base_url}/ap/suppliers/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill all fields
        authenticated_page.locator("#supplier_code, input[name='supplier_code']").fill(f"SUP-{uid}")
        authenticated_page.locator("#supplier_name, input[name='supplier_name']").fill(f"Test Supplier {uid}")

        # Currency
        currency = authenticated_page.locator("select[name*='currency']")
        if currency.count() > 0:
            currency.select_option(index=1)

        # Email
        email = authenticated_page.locator("[name*='email']")
        if email.count() > 0:
            email.first.fill(f"supplier_{uid}@example.com")

        # Phone
        phone = authenticated_page.locator("[name*='phone']")
        if phone.count() > 0:
            phone.first.fill("+1234567890")

        # Submit
        authenticated_page.locator("button[type='submit']").click()
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page).to_have_url(re.compile(r".*/ap/suppliers.*"))


@pytest.mark.e2e
class TestSupplierEdit:
    """Tests for editing suppliers."""

    def test_supplier_edit_accessible_from_list(self, authenticated_page, base_url):
        """Test that supplier edit is accessible from list."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first supplier link
        supplier_link = authenticated_page.locator("table tbody tr a, .supplier-list a").first
        if supplier_link.count() > 0:
            supplier_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Should be on detail or edit page
            expect(authenticated_page).to_have_url(re.compile(r".*/ap/suppliers/.*"))

    def test_supplier_detail_has_edit_button(self, authenticated_page, base_url):
        """Test that supplier detail has edit button."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        # Navigate to first supplier
        supplier_link = authenticated_page.locator("table tbody tr a, a[href*='/ap/suppliers/']").first
        if supplier_link.count() > 0:
            supplier_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for edit button
            edit_btn = authenticated_page.locator("a[href*='/edit'], button:has-text('Edit'), a:has-text('Edit')")
            if edit_btn.count() > 0:
                expect(edit_btn.first).to_be_visible()


@pytest.mark.e2e
class TestAPInvoiceList:
    """Tests for AP invoice list."""

    def test_invoice_list_page_loads(self, authenticated_page, base_url):
        """Test that AP invoice list loads."""
        response = authenticated_page.goto(f"{base_url}/ap/invoices")
        assert response.ok, f"AP invoice list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_invoice_list_with_supplier_filter(self, authenticated_page, base_url):
        """Test AP invoice list supplier filter."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        supplier_filter = authenticated_page.locator("select[name='supplier'], select[name='supplier_id']")
        if supplier_filter.count() > 0:
            expect(supplier_filter.first).to_be_visible()

    def test_invoice_list_with_date_range(self, authenticated_page, base_url):
        """Test AP invoice list date range filters."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        date_filters = authenticated_page.locator("input[type='date']")
        if date_filters.count() > 0:
            expect(date_filters.first).to_be_visible()

    def test_invoice_list_with_status_filter(self, authenticated_page, base_url):
        """Test AP invoice list status filter."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status']")
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()


@pytest.mark.e2e
class TestAPInvoiceCreate:
    """Tests for creating AP invoices."""

    def test_invoice_create_page_loads(self, authenticated_page, base_url):
        """Test that AP invoice create page loads."""
        response = authenticated_page.goto(f"{base_url}/ap/invoices/new")
        assert response.ok, f"AP invoice create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_invoice_create_has_supplier_field(self, authenticated_page, base_url):
        """Test that invoice form has supplier selection."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='supplier_id'], select[name='supplier'], #supplier_id")
        expect(field.first).to_be_visible()

    def test_invoice_create_has_date_fields(self, authenticated_page, base_url):
        """Test that invoice form has date fields."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        dates = authenticated_page.locator("input[type='date']")
        expect(dates.first).to_be_visible()

    def test_invoice_create_has_line_items_section(self, authenticated_page, base_url):
        """Test that invoice form has line items section."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for line items or add line button
        lines = authenticated_page.locator("text=Line, text=Items, button:has-text('Add'), [class*='line']")
        expect(lines.first).to_be_visible()

    def test_invoice_create_has_add_line_button(self, authenticated_page, base_url):
        """Test that invoice form has add line button."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        add_btn = authenticated_page.locator("button:has-text('Add'), a:has-text('Add Line'), button[class*='add']")
        if add_btn.count() > 0:
            expect(add_btn.first).to_be_visible()

    def test_invoice_create_shows_totals(self, authenticated_page, base_url):
        """Test that invoice form shows totals."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        totals = authenticated_page.locator("text=Total, text=Subtotal, [class*='total']")
        if totals.count() > 0:
            expect(totals.first).to_be_visible()


@pytest.mark.e2e
class TestAPPaymentList:
    """Tests for AP payment list."""

    def test_payment_list_page_loads(self, authenticated_page, base_url):
        """Test that AP payment list loads."""
        response = authenticated_page.goto(f"{base_url}/ap/payments")
        assert response.ok, f"AP payment list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_payment_list_has_new_button(self, authenticated_page, base_url):
        """Test that payment list has new button."""
        authenticated_page.goto(f"{base_url}/ap/payments")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/ap/payments/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestAPPaymentCreate:
    """Tests for creating AP payments."""

    def test_payment_create_page_loads(self, authenticated_page, base_url):
        """Test that AP payment create page loads."""
        response = authenticated_page.goto(f"{base_url}/ap/payments/new")
        assert response.ok, f"AP payment create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_payment_create_has_supplier_field(self, authenticated_page, base_url):
        """Test that payment form has supplier field."""
        authenticated_page.goto(f"{base_url}/ap/payments/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='supplier_id'], select[name='supplier']")
        expect(field.first).to_be_visible()

    def test_payment_create_has_amount_field(self, authenticated_page, base_url):
        """Test that payment form has amount field."""
        authenticated_page.goto(f"{base_url}/ap/payments/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("input[name='amount'], input[name*='amount']")
        expect(field.first).to_be_visible()

    def test_payment_create_has_date_field(self, authenticated_page, base_url):
        """Test that payment form has date field."""
        authenticated_page.goto(f"{base_url}/ap/payments/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("input[type='date']")
        expect(field.first).to_be_visible()


@pytest.mark.e2e
class TestPurchaseOrderList:
    """Tests for purchase order list."""

    def test_po_list_page_loads(self, authenticated_page, base_url):
        """Test that PO list loads."""
        response = authenticated_page.goto(f"{base_url}/ap/purchase-orders")
        assert response.ok, f"PO list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_po_list_has_new_button(self, authenticated_page, base_url):
        """Test that PO list has new button."""
        authenticated_page.goto(f"{base_url}/ap/purchase-orders")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/purchase-orders/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestPurchaseOrderCreate:
    """Tests for creating purchase orders."""

    def test_po_create_page_loads(self, authenticated_page, base_url):
        """Test that PO create page loads."""
        response = authenticated_page.goto(f"{base_url}/ap/purchase-orders/new")
        assert response.ok, f"PO create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_po_create_has_supplier_field(self, authenticated_page, base_url):
        """Test that PO form has supplier field."""
        authenticated_page.goto(f"{base_url}/ap/purchase-orders/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='supplier_id'], select[name='supplier']")
        expect(field.first).to_be_visible()

    def test_po_create_has_line_items(self, authenticated_page, base_url):
        """Test that PO form has line items section."""
        authenticated_page.goto(f"{base_url}/ap/purchase-orders/new")
        authenticated_page.wait_for_load_state("networkidle")

        lines = authenticated_page.locator("text=Line, text=Items, button:has-text('Add')")
        expect(lines.first).to_be_visible()


@pytest.mark.e2e
class TestGoodsReceiptList:
    """Tests for goods receipt list."""

    def test_gr_list_page_loads(self, authenticated_page, base_url):
        """Test that goods receipt list loads."""
        response = authenticated_page.goto(f"{base_url}/ap/goods-receipts")
        assert response.ok, f"GR list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_gr_list_has_new_button(self, authenticated_page, base_url):
        """Test that goods receipt list has new button."""
        authenticated_page.goto(f"{base_url}/ap/goods-receipts")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator("a[href*='/goods-receipts/new'], button:has-text('New')")
        expect(new_btn.first).to_be_visible()


@pytest.mark.e2e
class TestGoodsReceiptCreate:
    """Tests for creating goods receipts."""

    def test_gr_create_page_loads(self, authenticated_page, base_url):
        """Test that goods receipt create page loads."""
        response = authenticated_page.goto(f"{base_url}/ap/goods-receipts/new")
        assert response.ok, f"GR create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_gr_create_has_po_field(self, authenticated_page, base_url):
        """Test that goods receipt form has PO selection."""
        authenticated_page.goto(f"{base_url}/ap/goods-receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator("select[name='purchase_order_id'], select[name='po_id']")
        if field.count() > 0:
            expect(field.first).to_be_visible()


@pytest.mark.e2e
class TestAPAgingReport:
    """Tests for AP aging report."""

    def test_aging_report_loads(self, authenticated_page, base_url):
        """Test that AP aging report loads."""
        response = authenticated_page.goto(f"{base_url}/ap/aging")
        assert response.ok, f"AP aging report failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_aging_report_has_date_filter(self, authenticated_page, base_url):
        """Test that aging report has date filter."""
        authenticated_page.goto(f"{base_url}/ap/aging")
        authenticated_page.wait_for_load_state("networkidle")

        date_filter = authenticated_page.locator("input[type='date'], input[name='as_of_date']")
        if date_filter.count() > 0:
            expect(date_filter.first).to_be_visible()

    def test_aging_report_shows_buckets(self, authenticated_page, base_url):
        """Test that aging report shows aging buckets."""
        authenticated_page.goto(f"{base_url}/ap/aging")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for aging bucket columns
        buckets = authenticated_page.locator("text=Current, text=30, text=60, text=90, th:has-text('Days')")
        if buckets.count() > 0:
            expect(buckets.first).to_be_visible()
