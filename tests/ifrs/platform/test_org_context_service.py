"""
Tests for OrgContextService.

Covers all 5 methods plus edge cases for 90%+ coverage.
"""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.services.ifrs.platform.org_context import (
    OrgContextService,
    org_context_service,
)


# ============ Mock Model ============


class MockOrganization:
    """Mock Organization model for testing."""

    def __init__(
        self,
        organization_id=None,
        functional_currency_code: str = "USD",
        presentation_currency_code: str = "USD",
        fiscal_year_end_month: int = 12,
        fiscal_year_end_day: int = 31,
    ):
        self.organization_id = organization_id or uuid4()
        self.functional_currency_code = functional_currency_code
        self.presentation_currency_code = presentation_currency_code
        self.fiscal_year_end_month = fiscal_year_end_month
        self.fiscal_year_end_day = fiscal_year_end_day


# ============ Fixtures ============


@pytest.fixture
def mock_db():
    """Create mock database session."""
    return MagicMock()


@pytest.fixture
def org_id():
    """Create test organization ID."""
    return uuid4()


@pytest.fixture
def mock_org():
    """Create mock organization with default values."""
    return MockOrganization()


@pytest.fixture
def mock_org_eur():
    """Create mock organization with EUR currencies."""
    return MockOrganization(
        functional_currency_code="EUR",
        presentation_currency_code="GBP",
    )


@pytest.fixture
def mock_org_fiscal_june():
    """Create mock organization with June 30 fiscal year end."""
    return MockOrganization(
        fiscal_year_end_month=6,
        fiscal_year_end_day=30,
    )


# ============ TestGetFunctionalCurrency ============


class TestGetFunctionalCurrency:
    """Tests for get_functional_currency method."""

    def test_returns_currency_code_when_found(self, mock_db, org_id, mock_org):
        """Test successful retrieval of functional currency."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_functional_currency(mock_db, org_id)

        assert result == "USD"
        mock_db.get.assert_called_once()

    def test_returns_eur_when_org_has_eur(self, mock_db, org_id, mock_org_eur):
        """Test retrieval of EUR functional currency."""
        mock_db.get.return_value = mock_org_eur

        result = OrgContextService.get_functional_currency(mock_db, org_id)

        assert result == "EUR"

    def test_raises_valueerror_when_not_found(self, mock_db, org_id):
        """Test ValueError raised when organization not found."""
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc:
            OrgContextService.get_functional_currency(mock_db, org_id)

        assert str(org_id) in str(exc.value)
        assert "not found" in str(exc.value)

    def test_accepts_string_organization_id(self, mock_db, mock_org):
        """Test that string organization_id is coerced to UUID."""
        mock_db.get.return_value = mock_org
        org_id_str = str(uuid4())

        result = OrgContextService.get_functional_currency(mock_db, org_id_str)

        assert result == "USD"
        mock_db.get.assert_called_once()

    def test_accepts_uuid_directly(self, mock_db, org_id, mock_org):
        """Test that UUID is passed directly."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_functional_currency(mock_db, org_id)

        assert result == "USD"


# ============ TestGetPresentationCurrency ============


