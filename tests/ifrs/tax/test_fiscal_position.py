"""
Tests for FiscalPositionService.

Tests cover:
- Tax code remapping (map_taxes)
- GL account remapping (map_account)
- Partner matching score calculation (_match_score)
- Auto-detection logic (get_for_partner)
"""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from app.services.finance.tax.fiscal_position_service import FiscalPositionService

# ---------------------------------------------------------------------------
# Mock objects
# ---------------------------------------------------------------------------


class MockFiscalPosition:
    """Mock FiscalPosition with configurable fields."""

    def __init__(
        self,
        fiscal_position_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        name: str = "Test Position",
        auto_apply: bool = True,
        customer_type: str | None = None,
        supplier_type: str | None = None,
        country_code: str | None = None,
        state_code: str | None = None,
        priority: int = 10,
        is_active: bool = True,
        tax_maps: list | None = None,
        account_maps: list | None = None,
    ) -> None:
        self.fiscal_position_id = fiscal_position_id or uuid.uuid4()
        self.organization_id = organization_id or uuid.uuid4()
        self.name = name
        self.auto_apply = auto_apply
        self.customer_type = customer_type
        self.supplier_type = supplier_type
        self.country_code = country_code
        self.state_code = state_code
        self.priority = priority
        self.is_active = is_active
        self.tax_maps = tax_maps or []
        self.account_maps = account_maps or []


class MockTaxMap:
    """Mock FiscalPositionTaxMap."""

    def __init__(
        self,
        tax_source_id: uuid.UUID,
        tax_dest_id: uuid.UUID | None = None,
    ) -> None:
        self.tax_source_id = tax_source_id
        self.tax_dest_id = tax_dest_id


class MockAccountMap:
    """Mock FiscalPositionAccountMap."""

    def __init__(
        self,
        account_source_id: uuid.UUID,
        account_dest_id: uuid.UUID,
    ) -> None:
        self.account_source_id = account_source_id
        self.account_dest_id = account_dest_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def org_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def mock_db() -> MagicMock:
    return MagicMock()


@pytest.fixture
def service(mock_db: MagicMock) -> FiscalPositionService:
    return FiscalPositionService(mock_db)


# ---------------------------------------------------------------------------
# map_taxes tests
# ---------------------------------------------------------------------------


class TestMapTaxes:
    """Tests for tax code remapping."""

    def test_map_taxes_replaces_source_with_dest(
        self, service: FiscalPositionService
    ) -> None:
        """Source tax → destination tax replacement."""
        vat_7 = uuid.uuid4()
        vat_0 = uuid.uuid4()

        fp = MockFiscalPosition(
            tax_maps=[MockTaxMap(tax_source_id=vat_7, tax_dest_id=vat_0)]
        )

        result = service.map_taxes(fp, [vat_7])  # type: ignore[arg-type]
        assert result == [vat_0]

    def test_map_taxes_exempt_removes_tax(self, service: FiscalPositionService) -> None:
        """Source tax → None (exempt) removes the tax from the list."""
        vat_7 = uuid.uuid4()

        fp = MockFiscalPosition(
            tax_maps=[MockTaxMap(tax_source_id=vat_7, tax_dest_id=None)]
        )

        result = service.map_taxes(fp, [vat_7])  # type: ignore[arg-type]
        assert result == []

    def test_map_taxes_unmapped_passes_through(
        self, service: FiscalPositionService
    ) -> None:
        """Tax codes without a mapping pass through unchanged."""
        vat_7 = uuid.uuid4()
        stamp = uuid.uuid4()

        fp = MockFiscalPosition(
            tax_maps=[MockTaxMap(tax_source_id=vat_7, tax_dest_id=None)]
        )

        result = service.map_taxes(fp, [vat_7, stamp])  # type: ignore[arg-type]
        assert result == [stamp]

    def test_map_taxes_multiple_mappings(self, service: FiscalPositionService) -> None:
        """Multiple tax codes are each remapped independently."""
        vat_7 = uuid.uuid4()
        vat_0 = uuid.uuid4()
        wht_5 = uuid.uuid4()
        wht_10 = uuid.uuid4()

        fp = MockFiscalPosition(
            tax_maps=[
                MockTaxMap(tax_source_id=vat_7, tax_dest_id=vat_0),
                MockTaxMap(tax_source_id=wht_5, tax_dest_id=wht_10),
            ]
        )

        result = service.map_taxes(fp, [vat_7, wht_5])  # type: ignore[arg-type]
        assert result == [vat_0, wht_10]

    def test_map_taxes_empty_list(self, service: FiscalPositionService) -> None:
        """Empty input returns empty output."""
        fp = MockFiscalPosition(tax_maps=[MockTaxMap(tax_source_id=uuid.uuid4())])
        result = service.map_taxes(fp, [])  # type: ignore[arg-type]
        assert result == []

    def test_map_taxes_no_mappings(self, service: FiscalPositionService) -> None:
        """Fiscal position with no tax maps passes all taxes through."""
        vat_7 = uuid.uuid4()
        fp = MockFiscalPosition(tax_maps=[])

        result = service.map_taxes(fp, [vat_7])  # type: ignore[arg-type]
        assert result == [vat_7]


