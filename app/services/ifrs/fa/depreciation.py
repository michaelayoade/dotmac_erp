"""
DepreciationService - Fixed Asset depreciation calculations.

Manages depreciation runs, calculations, and posting to GL.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.ifrs.fa.asset import Asset, AssetStatus
from app.models.ifrs.fa.asset_category import AssetCategory, DepreciationMethod
from app.models.ifrs.fa.depreciation_run import DepreciationRun, DepreciationRunStatus
from app.models.ifrs.fa.depreciation_schedule import DepreciationSchedule
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class DepreciationCalculation:
    """Result of a single asset depreciation calculation."""

    asset_id: UUID
    asset_number: str
    depreciation_amount: Decimal
    opening_nbv: Decimal
    closing_nbv: Decimal
    opening_accum_dep: Decimal
    closing_accum_dep: Decimal
    remaining_life_opening: int
    remaining_life_closing: int
    expense_account_id: UUID
    accum_dep_account_id: UUID
    cost_center_id: Optional[UUID] = None


@dataclass
class DepreciationRunSummary:
    """Summary of a depreciation run."""

    run_id: UUID
    status: str
    assets_processed: int
    total_depreciation: Decimal
    by_category: dict[str, Decimal]


class DepreciationService(ListResponseMixin):
    """
    Service for fixed asset depreciation.

    Handles depreciation calculation, run management, and GL posting.
    """

    @staticmethod
    def calculate_straight_line(
        cost_basis: Decimal,
        residual_value: Decimal,
        useful_life_months: int,
        periods: int = 1,
    ) -> Decimal:
        """Calculate straight-line depreciation for a period."""
        if useful_life_months <= 0:
            return Decimal("0")

        depreciable_amount = cost_basis - residual_value
        monthly_depreciation = depreciable_amount / Decimal(useful_life_months)

        return (monthly_depreciation * Decimal(periods)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def calculate_declining_balance(
        net_book_value: Decimal,
        residual_value: Decimal,
        useful_life_months: int,
        remaining_life_months: int,
        rate_multiplier: Decimal = Decimal("1.0"),
        periods: int = 1,
    ) -> Decimal:
        """
        Calculate declining balance depreciation.

        Args:
            net_book_value: Current NBV
            residual_value: Residual value
            useful_life_months: Total useful life
            remaining_life_months: Remaining life
            rate_multiplier: 1.0 for declining, 2.0 for double-declining
            periods: Number of periods to calculate

        Returns:
            Depreciation amount
        """
        if useful_life_months <= 0 or remaining_life_months <= 0:
            return Decimal("0")

        # Calculate annual rate
        annual_rate = (Decimal("1") / Decimal(useful_life_months / 12)) * rate_multiplier
        monthly_rate = annual_rate / Decimal("12")

        depreciation = net_book_value * monthly_rate * Decimal(periods)

        # Cannot depreciate below residual value
        max_depreciation = net_book_value - residual_value
        if max_depreciation <= 0:
            return Decimal("0")

        return min(depreciation, max_depreciation).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def calculate_sum_of_years(
        cost_basis: Decimal,
        residual_value: Decimal,
        useful_life_months: int,
        remaining_life_months: int,
        periods: int = 1,
    ) -> Decimal:
        """Calculate sum-of-years-digits depreciation."""
        if useful_life_months <= 0 or remaining_life_months <= 0:
            return Decimal("0")

        useful_life_years = useful_life_months // 12
        remaining_years = (remaining_life_months + 11) // 12  # Round up

        # Sum of years = n(n+1)/2
        sum_of_years = Decimal(useful_life_years * (useful_life_years + 1)) / Decimal("2")

        if sum_of_years == 0:
            return Decimal("0")

        depreciable_amount = cost_basis - residual_value
        annual_depreciation = (Decimal(remaining_years) / sum_of_years) * depreciable_amount
        monthly_depreciation = annual_depreciation / Decimal("12")

        return (monthly_depreciation * Decimal(periods)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    @staticmethod
    def calculate_asset_depreciation(
        db: Session,
        asset: Asset,
        periods: int = 1,
    ) -> DepreciationCalculation:
        """
        Calculate depreciation for a single asset.

        Args:
            db: Database session
            asset: Asset to depreciate
            periods: Number of periods (months) to calculate

        Returns:
            DepreciationCalculation with results
        """
        # Get category for account references
        category = db.get(AssetCategory, asset.category_id)
        if not category:
            raise ValueError(f"Category not found for asset {asset.asset_number}")

        cost_basis = asset.revalued_amount or asset.acquisition_cost
        method = asset.depreciation_method

        # Calculate depreciation based on method
        if method == DepreciationMethod.STRAIGHT_LINE.value:
            depreciation = DepreciationService.calculate_straight_line(
                cost_basis=cost_basis,
                residual_value=asset.residual_value,
                useful_life_months=asset.useful_life_months,
                periods=periods,
            )
        elif method == DepreciationMethod.DECLINING_BALANCE.value:
            depreciation = DepreciationService.calculate_declining_balance(
                net_book_value=asset.net_book_value,
                residual_value=asset.residual_value,
                useful_life_months=asset.useful_life_months,
                remaining_life_months=asset.remaining_life_months,
                rate_multiplier=Decimal("1.0"),
                periods=periods,
            )
        elif method == DepreciationMethod.DOUBLE_DECLINING.value:
            depreciation = DepreciationService.calculate_declining_balance(
                net_book_value=asset.net_book_value,
                residual_value=asset.residual_value,
                useful_life_months=asset.useful_life_months,
                remaining_life_months=asset.remaining_life_months,
                rate_multiplier=Decimal("2.0"),
                periods=periods,
            )
        elif method == DepreciationMethod.SUM_OF_YEARS.value:
            depreciation = DepreciationService.calculate_sum_of_years(
                cost_basis=cost_basis,
                residual_value=asset.residual_value,
                useful_life_months=asset.useful_life_months,
                remaining_life_months=asset.remaining_life_months,
                periods=periods,
            )
        else:
            # Default to straight line
            depreciation = DepreciationService.calculate_straight_line(
                cost_basis=cost_basis,
                residual_value=asset.residual_value,
                useful_life_months=asset.useful_life_months,
                periods=periods,
            )

        # Ensure we don't depreciate below residual value
        max_depreciation = asset.net_book_value - asset.residual_value
        if max_depreciation <= 0:
            depreciation = Decimal("0")
        else:
            depreciation = min(depreciation, max_depreciation)

        closing_accum = asset.accumulated_depreciation + depreciation
        closing_nbv = asset.net_book_value - depreciation
        remaining_closing = max(0, asset.remaining_life_months - periods)

        return DepreciationCalculation(
            asset_id=asset.asset_id,
            asset_number=asset.asset_number,
            depreciation_amount=depreciation,
            opening_nbv=asset.net_book_value,
            closing_nbv=closing_nbv,
            opening_accum_dep=asset.accumulated_depreciation,
            closing_accum_dep=closing_accum,
            remaining_life_opening=asset.remaining_life_months,
            remaining_life_closing=remaining_closing,
            expense_account_id=category.depreciation_expense_account_id,
            accum_dep_account_id=category.accumulated_depreciation_account_id,
            cost_center_id=asset.cost_center_id,
        )

    @staticmethod
    def create_depreciation_run(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        created_by_user_id: UUID,
        description: Optional[str] = None,
    ) -> DepreciationRun:
        """
        Create a new depreciation run for a fiscal period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period for the run
            created_by_user_id: User creating the run

        Returns:
            Created DepreciationRun
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(created_by_user_id)

        # Get next run number for this period
        existing_runs = (
            db.query(func.count(DepreciationRun.run_id))
            .filter(
                and_(
                    DepreciationRun.organization_id == org_id,
                    DepreciationRun.fiscal_period_id == period_id,
                )
            )
            .scalar()
        )

        run = DepreciationRun(
            organization_id=org_id,
            fiscal_period_id=period_id,
            run_number=existing_runs + 1,
            run_description=description,
            status=DepreciationRunStatus.DRAFT,
            assets_processed=0,
            total_depreciation=Decimal("0"),
            created_by_user_id=user_id,
        )

        db.add(run)
        db.commit()
        db.refresh(run)

        return run

    @staticmethod
    def calculate_run(
        db: Session,
        organization_id: UUID,
        run_id: UUID,
    ) -> DepreciationRun:
        """
        Calculate depreciation for all eligible assets in a run.

        Args:
            db: Database session
            organization_id: Organization scope
            run_id: Depreciation run to calculate

        Returns:
            Updated DepreciationRun
        """
        org_id = coerce_uuid(organization_id)
        r_id = coerce_uuid(run_id)

        run = db.get(DepreciationRun, r_id)
        if not run or run.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Depreciation run not found")

        if run.status not in [DepreciationRunStatus.DRAFT, DepreciationRunStatus.FAILED]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot calculate run with status '{run.status.value}'",
            )

        # Update status
        run.status = DepreciationRunStatus.CALCULATING
        run.calculation_started_at = datetime.now(timezone.utc)
        db.flush()

        try:
            # Get all depreciable assets
            assets = (
                db.query(Asset)
                .filter(
                    and_(
                        Asset.organization_id == org_id,
                        Asset.status == AssetStatus.ACTIVE,
                        Asset.remaining_life_months > 0,
                        Asset.net_book_value > Asset.residual_value,
                    )
                )
                .all()
            )

            total_depreciation = Decimal("0")
            assets_processed = 0

            # Delete any existing schedules for this run
            db.query(DepreciationSchedule).filter(
                DepreciationSchedule.run_id == r_id
            ).delete()

            for asset in assets:
                if asset.depreciation_start_date is None:
                    continue

                calc = DepreciationService.calculate_asset_depreciation(db, asset)

                if calc.depreciation_amount > 0:
                    schedule = DepreciationSchedule(
                        run_id=r_id,
                        asset_id=asset.asset_id,
                        cost_basis=asset.revalued_amount or asset.acquisition_cost,
                        accumulated_depreciation_opening=calc.opening_accum_dep,
                        net_book_value_opening=calc.opening_nbv,
                        depreciation_amount=calc.depreciation_amount,
                        accumulated_depreciation_closing=calc.closing_accum_dep,
                        net_book_value_closing=calc.closing_nbv,
                        remaining_life_months_opening=calc.remaining_life_opening,
                        remaining_life_months_closing=calc.remaining_life_closing,
                        expense_account_id=calc.expense_account_id,
                        accumulated_depreciation_account_id=calc.accum_dep_account_id,
                        cost_center_id=calc.cost_center_id,
                    )
                    db.add(schedule)

                    total_depreciation += calc.depreciation_amount
                    assets_processed += 1

            run.status = DepreciationRunStatus.CALCULATED
            run.calculation_completed_at = datetime.now(timezone.utc)
            run.assets_processed = assets_processed
            run.total_depreciation = total_depreciation

            db.commit()
            db.refresh(run)

            return run

        except Exception as e:
            run.status = DepreciationRunStatus.FAILED
            db.commit()
            raise HTTPException(
                status_code=500,
                detail=f"Depreciation calculation failed: {str(e)}",
            )

    @staticmethod
    def post_run(
        db: Session,
        organization_id: UUID,
        run_id: UUID,
        posted_by_user_id: UUID,
        posting_date: Optional[date] = None,
    ) -> DepreciationRun:
        """
        Post a calculated depreciation run to the GL.

        Args:
            db: Database session
            organization_id: Organization scope
            run_id: Depreciation run to post
            posted_by_user_id: User posting the run
            posting_date: Date for GL posting

        Returns:
            Updated DepreciationRun
        """
        from app.services.ifrs.fa.fa_posting_adapter import FAPostingAdapter

        org_id = coerce_uuid(organization_id)
        r_id = coerce_uuid(run_id)
        user_id = coerce_uuid(posted_by_user_id)

        run = db.get(DepreciationRun, r_id)
        if not run or run.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Depreciation run not found")

        if run.status != DepreciationRunStatus.CALCULATED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot post run with status '{run.status.value}'",
            )

        # SoD check
        if run.created_by_user_id == user_id:
            raise HTTPException(
                status_code=400,
                detail="Segregation of duties violation: creator cannot post",
            )

        run.status = DepreciationRunStatus.POSTING
        db.flush()

        try:
            result = FAPostingAdapter.post_depreciation_run(
                db=db,
                organization_id=org_id,
                run_id=r_id,
                posting_date=posting_date or date.today(),
                posted_by_user_id=user_id,
            )

            if not result.success:
                run.status = DepreciationRunStatus.FAILED
                db.commit()
                raise HTTPException(status_code=400, detail=result.message)

            # Update asset records
            schedules = (
                db.query(DepreciationSchedule)
                .filter(DepreciationSchedule.run_id == r_id)
                .all()
            )

            for schedule in schedules:
                asset = db.get(Asset, schedule.asset_id)
                if asset:
                    asset.accumulated_depreciation = schedule.accumulated_depreciation_closing
                    asset.net_book_value = schedule.net_book_value_closing
                    asset.remaining_life_months = schedule.remaining_life_months_closing

                    # Check if fully depreciated
                    if asset.net_book_value <= asset.residual_value:
                        asset.status = AssetStatus.FULLY_DEPRECIATED

            run.status = DepreciationRunStatus.POSTED
            run.posted_at = datetime.now(timezone.utc)
            run.posted_by_user_id = user_id
            run.journal_entry_id = result.journal_entry_id
            run.posting_batch_id = result.posting_batch_id

            db.commit()
            db.refresh(run)

            return run

        except HTTPException:
            raise
        except Exception as e:
            run.status = DepreciationRunStatus.FAILED
            db.commit()
            raise HTTPException(
                status_code=500,
                detail=f"Posting failed: {str(e)}",
            )

    @staticmethod
    def get_run_schedules(
        db: Session,
        organization_id: UUID,
        run_id: UUID,
    ) -> list[DepreciationSchedule]:
        """Get all depreciation schedules for a run."""
        org_id = coerce_uuid(organization_id)
        r_id = coerce_uuid(run_id)

        run = db.get(DepreciationRun, r_id)
        if not run or run.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Depreciation run not found")

        return (
            db.query(DepreciationSchedule)
            .filter(DepreciationSchedule.run_id == r_id)
            .all()
        )

    @staticmethod
    def get(
        db: Session,
        run_id: str,
    ) -> DepreciationRun:
        """Get a depreciation run by ID."""
        run = db.get(DepreciationRun, coerce_uuid(run_id))
        if not run:
            raise HTTPException(status_code=404, detail="Depreciation run not found")
        return run

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        fiscal_period_id: Optional[str] = None,
        status: Optional[DepreciationRunStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DepreciationRun]:
        """List depreciation runs with optional filters."""
        query = db.query(DepreciationRun)

        if organization_id:
            query = query.filter(
                DepreciationRun.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_period_id:
            query = query.filter(
                DepreciationRun.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if status:
            query = query.filter(DepreciationRun.status == status)

        query = query.order_by(DepreciationRun.created_at.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
depreciation_service = DepreciationService()
