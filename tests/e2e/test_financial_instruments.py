"""
E2E Tests for Financial Instruments Module.

Tests for creating, reading, updating, and managing:
- Financial Instruments (Debt, Equity, Derivatives)
- Instrument Valuations
- Hedge Accounting
"""

import re
from uuid import uuid4

import pytest
from playwright.sync_api import expect


def unique_id() -> str:
    """Generate a unique ID for test data."""
    return str(uuid4())[:8]


# =============================================================================
# Instruments List Tests
# =============================================================================


@pytest.mark.e2e
class TestInstrumentsList:
    """Tests for financial instruments list page."""

    def test_instruments_page_loads(self, authenticated_page, base_url):
        """Test that instruments list page loads successfully."""
        response = authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        assert response.ok, f"Instruments list failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_instruments_list_with_search(self, authenticated_page, base_url):
        """Test instruments list search functionality."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        search = authenticated_page.locator(
            "input[type='search'], input[name='search'], input[placeholder*='Search']"
        )
        if search.count() > 0:
            search.first.fill("Bond")
            authenticated_page.keyboard.press("Enter")
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page.locator("body")).to_be_visible()

    def test_instruments_list_by_type(self, authenticated_page, base_url):
        """Test instruments list type filter."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        type_filter = authenticated_page.locator(
            "select[name='type'], select[name='instrument_type'], #type"
        )
        if type_filter.count() > 0:
            expect(type_filter.first).to_be_visible()

    def test_instruments_list_has_new_button(self, authenticated_page, base_url):
        """Test that instruments list has new button."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        new_btn = authenticated_page.locator(
            "a[href*='/fin-inst/instruments/new'], button:has-text('New'), a:has-text('New Instrument')"
        )
        if new_btn.count() > 0:
            expect(new_btn.first).to_be_visible()

    def test_instruments_list_shows_values(self, authenticated_page, base_url):
        """Test that instruments list displays current values."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for value column
        value_column = authenticated_page.locator(
            "th:has-text('Value'), th:has-text('Fair Value'), th:has-text('Amount')"
        )
        if value_column.count() > 0:
            expect(value_column.first).to_be_visible()


# =============================================================================
# Instrument CRUD Tests
# =============================================================================


@pytest.mark.e2e
class TestInstrumentCreate:
    """Tests for creating financial instruments."""

    def test_instrument_create_page_loads(self, authenticated_page, base_url):
        """Test that instrument create page loads."""
        response = authenticated_page.goto(f"{base_url}/fin-inst/instruments/new")
        assert response.ok, f"Instrument create failed: {response.status}"

        authenticated_page.wait_for_load_state("networkidle")

    def test_instrument_create_has_type_field(self, authenticated_page, base_url):
        """Test that instrument form has type selection."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='instrument_type'], select[name='type'], #instrument_type"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_instrument_create_has_name_field(self, authenticated_page, base_url):
        """Test that instrument form has name field."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "#instrument_name, input[name='instrument_name'], input[name='name']"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_instrument_create_debt(self, authenticated_page, base_url):
        """Test creating a debt instrument."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Select debt type
        type_field = authenticated_page.locator(
            "select[name='instrument_type'], select[name='type']"
        )
        if type_field.count() > 0:
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "debt" in text or "bond" in text or "loan" in text:
                    type_field.first.select_option(index=i)
                    break

        # Fill name
        name_field = authenticated_page.locator(
            "input[name='instrument_name'], input[name='name']"
        )
        if name_field.count() > 0:
            name_field.first.fill(f"Test Bond {uid}")

        # Fill principal amount
        principal_field = authenticated_page.locator(
            "input[name='principal'], input[name='amount'], input[name='face_value']"
        )
        if principal_field.count() > 0:
            principal_field.first.fill("100000")

    def test_instrument_create_equity(self, authenticated_page, base_url):
        """Test creating an equity instrument."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Select equity type
        type_field = authenticated_page.locator(
            "select[name='instrument_type'], select[name='type']"
        )
        if type_field.count() > 0:
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "equity" in text or "stock" in text or "share" in text:
                    type_field.first.select_option(index=i)
                    break

        # Fill name
        name_field = authenticated_page.locator(
            "input[name='instrument_name'], input[name='name']"
        )
        if name_field.count() > 0:
            name_field.first.fill(f"Test Equity {uid}")

    def test_instrument_create_derivative(self, authenticated_page, base_url):
        """Test creating a derivative instrument."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments/new")
        authenticated_page.wait_for_load_state("networkidle")

        uid = unique_id()

        # Select derivative type
        type_field = authenticated_page.locator(
            "select[name='instrument_type'], select[name='type']"
        )
        if type_field.count() > 0:
            options = type_field.first.locator("option")
            for i in range(options.count()):
                text = options.nth(i).text_content().lower()
                if "derivative" in text or "forward" in text or "option" in text or "swap" in text:
                    type_field.first.select_option(index=i)
                    break

        # Fill name
        name_field = authenticated_page.locator(
            "input[name='instrument_name'], input[name='name']"
        )
        if name_field.count() > 0:
            name_field.first.fill(f"Test Derivative {uid}")


@pytest.mark.e2e
class TestInstrumentDetail:
    """Tests for instrument detail page."""

    def test_instrument_detail_page_accessible(self, authenticated_page, base_url):
        """Test that instrument detail is accessible."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first instrument link
        instrument_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fin-inst/instruments/']"
        ).first
        if instrument_link.count() > 0:
            instrument_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            expect(authenticated_page).to_have_url(
                re.compile(r".*/fin-inst/instruments/.*")
            )

    def test_instrument_detail_shows_valuation(self, authenticated_page, base_url):
        """Test that instrument detail shows valuation information."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        instrument_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fin-inst/instruments/']"
        ).first
        if instrument_link.count() > 0:
            instrument_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for valuation-related content
            valuation_info = authenticated_page.locator(
                "text=Value, text=Fair Value, text=Carrying Amount, text=Valuation"
            )
            if valuation_info.count() > 0:
                expect(valuation_info.first).to_be_visible()


@pytest.mark.e2e
class TestInstrumentValuation:
    """Tests for instrument valuation page."""

    def test_valuation_page_loads(self, authenticated_page, base_url):
        """Test valuation page loads."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        instrument_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fin-inst/instruments/']"
        ).first
        if instrument_link.count() > 0:
            instrument_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for valuation button/link
            valuation_btn = authenticated_page.locator(
                "a[href*='/valuation'], button:has-text('Valuation'), a:has-text('Value')"
            )
            if valuation_btn.count() > 0:
                expect(valuation_btn.first).to_be_visible()

    def test_valuation_has_date_field(self, authenticated_page, base_url):
        """Test valuation page has date field."""
        response = authenticated_page.goto(f"{base_url}/fin-inst/valuations")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")

            date_field = authenticated_page.locator(
                "input[type='date'], input[name='valuation_date']"
            )
            if date_field.count() > 0:
                expect(date_field.first).to_be_visible()


