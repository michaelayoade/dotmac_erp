"""
DepreciationService - Fixed Asset depreciation calculations.

Manages depreciation runs, calculations, and posting to GL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.domain_settings import SettingDomain
from app.models.fixed_assets.asset import Asset, AssetStatus
from app.models.fixed_assets.asset_category import AssetCategory, DepreciationMethod
from app.models.fixed_assets.depreciation_run import (
    DepreciationRun,
    DepreciationRunStatus,
)
from app.models.fixed_assets.depreciation_schedule import DepreciationSchedule
from app.models.finance.core_org.organization import Organization
from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.services.common import coerce_uuid
from app.services.people.assets.lifecycle_event_service import (
    record_asset_lifecycle_event,
)
from app.services.response import ListResponseMixin
from app.services.settings_spec import resolve_value

logger = logging.getLogger(__name__)

AUTOMATION_RUN_CREATOR_USER_ID = UUID("00000000-0000-0000-0000-000000000098")
AUTOMATION_RUN_POSTER_USER_ID = UUID("00000000-0000-0000-0000-000000000099")


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
    cost_center_id: UUID | None = None


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

    _AUTOMATION_BLOCKING_STATUSES = (
        DepreciationRunStatus.DRAFT,
        DepreciationRunStatus.CALCULATING,
        DepreciationRunStatus.CALCULATED,
        DepreciationRunStatus.POSTING,
        DepreciationRunStatus.POSTED,
    )

    @staticmethod
    def _elapsed_months(start_date: date | None, as_of_date: date) -> int:
        """Return elapsed depreciation months between start and the as-of date."""
        if start_date is None or start_date > as_of_date:
            return 0

        months = (as_of_date.year - start_date.year) * 12 + (
            as_of_date.month - start_date.month
        )
        if as_of_date.day >= start_date.day:
            months += 1
        return max(months, 0)

    @staticmethod
    def periods_due_for_run(asset: Asset, as_of_date: date) -> int:
        """Calculate catch-up depreciation periods due for an asset."""
        useful_life_months = int(asset.useful_life_months or 0)
        remaining_life_months = int(asset.remaining_life_months or 0)
        if useful_life_months <= 0 or remaining_life_months <= 0:
            return 0

        total_periods_due = min(
            useful_life_months,
            DepreciationService._elapsed_months(
                asset.depreciation_start_date, as_of_date
            ),
        )
        already_recognized_periods = max(0, useful_life_months - remaining_life_months)
        return max(0, total_periods_due - already_recognized_periods)

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
        annual_rate = (
            Decimal("1") / Decimal(useful_life_months / 12)
        ) * rate_multiplier
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
        sum_of_years = Decimal(useful_life_years * (useful_life_years + 1)) / Decimal(
            "2"
        )

        if sum_of_years == 0:
            return Decimal("0")

        depreciable_amount = cost_basis - residual_value
        annual_depreciation = (
            Decimal(remaining_years) / sum_of_years
        ) * depreciable_amount
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
        description: str | None = None,
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
        existing_runs = db.scalar(
            select(func.count(DepreciationRun.run_id)).where(
                and_(
                    DepreciationRun.organization_id == org_id,
                    DepreciationRun.fiscal_period_id == period_id,
                )
            )
        )

        run = DepreciationRun(
            organization_id=org_id,
            fiscal_period_id=period_id,
            run_number=int(existing_runs or 0) + 1,
            run_description=description,
            status=DepreciationRunStatus.DRAFT,
            assets_processed=0,
            total_depreciation=Decimal("0"),
            created_by_user_id=user_id,
        )

        db.add(run)
        db.flush()
        db.refresh(run)

        return run

    @staticmethod
    def list_active_organization_ids(db: Session) -> list[UUID]:
        """Return active organization IDs for scheduled automation scans."""
        return list(
            db.scalars(
                select(Organization.organization_id).where(
                    Organization.is_active.is_(True)
                )
            ).all()
        )

    @staticmethod
    def get_next_automation_period(
        db: Session,
        organization_id: UUID,
        as_of_date: date | None = None,
    ) -> FiscalPeriod | None:
        """
        Return the next monthly fiscal period due for depreciation automation.

        The task only processes periods that have already ended and do not
        already have a non-failed depreciation run.
        """
        org_id = coerce_uuid(organization_id)
        cutoff_date = as_of_date or date.today()

        blocked_period_ids = select(DepreciationRun.fiscal_period_id).where(
            and_(
                DepreciationRun.organization_id == org_id,
                DepreciationRun.status.in_(
                    DepreciationService._AUTOMATION_BLOCKING_STATUSES
                ),
            )
        )

        return db.scalar(
            select(FiscalPeriod)
            .where(
                and_(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.status.in_(tuple(PeriodStatus.accepts_postings())),
                    FiscalPeriod.is_adjustment_period.is_(False),
                    FiscalPeriod.end_date <= cutoff_date,
                    ~FiscalPeriod.fiscal_period_id.in_(blocked_period_ids),
                )
            )
            .order_by(FiscalPeriod.end_date.asc(), FiscalPeriod.period_number.asc())
        )

    @staticmethod
    def create_automated_monthly_run(
        db: Session,
        organization_id: UUID,
        *,
        as_of_date: date | None = None,
        auto_post: bool = False,
    ) -> dict[str, object]:
        """Create the next monthly depreciation run for one organization."""
        org_id = coerce_uuid(organization_id)
        fiscal_period = DepreciationService.get_next_automation_period(
            db, org_id, as_of_date=as_of_date
        )
        if not fiscal_period:
            return {
                "status": "skipped",
                "reason": "no_due_period",
                "organization_id": str(org_id),
            }

        run = DepreciationService.create_depreciation_run(
            db=db,
            organization_id=org_id,
            fiscal_period_id=fiscal_period.fiscal_period_id,
            created_by_user_id=AUTOMATION_RUN_CREATOR_USER_ID,
            description=(
                f"System-generated monthly depreciation for {fiscal_period.period_name}"
            ),
        )
        run = DepreciationService.calculate_run(db, org_id, run.run_id)

        result: dict[str, object] = {
            "status": "calculated",
            "organization_id": str(org_id),
            "period_id": str(fiscal_period.fiscal_period_id),
            "period_name": fiscal_period.period_name,
            "run_id": str(run.run_id),
            "run_number": run.run_number,
            "assets_processed": run.assets_processed,
            "total_depreciation": str(run.total_depreciation),
        }

        if auto_post and run.assets_processed > 0:
            run = DepreciationService.post_run(
                db=db,
                organization_id=org_id,
                run_id=run.run_id,
                posted_by_user_id=AUTOMATION_RUN_POSTER_USER_ID,
                posting_date=fiscal_period.end_date,
            )
            result["status"] = "posted"
            result["journal_entry_id"] = (
                str(run.journal_entry_id) if run.journal_entry_id else None
            )
            try:
                from app.services.fixed_assets.reconciliation import (
                    FixedAssetDepreciationReconciliationService,
                )

                reconciliation = (
                    FixedAssetDepreciationReconciliationService.reconcile_run(
                        db,
                        org_id,
                        run.run_id,
                    )
                )
                result["gl_reconciliation"] = reconciliation.as_dict()
            except Exception as exc:
                logger.exception(
                    "Automated depreciation GL reconciliation failed for run %s",
                    run.run_id,
                )
                result["gl_reconciliation"] = {
                    "status": "failed",
                    "error": str(exc),
                }
        elif auto_post:
            result["reason"] = "no_assets_to_post"

        return result

    @staticmethod
    def automation_enabled(db: Session) -> bool:
        """Return whether monthly FA depreciation automation is enabled."""
        return bool(
            resolve_value(
                db, SettingDomain.automation, "fa_depreciation_auto_run_enabled"
            )
        )

    @staticmethod
    def automation_auto_post_enabled(db: Session) -> bool:
        """Return whether automated runs should post immediately."""
        return bool(
            resolve_value(
                db, SettingDomain.automation, "fa_depreciation_auto_post_enabled"
            )
        )

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

        if run.status not in [
            DepreciationRunStatus.DRAFT,
            DepreciationRunStatus.FAILED,
        ]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot calculate run with status '{run.status.value}'",
            )

        # Update status
        run.status = DepreciationRunStatus.CALCULATING
        run.calculation_started_at = datetime.now(UTC)
        db.flush()

        try:
            fiscal_period = db.get(FiscalPeriod, run.fiscal_period_id)
            if not fiscal_period or fiscal_period.organization_id != org_id:
                raise HTTPException(status_code=404, detail="Fiscal period not found")
            as_of_date = min(fiscal_period.end_date, date.today())

            # Get all depreciable assets
            assets = list(
                db.scalars(
                    select(Asset).where(
                        and_(
                            Asset.organization_id == org_id,
                            Asset.status == AssetStatus.IN_USE,
                            Asset.remaining_life_months > 0,
                            Asset.net_book_value > Asset.residual_value,
                        )
                    )
                )
            )

            total_depreciation = Decimal("0")
            assets_processed = 0

            # Delete any existing schedules for this run
            db.execute(
                delete(DepreciationSchedule).where(DepreciationSchedule.run_id == r_id)
            )

            for asset in assets:
                if asset.depreciation_start_date is None:
                    continue
                periods = DepreciationService.periods_due_for_run(asset, as_of_date)
                if periods <= 0:
                    continue

                calc = DepreciationService.calculate_asset_depreciation(
                    db, asset, periods=periods
                )

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
            run.calculation_completed_at = datetime.now(UTC)
            run.assets_processed = assets_processed
            run.total_depreciation = total_depreciation

            db.flush()
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
        posting_date: date | None = None,
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
        from app.services.fixed_assets.fa_posting_adapter import FAPostingAdapter

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

        schedules = list(
            db.scalars(
                select(DepreciationSchedule).where(DepreciationSchedule.run_id == r_id)
            )
        )
        if not schedules:
            raise HTTPException(
                status_code=400,
                detail="Cannot post depreciation run with no schedules",
            )

        for schedule in schedules:
            asset = db.get(Asset, schedule.asset_id)
            if not asset or asset.organization_id != org_id:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot post depreciation run with missing asset schedule",
                )

            is_stale = (
                schedule.accumulated_depreciation_opening
                != asset.accumulated_depreciation
                or schedule.net_book_value_opening != asset.net_book_value
                or int(schedule.remaining_life_months_opening)
                != int(asset.remaining_life_months)
            )
            if is_stale:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Depreciation run is stale for asset "
                        f"{asset.asset_number}; recalculate the run before posting"
                    ),
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

            for schedule in schedules:
                asset = db.get(Asset, schedule.asset_id)
                if asset:
                    previous_status = asset.status
                    asset.accumulated_depreciation = (
                        schedule.accumulated_depreciation_closing
                    )
                    asset.net_book_value = schedule.net_book_value_closing
                    asset.remaining_life_months = schedule.remaining_life_months_closing
                    asset.current_depreciation_schedule_id = schedule.schedule_id

                    # Check if fully depreciated
                    if (
                        asset.net_book_value <= asset.residual_value
                        and previous_status != AssetStatus.FULLY_DEPRECIATED
                    ):
                        asset.status = AssetStatus.FULLY_DEPRECIATED
                        record_asset_lifecycle_event(
                            db,
                            org_id=org_id,
                            asset_id=asset.asset_id,
                            event_category="STATE",
                            event_type="STATE_CHANGED",
                            source_type="depreciation_run",
                            source_record_id=run.run_id,
                            previous_status=previous_status.value,
                            new_status=asset.status.value,
                            notes="Depreciation posting updated asset state",
                            event_payload={
                                "run_id": str(run.run_id),
                                "schedule_id": str(schedule.schedule_id),
                            },
                        )

            run.status = DepreciationRunStatus.POSTED
            run.posted_at = datetime.now(UTC)
            run.posted_by_user_id = user_id
            run.journal_entry_id = result.journal_entry_id
            run.posting_batch_id = result.posting_batch_id

            db.flush()
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

        return list(
            db.scalars(
                select(DepreciationSchedule).where(DepreciationSchedule.run_id == r_id)
            ).all()
        )

    @staticmethod
    def get(
        db: Session,
        run_id: str,
        organization_id: UUID | None = None,
    ) -> DepreciationRun:
        """Get a depreciation run by ID."""
        run = db.get(DepreciationRun, coerce_uuid(run_id))
        if not run:
            raise HTTPException(status_code=404, detail="Depreciation run not found")
        if organization_id is not None and run.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Depreciation run not found")
        return run

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        fiscal_period_id: str | None = None,
        status: DepreciationRunStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DepreciationRun]:
        """List depreciation runs with optional filters."""
        query = select(DepreciationRun)

        if organization_id:
            query = query.where(
                DepreciationRun.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_period_id:
            query = query.where(
                DepreciationRun.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if status:
            query = query.where(DepreciationRun.status == status)

        return list(
            db.scalars(
                query.order_by(DepreciationRun.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )


# Module-level singleton instance
depreciation_service = DepreciationService()
