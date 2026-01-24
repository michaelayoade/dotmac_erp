"""
Tests for DepreciationService.
"""

import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from tests.ifrs.fa.conftest import (
    MockAsset,
    MockAssetCategory,
    MockAssetStatus,
    MockDepreciationMethod,
    MockDepreciationRun,
    MockDepreciationSchedule,
)


class TestDepreciationCalculations:
    """Tests for depreciation calculation methods."""

    def test_calculate_straight_line(self):
        """Test straight-line depreciation calculation."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_straight_line(
            cost_basis=Decimal("12000"),
            residual_value=Decimal("0"),
            useful_life_months=60,
        )

        # Monthly depreciation: 12000 / 60 = 200
        assert result == Decimal("200.00")

    def test_calculate_straight_line_with_residual(self):
        """Test straight-line depreciation with residual value."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_straight_line(
            cost_basis=Decimal("12000"),
            residual_value=Decimal("2000"),
            useful_life_months=60,
        )

        # Monthly depreciation: (12000 - 2000) / 60 = 166.67
        expected = Decimal("166.67")
        assert result == expected

    def test_calculate_straight_line_zero_life(self):
        """Test straight-line with zero useful life returns zero."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_straight_line(
            cost_basis=Decimal("12000"),
            residual_value=Decimal("0"),
            useful_life_months=0,
        )

        assert result == Decimal("0")

    def test_calculate_declining_balance(self):
        """Test declining balance depreciation calculation."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_declining_balance(
            net_book_value=Decimal("10000"),
            residual_value=Decimal("0"),
            useful_life_months=60,
            remaining_life_months=60,
            rate_multiplier=Decimal("1.0"),
        )

        # Result should be positive
        assert result > Decimal("0")

    def test_calculate_declining_balance_respects_residual(self):
        """Test declining balance respects residual value."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_declining_balance(
            net_book_value=Decimal("500"),
            residual_value=Decimal("400"),
            useful_life_months=60,
            remaining_life_months=12,
            rate_multiplier=Decimal("1.0"),
        )

        # Cannot depreciate below residual (500 - 400 = 100 max)
        assert result <= Decimal("100")

    def test_calculate_declining_balance_zero_life(self):
        """Test declining balance with zero life returns zero."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_declining_balance(
            net_book_value=Decimal("10000"),
            residual_value=Decimal("0"),
            useful_life_months=0,
            remaining_life_months=0,
            rate_multiplier=Decimal("1.0"),
        )

        assert result == Decimal("0")

    def test_calculate_sum_of_years(self):
        """Test sum of years digits depreciation."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_sum_of_years(
            cost_basis=Decimal("10000"),
            residual_value=Decimal("0"),
            useful_life_months=60,  # 5 years
            remaining_life_months=60,
        )

        # Sum of years: 5+4+3+2+1 = 15
        # First year fraction: 5/15 = 1/3
        # Annual depreciation: 10000 * 5/15 = 3333.33
        # Monthly: 3333.33 / 12 = 277.78
        assert result > Decimal("0")

    def test_calculate_sum_of_years_zero_life(self):
        """Test sum of years with zero life returns zero."""
        from app.services.finance.fa.depreciation import DepreciationService

        result = DepreciationService.calculate_sum_of_years(
            cost_basis=Decimal("10000"),
            residual_value=Decimal("0"),
            useful_life_months=0,
            remaining_life_months=0,
        )

        assert result == Decimal("0")


class TestDepreciationRunService:
    """Tests for depreciation run operations."""

    def test_create_depreciation_run_success(self, mock_db, org_id, user_id):
        """Test successful depreciation run creation."""
        from app.services.finance.fa.depreciation import DepreciationService

        fiscal_period_id = uuid.uuid4()
        mock_db.query.return_value.filter.return_value.scalar.return_value = 0

        result = DepreciationService.create_depreciation_run(
            mock_db,
            org_id,
            fiscal_period_id,
            user_id,
            description="January 2024 depreciation",
        )

        mock_db.add.assert_called_once()
        mock_db.commit.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_get_depreciation_run(self, mock_db, mock_depreciation_run):
        """Test getting a depreciation run."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_db.get.return_value = mock_depreciation_run

        result = DepreciationService.get(mock_db, str(mock_depreciation_run.run_id))

        assert result is not None
        assert result.run_id == mock_depreciation_run.run_id

    def test_get_depreciation_run_not_found(self, mock_db):
        """Test getting non-existent depreciation run."""
        from app.services.finance.fa.depreciation import DepreciationService
        from fastapi import HTTPException

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            DepreciationService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_depreciation_runs(self, mock_db, org_id):
        """Test listing depreciation runs."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_runs = [MockDepreciationRun(organization_id=org_id) for _ in range(5)]
        mock_db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_runs

        result = DepreciationService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_depreciation_runs_with_filters(self, mock_db, org_id):
        """Test listing depreciation runs with status filter."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_runs = [MockDepreciationRun(organization_id=org_id, status="POSTED")]
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.limit.return_value.offset.return_value.all.return_value = mock_runs

        result = DepreciationService.list(mock_db, str(org_id), status="POSTED")

        assert len(result) == 1

    def test_get_run_schedules(self, mock_db, org_id, mock_depreciation_run):
        """Test getting schedules for a depreciation run."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_db.get.return_value = mock_depreciation_run

        mock_schedules = [
            MockDepreciationSchedule(run_id=mock_depreciation_run.run_id)
            for _ in range(5)
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = mock_schedules

        result = DepreciationService.get_run_schedules(mock_db, org_id, mock_depreciation_run.run_id)

        assert len(result) == 5


class TestAssetDepreciationCalculation:
    """Tests for per-asset depreciation calculation."""

    def test_calculate_asset_depreciation_straight_line(self, mock_db, mock_asset, mock_category):
        """Test calculating depreciation for a single asset."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.STRAIGHT_LINE
        mock_asset.acquisition_cost = Decimal("12000")
        mock_asset.residual_value = Decimal("0")
        mock_asset.useful_life_months = 60
        mock_asset.accumulated_depreciation = Decimal("0")
        mock_asset.net_book_value = Decimal("12000")
        mock_asset.remaining_life_months = 60
        mock_asset.depreciation_start_date = date.today()
        mock_asset.revalued_amount = None

        mock_db.get.return_value = mock_category

        result = DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        # Monthly: 12000 / 60 = 200
        assert result.depreciation_amount == Decimal("200.00")
        assert result.asset_id == mock_asset.asset_id

    def test_calculate_asset_depreciation_fully_depreciated(self, mock_db, mock_asset, mock_category):
        """Test that fully depreciated assets return zero depreciation."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.STRAIGHT_LINE
        mock_asset.acquisition_cost = Decimal("12000")
        mock_asset.residual_value = Decimal("0")
        mock_asset.useful_life_months = 60
        mock_asset.accumulated_depreciation = Decimal("12000")  # Fully depreciated
        mock_asset.net_book_value = Decimal("0")
        mock_asset.remaining_life_months = 0
        mock_asset.revalued_amount = None

        mock_db.get.return_value = mock_category

        result = DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        assert result.depreciation_amount == Decimal("0")

    def test_calculate_asset_depreciation_with_residual(self, mock_db, mock_asset, mock_category):
        """Test depreciation stops at residual value."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.STRAIGHT_LINE
        mock_asset.acquisition_cost = Decimal("12000")
        mock_asset.residual_value = Decimal("2000")
        mock_asset.useful_life_months = 60
        mock_asset.accumulated_depreciation = Decimal("9800")
        mock_asset.net_book_value = Decimal("2200")  # Close to residual
        mock_asset.remaining_life_months = 1
        mock_asset.revalued_amount = None

        mock_db.get.return_value = mock_category

        result = DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        # Should only depreciate up to residual value (200 remaining)
        assert result.depreciation_amount <= Decimal("200.00")

    def test_calculate_asset_depreciation_declining_balance(self, mock_db, mock_asset, mock_category):
        """Test declining balance depreciation calculation for asset."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.DECLINING_BALANCE
        mock_asset.acquisition_cost = Decimal("10000")
        mock_asset.residual_value = Decimal("0")
        mock_asset.useful_life_months = 60
        mock_asset.accumulated_depreciation = Decimal("0")
        mock_asset.net_book_value = Decimal("10000")
        mock_asset.remaining_life_months = 60
        mock_asset.revalued_amount = None

        mock_db.get.return_value = mock_category

        result = DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        # Result depends on declining balance rate
        assert result.depreciation_amount > Decimal("0")

    def test_calculate_asset_depreciation_sum_of_years(self, mock_db, mock_asset, mock_category):
        """Test sum of years depreciation calculation for asset."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.SUM_OF_YEARS
        mock_asset.acquisition_cost = Decimal("10000")
        mock_asset.residual_value = Decimal("0")
        mock_asset.useful_life_months = 60
        mock_asset.accumulated_depreciation = Decimal("0")
        mock_asset.net_book_value = Decimal("10000")
        mock_asset.remaining_life_months = 60
        mock_asset.revalued_amount = None

        mock_db.get.return_value = mock_category

        result = DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        assert result.depreciation_amount > Decimal("0")

    def test_calculate_asset_depreciation_category_not_found(self, mock_db, mock_asset):
        """Test depreciation calculation fails when category not found."""
        from app.services.finance.fa.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.STRAIGHT_LINE
        mock_asset.revalued_amount = None

        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        assert "Category not found" in str(exc_info.value)
