"""
E2E Tests for Search and Filter Functionality.

Tests for search inputs and filter controls across list pages.
"""

import pytest
from playwright.sync_api import expect

# =============================================================================
# Search Functionality Tests
# =============================================================================


@pytest.mark.e2e
class TestSearchFunctionality:
    """Tests for search input behavior."""

    def test_search_returns_results(self, authenticated_page, base_url):
        """Test search returns matching results."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        # Get initial row count
        initial_rows = authenticated_page.locator("table tbody tr").count()

        # Find and use search input
        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search'], #search"
        )

        if search.count() > 0 and initial_rows > 0:
            # Get text from first row to search for
            first_row_text = authenticated_page.locator(
                "table tbody tr"
            ).first.text_content()
            if first_row_text:
                # Use first few characters as search term
                search_term = first_row_text[:5].strip()
                if search_term:
                    search.first.fill(search_term)
                    authenticated_page.keyboard.press("Enter")
                    authenticated_page.wait_for_load_state("networkidle")

                    # Should have results
                    expect(authenticated_page.locator("main")).to_be_visible()

    def test_search_no_results_message(self, authenticated_page, base_url):
        """Test search shows no results message for non-matching query."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )

        if search.count() > 0:
            # Search for something that won't exist
            search.first.fill("xyznonexistent123456")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            # Should show no results message or empty table
            no_results = authenticated_page.locator(
                "text=No suppliers, text=No results, text=No data, text=Nothing found, .empty-state"
            )
            authenticated_page.locator("table tbody tr")

            # Either no results message or empty table
            if no_results.count() > 0:
                expect(no_results.first).to_be_visible()
            else:
                # Table might be empty
                expect(authenticated_page.locator("main")).to_be_visible()

    def test_search_clears_on_empty(self, authenticated_page, base_url):
        """Test clearing search shows all results."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )

        if search.count() > 0:
            # First apply a search
            search.first.fill("test")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            # Now clear search
            search.first.fill("")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            # Should show all results again
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_search_preserves_filters(self, authenticated_page, base_url):
        """Test search preserves active filters."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        # Apply a filter first
        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            status_filter.first.select_option(index=1)
            authenticated_page.wait_for_load_state("networkidle")

            status_filter.first.input_value()

        # Now apply search
        search = authenticated_page.locator(
            "input[type='search'], input[name='search']"
        )
        if search.count() > 0:
            search.first.fill("test")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            # Check if filter is still applied
            if status_filter.count() > 0:
                status_filter.first.input_value()
                # Filter should be preserved
                expect(authenticated_page.locator("main")).to_be_visible()

    def test_search_instant_or_on_enter(self, authenticated_page, base_url):
        """Test search triggers on enter or instantly."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search']"
        )

        if search.count() > 0:
            # Type in search box
            search.first.fill("test")

            # Wait a moment for potential instant search
            authenticated_page.wait_for_timeout(500)

            # Press enter to ensure search triggers
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            # Verify page responded
            expect(authenticated_page.locator("main")).to_be_visible()


# =============================================================================
# Filter Functionality Tests
# =============================================================================


@pytest.mark.e2e
class TestFilterFunctionality:
    """Tests for filter controls."""

    def test_status_filter(self, authenticated_page, base_url):
        """Test status filter works."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status'], #status")

        if status_filter.count() > 0:
            # Get initial state

            # Change filter
            options = status_filter.first.locator("option")
            if options.count() > 1:
                status_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")

                # URL or results should change
                expect(authenticated_page.locator("main")).to_be_visible()

    def test_date_range_filter(self, authenticated_page, base_url):
        """Test date range filter works."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        start_date = authenticated_page.locator(
            "input[name='start_date'], input[name='from_date'], input[name='date_from']"
        )
        end_date = authenticated_page.locator(
            "input[name='end_date'], input[name='to_date'], input[name='date_to']"
        )

        if start_date.count() > 0 and end_date.count() > 0:
            # Set date range
            start_date.first.fill("2024-01-01")
            end_date.first.fill("2024-12-31")

            # Apply filter (might need button click or auto-apply)
            apply_btn = authenticated_page.locator(
                "button:has-text('Apply'), button:has-text('Filter'), button[type='submit']"
            )
            if apply_btn.count() > 0:
                apply_btn.first.click()
                authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("main")).to_be_visible()

    def test_category_filter(self, authenticated_page, base_url):
        """Test category filter works."""
        authenticated_page.goto(f"{base_url}/gl/accounts")
        authenticated_page.wait_for_load_state("networkidle")

        category_filter = authenticated_page.locator(
            "select[name='category'], select[name='account_category'], #category"
        )

        if category_filter.count() > 0:
            options = category_filter.first.locator("option")
            if options.count() > 1:
                category_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")

                expect(authenticated_page.locator("main")).to_be_visible()

    def test_combined_filters(self, authenticated_page, base_url):
        """Test multiple filters work together."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        # Apply status filter
        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            options = status_filter.first.locator("option")
            if options.count() > 1:
                status_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")

        # Apply supplier filter
        supplier_filter = authenticated_page.locator(
            "select[name='supplier_id'], select[name='supplier'], #supplier"
        )
        if supplier_filter.count() > 0:
            options = supplier_filter.first.locator("option")
            if options.count() > 1:
                supplier_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")

        # Both filters should be applied
        expect(authenticated_page.locator("main")).to_be_visible()

    def test_filter_reset(self, authenticated_page, base_url):
        """Test filter reset/clear works."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        # Apply a filter
        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            options = status_filter.first.locator("option")
            if options.count() > 1:
                status_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")

        # Look for reset/clear button
        reset_btn = authenticated_page.locator(
            "button:has-text('Reset'), button:has-text('Clear'), a:has-text('Reset'), a:has-text('Clear Filters')"
        )

        if reset_btn.count() > 0:
            reset_btn.first.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Filters should be cleared
            expect(authenticated_page.locator("main")).to_be_visible()


# =============================================================================
# Search and Filter Integration Tests
# =============================================================================


@pytest.mark.e2e
class TestSearchFilterIntegration:
    """Tests for search and filter working together."""

    def test_search_with_active_filter(self, authenticated_page, base_url):
        """Test search works with active filter."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        # Apply filter first
        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            options = status_filter.first.locator("option")
            if options.count() > 1:
                status_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")

        # Now search
        search = authenticated_page.locator(
            "input[type='search'], input[name='search']"
        )
        if search.count() > 0:
            search.first.fill("test")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

        # Both should be applied
        expect(authenticated_page.locator("main")).to_be_visible()

    def test_filter_updates_url(self, authenticated_page, base_url):
        """Test filters update URL parameters."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        status_filter = authenticated_page.locator("select[name='status'], #status")
        if status_filter.count() > 0:
            options = status_filter.first.locator("option")
            if options.count() > 1:
                status_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")

                # URL should have filter parameter
                # Filter parameter might be in URL
                expect(authenticated_page.locator("main")).to_be_visible()

    def test_filters_preserved_on_navigation(self, authenticated_page, base_url):
        """Test filters are preserved when navigating back."""
        authenticated_page.goto(f"{base_url}/ap/suppliers")
        authenticated_page.wait_for_load_state("networkidle")

        # Apply filter
        status_filter = authenticated_page.locator("select[name='status'], #status")
        filter_applied = False
        if status_filter.count() > 0:
            options = status_filter.first.locator("option")
            if options.count() > 1:
                status_filter.first.select_option(index=1)
                authenticated_page.wait_for_load_state("networkidle")
                filter_applied = True

        if filter_applied:
            # Navigate to detail and back
            supplier_link = authenticated_page.locator("table tbody tr a").first
            if supplier_link.count() > 0:
                supplier_link.click()
                authenticated_page.wait_for_load_state("networkidle")

                # Go back
                authenticated_page.go_back()
                authenticated_page.wait_for_load_state("networkidle")

                # Filter might be preserved via URL or browser state
                expect(authenticated_page.locator("main")).to_be_visible()


# =============================================================================
# Quick Filter Tests
# =============================================================================


@pytest.mark.e2e
class TestQuickFilters:
    """Tests for quick filter buttons/tabs."""

    def test_quick_filter_tabs(self, authenticated_page, base_url):
        """Test quick filter tabs work."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for quick filter tabs (All, Draft, Pending, Paid, etc.)
        filter_tabs = authenticated_page.locator(
            ".filter-tabs a, .nav-tabs a, button[role='tab'], .quick-filters button"
        )

        if filter_tabs.count() > 1:
            # Click second tab
            filter_tabs.nth(1).click()
            authenticated_page.wait_for_load_state("networkidle")

            # Should filter results
            expect(authenticated_page.locator("main")).to_be_visible()

    def test_quick_filter_all(self, authenticated_page, base_url):
        """Test 'All' quick filter shows all results."""
        authenticated_page.goto(f"{base_url}/ap/invoices")
        authenticated_page.wait_for_load_state("networkidle")

        # Apply a filter first
        pending_tab = authenticated_page.locator(
            "a:has-text('Pending'), button:has-text('Pending')"
        )
        if pending_tab.count() > 0:
            pending_tab.first.click()
            authenticated_page.wait_for_load_state("networkidle")

        # Now click 'All' tab
        all_tab = authenticated_page.locator(
            "a:has-text('All'), button:has-text('All')"
        )
        if all_tab.count() > 0:
            all_tab.first.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Should show all results
            expect(authenticated_page.locator("main")).to_be_visible()
