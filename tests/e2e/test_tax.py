"""
E2E Tests for Tax Module.

Tests key tax pages using Playwright.
"""

from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestTaxReturns:
    """Test tax returns pages."""

    @pytest.mark.e2e
    def test_returns_page_has_cta(self, authenticated_page: Page, base_url: str):
        """Ensure returns page loads and shows the new return CTA."""
        response = authenticated_page.goto(f"{base_url}/tax/returns")
        assert response is not None, "No response from returns navigation"
        assert response.ok, f"Returns page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Tax Returns")
        expect(authenticated_page.get_by_test_id("tax-returns-new")).to_be_visible()
        expect(authenticated_page.get_by_test_id("tax-returns-table")).to_be_visible()


class TestTaxOverduePeriods:
    """Test overdue tax periods pages."""

    @pytest.mark.e2e
    def test_overdue_periods_page_has_filter(self, authenticated_page: Page, base_url: str):
        """Ensure overdue periods page loads and shows the date filter."""
        response = authenticated_page.goto(f"{base_url}/tax/periods/overdue")
        assert response is not None, "No response from overdue periods navigation"
        assert response.ok, f"Overdue periods page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Overdue Tax Periods"
        )
        expect(authenticated_page.get_by_test_id("tax-overdue-date")).to_be_visible()
        expect(authenticated_page.get_by_test_id("tax-overdue-table")).to_be_visible()


class TestTaxReferenceData:
    """Test tax reference data pages."""

    @pytest.mark.e2e
    def test_tax_codes_page_loads(self, authenticated_page: Page, base_url: str):
        """Ensure tax codes page loads."""
        authenticated_page.goto(f"{base_url}/tax/codes")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Tax Codes")

    @pytest.mark.e2e
    def test_tax_jurisdictions_page_loads(self, authenticated_page: Page, base_url: str):
        """Ensure tax jurisdictions page loads."""
        authenticated_page.goto(f"{base_url}/tax/jurisdictions")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text(
            "Tax Jurisdictions"
        )

    @pytest.mark.e2e
    def test_tax_periods_page_loads(self, authenticated_page: Page, base_url: str):
        """Ensure tax periods page loads."""
        response = authenticated_page.goto(f"{base_url}/tax/periods")
        assert response is not None, "No response from tax periods navigation"
        assert response.ok, f"Tax periods page failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_test_id("page-title")).to_contain_text("Tax Periods")


class TestTaxReturnDetail:
    """Test tax return detail pages."""

    @pytest.mark.e2e
    def test_tax_return_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure tax return detail shows not found state."""
        return_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/tax/returns/{return_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Tax return not found")).to_be_visible()

    @pytest.mark.e2e
    def test_tax_return_form_loads(self, authenticated_page: Page, base_url: str):
        """Ensure tax return form loads."""
        response = authenticated_page.goto(f"{base_url}/tax/returns/new")
        assert response is not None, "No response from tax return form navigation"
        assert response.ok, f"Tax return form failed with status {response.status}"
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.locator("form").first).to_be_visible()
