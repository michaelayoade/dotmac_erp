"""
Tests for DeferredTaxService.

Tests IAS 12 deferred tax calculations, basis tracking, and movements.
"""

import pytest
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.models.finance.tax.deferred_tax_basis import DifferenceType
from app.services.finance.tax.deferred_tax import (
    DeferredTaxService,
    DeferredTaxBasisInput,
    DeferredTaxCalculationResult,
    DeferredTaxMovementResult,
    DeferredTaxSummary,
)


class MockDeferredTaxBasis:
    """Mock DeferredTaxBasis model."""

    def __init__(
        self,
        basis_id=None,
        organization_id=None,
        jurisdiction_id=None,
        basis_code="DTB-001",
        basis_name="Test Basis",
        description=None,
        difference_type=DifferenceType.TEMPORARY_TAXABLE,
        source_type="FIXED_ASSET",
        source_id=None,
        gl_account_id=None,
        accounting_base=Decimal("1000.00"),
        tax_base=Decimal("800.00"),
        temporary_difference=Decimal("200.00"),
        applicable_tax_rate=Decimal("0.25"),
        deferred_tax_amount=Decimal("50.00"),
        is_asset=False,
        is_recognized=True,
        recognition_probability=Decimal("1.0"),
        unrecognized_amount=Decimal("0"),
        expected_reversal_year=None,
        is_current_year_reversal=False,
        is_active=True,
    ):
        self.basis_id = basis_id or uuid4()
        self.organization_id = organization_id or uuid4()
        self.jurisdiction_id = jurisdiction_id or uuid4()
        self.basis_code = basis_code
        self.basis_name = basis_name
        self.description = description
        self.difference_type = difference_type
        self.source_type = source_type
        self.source_id = source_id
        self.gl_account_id = gl_account_id
        self.accounting_base = accounting_base
        self.tax_base = tax_base
        self.temporary_difference = temporary_difference
        self.applicable_tax_rate = applicable_tax_rate
        self.deferred_tax_amount = deferred_tax_amount
        self.is_asset = is_asset
        self.is_recognized = is_recognized
        self.recognition_probability = recognition_probability
        self.unrecognized_amount = unrecognized_amount
        self.expected_reversal_year = expected_reversal_year
        self.is_current_year_reversal = is_current_year_reversal
        self.is_active = is_active


class MockDeferredTaxMovement:
    """Mock DeferredTaxMovement model."""

    def __init__(
        self,
        movement_id=None,
        basis_id=None,
        fiscal_period_id=None,
        accounting_base_opening=Decimal("1000.00"),
        tax_base_opening=Decimal("800.00"),
        temporary_difference_opening=Decimal("200.00"),
        deferred_tax_opening=Decimal("50.00"),
        accounting_base_movement=Decimal("100.00"),
        tax_base_movement=Decimal("80.00"),
        temporary_difference_movement=Decimal("20.00"),
        tax_rate_opening=Decimal("0.25"),
        tax_rate_closing=Decimal("0.25"),
        tax_rate_change_impact=Decimal("0"),
        deferred_tax_movement_pl=Decimal("5.00"),
        deferred_tax_movement_oci=Decimal("0"),
        deferred_tax_movement_equity=Decimal("0"),
        accounting_base_closing=Decimal("1100.00"),
        tax_base_closing=Decimal("880.00"),
        temporary_difference_closing=Decimal("220.00"),
        deferred_tax_closing=Decimal("55.00"),
        recognition_change=Decimal("0"),
        unrecognized_closing=Decimal("0"),
        movement_description=None,
        movement_category="OPERATING",
    ):
        self.movement_id = movement_id or uuid4()
        self.basis_id = basis_id or uuid4()
        self.fiscal_period_id = fiscal_period_id or uuid4()
        self.accounting_base_opening = accounting_base_opening
        self.tax_base_opening = tax_base_opening
        self.temporary_difference_opening = temporary_difference_opening
        self.deferred_tax_opening = deferred_tax_opening
        self.accounting_base_movement = accounting_base_movement
        self.tax_base_movement = tax_base_movement
        self.temporary_difference_movement = temporary_difference_movement
        self.tax_rate_opening = tax_rate_opening
        self.tax_rate_closing = tax_rate_closing
        self.tax_rate_change_impact = tax_rate_change_impact
        self.deferred_tax_movement_pl = deferred_tax_movement_pl
        self.deferred_tax_movement_oci = deferred_tax_movement_oci
        self.deferred_tax_movement_equity = deferred_tax_movement_equity
        self.accounting_base_closing = accounting_base_closing
        self.tax_base_closing = tax_base_closing
        self.temporary_difference_closing = temporary_difference_closing
        self.deferred_tax_closing = deferred_tax_closing
        self.recognition_change = recognition_change
        self.unrecognized_closing = unrecognized_closing
        self.movement_description = movement_description
        self.movement_category = movement_category


