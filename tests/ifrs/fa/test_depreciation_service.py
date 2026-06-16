"""
Tests for DepreciationService.
"""

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.ifrs.fa.conftest import (
    MockDepreciationMethod,
    MockDepreciationRun,
    MockDepreciationSchedule,
    MockAssetStatus,
)


class TestDepreciationCalculations:
    """Tests for depreciation calculation methods."""

    def test_calculate_straight_line(self):
        """Test straight-line depreciation calculation."""
        from app.services.fixed_assets.depreciation import DepreciationService

        result = DepreciationService.calculate_straight_line(
            cost_basis=Decimal("12000"),
            residual_value=Decimal("0"),
            useful_life_months=60,
        )

        # Monthly depreciation: 12000 / 60 = 200
        assert result == Decimal("200.00")

    def test_calculate_straight_line_with_residual(self):
        """Test straight-line depreciation with residual value."""
        from app.services.fixed_assets.depreciation import DepreciationService

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
        from app.services.fixed_assets.depreciation import DepreciationService

        result = DepreciationService.calculate_straight_line(
            cost_basis=Decimal("12000"),
            residual_value=Decimal("0"),
            useful_life_months=0,
        )

        assert result == Decimal("0")

    def test_calculate_declining_balance(self):
        """Test declining balance depreciation calculation."""
        from app.services.fixed_assets.depreciation import DepreciationService

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
        from app.services.fixed_assets.depreciation import DepreciationService

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
        from app.services.fixed_assets.depreciation import DepreciationService

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
        from app.services.fixed_assets.depreciation import DepreciationService

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
        from app.services.fixed_assets.depreciation import DepreciationService

        result = DepreciationService.calculate_sum_of_years(
            cost_basis=Decimal("10000"),
            residual_value=Decimal("0"),
            useful_life_months=0,
            remaining_life_months=0,
        )

        assert result == Decimal("0")

    def test_periods_due_for_run_catches_up_from_start_date(self, mock_asset):
        """Depreciation should catch up from start date through the run period."""
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_asset.depreciation_start_date = date(2021, 4, 28)
        mock_asset.useful_life_months = 60
        mock_asset.remaining_life_months = 60

        result = DepreciationService.periods_due_for_run(mock_asset, date(2026, 4, 28))

        assert result == 60

    def test_periods_due_for_run_subtracts_already_recognized_periods(self, mock_asset):
        """Previously recognized months should not be recalculated."""
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_asset.depreciation_start_date = date(2021, 4, 28)
        mock_asset.useful_life_months = 60
        mock_asset.remaining_life_months = 58

        result = DepreciationService.periods_due_for_run(mock_asset, date(2026, 4, 28))

        assert result == 58


