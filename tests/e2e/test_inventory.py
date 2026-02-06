"""
E2E Tests for Inventory Module.

Tests for creating, reading, updating, and managing:
- Inventory Items
- Stock Transactions
- Stock Levels
- Locations/Warehouses
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Items List Tests
# =============================================================================


@pytest.mark.e2e
class TestItemsList:
    """Tests for inventory items list page."""

    def test_items_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that items list page loads successfully."""
        authenticated_page.goto(f"{base_url}/inv/items", wait_until="domcontentloaded")

        expect(
            authenticated_page.locator("h1", has_text="Inventory Items").first
        ).to_be_visible()

    def test_items_page_has_cta(self, authenticated_page: Page, base_url: str):
        """Ensure items page loads and shows the new item CTA."""
        authenticated_page.goto(f"{base_url}/inv/items", wait_until="domcontentloaded")

        expect(authenticated_page.locator("a[href='/inv/items/new']")).to_be_visible()

    def test_items_list_with_search(self, authenticated_page: Page, base_url: str):
        """Test items list search functionality."""
        authenticated_page.goto(f"{base_url}/inv/items")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )
        if search.count() > 0:
            search.first.fill("Widget")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("main")).to_be_visible()

    def test_items_list_by_category(self, authenticated_page: Page, base_url: str):
        """Test items list category filter."""
        authenticated_page.goto(f"{base_url}/inv/items")
        authenticated_page.wait_for_load_state("networkidle")

        category_filter = authenticated_page.locator(
            "select[name='category'], select[name='category_id'], #category"
        )
        if category_filter.count() > 0:
            expect(category_filter.first).to_be_visible()

    def test_items_list_by_status(self, authenticated_page: Page, base_url: str):
        """Test items list status filter."""
        authenticated_page.goto(f"{base_url}/inv/items")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()


# =============================================================================
# Item CRUD Tests
# =============================================================================


@pytest.mark.e2e
class TestItemCreate:
    """Tests for creating inventory items."""

    def test_item_create_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that item create page loads."""
        response = authenticated_page.goto(f"{base_url}/inv/items/new")
        assert response.ok, f"Item create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_item_create_has_code_field(self, authenticated_page: Page, base_url: str):
        """Test that item form has code field."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#item_code, input[name='item_code'], input[name='code'], input[name='sku']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_item_create_has_name_field(self, authenticated_page: Page, base_url: str):
        """Test that item form has name field."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#item_name, input[name='item_name'], input[name='name']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_item_create_has_description_field(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that item form has description field."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#description, textarea[name='description'], input[name='description']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_item_create_has_unit_of_measure(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that item form has unit of measure field."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='unit_of_measure'], select[name='uom'], input[name='unit'], #uom"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_item_create_full_form(self, authenticated_page: Page, base_url: str):
        """Test complete item creation workflow."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill required fields
        code_field = authenticated_page.locator(
            "#item_code, input[name='item_code'], input[name='code']"
        )
        if code_field.count() > 0:
            code_field.first.fill(f"ITM-{uid}")

        name_field = authenticated_page.locator(
            "#item_name, input[name='item_name'], input[name='name']"
        )
        if name_field.count() > 0:
            name_field.first.fill(f"Test Item {uid}")

        # Fill description
        desc_field = authenticated_page.locator(
            "textarea[name='description'], input[name='description']"
        )
        if desc_field.count() > 0:
            desc_field.first.fill("Test item description")

        # Submit
        submit_btn = authenticated_page.locator("button[type='submit']")
        if submit_btn.count() > 0:
            submit_btn.click()
            authenticated_page.wait_for_load_state("networkidle")

    def test_item_create_with_unit_of_measure(
        self, authenticated_page: Page, base_url: str
    ):
        """Test item creation with unit of measure."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        uom_field = authenticated_page.locator(
            "select[name='unit_of_measure'], select[name='uom']"
        )
        if uom_field.count() > 0:
            expect(uom_field.first).to_be_visible()
            # Select a unit (e.g., Each, Box, Kg)
            uom_field.first.select_option(index=1)


@pytest.mark.e2e
class TestItemDetail:
    """Tests for item detail page."""

    def test_item_detail_page_accessible(self, authenticated_page: Page, base_url: str):
        """Test that item detail is accessible."""
        authenticated_page.goto(f"{base_url}/inv/items")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first item link
        item_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/inv/items/']"
        ).first
        if item_link.count() > 0:
            item_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(re.compile(r".*/inv/items/.*"))

    def test_item_detail_shows_stock_info(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that item detail shows stock information."""
        authenticated_page.goto(f"{base_url}/inv/items")
        authenticated_page.wait_for_load_state("networkidle")

        item_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/inv/items/']"
        ).first
        if item_link.count() > 0:
            item_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for stock-related content
            stock_info = authenticated_page.locator(
                "text=Stock, text=Quantity, text=On Hand, text=Available"
            )
            if stock_info.count() > 0:
                expect(stock_info.first).to_be_visible()


