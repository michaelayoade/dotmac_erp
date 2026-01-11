"""
E2E Tests for Financial Instruments Module.

Tests key financial instrument pages using Playwright.
"""

import pytest
from playwright.sync_api import Page, expect


class TestFinancialInstruments:
    """Test financial instruments pages."""

    @pytest.mark.e2e
    def test_instruments_page_has_cta(self, authenticated_page: Page, base_url: str):
        """Ensure instruments page loads and shows the new instrument CTA."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        expect(
            authenticated_page.locator("h1", has_text="Financial Instruments").first
        ).to_be_visible()
        expect(authenticated_page.locator("a[href='/fin-inst/instruments/new']")).to_be_visible()


class TestHedgeAccounting:
    """Test hedge accounting pages."""

    @pytest.mark.e2e
    def test_hedges_page_has_cta(self, authenticated_page: Page, base_url: str):
        """Ensure hedges page loads and shows the new hedge CTA."""
        authenticated_page.goto(f"{base_url}/fin-inst/hedges")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.locator("h1", has_text="Hedge Accounting").first).to_be_visible()
        expect(authenticated_page.locator("a[href='/fin-inst/hedges/new']")).to_be_visible()