# ---------------------------------------------------------------------------
# map_account tests
# ---------------------------------------------------------------------------


class TestMapAccount:
    """Tests for GL account remapping."""

    def test_map_account_replaces_matched(self, service: FiscalPositionService) -> None:
        """Source account is replaced with destination."""
        domestic_rev = uuid.uuid4()
        export_rev = uuid.uuid4()

        fp = MockFiscalPosition(
            account_maps=[
                MockAccountMap(
                    account_source_id=domestic_rev,
                    account_dest_id=export_rev,
                )
            ]
        )

        result = service.map_account(fp, domestic_rev)  # type: ignore[arg-type]
        assert result == export_rev

    def test_map_account_unmapped_passes_through(
        self, service: FiscalPositionService
    ) -> None:
        """Unmapped accounts return unchanged."""
        some_account = uuid.uuid4()
        fp = MockFiscalPosition(account_maps=[])

        result = service.map_account(fp, some_account)  # type: ignore[arg-type]
        assert result == some_account

    def test_map_account_only_matches_exact_source(
        self, service: FiscalPositionService
    ) -> None:
        """Account mapping only applies to exact source match."""
        domestic_rev = uuid.uuid4()
        export_rev = uuid.uuid4()
        other = uuid.uuid4()

        fp = MockFiscalPosition(
            account_maps=[
                MockAccountMap(
                    account_source_id=domestic_rev,
                    account_dest_id=export_rev,
                )
            ]
        )

        result = service.map_account(fp, other)  # type: ignore[arg-type]
        assert result == other  # unchanged


# ---------------------------------------------------------------------------
# _match_score tests
# ---------------------------------------------------------------------------


class TestMatchScore:
    """Tests for partner matching score calculation."""

    def test_exact_customer_type_match(self) -> None:
        """Matching customer_type adds 1 to score."""
        fp = MockFiscalPosition(customer_type="GOVERNMENT")
        score = FiscalPositionService._match_score(
            fp,
            "customer",
            "GOVERNMENT",
            None,
            None,  # type: ignore[arg-type]
        )
        assert score == 1

    def test_customer_type_mismatch_returns_zero(self) -> None:
        """Mismatched customer_type returns 0 (no match)."""
        fp = MockFiscalPosition(customer_type="GOVERNMENT")
        score = FiscalPositionService._match_score(
            fp,
            "customer",
            "INDIVIDUAL",
            None,
            None,  # type: ignore[arg-type]
        )
        assert score == 0

    def test_supplier_type_match(self) -> None:
        """Matching supplier_type adds 1 to score."""
        fp = MockFiscalPosition(supplier_type="GOVERNMENT")
        score = FiscalPositionService._match_score(
            fp,
            "supplier",
            "GOVERNMENT",
            None,
            None,  # type: ignore[arg-type]
        )
        assert score == 1

    def test_country_code_match(self) -> None:
        """Matching country_code adds 1 to score."""
        fp = MockFiscalPosition(country_code="NGA")
        score = FiscalPositionService._match_score(
            fp,
            "customer",
            None,
            "NGA",
            None,  # type: ignore[arg-type]
        )
        assert score == 1

    def test_country_code_mismatch_returns_zero(self) -> None:
        """Mismatched country_code returns 0."""
        fp = MockFiscalPosition(country_code="NGA")
        score = FiscalPositionService._match_score(
            fp,
            "customer",
            None,
            "USA",
            None,  # type: ignore[arg-type]
        )
        assert score == 0

    def test_multi_criteria_stacks_score(self) -> None:
        """Multiple matching criteria stack (higher score = more specific)."""
        fp = MockFiscalPosition(
            customer_type="GOVERNMENT", country_code="NGA", state_code="LA"
        )
        score = FiscalPositionService._match_score(
            fp,
            "customer",
            "GOVERNMENT",
            "NGA",
            "LA",  # type: ignore[arg-type]
        )
        assert score == 3

    def test_no_criteria_returns_zero(self) -> None:
        """Position with no criteria scores 0 (won't auto-match)."""
        fp = MockFiscalPosition()
        score = FiscalPositionService._match_score(
            fp,
            "customer",
            "INDIVIDUAL",
            None,
            None,  # type: ignore[arg-type]
        )
        assert score == 0

    def test_partial_mismatch_returns_zero(self) -> None:
        """If any set criterion doesn't match, score is 0."""
        fp = MockFiscalPosition(customer_type="GOVERNMENT", country_code="NGA")
        score = FiscalPositionService._match_score(
            fp,
            "customer",
            "GOVERNMENT",
            "USA",
            None,  # type: ignore[arg-type]
        )
        assert score == 0  # country_code mismatch voids the match