class TestGetPresentationCurrency:
    """Tests for get_presentation_currency method."""

    def test_returns_currency_code_when_found(self, mock_db, org_id, mock_org):
        """Test successful retrieval of presentation currency."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_presentation_currency(mock_db, org_id)

        assert result == "USD"
        mock_db.get.assert_called_once()

    def test_returns_gbp_when_org_has_gbp(self, mock_db, org_id, mock_org_eur):
        """Test retrieval of GBP presentation currency."""
        mock_db.get.return_value = mock_org_eur

        result = OrgContextService.get_presentation_currency(mock_db, org_id)

        assert result == "GBP"

    def test_raises_valueerror_when_not_found(self, mock_db, org_id):
        """Test ValueError raised when organization not found."""
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc:
            OrgContextService.get_presentation_currency(mock_db, org_id)

        assert str(org_id) in str(exc.value)
        assert "not found" in str(exc.value)

    def test_accepts_string_organization_id(self, mock_db, mock_org):
        """Test that string organization_id is coerced to UUID."""
        mock_db.get.return_value = mock_org
        org_id_str = str(uuid4())

        result = OrgContextService.get_presentation_currency(mock_db, org_id_str)

        assert result == "USD"


# ============ TestGetCurrencySettings ============


class TestGetCurrencySettings:
    """Tests for get_currency_settings method."""

    def test_returns_dict_with_both_currencies(self, mock_db, org_id, mock_org):
        """Test successful retrieval of currency settings."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_currency_settings(mock_db, org_id)

        assert result == {"functional": "USD", "presentation": "USD"}

    def test_returns_different_currencies(self, mock_db, org_id, mock_org_eur):
        """Test retrieval when functional != presentation."""
        mock_db.get.return_value = mock_org_eur

        result = OrgContextService.get_currency_settings(mock_db, org_id)

        assert result == {"functional": "EUR", "presentation": "GBP"}

    def test_dict_has_exactly_two_keys(self, mock_db, org_id, mock_org):
        """Test that result dict has exactly 'functional' and 'presentation' keys."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_currency_settings(mock_db, org_id)

        assert set(result.keys()) == {"functional", "presentation"}
        assert len(result) == 2

    def test_values_are_strings(self, mock_db, org_id, mock_org):
        """Test that currency values are strings."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_currency_settings(mock_db, org_id)

        assert isinstance(result["functional"], str)
        assert isinstance(result["presentation"], str)

    def test_raises_valueerror_when_not_found(self, mock_db, org_id):
        """Test ValueError raised when organization not found."""
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc:
            OrgContextService.get_currency_settings(mock_db, org_id)

        assert "not found" in str(exc.value)

    def test_accepts_string_organization_id(self, mock_db, mock_org):
        """Test that string organization_id is coerced to UUID."""
        mock_db.get.return_value = mock_org
        org_id_str = str(uuid4())

        result = OrgContextService.get_currency_settings(mock_db, org_id_str)

        assert "functional" in result
        assert "presentation" in result


# ============ TestGetFiscalYearEnd ============


