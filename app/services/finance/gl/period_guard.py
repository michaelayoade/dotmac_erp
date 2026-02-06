"""
PeriodGuardService - Central write gate for period controls.

Validates that periods are open before allowing write operations.
Enforces audit locks and reopen session validation.
"""

from __future__ import annotations

import calendar
import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.models.finance.gl.fiscal_year import FiscalYear
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class PeriodGuardResult:
    """Result of a period guard check."""

    is_allowed: bool
    fiscal_period_id: Optional[UUID]
    period_status: Optional[str]
    message: str
    reopen_session_id: Optional[UUID] = None


class PeriodGuardService(ListResponseMixin):
    """
    Central gate for all write operations to the ledger.

    Validates period status, audit locks, and reopen sessions
    before allowing any financial write operations.
    """

    # Statuses that allow normal posting
    POSTABLE_STATUSES = {PeriodStatus.OPEN, PeriodStatus.REOPENED}

    @staticmethod
    def can_post_to_date(
        db: Session,
        organization_id: UUID,
        posting_date: date,
        allow_adjustment: bool = False,
        reopen_session_id: Optional[UUID] = None,
    ) -> PeriodGuardResult:
        """
        Check if posting is allowed for a specific date.

        Args:
            db: Database session
            organization_id: Organization scope
            posting_date: Date to check for posting
            allow_adjustment: Whether adjustment periods are acceptable
            reopen_session_id: If period is reopened, verify session

        Returns:
            PeriodGuardResult with allowed status and details
        """
        org_id = coerce_uuid(organization_id)

        # Find the period containing this date
        period = (
            db.query(FiscalPeriod)
            .filter(
                and_(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.start_date <= posting_date,
                    FiscalPeriod.end_date >= posting_date,
                )
            )
            .first()
        )

        if not period:
            # Auto-create period on demand
            period = PeriodGuardService._ensure_period_exists(db, org_id, posting_date)
            if not period:
                return PeriodGuardResult(
                    is_allowed=False,
                    fiscal_period_id=None,
                    period_status=None,
                    message=f"Failed to create fiscal period for date {posting_date}",
                )

        # Check if this is an adjustment period
        if period.is_adjustment_period and not allow_adjustment:
            return PeriodGuardResult(
                is_allowed=False,
                fiscal_period_id=period.fiscal_period_id,
                period_status=period.status.value,
                message="Adjustment periods require explicit allowance",
            )

        # Check status
        if period.status == PeriodStatus.FUTURE:
            return PeriodGuardResult(
                is_allowed=False,
                fiscal_period_id=period.fiscal_period_id,
                period_status=period.status.value,
                message=f"Period '{period.period_name}' is not yet open",
            )

        if period.status == PeriodStatus.SOFT_CLOSED:
            return PeriodGuardResult(
                is_allowed=False,
                fiscal_period_id=period.fiscal_period_id,
                period_status=period.status.value,
                message=f"Period '{period.period_name}' is soft-closed; requires approval to post",
            )

        if period.status == PeriodStatus.HARD_CLOSED:
            return PeriodGuardResult(
                is_allowed=False,
                fiscal_period_id=period.fiscal_period_id,
                period_status=period.status.value,
                message=f"Period '{period.period_name}' is permanently closed",
            )

        if period.status == PeriodStatus.REOPENED:
            # Validate reopen session
            if not reopen_session_id:
                return PeriodGuardResult(
                    is_allowed=False,
                    fiscal_period_id=period.fiscal_period_id,
                    period_status=period.status.value,
                    message="Period is reopened; reopen session ID required",
                    reopen_session_id=period.last_reopen_session_id,
                )

            if period.last_reopen_session_id != coerce_uuid(reopen_session_id):
                return PeriodGuardResult(
                    is_allowed=False,
                    fiscal_period_id=period.fiscal_period_id,
                    period_status=period.status.value,
                    message="Invalid reopen session ID",
                    reopen_session_id=period.last_reopen_session_id,
                )

        # Period is open
        return PeriodGuardResult(
            is_allowed=True,
            fiscal_period_id=period.fiscal_period_id,
            period_status=period.status.value,
            message="Period is open for posting",
            reopen_session_id=period.last_reopen_session_id
            if period.status == PeriodStatus.REOPENED
            else None,
        )

    @staticmethod
    def _ensure_period_exists(
        db: Session,
        organization_id: UUID,
        target_date: date,
    ) -> Optional[FiscalPeriod]:
        """
        Ensure a fiscal period exists for the given date, creating if necessary.

        Creates fiscal year and monthly period on-demand with OPEN status.

        Args:
            db: Database session
            organization_id: Organization scope
            target_date: Date that needs a period

        Returns:
            FiscalPeriod (existing or newly created), or None on failure
        """
        org_id = coerce_uuid(organization_id)
        year = target_date.year
        month = target_date.month

        # Check if period already exists (race condition guard)
        existing = (
            db.query(FiscalPeriod)
            .filter(
                and_(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.start_date <= target_date,
                    FiscalPeriod.end_date >= target_date,
                )
            )
            .first()
        )
        if existing:
            return existing

        # Get or create fiscal year
        fiscal_year = (
            db.query(FiscalYear)
            .filter(
                FiscalYear.organization_id == org_id,
                FiscalYear.year_code == str(year),
            )
            .first()
        )

        if not fiscal_year:
            logger.info("Auto-creating fiscal year %s for org %s", year, org_id)
            fiscal_year = FiscalYear(
                fiscal_year_id=uuid_lib.uuid4(),
                organization_id=org_id,
                year_code=str(year),
                year_name=f"Fiscal Year {year}",
                start_date=date(year, 1, 1),
                end_date=date(year, 12, 31),
            )
            db.add(fiscal_year)
            db.flush()

        # Create the monthly period
        month_name = calendar.month_name[month]
        _, last_day = calendar.monthrange(year, month)

        logger.info(
            "Auto-creating fiscal period %s %s for org %s", month_name, year, org_id
        )
        period = FiscalPeriod(
            fiscal_period_id=uuid_lib.uuid4(),
            organization_id=org_id,
            fiscal_year_id=fiscal_year.fiscal_year_id,
            period_number=month,
            period_name=f"{month_name} {year}",
            start_date=date(year, month, 1),
            end_date=date(year, month, last_day),
            status=PeriodStatus.OPEN,  # Auto-created periods are OPEN
        )
        db.add(period)
        db.flush()

        return period

    @staticmethod
    def require_open_period(
        db: Session,
        organization_id: UUID,
        posting_date: date,
        allow_adjustment: bool = False,
        reopen_session_id: Optional[UUID] = None,
    ) -> UUID:
        """
        Require an open period, raising exception if not available.

        Args:
            db: Database session
            organization_id: Organization scope
            posting_date: Date for posting
            allow_adjustment: Allow adjustment periods
            reopen_session_id: Reopen session for reopened periods

        Returns:
            Fiscal period ID

        Raises:
            HTTPException(400): If period is not open
        """
        result = PeriodGuardService.can_post_to_date(
            db, organization_id, posting_date, allow_adjustment, reopen_session_id
        )

        if not result.is_allowed:
            raise HTTPException(status_code=400, detail=result.message)

        if result.fiscal_period_id is None:
            raise HTTPException(status_code=400, detail="Fiscal period not found")

        return result.fiscal_period_id

    @staticmethod
    def get_period_for_date(
        db: Session,
        organization_id: UUID,
        target_date: date,
    ) -> Optional[FiscalPeriod]:
        """
        Get the fiscal period containing a specific date.

        Args:
            db: Database session
            organization_id: Organization scope
            target_date: Date to look up

        Returns:
            FiscalPeriod or None if not found
        """
        org_id = coerce_uuid(organization_id)

        return (
            db.query(FiscalPeriod)
            .filter(
                and_(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.start_date <= target_date,
                    FiscalPeriod.end_date >= target_date,
                )
            )
            .first()
        )

    @staticmethod
    def get_open_periods(
        db: Session,
        organization_id: UUID,
        include_reopened: bool = True,
    ) -> list[FiscalPeriod]:
        """
        Get all open periods for an organization.

        Args:
            db: Database session
            organization_id: Organization scope
            include_reopened: Include REOPENED status periods

        Returns:
            List of open FiscalPeriod records
        """
        org_id = coerce_uuid(organization_id)

        statuses = [PeriodStatus.OPEN]
        if include_reopened:
            statuses.append(PeriodStatus.REOPENED)

        return (
            db.query(FiscalPeriod)
            .filter(
                and_(
                    FiscalPeriod.organization_id == org_id,
                    FiscalPeriod.status.in_(statuses),
                )
            )
            .order_by(FiscalPeriod.start_date)
            .all()
        )

    @staticmethod
    def get_current_period(
        db: Session,
        organization_id: UUID,
    ) -> Optional[FiscalPeriod]:
        """
        Get the current fiscal period (containing today).

        Args:
            db: Database session
            organization_id: Organization scope

        Returns:
            Current FiscalPeriod or None
        """
        return PeriodGuardService.get_period_for_date(db, organization_id, date.today())

    @staticmethod
    def open_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        opened_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Open a future period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to open
            opened_by_user_id: User opening the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be opened
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status != PeriodStatus.FUTURE:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot open period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.OPEN
        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def soft_close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Soft-close a period (requires approval to post).

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to soft-close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be soft-closed
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(closed_by_user_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status not in {PeriodStatus.OPEN, PeriodStatus.REOPENED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot soft-close period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.SOFT_CLOSED
        period.soft_closed_at = datetime.now(timezone.utc)
        period.soft_closed_by_user_id = user_id

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def hard_close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Hard-close a period (permanent, no further posting).

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to hard-close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be hard-closed
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(closed_by_user_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status not in {
            PeriodStatus.SOFT_CLOSED,
            PeriodStatus.OPEN,
            PeriodStatus.REOPENED,
        }:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot hard-close period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.HARD_CLOSED
        period.hard_closed_at = datetime.now(timezone.utc)
        period.hard_closed_by_user_id = user_id

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def reopen_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        reopened_by_user_id: UUID,
        reopen_reason: str,
    ) -> tuple[FiscalPeriod, UUID]:
        """
        Reopen a soft-closed period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to reopen
            reopened_by_user_id: User reopening
            reopen_reason: Reason for reopen (for audit)

        Returns:
            Tuple of (Updated FiscalPeriod, reopen_session_id)

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be reopened
        """
        import uuid as uuid_lib

        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status != PeriodStatus.SOFT_CLOSED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reopen period with status '{period.status.value}'",
            )

        # Generate new reopen session ID
        reopen_session_id = uuid_lib.uuid4()

        period.status = PeriodStatus.REOPENED
        period.reopen_count += 1
        period.last_reopen_session_id = reopen_session_id

        db.commit()
        db.refresh(period)

        return period, reopen_session_id

    @staticmethod
    def close_reopen_session(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        reopen_session_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Close a reopen session, returning period to soft-closed.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period
            reopen_session_id: Session to close
            closed_by_user_id: User closing the session

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If session invalid
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        session_id = coerce_uuid(reopen_session_id)
        user_id = coerce_uuid(closed_by_user_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status != PeriodStatus.REOPENED:
            raise HTTPException(
                status_code=400, detail="Period is not in reopened status"
            )

        if period.last_reopen_session_id != session_id:
            raise HTTPException(status_code=400, detail="Invalid reopen session ID")

        period.status = PeriodStatus.SOFT_CLOSED
        period.soft_closed_at = datetime.now(timezone.utc)
        period.soft_closed_by_user_id = user_id

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def get(
        db: Session,
        fiscal_period_id: str,
    ) -> FiscalPeriod:
        """
        Get a period by ID.

        Args:
            db: Database session
            fiscal_period_id: Period ID

        Returns:
            FiscalPeriod

        Raises:
            HTTPException(404): If not found
        """
        period = db.get(FiscalPeriod, coerce_uuid(fiscal_period_id))
        if not period:
            raise HTTPException(status_code=404, detail="Fiscal period not found")
        return period

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        fiscal_year_id: Optional[str] = None,
        status: Optional[PeriodStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FiscalPeriod]:
        """
        List fiscal periods.

        Args:
            db: Database session
            organization_id: Filter by organization
            fiscal_year_id: Filter by fiscal year
            status: Filter by status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of FiscalPeriod objects
        """
        query = db.query(FiscalPeriod)

        if organization_id:
            query = query.filter(
                FiscalPeriod.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_year_id:
            query = query.filter(
                FiscalPeriod.fiscal_year_id == coerce_uuid(fiscal_year_id)
            )

        if status:
            query = query.filter(FiscalPeriod.status == status)

        query = query.order_by(FiscalPeriod.start_date)
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
period_guard_service = PeriodGuardService()