# ---------------------------------------------------------------------------
# get_for_partner tests
# ---------------------------------------------------------------------------


class TestGetForPartner:
    """Tests for auto-detection of fiscal position."""

    def test_returns_best_matching_position(
        self, service: FiscalPositionService, org_id: uuid.UUID
    ) -> None:
        """Highest-scoring position is selected."""
        generic_fp = MockFiscalPosition(
            organization_id=org_id,
            name="Generic Govt",
            customer_type="GOVERNMENT",
            priority=10,
        )
        specific_fp = MockFiscalPosition(
            organization_id=org_id,
            name="Govt + NGA",
            customer_type="GOVERNMENT",
            country_code="NGA",
            priority=10,
        )

        # Mock DB to return both candidates
        service.db.scalars.return_value.all.return_value = [generic_fp, specific_fp]

        result = service.get_for_partner(
            organization_id=org_id,
            partner_type="customer",
            partner_classification="GOVERNMENT",
            country_code="NGA",
        )

        assert result is specific_fp  # score=2 beats score=1

    def test_returns_none_when_no_match(
        self, service: FiscalPositionService, org_id: uuid.UUID
    ) -> None:
        """Returns None if no candidates match."""
        fp = MockFiscalPosition(
            organization_id=org_id,
            customer_type="GOVERNMENT",
        )
        service.db.scalars.return_value.all.return_value = [fp]

        result = service.get_for_partner(
            organization_id=org_id,
            partner_type="customer",
            partner_classification="INDIVIDUAL",
        )

        assert result is None

    def test_returns_none_when_no_candidates(
        self, service: FiscalPositionService, org_id: uuid.UUID
    ) -> None:
        """Returns None if DB has no fiscal positions."""
        service.db.scalars.return_value.all.return_value = []

        result = service.get_for_partner(
            organization_id=org_id,
            partner_type="customer",
            partner_classification="GOVERNMENT",
        )

        assert result is None

    def test_priority_tiebreaker(
        self, service: FiscalPositionService, org_id: uuid.UUID
    ) -> None:
        """When scores are equal, lower priority (first in DB order) wins."""
        fp_high = MockFiscalPosition(
            organization_id=org_id,
            name="High Priority",
            customer_type="GOVERNMENT",
            priority=1,
        )
        fp_low = MockFiscalPosition(
            organization_id=org_id,
            name="Low Priority",
            customer_type="GOVERNMENT",
            priority=20,
        )

        # DB returns in priority order (ORDER BY priority ASC)
        service.db.scalars.return_value.all.return_value = [fp_high, fp_low]

        result = service.get_for_partner(
            organization_id=org_id,
            partner_type="customer",
            partner_classification="GOVERNMENT",
        )

        # Both score=1, but fp_high is first in iteration (lower priority value)
        assert result is fp_high