@pytest.fixture
def org_id():
    return uuid4()


@pytest.fixture
def mock_db():
    return MagicMock()


@pytest.fixture
def jurisdiction_id():
    return uuid4()


@pytest.fixture
def sample_basis_input(jurisdiction_id):
    return DeferredTaxBasisInput(
        basis_code="DTB-001",
        basis_name="Accelerated Depreciation",
        jurisdiction_id=jurisdiction_id,
        difference_type=DifferenceType.TEMPORARY_TAXABLE,
        source_type="FIXED_ASSET",
        applicable_tax_rate=Decimal("0.25"),
        accounting_base=Decimal("1000.00"),
        tax_base=Decimal("800.00"),
    )


class TestCalculateTemporaryDifference:
    """Tests for calculate_temporary_difference method."""

    def test_positive_difference_dtl(self):
        """Test positive difference (DTL - taxable)."""
        result = DeferredTaxService.calculate_temporary_difference(
            accounting_base=Decimal("1000.00"),
            tax_base=Decimal("800.00"),
        )

        assert result == Decimal("200.00")

    def test_negative_difference_dta(self):
        """Test negative difference (DTA - deductible)."""
        result = DeferredTaxService.calculate_temporary_difference(
            accounting_base=Decimal("800.00"),
            tax_base=Decimal("1000.00"),
        )

        assert result == Decimal("-200.00")

    def test_zero_difference(self):
        """Test zero difference."""
        result = DeferredTaxService.calculate_temporary_difference(
            accounting_base=Decimal("1000.00"),
            tax_base=Decimal("1000.00"),
        )

        assert result == Decimal("0")


class TestCalculateDeferredTax:
    """Tests for calculate_deferred_tax method."""

    def test_calculate_dtl(self):
        """Test calculating DTL (taxable difference)."""
        deferred_tax, is_asset = DeferredTaxService.calculate_deferred_tax(
            temporary_difference=Decimal("200.00"),
            tax_rate=Decimal("0.25"),
        )

        assert deferred_tax == Decimal("50.00")
        assert is_asset is False

    def test_calculate_dta(self):
        """Test calculating DTA (deductible difference)."""
        deferred_tax, is_asset = DeferredTaxService.calculate_deferred_tax(
            temporary_difference=Decimal("-200.00"),
            tax_rate=Decimal("0.25"),
        )

        assert deferred_tax == Decimal("50.00")
        assert is_asset is True

    def test_calculate_with_rounding(self):
        """Test calculation with rounding."""
        deferred_tax, is_asset = DeferredTaxService.calculate_deferred_tax(
            temporary_difference=Decimal("333.33"),
            tax_rate=Decimal("0.21"),
        )

        # 333.33 * 0.21 = 70.0 (rounded to 2 decimal places)
        assert deferred_tax == Decimal("70.00")

    def test_calculate_zero_difference(self):
        """Test calculation with zero difference."""
        deferred_tax, is_asset = DeferredTaxService.calculate_deferred_tax(
            temporary_difference=Decimal("0"),
            tax_rate=Decimal("0.25"),
        )

        assert deferred_tax == Decimal("0.00")


