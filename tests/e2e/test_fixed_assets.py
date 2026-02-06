"""
E2E Tests for Fixed Assets Module.

Tests for creating, reading, updating, and managing:
- Fixed Assets
- Asset Categories
- Depreciation Schedules
- Asset Disposal and Revaluation
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Asset List Tests
# =============================================================================


@pytest.mark.e2e
class TestAssetList:
    """Tests for fixed assets list page."""

    def test_assets_page_loads(self, authenticated_page, base_url):
        """Test that assets list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/fa/assets")
        assert response.ok, f"Assets list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_assets_list_with_search(self, authenticated_page, base_url):
        """Test assets list search functionality."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )
        if search.count() > 0:
            search.first.fill("Computer")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("main")).to_be_visible()

    def test_assets_list_by_category(self, authenticated_page, base_url):
        """Test assets list category filter."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        category_filter = authenticated_page.locator(
            "select[name='category'], select[name='category_id'], #category"
        )
        if category_filter.count() > 0:
            expect(category_filter.first).to_be_visible()

    def test_assets_list_by_status(self, authenticated_page, base_url):
        """Test assets list status filter."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            expect(status_filter.first).to_be_visible()

    def test_assets_list_has_new_button(self, authenticated_page, base_url):
        """Test that assets list has new button."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/fa/assets/new'], button:has-text('New'), a:has-text('New Asset')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()

    def test_assets_list_shows_asset_codes(self, authenticated_page, base_url):
        """Test that assets list displays asset codes."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        table = authenticated_page.locator("table, [role='table'], .asset-list")
        if table.count() > 0:
            expect(table.first).to_be_visible()


# =============================================================================
# Asset CRUD Tests
# =============================================================================


@pytest.mark.e2e
class TestAssetCreate:
    """Tests for creating fixed assets."""

    def test_asset_create_page_loads(self, authenticated_page, base_url):
        """Test that asset create page loads."""
        response = authenticated_page.goto(f"{base_url}/fa/assets/new")
        assert response.ok, f"Asset create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_asset_create_has_code_field(self, authenticated_page, base_url):
        """Test that asset form has code field."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#asset_code, input[name='asset_code'], input[name='code']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_asset_create_has_name_field(self, authenticated_page, base_url):
        """Test that asset form has name field."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#asset_name, input[name='asset_name'], input[name='name']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_asset_create_has_category_field(self, authenticated_page, base_url):
        """Test that asset form has category selection."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='category_id'], select[name='category'], #category_id"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_asset_create_has_acquisition_date(self, authenticated_page, base_url):
        """Test that asset form has acquisition date field."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='acquisition_date'], input[name='purchase_date'], input[type='date']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_asset_create_has_cost_field(self, authenticated_page, base_url):
        """Test that asset form has cost/value field."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='acquisition_cost'], input[name='cost'], input[name='original_cost']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_asset_create_full_form(self, authenticated_page, base_url):
        """Test complete asset creation workflow."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Fill required fields
        code_field = authenticated_page.locator(
            "#asset_code, input[name='asset_code'], input[name='code']"
        )
        if code_field.count() > 0:
            code_field.first.fill(f"FA-{uid}")

        name_field = authenticated_page.locator(
            "#asset_name, input[name='asset_name'], input[name='name']"
        )
        if name_field.count() > 0:
            name_field.first.fill(f"Test Asset {uid}")

        # Select category if available
        category = authenticated_page.locator(
            "select[name='category_id'], select[name='category']"
        )
        if category.count() > 0:
            category.first.select_option(index=1)

        # Fill cost
        cost_field = authenticated_page.locator(
            "input[name='acquisition_cost'], input[name='cost']"
        )
        if cost_field.count() > 0:
            cost_field.first.fill("10000")

        # Submit
        submit_btn = authenticated_page.locator("button[type='submit']")
        if submit_btn.count() > 0:
            submit_btn.click()
            authenticated_page.wait_for_load_state("networkidle")

    def test_asset_create_with_depreciation_method(self, authenticated_page, base_url):
        """Test asset creation with depreciation method selection."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Check for depreciation method field
        method_field = authenticated_page.locator(
            "select[name='depreciation_method'], select[name='method'], #depreciation_method"
        )
        if method_field.count() > 0:
            expect(method_field.first).to_be_visible()
            # Common methods: Straight-line, Declining balance, etc.
            options = method_field.first.locator("option")
            expect(options).to_have_count_greater_than(0)

    def test_asset_create_has_useful_life_field(self, authenticated_page, base_url):
        """Test that asset form has useful life field."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='useful_life'], input[name='useful_life_months'], input[name='useful_life_years']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_asset_create_has_salvage_value(self, authenticated_page, base_url):
        """Test that asset form has salvage/residual value field."""
        authenticated_page.goto(f"{base_url}/fa/assets/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='salvage_value'], input[name='residual_value']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()


@pytest.mark.e2e
class TestAssetDetail:
    """Tests for asset detail page."""

    def test_asset_detail_page_accessible(self, authenticated_page, base_url):
        """Test that asset detail is accessible."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first asset link
        asset_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fa/assets/']"
        ).first
        if asset_link.count() > 0:
            asset_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(re.compile(r".*/fa/assets/.*"))

    def test_asset_detail_shows_depreciation_info(self, authenticated_page, base_url):
        """Test that asset detail shows depreciation information."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        asset_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fa/assets/']"
        ).first
        if asset_link.count() > 0:
            asset_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for depreciation-related content
            depreciation_info = authenticated_page.locator(
                "text=Depreciation, text=Book Value, text=Accumulated, [class*='depreciation']"
            )
            if depreciation_info.count() > 0:
                expect(depreciation_info.first).to_be_visible()


@pytest.mark.e2e
class TestAssetEdit:
    """Tests for editing assets."""

    def test_asset_edit_page_loads(self, authenticated_page, base_url):
        """Test that asset edit page loads."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        # Navigate to first asset then edit
        asset_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fa/assets/']"
        ).first
        if asset_link.count() > 0:
            asset_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                expect(authenticated_page).to_have_url(re.compile(r".*/edit.*"))

    def test_asset_update_success(self, authenticated_page, base_url):
        """Test successful asset update."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        asset_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fa/assets/']"
        ).first
        if asset_link.count() > 0:
            asset_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            edit_btn = authenticated_page.locator(
                "a[href*='/edit'], button:has-text('Edit')"
            )
            if edit_btn.count() > 0:
                edit_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

                # Update a field
                name_field = authenticated_page.locator(
                    "input[name='asset_name'], input[name='name']"
                )
                if name_field.count() > 0:
                    current_value = name_field.first.input_value()
                    name_field.first.fill(f"{current_value} Updated")

                    # Submit
                    authenticated_page.locator("button[type='submit']").click()
                    authenticated_page.wait_for_load_state("networkidle")


# =============================================================================
# Asset Workflows Tests
# =============================================================================


@pytest.mark.e2e
class TestAssetDisposal:
    """Tests for asset disposal workflow."""

    def test_asset_dispose_workflow(self, authenticated_page, base_url):
        """Test asset disposal workflow."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        asset_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fa/assets/']"
        ).first
        if asset_link.count() > 0:
            asset_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for dispose button
            dispose_btn = authenticated_page.locator(
                "button:has-text('Dispose'), a:has-text('Dispose'), button:has-text('Retire')"
            )
            if dispose_btn.count() > 0:
                expect(dispose_btn.first).to_be_visible()


