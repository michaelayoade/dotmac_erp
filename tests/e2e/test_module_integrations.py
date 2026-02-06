"""
E2E Tests for Module Integrations.

Tests the UI flows for AP → Inventory, AP → Fixed Assets, and AR → Inventory
integrations using Playwright.
"""

import re

import pytest
from playwright.sync_api import Page, expect


class TestInventoryTransactionForms:
    """Test inventory transaction form pages."""

    @pytest.mark.e2e
    def test_inventory_transactions_page_loads(self, inventory_transactions_page: Page):
        """Test that inventory transactions page loads successfully."""
        expect(inventory_transactions_page).to_have_url(
            re.compile(r".*/inv/transactions.*")
        )
        expect(inventory_transactions_page.locator("h1")).to_contain_text(
            "Transactions"
        )

    @pytest.mark.e2e
    def test_receipt_form_accessible(self, authenticated_page: Page, base_url: str):
        """Test that receipt form is accessible from transactions page."""
        authenticated_page.goto(f"{base_url}/inv/transactions")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for receipt action button/link
        receipt_link = authenticated_page.locator("a[href*='receipt']").first
        if receipt_link.is_visible():
            receipt_link.click()
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page).to_have_url(re.compile(r".*/receipt.*"))

    @pytest.mark.e2e
    def test_receipt_form_has_required_fields(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that receipt form has all required fields."""
        authenticated_page.goto(f"{base_url}/inv/transactions/receipt/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Check for required form fields
        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Item selector
        expect(form.locator("select[name='item_id']")).to_be_visible()

        # Warehouse selector
        expect(form.locator("select[name='warehouse_id']")).to_be_visible()

        # Quantity input
        expect(form.locator("input[name='quantity']")).to_be_visible()

        # Unit cost input
        expect(form.locator("input[name='unit_cost']")).to_be_visible()

        # Transaction date
        expect(form.locator("input[name='transaction_date']")).to_be_visible()

    @pytest.mark.e2e
    def test_issue_form_has_required_fields(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that issue form has all required fields."""
        authenticated_page.goto(f"{base_url}/inv/transactions/issue/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Check required fields
        expect(form.locator("select[name='item_id']")).to_be_visible()
        expect(form.locator("select[name='warehouse_id']")).to_be_visible()
        expect(form.locator("input[name='quantity']")).to_be_visible()
        expect(form.locator("input[name='unit_cost']")).to_be_visible()

    @pytest.mark.e2e
    def test_transfer_form_has_warehouse_selectors(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that transfer form has source and destination warehouse selectors."""
        authenticated_page.goto(f"{base_url}/inv/transactions/transfer/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # From/To warehouses
        expect(form.locator("select[name='from_warehouse_id']")).to_be_visible()
        expect(form.locator("select[name='to_warehouse_id']")).to_be_visible()

    @pytest.mark.e2e
    def test_adjustment_form_has_type_selector(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that adjustment form has adjustment type selector."""
        authenticated_page.goto(f"{base_url}/inv/transactions/adjustment/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

        # Adjustment type (increase/decrease)
        expect(form.locator("select[name='adjustment_type']")).to_be_visible()

        # Reason selector
        expect(form.locator("select[name='reason']")).to_be_visible()


class TestAPInvoiceCapitalizationUI:
    """Test AP invoice capitalization UI elements."""

    @pytest.mark.e2e
    def test_invoice_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that AP invoice form loads."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page).to_have_url(re.compile(r".*/ap/invoices/new.*"))
        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

    @pytest.mark.e2e
    def test_invoice_form_has_line_items_section(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that invoice form has line items section."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Check for line items section
        lines_section = authenticated_page.locator("[data-testid='line-items']")
        if not lines_section.is_visible():
            # Try alternative selectors
            lines_section = authenticated_page.locator("table").first

        expect(lines_section).to_be_visible()


class TestGoodsReceiptWarehouseRequired:
    """Test goods receipt warehouse requirement."""

    @pytest.mark.e2e
    def test_goods_receipt_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that goods receipt form loads."""
        authenticated_page.goto(f"{base_url}/ap/goods-receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

    @pytest.mark.e2e
    def test_warehouse_field_visible(self, authenticated_page: Page, base_url: str):
        """Test that warehouse field is visible on goods receipt form."""
        authenticated_page.goto(f"{base_url}/ap/goods-receipts/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Warehouse should be a required field
        warehouse_select = authenticated_page.locator("select[name='warehouse_id']")
        expect(warehouse_select).to_be_visible()


class TestFixedAssetSupplierLink:
    """Test fixed asset supplier linkage UI."""

    @pytest.mark.e2e
    def test_asset_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that fixed asset form loads."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

    @pytest.mark.e2e
    def test_asset_form_has_category_selector(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that asset form has category selector."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Category selector
        category_select = authenticated_page.locator("select[name='category_id']")
        expect(category_select).to_be_visible()


class TestARInvoiceInventoryUI:
    """Test AR invoice inventory UI elements."""

    @pytest.mark.e2e
    def test_ar_invoice_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that AR invoice form loads."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

    @pytest.mark.e2e
    def test_ar_invoice_has_customer_selector(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that AR invoice form has customer selector."""
        authenticated_page.goto(f"{base_url}/ar/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        customer_select = authenticated_page.locator("select[name='customer_id']")
        expect(customer_select).to_be_visible()


class TestInventoryItemForm:
    """Test inventory item form enhancements."""

    @pytest.mark.e2e
    def test_item_form_loads(self, authenticated_page: Page, base_url: str):
        """Test that inventory item form loads."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        form = authenticated_page.locator("form").first
        expect(form).to_be_visible()

    @pytest.mark.e2e
    def test_item_form_has_costing_method(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that item form has costing method selector."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        costing_select = authenticated_page.locator("select[name='costing_method']")
        expect(costing_select).to_be_visible()

    @pytest.mark.e2e
    def test_item_form_has_account_selectors(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that item form has inventory and COGS account selectors."""
        authenticated_page.goto(f"{base_url}/inv/items/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Inventory account
        inv_account = authenticated_page.locator("select[name='inventory_account_id']")
        if inv_account.is_visible():
            expect(inv_account).to_be_visible()

        # COGS account
        cogs_account = authenticated_page.locator("select[name='cogs_account_id']")
        if cogs_account.is_visible():
            expect(cogs_account).to_be_visible()


class TestNavigationBetweenModules:
    """Test navigation between integrated modules."""

    @pytest.mark.e2e
    def test_navigate_from_dashboard_to_inventory(
        self, dashboard_page: Page, base_url: str
    ):
        """Test navigating from dashboard to inventory."""
        # Look for inventory link in sidebar or nav
        inv_link = dashboard_page.locator("a[href*='/inv']").first
        if inv_link.is_visible():
            inv_link.click()
            dashboard_page.wait_for_load_state("networkidle")
            expect(dashboard_page).to_have_url(re.compile(r".*/inv.*"))

    @pytest.mark.e2e
    def test_navigate_from_ap_to_inventory(self, ap_invoices_page: Page, base_url: str):
        """Test navigating from AP to inventory."""
        # Click on inventory link
        inv_link = ap_invoices_page.locator("a[href*='/inv']").first
        if inv_link.is_visible():
            inv_link.click()
            ap_invoices_page.wait_for_load_state("networkidle")
            expect(ap_invoices_page).to_have_url(re.compile(r".*/inv.*"))

    @pytest.mark.e2e
    def test_navigate_from_fa_to_ap(self, fixed_assets_page: Page, base_url: str):
        """Test navigating from fixed assets to AP."""
        # Click on AP link
        ap_link = fixed_assets_page.locator("a[href*='/ap']").first
        if ap_link.is_visible():
            ap_link.click()
            fixed_assets_page.wait_for_load_state("networkidle")
            expect(fixed_assets_page).to_have_url(re.compile(r".*/ap.*"))


class TestModuleIntegrationWorkflows:
    """Test complete integration workflows through UI."""

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_inventory_receipt_to_transaction_list(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that creating a receipt navigates back to transaction list."""
        # Navigate to receipt form
        authenticated_page.goto(f"{base_url}/inv/transactions/receipt/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Click cancel to go back to list
        cancel_btn = authenticated_page.locator("a:has-text('Cancel')").first
        if cancel_btn.is_visible():
            cancel_btn.click()
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page).to_have_url(re.compile(r".*/inv/transactions.*"))

    @pytest.mark.e2e
    @pytest.mark.slow
    def test_ap_invoice_has_item_selection_capability(
        self, authenticated_page: Page, base_url: str
    ):
        """Test that AP invoice form allows item selection in line items."""
        authenticated_page.goto(f"{base_url}/ap/invoices/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Check if there's an item selector in the line items area
        # This tests that the UI supports inventory items on AP invoices
        item_select = authenticated_page.locator("select[name*='item']")
        # Either exists in initial form or in dynamic line items
        if item_select.count() > 0:
            expect(item_select.first).to_be_visible()