class TestCreateBasis:
    """Tests for create_basis method."""

    @patch("app.services.ifrs.tax.deferred_tax.DeferredTaxBasis")
    def test_create_basis_success(
        self, mock_basis_class, mock_db, org_id, sample_basis_input
    ):
        """Test successful basis creation."""
        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_basis_class.return_value = MockDeferredTaxBasis(organization_id=org_id)

        result = DeferredTaxService.create_basis(mock_db, org_id, sample_basis_input)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_basis_duplicate_fails(self, mock_db, org_id, sample_basis_input):
        """Test that duplicate basis code fails."""
        existing = MockDeferredTaxBasis(
            organization_id=org_id,
            basis_code=sample_basis_input.basis_code,
        )
        mock_db.query.return_value.filter.return_value.first.return_value = existing

        with pytest.raises(HTTPException) as exc:
            DeferredTaxService.create_basis(mock_db, org_id, sample_basis_input)

        assert exc.value.status_code == 400
        assert "already exists" in exc.value.detail

    @patch("app.services.ifrs.tax.deferred_tax.DeferredTaxBasis")
    def test_create_basis_with_partial_recognition(
        self, mock_basis_class, mock_db, org_id, jurisdiction_id
    ):
        """Test basis creation with partial recognition probability."""
        input_data = DeferredTaxBasisInput(
            basis_code="DTB-002",
            basis_name="Tax Loss Carryforward",
            jurisdiction_id=jurisdiction_id,
            difference_type=DifferenceType.TEMPORARY_DEDUCTIBLE,
            source_type="TAX_LOSS",
            applicable_tax_rate=Decimal("0.25"),
            accounting_base=Decimal("0"),
            tax_base=Decimal("1000.00"),
            is_recognized=True,
            recognition_probability=Decimal("0.50"),  # 50% probability
        )

        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_basis_class.return_value = MockDeferredTaxBasis(organization_id=org_id)

        result = DeferredTaxService.create_basis(mock_db, org_id, input_data)

        # Verify basis was created
        mock_db.add.assert_called_once()

    @patch("app.services.ifrs.tax.deferred_tax.DeferredTaxBasis")
    def test_create_basis_not_recognized(
        self, mock_basis_class, mock_db, org_id, jurisdiction_id
    ):
        """Test basis creation with no recognition."""
        input_data = DeferredTaxBasisInput(
            basis_code="DTB-003",
            basis_name="Unrecognized DTA",
            jurisdiction_id=jurisdiction_id,
            difference_type=DifferenceType.TEMPORARY_DEDUCTIBLE,
            source_type="TAX_LOSS",
            applicable_tax_rate=Decimal("0.25"),
            accounting_base=Decimal("0"),
            tax_base=Decimal("1000.00"),
            is_recognized=False,
        )

        mock_db.query.return_value.filter.return_value.first.return_value = None
        mock_basis_class.return_value = MockDeferredTaxBasis(organization_id=org_id)

        result = DeferredTaxService.create_basis(mock_db, org_id, input_data)

        mock_db.add.assert_called_once()


