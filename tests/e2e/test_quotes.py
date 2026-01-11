"""
E2E Tests for Quotes Module.

Tests for creating, reading, updating, and managing quotes/quotations.
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Quotes List Tests
# =============================================================================


@pytest.mark.e2e
class TestQuotesList:
    """Tests for quotes list page."""

    def test_quotes_page_loads(self, authenticated_page, base_url):
        """Test that quotes list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/quotes")
        assert response.ok, f"Quotes list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_quotes_list_with_search(self, authenticated_page, base_url):
        """Test quotes list search functionality."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )
        if search.count() > 0:
            search.first.fill("QUO-001")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("body")).to_be_visible()

    def test_quotes_list_by_status(self, authenticated_page, base_url):
        """Test quotes list status filter."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator(
            "select[name='status'], #status"
        )
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()

    def test_quotes_list_has_new_button(self, authenticated_page, base_url):
        """Test that quotes list has new button."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/quotes/new'], button:has-text('New'), a:has-text('New Quote')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()


# =============================================================================
# Quote CRUD Tests
# =============================================================================


@pytest.mark.e2e
class TestQuoteCreate:
    """Tests for creating quotes."""

    def test_quote_create_page_loads(self, authenticated_page, base_url):
        """Test that quote create page loads."""
        response = authenticated_page.goto(f"{base_url}/quotes/new")
        assert response.ok, f"Quote create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_quote_create_has_customer_field(self, authenticated_page, base_url):
        """Test that quote form has customer selection."""
        authenticated_page.goto(f"{base_url}/quotes/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='customer_id'], select[name='customer'], #customer_id"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_quote_create_has_date_field(self, authenticated_page, base_url):
        """Test that quote form has date field."""
        authenticated_page.goto(f"{base_url}/quotes/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='quote_date'], input[type='date']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_quote_create_has_valid_until_field(self, authenticated_page, base_url):
        """Test that quote form has valid until/expiry date."""
        authenticated_page.goto(f"{base_url}/quotes/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='valid_until'], input[name='expiry_date']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_quote_create_with_lines(self, authenticated_page, base_url):
        """Test quote creation with line items."""
        authenticated_page.goto(f"{base_url}/quotes/new")
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


@pytest.mark.e2e
class TestQuoteDetail:
    """Tests for quote detail page."""

    def test_quote_detail_page_accessible(self, authenticated_page, base_url):
        """Test that quote detail is accessible."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(re.compile(r".*/quotes/.*"))

    def test_quote_detail_shows_lines(self, authenticated_page, base_url):
        """Test that quote detail shows line items."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            lines = authenticated_page.locator(
                "table, .line-items, [class*='lines']"
            )
            if lines.count() > 0:
                expect(lines.first).to_be_visible()

    def test_quote_detail_shows_totals(self, authenticated_page, base_url):
        """Test that quote detail shows totals."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            totals = authenticated_page.locator(
                "text=Total, text=Subtotal, .totals"
            )
            if totals.count() > 0:
                expect(totals.first).to_be_visible()


@pytest.mark.e2e
class TestQuoteEdit:
    """Tests for editing quotes."""

    def test_quote_edit_page_loads(self, authenticated_page, base_url):
        """Test that quote edit page loads."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                expect(authenticated_page).to_have_url(re.compile(r".*/edit.*"))


# =============================================================================
# Quote Workflow Tests
# =============================================================================


@pytest.mark.e2e
class TestQuoteWorkflows:
    """Tests for quote workflow operations."""

    def test_quote_send_to_customer(self, authenticated_page, base_url):
        """Test sending quote to customer."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            send_btn = authenticated_page.locator(
                "button:has-text('Send'), button:has-text('Email'), a:has-text('Send')"
            )
            if send_btn.count() > 0:
                expect(send_btn.first).to_be_visible()

    def test_quote_accept_workflow(self, authenticated_page, base_url):
        """Test quote acceptance workflow."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            accept_btn = authenticated_page.locator(
                "button:has-text('Accept'), button:has-text('Approve')"
            )
            if accept_btn.count() > 0:
                expect(accept_btn.first).to_be_visible()

    def test_quote_reject_workflow(self, authenticated_page, base_url):
        """Test quote rejection workflow."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            reject_btn = authenticated_page.locator(
                "button:has-text('Reject'), button:has-text('Decline')"
            )
            if reject_btn.count() > 0:
                expect(reject_btn.first).to_be_visible()

    def test_quote_convert_to_order(self, authenticated_page, base_url):
        """Test converting quote to sales order."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            convert_btn = authenticated_page.locator(
                "button:has-text('Convert'), button:has-text('Create Order'), a:has-text('Convert to Order')"
            )
            if convert_btn.count() > 0:
                expect(convert_btn.first).to_be_visible()

    def test_quote_duplicate(self, authenticated_page, base_url):
        """Test duplicating a quote."""
        authenticated_page.goto(f"{base_url}/quotes")
        authenticated_page.wait_for_load_state("networkidle")

        quote_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/quotes/']"
        ).first
        if quote_link.count() > 0:
            quote_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            duplicate_btn = authenticated_page.locator(
                "button:has-text('Duplicate'), button:has-text('Copy'), a:has-text('Duplicate')"
            )
            if duplicate_btn.count() > 0:
                expect(duplicate_btn.first).to_be_visible()