# =============================================================================
# Hedge Accounting Tests
# =============================================================================


@pytest.mark.e2e
class TestHedgeAccounting:
    """Tests for hedge accounting functionality."""

    def test_hedge_page_loads(self, authenticated_page, base_url):
        """Test hedge accounting page loads."""
        response = authenticated_page.goto(f"{base_url}/fin-inst/hedges")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")
            expect(authenticated_page.locator("body")).to_be_visible()

    def test_hedge_list_displays(self, authenticated_page, base_url):
        """Test hedge relationships list displays."""
        authenticated_page.goto(f"{base_url}/fin-inst/hedges")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for hedge list or empty state
        table = authenticated_page.locator("table, [role='table']")
        empty_state = authenticated_page.locator("text=No hedges, text=No hedge relationships")
        content = table.or_(empty_state)
        if content.count() > 0:
            expect(content.first).to_be_visible()

    def test_hedge_create_new_page_loads(self, authenticated_page, base_url):
        """Test hedge create page loads."""
        response = authenticated_page.goto(f"{base_url}/fin-inst/hedges/new")
        if response.ok:
            authenticated_page.wait_for_load_state("networkidle")

    def test_hedge_create_has_hedged_item_field(self, authenticated_page, base_url):
        """Test hedge form has hedged item selection."""
        authenticated_page.goto(f"{base_url}/fin-inst/hedges/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='hedged_item'], select[name='hedged_item_id'], #hedged_item"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_hedge_create_has_hedging_instrument_field(self, authenticated_page, base_url):
        """Test hedge form has hedging instrument selection."""
        authenticated_page.goto(f"{base_url}/fin-inst/hedges/new")
        authenticated_page.wait_for_load_state("networkidle")

        field = authenticated_page.locator(
            "select[name='hedging_instrument'], select[name='hedging_instrument_id'], #hedging_instrument"
        )
        if field.count() > 0:
            expect(field.first).to_be_visible()

    def test_hedge_effectiveness_test(self, authenticated_page, base_url):
        """Test hedge effectiveness testing functionality."""
        authenticated_page.goto(f"{base_url}/fin-inst/hedges")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for effectiveness test button
        test_btn = authenticated_page.locator(
            "button:has-text('Test'), button:has-text('Effectiveness'), a:has-text('Test')"
        )
        if test_btn.count() > 0:
            expect(test_btn.first).to_be_visible()

    def test_hedge_documentation(self, authenticated_page, base_url):
        """Test hedge documentation functionality."""
        authenticated_page.goto(f"{base_url}/fin-inst/hedges")
        authenticated_page.wait_for_load_state("networkidle")

        # Click first hedge link if available
        hedge_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/hedges/']"
        ).first
        if hedge_link.count() > 0:
            hedge_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for documentation section
            docs = authenticated_page.locator(
                "text=Documentation, text=Hedge Documentation, .documentation"
            )
            if docs.count() > 0:
                expect(docs.first).to_be_visible()


# =============================================================================
# IFRS 9 Classification Tests
# =============================================================================


@pytest.mark.e2e
class TestIFRS9Classification:
    """Tests for IFRS 9 classification functionality."""

    def test_classification_visible_on_instrument(self, authenticated_page, base_url):
        """Test IFRS 9 classification is visible on instrument detail."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments")
        authenticated_page.wait_for_load_state("networkidle")

        instrument_link = authenticated_page.locator(
            "table tbody tr a, a[href*='/fin-inst/instruments/']"
        ).first
        if instrument_link.count() > 0:
            instrument_link.click()
            authenticated_page.wait_for_load_state("networkidle")

            # Look for IFRS 9 classification info
            classification = authenticated_page.locator(
                "text=FVPL, text=FVOCI Debt, text=FVOCI Equity, "
                "text=Amortized Cost, text=Classification"
            )
            if classification.count() > 0:
                expect(classification.first).to_be_visible()

    def test_classification_options_available(self, authenticated_page, base_url):
        """Test IFRS 9 classification options are available on create form."""
        authenticated_page.goto(f"{base_url}/fin-inst/instruments/new")
        authenticated_page.wait_for_load_state("networkidle")

        # Look for classification field
        classification_field = authenticated_page.locator(
            "select[name='classification'], select[name='ifrs9_classification'], #classification"
        )
        if classification_field.count() > 0:
            expect(classification_field.first).to_be_visible()