class TestUpdateBasis:
    """Tests for update_basis method."""

    def test_update_basis_success(self, mock_db, org_id):
        """Test successful basis update."""
        basis = MockDeferredTaxBasis(
            organization_id=org_id,
            accounting_base=Decimal("1000.00"),
            tax_base=Decimal("800.00"),
            applicable_tax_rate=Decimal("0.25"),
            is_recognized=True,
            recognition_probability=None,
        )
        mock_db.get.return_value = basis

        result = DeferredTaxService.update_basis(
            mock_db,
            org_id,
            basis.basis_id,
            accounting_base=Decimal("1200.00"),
            tax_base=Decimal("900.00"),
        )

        assert isinstance(result, DeferredTaxCalculationResult)
        assert result.temporary_difference == Decimal("300.00")
        mock_db.commit.assert_called_once()

    def test_update_basis_not_found(self, mock_db, org_id):
        """Test updating non-existent basis."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            DeferredTaxService.update_basis(
                mock_db, org_id, uuid4(),
                Decimal("1000.00"), Decimal("800.00"),
            )

        assert exc.value.status_code == 404

    def test_update_basis_wrong_org(self, mock_db, org_id):
        """Test updating basis from different organization."""
        basis = MockDeferredTaxBasis(organization_id=uuid4())  # Different org
        mock_db.get.return_value = basis

        with pytest.raises(HTTPException) as exc:
            DeferredTaxService.update_basis(
                mock_db, org_id, basis.basis_id,
                Decimal("1000.00"), Decimal("800.00"),
            )

        assert exc.value.status_code == 404

    def test_update_basis_with_new_rate(self, mock_db, org_id):
        """Test updating basis with new tax rate."""
        basis = MockDeferredTaxBasis(
            organization_id=org_id,
            applicable_tax_rate=Decimal("0.25"),
            is_recognized=True,
        )
        mock_db.get.return_value = basis

        result = DeferredTaxService.update_basis(
            mock_db,
            org_id,
            basis.basis_id,
            accounting_base=Decimal("1000.00"),
            tax_base=Decimal("800.00"),
            tax_rate=Decimal("0.30"),  # New rate
        )

        assert basis.applicable_tax_rate == Decimal("0.30")


class TestCreateMovement:
    """Tests for create_movement method."""

    @patch("app.services.ifrs.tax.deferred_tax.DeferredTaxMovement")
    def test_create_movement_success(self, mock_movement_class, mock_db, org_id):
        """Test successful movement creation."""
        basis = MockDeferredTaxBasis(
            organization_id=org_id,
            accounting_base=Decimal("1000.00"),
            tax_base=Decimal("800.00"),
            temporary_difference=Decimal("200.00"),
            deferred_tax_amount=Decimal("50.00"),
            applicable_tax_rate=Decimal("0.25"),
            is_recognized=True,
            recognition_probability=None,
            unrecognized_amount=Decimal("0"),
        )
        mock_db.get.return_value = basis

        mock_movement = MockDeferredTaxMovement()
        mock_movement_class.return_value = mock_movement

        fiscal_period_id = uuid4()

        result = DeferredTaxService.create_movement(
            mock_db,
            org_id,
            basis.basis_id,
            fiscal_period_id,
            accounting_base_closing=Decimal("1100.00"),
            tax_base_closing=Decimal("880.00"),
            tax_rate_closing=Decimal("0.25"),
            movement_category="OPERATING",
        )

        assert isinstance(result, DeferredTaxMovementResult)
        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()

    def test_create_movement_basis_not_found(self, mock_db, org_id):
        """Test creating movement for non-existent basis."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            DeferredTaxService.create_movement(
                mock_db, org_id, uuid4(), uuid4(),
                Decimal("1000.00"), Decimal("800.00"), Decimal("0.25"),
                "OPERATING",
            )

        assert exc.value.status_code == 404

    @patch("app.services.ifrs.tax.deferred_tax.DeferredTaxMovement")
    def test_create_movement_with_rate_change(self, mock_movement_class, mock_db, org_id):
        """Test movement with tax rate change."""
        basis = MockDeferredTaxBasis(
            organization_id=org_id,
            accounting_base=Decimal("1000.00"),
            tax_base=Decimal("800.00"),
            temporary_difference=Decimal("200.00"),
            deferred_tax_amount=Decimal("50.00"),
            applicable_tax_rate=Decimal("0.25"),
            is_recognized=True,
            unrecognized_amount=Decimal("0"),
        )
        mock_db.get.return_value = basis

        mock_movement = MockDeferredTaxMovement()
        mock_movement_class.return_value = mock_movement

        result = DeferredTaxService.create_movement(
            mock_db,
            org_id,
            basis.basis_id,
            uuid4(),
            accounting_base_closing=Decimal("1000.00"),
            tax_base_closing=Decimal("800.00"),
            tax_rate_closing=Decimal("0.30"),  # Rate changed from 0.25 to 0.30
            movement_category="RATE_CHANGE",
        )

        assert isinstance(result, DeferredTaxMovementResult)

    @patch("app.services.ifrs.tax.deferred_tax.DeferredTaxMovement")
    def test_create_movement_with_oci(self, mock_movement_class, mock_db, org_id):
        """Test movement with OCI component."""
        basis = MockDeferredTaxBasis(
            organization_id=org_id,
            is_recognized=True,
            unrecognized_amount=Decimal("0"),
        )
        mock_db.get.return_value = basis

        mock_movement = MockDeferredTaxMovement()
        mock_movement_class.return_value = mock_movement

        result = DeferredTaxService.create_movement(
            mock_db,
            org_id,
            basis.basis_id,
            uuid4(),
            accounting_base_closing=Decimal("1100.00"),
            tax_base_closing=Decimal("880.00"),
            tax_rate_closing=Decimal("0.25"),
            movement_category="REVALUATION",
            deferred_tax_movement_oci=Decimal("10.00"),
        )

        assert result.deferred_tax_movement_oci == Decimal("10.00")


