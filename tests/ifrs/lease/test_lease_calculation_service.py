"""
Tests for LeaseCalculationService.
"""

import uuid
from datetime import date
from decimal import Decimal

import pytest


class TestLeaseCalculationService:
    """Tests for LeaseCalculationService."""

    def test_calculate_pv_zero_payments(self):
        """Test PV calculation with zero payments."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.calculate_pv(
            payment=Decimal("0"),
            rate=Decimal("0.05"),
            periods=60,
        )

        assert result == Decimal("0")

    def test_calculate_pv_zero_periods(self):
        """Test PV calculation with zero periods."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.calculate_pv(
            payment=Decimal("1000"),
            rate=Decimal("0.05"),
            periods=0,
        )

        assert result == Decimal("0")

    def test_calculate_pv_zero_rate(self):
        """Test PV calculation with zero interest rate."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.calculate_pv(
            payment=Decimal("1000"),
            rate=Decimal("0"),
            periods=12,
        )

        # With 0% rate, PV = payment * periods
        assert result == Decimal("12000")

    def test_calculate_pv_ordinary_annuity(self):
        """Test PV calculation for ordinary annuity (arrears)."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.calculate_pv(
            payment=Decimal("1000"),
            rate=Decimal("0.01"),  # 1% per period
            periods=12,
            payment_timing="ARREARS",
        )

        # PV of ordinary annuity = P * [(1-(1+r)^-n) / r]
        # Should be approximately 11,255.08
        assert result > Decimal("11000")
        assert result < Decimal("12000")

    def test_calculate_pv_annuity_due(self):
        """Test PV calculation for annuity due (advance)."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result_advance = LeaseCalculationService.calculate_pv(
            payment=Decimal("1000"),
            rate=Decimal("0.01"),
            periods=12,
            payment_timing="ADVANCE",
        )

        result_arrears = LeaseCalculationService.calculate_pv(
            payment=Decimal("1000"),
            rate=Decimal("0.01"),
            periods=12,
            payment_timing="ARREARS",
        )

        # Annuity due > Ordinary annuity (by factor of 1+r)
        assert result_advance > result_arrears

    def test_calculate_pv_single_zero_periods(self):
        """Test single PV with zero periods returns amount."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.calculate_pv_single(
            amount=Decimal("10000"),
            rate=Decimal("0.05"),
            periods=0,
        )

        assert result == Decimal("10000")

    def test_calculate_pv_single_zero_rate(self):
        """Test single PV with zero rate returns amount."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.calculate_pv_single(
            amount=Decimal("10000"),
            rate=Decimal("0"),
            periods=5,
        )

        assert result == Decimal("10000")

    def test_calculate_pv_single(self):
        """Test single PV calculation."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.calculate_pv_single(
            amount=Decimal("10000"),
            rate=Decimal("0.10"),
            periods=1,
        )

        # PV = FV / (1+r)^n = 10000 / 1.1 = 9090.91
        assert result == Decimal("9090.91")

    def test_get_periods_per_year_monthly(self):
        """Test periods per year for monthly frequency."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.get_periods_per_year("MONTHLY")
        assert result == 12

    def test_get_periods_per_year_quarterly(self):
        """Test periods per year for quarterly frequency."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.get_periods_per_year("QUARTERLY")
        assert result == 4

    def test_get_periods_per_year_semi_annual(self):
        """Test periods per year for semi-annual frequency."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.get_periods_per_year("SEMI_ANNUAL")
        assert result == 2

    def test_get_periods_per_year_annual(self):
        """Test periods per year for annual frequency."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.get_periods_per_year("ANNUAL")
        assert result == 1

    def test_get_periods_per_year_unknown_defaults_monthly(self):
        """Test unknown frequency defaults to monthly."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        result = LeaseCalculationService.get_periods_per_year("UNKNOWN")
        assert result == 12

    def test_generate_amortization_schedule_not_found(self, mock_db):
        """Test amortization schedule generation for non-existent lease."""
        from fastapi import HTTPException

        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseCalculationService.generate_amortization_schedule(
                mock_db,
                uuid.uuid4(),
            )

        assert exc_info.value.status_code == 404

    def test_generate_amortization_schedule_no_liability(self, mock_db, mock_contract):
        """Test amortization schedule with no liability fails."""
        from fastapi import HTTPException

        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_db.get.return_value = mock_contract
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseCalculationService.generate_amortization_schedule(
                mock_db,
                mock_contract.lease_id,
            )

        assert exc_info.value.status_code == 404
        assert "liability" in exc_info.value.detail.lower()

    def test_calculate_interest_accrual_not_found(self, mock_db):
        """Test interest accrual calculation for non-existent lease."""
        from fastapi import HTTPException

        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseCalculationService.calculate_interest_accrual(
                mock_db,
                uuid.uuid4(),
                accrual_date=date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_calculate_interest_accrual_no_liability(self, mock_db, mock_contract):
        """Test interest accrual with no liability fails."""
        from fastapi import HTTPException

        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_db.get.return_value = mock_contract
        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseCalculationService.calculate_interest_accrual(
                mock_db,
                mock_contract.lease_id,
                accrual_date=date.today(),
            )

        assert exc_info.value.status_code == 404

    def test_calculate_interest_accrual_success(
        self, mock_db, mock_contract, mock_liability
    ):
        """Test successful interest accrual calculation."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_contract.discount_rate_used = Decimal("0.06")  # 6% annual
        mock_liability.current_liability_balance = Decimal("120000.00")
        mock_db.get.return_value = mock_contract
        mock_db.scalars.return_value.first.return_value = mock_liability

        result = LeaseCalculationService.calculate_interest_accrual(
            mock_db,
            mock_contract.lease_id,
            accrual_date=date(2024, 1, 31),
        )

        # Monthly rate = 6% / 12 = 0.5%
        # Interest = 120,000 * 0.005 = 600
        assert result.interest_amount == Decimal("600.00")
        assert result.opening_balance == Decimal("120000.00")
        assert result.closing_balance == Decimal("120600.00")

    def test_calculate_rou_depreciation_not_found(self, mock_db):
        """Test ROU depreciation for non-existent lease asset."""
        from fastapi import HTTPException

        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_db.scalars.return_value.first.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            LeaseCalculationService.calculate_rou_depreciation(
                mock_db,
                uuid.uuid4(),
            )

        assert exc_info.value.status_code == 404

    def test_calculate_rou_depreciation_zero_life(self, mock_db, mock_asset):
        """Test ROU depreciation with zero useful life returns zero."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_asset.useful_life_months = 0
        mock_db.scalars.return_value.first.return_value = mock_asset

        result = LeaseCalculationService.calculate_rou_depreciation(
            mock_db,
            mock_asset.lease_id,
        )

        assert result == Decimal("0")

    def test_calculate_rou_depreciation_success(self, mock_db, mock_asset):
        """Test successful ROU depreciation calculation."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_asset.initial_rou_asset_value = Decimal("120000.00")
        mock_asset.residual_value = Decimal("0")
        mock_asset.useful_life_months = 60
        mock_asset.carrying_amount = Decimal("120000.00")
        mock_db.scalars.return_value.first.return_value = mock_asset

        result = LeaseCalculationService.calculate_rou_depreciation(
            mock_db,
            mock_asset.lease_id,
            periods=1,
        )

        # Monthly depreciation = 120,000 / 60 = 2,000
        assert result == Decimal("2000.00")

    def test_calculate_rou_depreciation_multiple_periods(self, mock_db, mock_asset):
        """Test ROU depreciation for multiple periods."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_asset.initial_rou_asset_value = Decimal("120000.00")
        mock_asset.residual_value = Decimal("0")
        mock_asset.useful_life_months = 60
        mock_asset.carrying_amount = Decimal("120000.00")
        mock_db.scalars.return_value.first.return_value = mock_asset

        result = LeaseCalculationService.calculate_rou_depreciation(
            mock_db,
            mock_asset.lease_id,
            periods=3,  # 3 months
        )

        # 3 months depreciation = 3 * (120,000 / 60) = 6,000
        assert result == Decimal("6000.00")

    def test_calculate_rou_depreciation_with_residual(self, mock_db, mock_asset):
        """Test ROU depreciation with residual value."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_asset.initial_rou_asset_value = Decimal("120000.00")
        mock_asset.residual_value = Decimal("20000.00")
        mock_asset.useful_life_months = 60
        mock_asset.carrying_amount = Decimal("120000.00")
        mock_db.scalars.return_value.first.return_value = mock_asset

        result = LeaseCalculationService.calculate_rou_depreciation(
            mock_db,
            mock_asset.lease_id,
            periods=1,
        )

        # Depreciable = 120,000 - 20,000 = 100,000
        # Monthly = 100,000 / 60 = 1,666.67
        assert result == Decimal("1666.67")

    def test_calculate_rou_depreciation_capped_at_carrying(self, mock_db, mock_asset):
        """Test depreciation doesn't exceed carrying amount minus residual."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_asset.initial_rou_asset_value = Decimal("120000.00")
        mock_asset.residual_value = Decimal("0")
        mock_asset.useful_life_months = 60
        mock_asset.carrying_amount = Decimal("1000.00")  # Almost fully depreciated
        mock_db.scalars.return_value.first.return_value = mock_asset

        result = LeaseCalculationService.calculate_rou_depreciation(
            mock_db,
            mock_asset.lease_id,
            periods=12,  # Try to depreciate 12 months
        )

        # Should be capped at remaining carrying amount (1,000)
        assert result == Decimal("1000.00")

    def test_calculate_rou_depreciation_fully_depreciated(self, mock_db, mock_asset):
        """Test depreciation returns zero when fully depreciated."""
        from app.services.finance.lease.lease_calculation import LeaseCalculationService

        mock_asset.initial_rou_asset_value = Decimal("120000.00")
        mock_asset.residual_value = Decimal("10000.00")
        mock_asset.useful_life_months = 60
        mock_asset.carrying_amount = Decimal("10000.00")  # At residual value
        mock_db.scalars.return_value.first.return_value = mock_asset

        result = LeaseCalculationService.calculate_rou_depreciation(
            mock_db,
            mock_asset.lease_id,
        )

        # Already at residual, no more depreciation
        assert result == Decimal("0")