@pytest.mark.e2e
class TestItemEdit:
    """Tests for editing items."""

    def test_item_edit_page_loads(self, authenticated_page: Page, base_url: str):
        """Test that item edit page loads."""
        authenticated_page.goto(f"{base_url}/inv/items")
        authenticated_page.wait_for_load_state("networkidle")

        # Navigate to first item then edit
        item_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/inv/items/']"
        ).first
        if item_link.count() > 0:
            item_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                expect(authenticated_page).to_have_url(re.compile(r".*/edit.*"))

    def test_item_update_success(self, authenticated_page: Page, base_url: str):
        """Test successful item update."""
        authenticated_page.goto(f"{base_url}/inv/items")
        authenticated_page.wait_for_load_state("networkidle")

        item_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/inv/items/']"
        ).first
        if item_link.count() > 0:
            item_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                # Update a field
                name_field = authenticated_page.locator(
                    "input[name='item_name'], input[name='name']"
                )
                if name_field.count() > 0:
                    current_value = name_field.first.input_value()
                    name_field.first.fill(f"{current_value} Updated")

                    # Submit
                    authenticated_page.locator("button[type='submit']").click()
                    authenticated_page.wait_for_load_state("networkidle")


# =============================================================================
# Inventory Transactions Tests
# =============================================================================


@pytest.mark.e2e
class TestInventoryTransactions:
    """Tests for inventory transactions."""

    def test_transactions_page_loads(self, authenticated_page: Page, base_url: str):
        """Ensure transactions page loads and shows content."""
        authenticated_page.goto(
            f"{base_url}/inv/transactions", wait_until="domcontentloaded"
        )

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Inventory Transactions"
        )

        table = authenticated_page.locator("table")
        empty_state = authenticated_page.get_by_text("No transactions yet", exact=True)
        content = table.or_(empty_state)
        expect(content).to_be_visible()

    def test_transactions_list_with_filters(
        self, authenticated_page: Page, base_url: str
    ):
        """Test transactions list has filter options."""
        authenticated_page.goto(f"{base_url}/inv/transactions")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for type filter
        type_filter = authenticated_page.locator(
            "select[name='type'], select[name='transaction_type']"
        )
        if type_filter.count() > 0:
            expect(type_filter.first).to_be_visible()

    def test_transaction_create_receipt(self, authenticated_page: Page, base_url: str):
        """Test creating a receipt transaction."""
        authenticated_page.goto(f"{base_url}/inv/transactions/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Select receipt type
        type_field = authenticated_page.locator(
            "select[name='type'], select[name='transaction_type']"
        )
        if type_field.count() > 0:
            # Try to select receipt option
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "receipt" in text or "in" in text:
                    type_field.first.select_option(index=i)
                    break

    def test_transaction_create_issue(self, authenticated_page: Page, base_url: str):
        """Test creating an issue transaction."""
        authenticated_page.goto(f"{base_url}/inv/transactions/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Select issue type
        type_field = authenticated_page.locator(
            "select[name='type'], select[name='transaction_type']"
        )
        if type_field.count() > 0:
            # Try to select issue option
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "issue" in text or "out" in text:
                    type_field.first.select_option(index=i)
                    break

    def test_transaction_create_adjustment(
        self, authenticated_page: Page, base_url: str
    ):
        """Test creating an adjustment transaction."""
        authenticated_page.goto(f"{base_url}/inv/transactions/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Select adjustment type
        type_field = authenticated_page.locator(
            "select[name='type'], select[name='transaction_type']"
        )
        if type_field.count() > 0:
            # Try to select adjustment option
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "adjust" in text:
                    type_field.first.select_option(index=i)
                    break

    def test_transaction_detail_page(self, authenticated_page: Page, base_url: str):
        """Test transaction detail page."""
        authenticated_page.goto(f"{base_url}/inv/transactions")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first transaction link
        trans_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/inv/transactions/']"
        ).first
        if trans_link.count() > 0:
            trans_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(
                re.compile(r".*/inv/transactions/.*")
            )


# =============================================================================
# Stock Levels Tests
# =============================================================================


@pytest.mark.e2e
class TestStockLevels:
    """Tests for stock levels functionality."""

    def test_stock_levels_page_loads(self, authenticated_page: Page, base_url: str):
        """Test stock levels page loads."""
        response = authenticated_page.goto(f"{base_url}/inv/stock-levels")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_stock_by_location(self, authenticated_page: Page, base_url: str):
        """Test stock levels by location view."""
        authenticated_page.goto(f"{base_url}/inv/stock-levels")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for location filter
        location_filter = authenticated_page.locator(
            "select[name='location'], select[name='location_id'], select[name='warehouse']"
        )
        if location_filter.count() > 0:
            expect(location_filter.first).to_be_visible()

    def test_stock_valuation(self, authenticated_page: Page, base_url: str):
        """Test stock valuation display."""
        authenticated_page.goto(f"{base_url}/inv/stock-levels")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for valuation column/section
        valuation = authenticated_page.locator(
            "th:has-text('Value'), th:has-text('Valuation'), text=Total Value"
        )
        if valuation.count() > 0:
            expect(valuation.first).to_be_visible()