class TestGetSummary:
    """Tests for get_summary method."""

    def test_get_summary_success(self, mock_db, org_id):
        """Test getting deferred tax summary."""
        bases = [
            MockDeferredTaxBasis(
                organization_id=org_id,
                is_asset=True,
                deferred_tax_amount=Decimal("100.00"),
                unrecognized_amount=Decimal("20.00"),
            ),
            MockDeferredTaxBasis(
                organization_id=org_id,
                is_asset=False,
                deferred_tax_amount=Decimal("50.00"),
                unrecognized_amount=Decimal("0"),
            ),
        ]

        mock_db.query.return_value.filter.return_value.all.return_value = bases

        result = DeferredTaxService.get_summary(mock_db, org_id)

        assert isinstance(result, DeferredTaxSummary)
        assert result.total_dta == Decimal("100.00")
        assert result.total_dtl == Decimal("50.00")
        assert result.net_position == Decimal("50.00")
        assert result.unrecognized_dta == Decimal("20.00")
        assert result.items_count == 2

    def test_get_summary_empty(self, mock_db, org_id):
        """Test summary with no bases."""
        mock_db.query.return_value.filter.return_value.all.return_value = []

        result = DeferredTaxService.get_summary(mock_db, org_id)

        assert result.total_dta == Decimal("0")
        assert result.total_dtl == Decimal("0")
        assert result.items_count == 0

    def test_get_summary_with_jurisdiction_filter(self, mock_db, org_id, jurisdiction_id):
        """Test summary filtered by jurisdiction."""
        bases = [MockDeferredTaxBasis(jurisdiction_id=jurisdiction_id)]

        mock_db.query.return_value.filter.return_value.filter.return_value.all.return_value = bases

        result = DeferredTaxService.get_summary(
            mock_db, org_id, jurisdiction_id=jurisdiction_id
        )

        assert result.items_count == 1


class TestGetMovementSummary:
    """Tests for get_movement_summary method."""

    def test_get_movement_summary_success(self, mock_db, org_id):
        """Test getting movement summary for a period."""
        mock_result = MagicMock()
        mock_result.pl_total = Decimal("100.00")
        mock_result.oci_total = Decimal("20.00")
        mock_result.equity_total = Decimal("0")
        mock_result.rate_change = Decimal("5.00")
        mock_result.recognition = Decimal("0")

        mock_db.query.return_value.join.return_value.filter.return_value.first.return_value = mock_result

        fiscal_period_id = uuid4()
        result = DeferredTaxService.get_movement_summary(
            mock_db, org_id, fiscal_period_id
        )

        assert result["deferred_tax_expense_pl"] == Decimal("100.00")
        assert result["deferred_tax_oci"] == Decimal("20.00")
        assert result["tax_rate_change_impact"] == Decimal("5.00")

    def test_get_movement_summary_no_movements(self, mock_db, org_id):
        """Test movement summary with no movements."""
        mock_result = MagicMock()
        mock_result.pl_total = None
        mock_result.oci_total = None
        mock_result.equity_total = None
        mock_result.rate_change = None
        mock_result.recognition = None

        mock_db.query.return_value.join.return_value.filter.return_value.first.return_value = mock_result

        result = DeferredTaxService.get_movement_summary(mock_db, org_id, uuid4())

        assert result["deferred_tax_expense_pl"] == Decimal("0")
        assert result["deferred_tax_oci"] == Decimal("0")


class TestGet:
    """Tests for get method."""

    def test_get_basis_success(self, mock_db):
        """Test getting a basis by ID."""
        basis = MockDeferredTaxBasis()
        mock_db.get.return_value = basis

        result = DeferredTaxService.get(mock_db, str(basis.basis_id))

        assert result == basis

    def test_get_basis_not_found(self, mock_db):
        """Test getting non-existent basis."""
        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc:
            DeferredTaxService.get(mock_db, str(uuid4()))

        assert exc.value.status_code == 404