class TestDepreciationRunService:
    """Tests for depreciation run operations."""

    def test_create_depreciation_run_success(self, mock_db, org_id, user_id):
        """Test successful depreciation run creation."""
        from app.services.fixed_assets.depreciation import DepreciationService

        fiscal_period_id = uuid.uuid4()
        mock_db.scalar.return_value = 0

        DepreciationService.create_depreciation_run(
            mock_db,
            org_id,
            fiscal_period_id,
            user_id,
            description="January 2024 depreciation",
        )

        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()
        mock_db.refresh.assert_called_once()

    def test_get_depreciation_run(self, mock_db, mock_depreciation_run):
        """Test getting a depreciation run."""
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_db.get.return_value = mock_depreciation_run

        result = DepreciationService.get(mock_db, str(mock_depreciation_run.run_id))

        assert result is not None
        assert result.run_id == mock_depreciation_run.run_id

    def test_get_depreciation_run_not_found(self, mock_db):
        """Test getting non-existent depreciation run."""
        from fastapi import HTTPException

        from app.services.fixed_assets.depreciation import DepreciationService

        mock_db.get.return_value = None

        with pytest.raises(HTTPException) as exc_info:
            DepreciationService.get(mock_db, str(uuid.uuid4()))

        assert exc_info.value.status_code == 404

    def test_list_depreciation_runs(self, mock_db, org_id):
        """Test listing depreciation runs."""
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_runs = [MockDepreciationRun(organization_id=org_id) for _ in range(5)]
        mock_db.scalars.return_value.all.return_value = mock_runs

        result = DepreciationService.list(mock_db, str(org_id))

        assert len(result) == 5

    def test_list_depreciation_runs_with_filters(self, mock_db, org_id):
        """Test listing depreciation runs with status filter."""
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_runs = [MockDepreciationRun(organization_id=org_id, status="POSTED")]
        mock_db.scalars.return_value.all.return_value = mock_runs

        result = DepreciationService.list(mock_db, str(org_id), status="POSTED")

        assert len(result) == 1

    def test_get_run_schedules(self, mock_db, org_id, mock_depreciation_run):
        """Test getting schedules for a depreciation run."""
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_db.get.return_value = mock_depreciation_run

        mock_schedules = [
            MockDepreciationSchedule(run_id=mock_depreciation_run.run_id)
            for _ in range(5)
        ]
        mock_db.scalars.return_value.all.return_value = mock_schedules

        result = DepreciationService.get_run_schedules(
            mock_db, org_id, mock_depreciation_run.run_id
        )

        assert len(result) == 5

    def test_calculate_run_uses_catch_up_periods(
        self, mock_db, org_id, mock_asset, mock_category
    ):
        """Run calculation should use all due periods through the fiscal period."""
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.depreciation import (
            DepreciationCalculation,
            DepreciationService,
        )

        run_id = uuid.uuid4()
        fiscal_period_id = uuid.uuid4()
        mock_run = SimpleNamespace(
            run_id=run_id,
            organization_id=org_id,
            fiscal_period_id=fiscal_period_id,
            status=DepreciationRunStatus.DRAFT,
            calculation_started_at=None,
            calculation_completed_at=None,
            assets_processed=0,
            total_depreciation=Decimal("0"),
        )
        mock_period = SimpleNamespace(
            fiscal_period_id=fiscal_period_id,
            organization_id=org_id,
            end_date=date(2025, 12, 31),
        )
        mock_asset.organization_id = org_id
        mock_asset.status = MockAssetStatus.IN_USE
        mock_asset.depreciation_start_date = date(2021, 4, 28)
        mock_asset.useful_life_months = 60
        mock_asset.remaining_life_months = 60
        mock_asset.net_book_value = Decimal("12000")
        mock_asset.residual_value = Decimal("0")
        mock_asset.accumulated_depreciation = Decimal("0")

        mock_db.get.side_effect = [mock_run, mock_period]
        mock_db.scalars.return_value = [mock_asset]

        calc_result = DepreciationCalculation(
            asset_id=mock_asset.asset_id,
            asset_number=mock_asset.asset_number,
            depreciation_amount=Decimal("11400.00"),
            opening_nbv=Decimal("12000"),
            closing_nbv=Decimal("600.00"),
            opening_accum_dep=Decimal("0"),
            closing_accum_dep=Decimal("11400.00"),
            remaining_life_opening=60,
            remaining_life_closing=3,
            expense_account_id=mock_category.depreciation_expense_account_id,
            accum_dep_account_id=mock_category.accumulated_depreciation_account_id,
            cost_center_id=None,
        )

        with patch.object(
            DepreciationService,
            "calculate_asset_depreciation",
            return_value=calc_result,
        ) as calc_mock:
            result = DepreciationService.calculate_run(mock_db, org_id, run_id)

        calc_mock.assert_called_once_with(mock_db, mock_asset, periods=57)
        assert result.status == DepreciationRunStatus.CALCULATED
        assert result.assets_processed == 1
        assert result.total_depreciation == Decimal("11400.00")

    def test_create_automated_monthly_run_skips_when_no_due_period(
        self, mock_db, org_id
    ):
        """Automation should skip when no ended open period is due."""
        from app.services.fixed_assets.depreciation import DepreciationService

        with patch.object(
            DepreciationService,
            "get_next_automation_period",
            return_value=None,
        ):
            result = DepreciationService.create_automated_monthly_run(
                mock_db,
                org_id,
                auto_post=True,
            )

        assert result["status"] == "skipped"
        assert result["reason"] == "no_due_period"

    def test_create_automated_monthly_run_posts_when_enabled(self, mock_db, org_id):
        """Automation should post the run when auto-post is enabled."""
        from app.services.fixed_assets.depreciation import DepreciationService

        period_id = uuid.uuid4()
        run_id = uuid.uuid4()
        journal_entry_id = uuid.uuid4()
        fiscal_period = SimpleNamespace(
            fiscal_period_id=period_id,
            period_name="April 2026",
            end_date=date(2026, 4, 30),
        )
        created_run = SimpleNamespace(run_id=run_id, run_number=1)
        calculated_run = SimpleNamespace(
            run_id=run_id,
            run_number=1,
            assets_processed=3,
            total_depreciation=Decimal("39650.01"),
        )
        posted_run = SimpleNamespace(
            run_id=run_id,
            run_number=1,
            assets_processed=3,
            total_depreciation=Decimal("39650.01"),
            journal_entry_id=journal_entry_id,
        )

        with (
            patch.object(
                DepreciationService,
                "get_next_automation_period",
                return_value=fiscal_period,
            ),
            patch.object(
                DepreciationService,
                "create_depreciation_run",
                return_value=created_run,
            ),
            patch.object(
                DepreciationService,
                "calculate_run",
                return_value=calculated_run,
            ),
            patch.object(
                DepreciationService,
                "post_run",
                return_value=posted_run,
            ) as post_mock,
        ):
            result = DepreciationService.create_automated_monthly_run(
                mock_db,
                org_id,
                auto_post=True,
            )

        post_mock.assert_called_once()
        assert result["status"] == "posted"
        assert result["period_name"] == "April 2026"
        assert result["journal_entry_id"] == str(journal_entry_id)

    def test_create_automated_monthly_run_keeps_empty_run_calculated(
        self, mock_db, org_id
    ):
        """Auto-post should not fail when the calculated run has no schedules."""
        from app.services.fixed_assets.depreciation import DepreciationService

        period_id = uuid.uuid4()
        run_id = uuid.uuid4()
        fiscal_period = SimpleNamespace(
            fiscal_period_id=period_id,
            period_name="April 2026",
            end_date=date(2026, 4, 30),
        )
        created_run = SimpleNamespace(run_id=run_id, run_number=1)
        calculated_run = SimpleNamespace(
            run_id=run_id,
            run_number=1,
            assets_processed=0,
            total_depreciation=Decimal("0.00"),
        )

        with (
            patch.object(
                DepreciationService,
                "get_next_automation_period",
                return_value=fiscal_period,
            ),
            patch.object(
                DepreciationService,
                "create_depreciation_run",
                return_value=created_run,
            ),
            patch.object(
                DepreciationService,
                "calculate_run",
                return_value=calculated_run,
            ),
            patch.object(DepreciationService, "post_run") as post_mock,
        ):
            result = DepreciationService.create_automated_monthly_run(
                mock_db,
                org_id,
                auto_post=True,
            )

        post_mock.assert_not_called()
        assert result["status"] == "calculated"
        assert result["reason"] == "no_assets_to_post"

    def test_post_run_rejects_stale_calculated_schedule(self, mock_db, org_id, user_id):
        """Posting must fail if asset values changed after run calculation."""
        from fastapi import HTTPException

        from app.services.fixed_assets.depreciation import DepreciationService
        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus

        run_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        run = SimpleNamespace(
            run_id=run_id,
            organization_id=org_id,
            status=DepreciationRunStatus.CALCULATED,
            created_by_user_id=creator_id,
        )
        schedule = SimpleNamespace(
            asset_id=asset_id,
            accumulated_depreciation_opening=Decimal("100.00"),
            net_book_value_opening=Decimal("900.00"),
            remaining_life_months_opening=9,
        )
        asset = SimpleNamespace(
            asset_id=asset_id,
            organization_id=org_id,
            asset_number="FA-STALE",
            accumulated_depreciation=Decimal("200.00"),
            net_book_value=Decimal("800.00"),
            remaining_life_months=8,
        )

        mock_db.get.side_effect = [run, asset]
        mock_db.scalars.return_value = [schedule]

        with pytest.raises(HTTPException) as exc:
            DepreciationService.post_run(mock_db, org_id, run_id, user_id)

        assert exc.value.status_code == 400
        assert "recalculate the run before posting" in exc.value.detail
        mock_db.flush.assert_not_called()