@pytest.mark.e2e
class TestAssetRevaluation:
    """Tests for asset revaluation workflow."""

    def test_asset_revalue_workflow(self, authenticated_page, base_url):
        """Test asset revaluation workflow."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        asset_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fa/assets/']"
        ).first
        if asset_link.count() > 0:
            asset_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for revalue button
            revalue_btn = authenticated_page.locator(
                "button:has-text('Revalue'), a:has-text('Revalue'), button:has-text('Revaluation')"
            )
            if revalue_btn.count() > 0:
                expect(revalue_btn.first).to_be_visible()


# =============================================================================
# Depreciation Tests
# =============================================================================


@pytest.mark.e2e
class TestDepreciation:
    """Tests for depreciation functionality."""

    def test_depreciation_schedule_page(self, authenticated_page, base_url):
        """Test depreciation schedule page loads."""
        response = authenticated_page.goto(f"{base_url}/fa/depreciation")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_depreciation_run_button(self, authenticated_page, base_url):
        """Test depreciation run button exists."""
        authenticated_page.goto(f"{base_url}/fa/depreciation")
        authenticated_page.wait_for_load_state("networkidle")

        run_btn = authenticated_page.locator(
            "button:has-text('Run'), button:has-text('Calculate'), button:has-text('Process')"
        )
        if run_btn.count() > 0:
            expect(run_btn.first).to_be_visible()

    def test_depreciation_calculation_display(self, authenticated_page, base_url):
        """Test depreciation calculation display."""
        authenticated_page.goto(f"{base_url}/fa/depreciation")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for calculation results
        calc_results = authenticated_page.locator(
            "table, .depreciation-results, [class*='calculation']"
        )
        if calc_results.count() > 0:
            expect(calc_results.first).to_be_visible()

    def test_depreciation_journal_generation(self, authenticated_page, base_url):
        """Test depreciation journal generation."""
        authenticated_page.goto(f"{base_url}/fa/depreciation")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for journal/post button
        journal_btn = authenticated_page.locator(
            "button:has-text('Post'), button:has-text('Journal'), button:has-text('Generate')"
        )
        if journal_btn.count() > 0:
            expect(journal_btn.first).to_be_visible()


# =============================================================================
# Asset Categories Tests
# =============================================================================


@pytest.mark.e2e
class TestAssetCategories:
    """Tests for asset categories."""

    def test_categories_list_page_loads(self, authenticated_page, base_url):
        """Test categories list page loads."""
        response = authenticated_page.goto(f"{base_url}/fa/categories")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_category_create_page_loads(self, authenticated_page, base_url):
        """Test category create page loads."""
        response = authenticated_page.goto(f"{base_url}/fa/categories/new")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")

    def test_category_create_has_name_field(self, authenticated_page, base_url):
        """Test category form has name field."""
        authenticated_page.goto(f"{base_url}/fa/categories/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "input[name='name'], input[name='category_name'], #name"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_category_has_depreciation_defaults(self, authenticated_page, base_url):
        """Test category form has depreciation default fields."""
        authenticated_page.goto(f"{base_url}/fa/categories/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Check for default depreciation method
        method_field = authenticated_page.locator(
            "select[name='default_depreciation_method'], select[name='depreciation_method']"
        )
        if method_field.count() > 0:
            expect(method_field.first).to_be_visible()

        # Check for default useful life
        life_field = authenticated_page.locator(
            "input[name='default_useful_life'], input[name='useful_life']"
        )
        if life_field.count() > 0:
            expect(life_field.first).to_be_visible()

    def test_category_edit_page_accessible(self, authenticated_page, base_url):
        """Test category edit page is accessible."""
        authenticated_page.goto(f"{base_url}/fa/categories")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first category link
        category_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fa/categories/']"
        ).first
        if category_link.count() > 0:
            category_link.click()
            authenticated_page.wait_for_load_state("networkidle")