class TestList:
    """Tests for list method."""

    def test_list_all_bases(self, mock_db):
        """Test listing all bases."""
        bases = [MockDeferredTaxBasis(), MockDeferredTaxBasis()]

        mock_db.query.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = bases

        result = DeferredTaxService.list(mock_db)

        assert len(result) == 2

    def test_list_with_org_filter(self, mock_db, org_id):
        """Test listing bases filtered by organization."""
        bases = [MockDeferredTaxBasis(organization_id=org_id)]

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = bases

        result = DeferredTaxService.list(mock_db, organization_id=str(org_id))

        assert len(result) == 1

    def test_list_with_difference_type_filter(self, mock_db, org_id):
        """Test listing bases filtered by difference type."""
        bases = [MockDeferredTaxBasis(difference_type=DifferenceType.TEMPORARY_TAXABLE)]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.limit.return_value.offset.return_value.all.return_value = bases
        mock_db.query.return_value = mock_query

        result = DeferredTaxService.list(
            mock_db,
            organization_id=str(org_id),
            difference_type=DifferenceType.TEMPORARY_TAXABLE,
        )

        assert len(result) == 1

    def test_list_with_is_asset_filter(self, mock_db, org_id):
        """Test listing bases filtered by is_asset."""
        bases = [MockDeferredTaxBasis(is_asset=True)]

        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.order_by.return_value.limit.return_value.offset.return_value.all.return_value = bases
        mock_db.query.return_value = mock_query

        result = DeferredTaxService.list(
            mock_db,
            organization_id=str(org_id),
            is_asset=True,
        )

        assert len(result) == 1

    def test_list_pagination(self, mock_db):
        """Test list pagination."""
        bases = [MockDeferredTaxBasis()]

        mock_query = MagicMock()
        mock_query.order_by.return_value.limit.return_value.offset.return_value.all.return_value = bases
        mock_db.query.return_value = mock_query

        result = DeferredTaxService.list(mock_db, limit=10, offset=20)

        mock_query.order_by.return_value.limit.assert_called_with(10)
        mock_query.order_by.return_value.limit.return_value.offset.assert_called_with(20)


class TestListMovements:
    """Tests for list_movements method."""

    def test_list_movements_success(self, mock_db):
        """Test listing movements for a basis."""
        movements = [MockDeferredTaxMovement(), MockDeferredTaxMovement()]

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = movements

        basis_id = uuid4()
        result = DeferredTaxService.list_movements(mock_db, str(basis_id))

        assert len(result) == 2

    def test_list_movements_pagination(self, mock_db):
        """Test movements list pagination."""
        movements = [MockDeferredTaxMovement()]

        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = movements

        result = DeferredTaxService.list_movements(
            mock_db, str(uuid4()), limit=10, offset=5
        )

        assert len(result) == 1


class TestDataclasses:
    """Tests for dataclass structures."""

    def test_deferred_tax_basis_input(self, jurisdiction_id):
        """Test DeferredTaxBasisInput dataclass."""
        input_data = DeferredTaxBasisInput(
            basis_code="DTB-001",
            basis_name="Test",
            jurisdiction_id=jurisdiction_id,
            difference_type=DifferenceType.TEMPORARY_TAXABLE,
            source_type="FIXED_ASSET",
            applicable_tax_rate=Decimal("0.25"),
        )

        assert input_data.basis_code == "DTB-001"
        assert input_data.accounting_base == Decimal("0")
        assert input_data.is_recognized is True

    def test_deferred_tax_calculation_result(self):
        """Test DeferredTaxCalculationResult dataclass."""
        result = DeferredTaxCalculationResult(
            temporary_difference=Decimal("200.00"),
            deferred_tax_amount=Decimal("50.00"),
            is_asset=False,
            recognized_amount=Decimal("50.00"),
            unrecognized_amount=Decimal("0"),
        )

        assert result.temporary_difference == Decimal("200.00")
        assert result.is_asset is False

    def test_deferred_tax_summary(self):
        """Test DeferredTaxSummary dataclass."""
        summary = DeferredTaxSummary(
            total_dta=Decimal("100.00"),
            total_dtl=Decimal("50.00"),
            net_position=Decimal("50.00"),
            unrecognized_dta=Decimal("10.00"),
            items_count=5,
        )

        assert summary.net_position == Decimal("50.00")
        assert summary.items_count == 5