class TestAssetDepreciationCalculation:
    """Tests for per-asset depreciation calculation."""

    def test_calculate_asset_depreciation_straight_line(
        self, mock_db, mock_asset, mock_category
    ):
        """Test calculating depreciation for a single asset."""
        from app.services.fixed_assets.depreciation import DepreciationService

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

    def test_calculate_asset_depreciation_fully_depreciated(
        self, mock_db, mock_asset, mock_category
    ):
        """Test that fully depreciated assets return zero depreciation."""
        from app.services.fixed_assets.depreciation import DepreciationService

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

    def test_calculate_asset_depreciation_with_residual(
        self, mock_db, mock_asset, mock_category
    ):
        """Test depreciation stops at residual value."""
        from app.services.fixed_assets.depreciation import DepreciationService

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

    def test_calculate_asset_depreciation_declining_balance(
        self, mock_db, mock_asset, mock_category
    ):
        """Test declining balance depreciation calculation for asset."""
        from app.services.fixed_assets.depreciation import DepreciationService

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

    def test_calculate_asset_depreciation_sum_of_years(
        self, mock_db, mock_asset, mock_category
    ):
        """Test sum of years depreciation calculation for asset."""
        from app.services.fixed_assets.depreciation import DepreciationService

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
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.STRAIGHT_LINE
        mock_asset.revalued_amount = None

        mock_db.get.return_value = None

        with pytest.raises(ValueError) as exc_info:
            DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        assert "Category not found" in str(exc_info.value)

    def test_declining_balance_final_period_trues_up_to_residual(
        self, mock_db, mock_asset, mock_category
    ):
        """F19: on the final period DB/DDB writes NBV down exactly to residual."""
        from app.services.fixed_assets.depreciation import DepreciationService

        mock_asset.depreciation_method = MockDepreciationMethod.DECLINING_BALANCE
        mock_asset.acquisition_cost = Decimal("10000")
        mock_asset.residual_value = Decimal("1000")
        mock_asset.useful_life_months = 60
        mock_asset.accumulated_depreciation = Decimal("8500")
        mock_asset.net_book_value = Decimal("1500")
        mock_asset.remaining_life_months = 1  # final period -> remaining_closing == 0
        mock_asset.revalued_amount = None

        mock_db.get.return_value = mock_category

        result = DepreciationService.calculate_asset_depreciation(mock_db, mock_asset)

        # True-up writes off the full remaining NBV above residual (1500 - 1000).
        assert result.depreciation_amount == Decimal("500")
        assert result.closing_nbv == Decimal("1000")
        assert result.remaining_life_closing == 0


