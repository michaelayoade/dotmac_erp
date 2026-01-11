"""
E2E Tests for Lease Module.

Tests key lease pages using Playwright.
"""

from uuid import uuid4

import pytest
from playwright.sync_api import Page, expect


class TestLeaseContracts:
    """Test lease contracts pages."""

    @pytest.mark.e2e
    def test_contracts_page_has_cta(self, authenticated_page: Page, base_url: str):
        """Ensure contracts page loads and shows the new contract CTA."""
        authenticated_page.goto(f"{base_url}/lease/contracts")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.locator("h1", has_text="Lease Contracts").first).to_be_visible()
        expect(authenticated_page.locator("a[href='/lease/contracts/new']")).to_be_visible()

    @pytest.mark.e2e
    def test_contract_form_loads(self, authenticated_page: Page, base_url: str):
        """Ensure the new contract form loads."""
        authenticated_page.goto(f"{base_url}/lease/contracts/new")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.locator("form").first).to_be_visible()


class TestLeaseContractDetail:
    """Test lease contract detail pages."""

    @pytest.mark.e2e
    def test_contract_detail_not_found(self, authenticated_page: Page, base_url: str):
        """Ensure contract detail shows not found state."""
        lease_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/lease/contracts/{lease_id}")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("Lease contract not found")).to_be_visible()

    @pytest.mark.e2e
    def test_contract_schedule_empty_state(self, authenticated_page: Page, base_url: str):
        """Ensure schedule page shows empty state for missing contract."""
        lease_id = str(uuid4())
        authenticated_page.goto(f"{base_url}/lease/contracts/{lease_id}/schedule")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("No scheduled payments")).to_be_visible()


class TestLeaseOverdue:
    """Test overdue lease pages."""

    @pytest.mark.e2e
    def test_overdue_page_has_filter(self, authenticated_page: Page, base_url: str):
        """Ensure overdue page loads and shows the date filter."""
        authenticated_page.goto(f"{base_url}/lease/overdue")
        authenticated_page.wait_for_load_state("networkidle")

        expect(
            authenticated_page.locator("h1", has_text="Overdue Lease Payments").first
        ).to_be_visible()
        expect(authenticated_page.locator("input[name='as_of_date']")).to_be_visible()
        expect(authenticated_page.locator("table").first).to_be_visible()


class TestLeaseModifications:
    """Test lease modifications page."""

    @pytest.mark.e2e
    def test_modifications_page_empty_state(self, authenticated_page: Page, base_url: str):
        """Ensure modifications page loads and shows empty state."""
        authenticated_page.goto(f"{base_url}/lease/modifications")
        authenticated_page.wait_for_load_state("networkidle")

        expect(authenticated_page.get_by_text("No modifications found")).to_be_visible()


class TestLeaseVariablePayments:
    """Test lease variable payments page."""

    @pytest.mark.e2e
    def test_variable_payments_empty_state(self, authenticated_page: Page, base_url: str):
        """Ensure variable payments page loads and shows empty state."""
        authenticated_page.goto(f"{base_url}/lease/variable-payments")
        authenticated_page.wait_for_load_state("networkidle")

        expect(
            authenticated_page.get_by_text("No variable payment records available.")
        ).to_be_visible()