class TestGetFiscalYearEnd:
    """Tests for get_fiscal_year_end method."""

    def test_returns_tuple_dec31(self, mock_db, org_id, mock_org):
        """Test December 31 fiscal year end."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert result == (12, 31)

    def test_returns_tuple_june30(self, mock_db, org_id, mock_org_fiscal_june):
        """Test June 30 fiscal year end."""
        mock_db.get.return_value = mock_org_fiscal_june

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert result == (6, 30)

    def test_returns_tuple_march31(self, mock_db, org_id):
        """Test March 31 fiscal year end (common in UK/India)."""
        mock_org = MockOrganization(
            fiscal_year_end_month=3,
            fiscal_year_end_day=31,
        )
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert result == (3, 31)

    def test_returns_tuple_structure(self, mock_db, org_id, mock_org):
        """Test that result is a tuple of length 2."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_tuple_values_are_integers(self, mock_db, org_id, mock_org):
        """Test that month and day are integers."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert isinstance(result[0], int)
        assert isinstance(result[1], int)

    def test_raises_valueerror_when_not_found(self, mock_db, org_id):
        """Test ValueError raised when organization not found."""
        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc:
            OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert "not found" in str(exc.value)

    def test_accepts_string_organization_id(self, mock_db, mock_org):
        """Test that string organization_id is coerced to UUID."""
        mock_db.get.return_value = mock_org
        org_id_str = str(uuid4())

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id_str)

        assert result == (12, 31)


# ============ TestGetOrganization ============


class TestGetOrganization:
    """Tests for get_organization method."""

    def test_returns_organization_when_found(self, mock_db, org_id, mock_org):
        """Test successful retrieval of organization."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_organization(mock_db, org_id)

        assert result == mock_org
        assert result is not None
        mock_db.get.assert_called_once()

    def test_returns_none_when_not_found(self, mock_db, org_id):
        """Test None returned when organization not found (no error!)."""
        mock_db.get.return_value = None

        result = OrgContextService.get_organization(mock_db, org_id)

        assert result is None
        # Key difference: does NOT raise ValueError like other methods

    def test_accepts_string_organization_id(self, mock_db, mock_org):
        """Test that string organization_id is coerced to UUID."""
        mock_db.get.return_value = mock_org
        org_id_str = str(uuid4())

        result = OrgContextService.get_organization(mock_db, org_id_str)

        assert result == mock_org

    def test_accepts_uuid_directly(self, mock_db, org_id, mock_org):
        """Test that UUID is passed directly."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_organization(mock_db, org_id)

        assert result == mock_org


# ============ TestDbGetPattern ============


class TestDbGetPattern:
    """Tests to verify correct db.get() usage pattern."""

    def test_functional_currency_uses_db_get(self, mock_db, org_id, mock_org):
        """Test get_functional_currency uses db.get() not db.query()."""
        mock_db.get.return_value = mock_org

        OrgContextService.get_functional_currency(mock_db, org_id)

        mock_db.get.assert_called_once()
        mock_db.query.assert_not_called()

    def test_presentation_currency_uses_db_get(self, mock_db, org_id, mock_org):
        """Test get_presentation_currency uses db.get() not db.query()."""
        mock_db.get.return_value = mock_org

        OrgContextService.get_presentation_currency(mock_db, org_id)

        mock_db.get.assert_called_once()
        mock_db.query.assert_not_called()

    def test_currency_settings_uses_db_get(self, mock_db, org_id, mock_org):
        """Test get_currency_settings uses db.get() not db.query()."""
        mock_db.get.return_value = mock_org

        OrgContextService.get_currency_settings(mock_db, org_id)

        mock_db.get.assert_called_once()
        mock_db.query.assert_not_called()

    def test_fiscal_year_end_uses_db_get(self, mock_db, org_id, mock_org):
        """Test get_fiscal_year_end uses db.get() not db.query()."""
        mock_db.get.return_value = mock_org

        OrgContextService.get_fiscal_year_end(mock_db, org_id)

        mock_db.get.assert_called_once()
        mock_db.query.assert_not_called()

    def test_get_organization_uses_db_get(self, mock_db, org_id, mock_org):
        """Test get_organization uses db.get() not db.query()."""
        mock_db.get.return_value = mock_org

        OrgContextService.get_organization(mock_db, org_id)

        mock_db.get.assert_called_once()
        mock_db.query.assert_not_called()


# ============ TestModuleSingleton ============


class TestModuleSingleton:
    """Tests for module-level singleton instance."""

    def test_singleton_exists(self):
        """Test that org_context_service singleton exists."""
        assert org_context_service is not None

    def test_singleton_is_instance_of_service(self):
        """Test that singleton is an OrgContextService instance."""
        assert isinstance(org_context_service, OrgContextService)

    def test_singleton_has_all_methods(self):
        """Test that singleton has all expected methods."""
        assert hasattr(org_context_service, "get_functional_currency")
        assert hasattr(org_context_service, "get_presentation_currency")
        assert hasattr(org_context_service, "get_currency_settings")
        assert hasattr(org_context_service, "get_fiscal_year_end")
        assert hasattr(org_context_service, "get_organization")


# ============ TestEdgeCases ============


class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_currency_code_three_characters(self, mock_db, org_id, mock_org):
        """Test that currency codes are 3 characters (ISO 4217)."""
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_functional_currency(mock_db, org_id)

        assert len(result) == 3

    def test_various_currency_codes(self, mock_db, org_id):
        """Test various ISO 4217 currency codes."""
        currencies = ["USD", "EUR", "GBP", "JPY", "NGN", "CAD", "AUD"]

        for currency in currencies:
            mock_org = MockOrganization(functional_currency_code=currency)
            mock_db.get.return_value = mock_org

            result = OrgContextService.get_functional_currency(mock_db, org_id)

            assert result == currency

    def test_fiscal_year_end_january(self, mock_db, org_id):
        """Test January fiscal year end."""
        mock_org = MockOrganization(
            fiscal_year_end_month=1,
            fiscal_year_end_day=31,
        )
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert result == (1, 31)

    def test_fiscal_year_end_february(self, mock_db, org_id):
        """Test February fiscal year end (leap year consideration)."""
        mock_org = MockOrganization(
            fiscal_year_end_month=2,
            fiscal_year_end_day=28,
        )
        mock_db.get.return_value = mock_org

        result = OrgContextService.get_fiscal_year_end(mock_db, org_id)

        assert result == (2, 28)