class TestPostRunDisposedAssetGuard:
    """F16: post_run must not depreciate a disposed/retired asset."""

    def test_post_run_rejects_retired_asset(self, mock_db, org_id, user_id):
        """A run referencing a now-retired asset must be rejected, not posted."""
        from fastapi import HTTPException

        from app.models.fixed_assets.depreciation_run import DepreciationRunStatus
        from app.services.fixed_assets.depreciation import DepreciationService

        run_id = uuid.uuid4()
        creator_id = uuid.uuid4()
        asset_id = uuid.uuid4()
        run = SimpleNamespace(
            run_id=run_id,
            organization_id=org_id,
            status=DepreciationRunStatus.CALCULATED,
            created_by_user_id=creator_id,
        )
        schedule = SimpleNamespace(
            asset_id=asset_id,
            accumulated_depreciation_opening=Decimal("100.00"),
            net_book_value_opening=Decimal("900.00"),
            remaining_life_months_opening=9,
        )
        asset = SimpleNamespace(
            asset_id=asset_id,
            organization_id=org_id,
            asset_number="FA-RETIRED",
            status=MockAssetStatus.RETIRED,
            accumulated_depreciation=Decimal("100.00"),
            net_book_value=Decimal("900.00"),
            remaining_life_months=9,
        )

        mock_db.get.side_effect = [run, asset]
        mock_db.scalars.return_value = [schedule]

        with pytest.raises(HTTPException) as exc:
            DepreciationService.post_run(mock_db, org_id, run_id, user_id)

        assert exc.value.status_code == 400
        assert "disposed/retired" in exc.value.detail
        mock_db.flush.assert_not_called()
