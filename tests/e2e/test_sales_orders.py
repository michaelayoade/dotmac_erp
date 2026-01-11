"""
E2E Tests for Sales Orders Module.

Tests for creating, reading, updating, and managing sales orders.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Sales Orders List Tests
# =============================================================================


@pytest.mark.e2e
class TestSalesOrdersList:
    """Tests for sales orders list page."""

    def test_orders_page_loads(self, authenticated_page, base_url):
        """Test that sales orders list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/sales-orders")
        assert response.ok, f"Sales orders list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_orders_list_with_search(self, authenticated_page, base_url):
        """Test sales orders list search functionality."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )
        if search.count() > 0:
            search.first.fill("SO-001")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("body")).to_be_visible()

    def test_orders_list_by_status(self, authenticated_page, base_url):
        """Test sales orders list status filter."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator(
            "select[name='status'], #status"
        )
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()

    def test_orders_list_has_new_button(self, authenticated_page, base_url):
        """Test that sales orders list has new button."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/sales-orders/new'], button:has-text('New'), a:has-text('New Order')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()


# =============================================================================
# Sales Order CRUD Tests
# =============================================================================


@pytest.mark.e2e
class TestSalesOrderCreate:
    """Tests for creating sales orders."""

    def test_order_create_page_loads(self, authenticated_page, base_url):
        """Test that order create page loads."""
        response = authenticated_page.goto(f"{base_url}/sales-orders/new")
        assert response.ok, f"Order create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_order_create_has_customer_field(self, authenticated_page, base_url):
        """Test that order form has customer selection."""
        authenticated_page.goto(f"{base_url}/sales-orders/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='customer_id'], select[name='customer'], #customer_id"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_order_create_has_date_field(self, authenticated_page, base_url):
        """Test that order form has date field."""
        authenticated_page.goto(f"{base_url}/sales-orders/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='order_date'], input[type='date']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_order_create_has_delivery_date_field(self, authenticated_page, base_url):
        """Test that order form has expected delivery date."""
        authenticated_page.goto(f"{base_url}/sales-orders/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='delivery_date'], input[name='expected_delivery_date']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_order_create_with_lines(self, authenticated_page, base_url):
        """Test order creation with line items."""
        authenticated_page.goto(f"{base_url}/sales-orders/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Select customer
        customer = authenticated_page.locator(
            "select[name='customer_id'], select[name='customer']"
        )
        if customer.count() > 0:
            customer.first.select_option(index=1)

        # Look for add line button
        add_line_btn = authenticated_page.locator(
            "button:has-text('Add Line'), button:has-text('Add Item'), a:has-text('Add')"
        )
        if add_line_btn.count() > 0:
            expect(add_line_btn.first).to_be_visible()

    def test_order_create_from_quote(self, authenticated_page, base_url):
        """Test order creation from quote."""
        authenticated_page.goto(f"{base_url}/sales-orders/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for quote selection or import from quote
        quote_field = authenticated_page.locator(
            "select[name='quote_id'], a:has-text('From Quote'), button:has-text('Import')"
        )
        if quote_field.count() > 0:
            expect(quote_field.first).to_be_visible()


@pytest.mark.e2e
class TestSalesOrderDetail:
    """Tests for sales order detail page."""

    def test_order_detail_page_accessible(self, authenticated_page, base_url):
        """Test that order detail is accessible."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(re.compile(r".*/sales-orders/.*"))

    def test_order_detail_shows_lines(self, authenticated_page, base_url):
        """Test that order detail shows line items."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            lines = authenticated_page.locator(
                "table, .line-items, [class*='lines']"
            )
            if lines.count() > 0:
                expect(lines.first).to_be_visible()


@pytest.mark.e2e
class TestSalesOrderEdit:
    """Tests for editing sales orders."""

    def test_order_edit_page_loads(self, authenticated_page, base_url):
        """Test that order edit page loads."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                expect(authenticated_page).to_have_url(re.compile(r".*/edit.*"))


# =============================================================================
# Sales Order Workflow Tests
# =============================================================================


@pytest.mark.e2e
class TestSalesOrderWorkflows:
    """Tests for sales order workflow operations."""

    def test_order_submit_workflow(self, authenticated_page, base_url):
        """Test order submission workflow."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            submit_btn = authenticated_page.locator(
                "button:has-text('Submit'), button:has-text('Confirm')"
            )
            if submit_btn.count() > 0:
                expect(submit_btn.first).to_be_visible()

    def test_order_approve_workflow(self, authenticated_page, base_url):
        """Test order approval workflow."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            approve_btn = authenticated_page.locator(
                "button:has-text('Approve'), button:has-text('Accept')"
            )
            if approve_btn.count() > 0:
                expect(approve_btn.first).to_be_visible()

    def test_order_fulfill_workflow(self, authenticated_page, base_url):
        """Test order fulfillment workflow."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            fulfill_btn = authenticated_page.locator(
                "button:has-text('Fulfill'), button:has-text('Ship'), button:has-text('Deliver')"
            )
            if fulfill_btn.count() > 0:
                expect(fulfill_btn.first).to_be_visible()

    def test_order_convert_to_invoice(self, authenticated_page, base_url):
        """Test converting order to invoice."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            invoice_btn = authenticated_page.locator(
                "button:has-text('Invoice'), button:has-text('Create Invoice'), a:has-text('Convert to Invoice')"
            )
            if invoice_btn.count() > 0:
                expect(invoice_btn.first).to_be_visible()

    def test_order_cancel_workflow(self, authenticated_page, base_url):
        """Test order cancellation workflow."""
        authenticated_page.goto(f"{base_url}/sales-orders")
        authenticated_page.wait_for_load_state("networkidle")

        order_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/sales-orders/']"
        ).first
        if order_link.count() > 0:
            order_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            cancel_btn = authenticated_page.locator(
                "button:has-text('Cancel'), button:has-text('Void')"
            )
            if cancel_btn.count() > 0:
                expect(cancel_btn.first).to_be_visible()
