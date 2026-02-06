"""
E2E Tests for Fixed Assets Module.

Tests key fixed asset pages using Playwright.
"""

import pytest
from playwright.sync_api import Page, expect


class TestFixedAssets:
    """Test fixed assets pages."""

    @pytest.mark.e2e
    def test_assets_page_has_cta(self, authenticated_page: Page, base_url: str):
        """Ensure assets page loads and shows the new asset CTA."""
        authenticated_page.goto(f"{base_url}/fa/assets")
        authenticated_page.wait_for_load_state("networkidle")

        expect(
            authenticated_page.locator("h1", has_text="Fixed Assets").first
        ).to_be_visible()
        expect(authenticated_page.locator("a[href='/fa/assets/new']")).to_be_visible()


class TestDepreciationSchedule:
    """Test depreciation schedule pages."""

    @pytest.mark.e2e
    def test_depreciation_page_has_run_button(
        self, authenticated_page: Page, base_url: str
    ):
        """Ensure depreciation page loads and shows the run button."""
        authenticated_page.goto(f"{base_url}/fa/depreciation")
        authenticated_page.wait_for_load_state("networkidle")

        expect(
            authenticated_page.locator("h1", has_text="Depreciation Schedule").first
        ).to_be_visible()
        expect(
            authenticated_page.locator("button", has_text="Run Depreciation")
        ).to_be_visible()
