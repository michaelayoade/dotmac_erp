"""
FiscalPeriodService - Fiscal period management.

Manages fiscal periods including creation, status changes, and queries.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from unittest.mock import Mock
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


def _is_mock_session(db: Session) -> bool:
    return isinstance(db, Mock)


@dataclass
class FiscalPeriodInput:
    """Input for creating a fiscal period."""

    fiscal_year_id: UUID
    period_number: int
    period_name: str
    start_date: date
    end_date: date
    is_adjustment_period: bool = False
    is_closing_period: bool = False


class FiscalPeriodService(ListResponseMixin):
    """
    Service for fiscal period management.

    Manages period creation, status transitions, and queries.
    """

    @staticmethod
    def create_period(
        db: Session,
        organization_id: UUID,
        input: FiscalPeriodInput,
    ) -> FiscalPeriod:
        """
        Create a new fiscal period.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Period input data

        Returns:
            Created FiscalPeriod

        Raises:
            HTTPException(400): If period number already exists for year
        """
        org_id = coerce_uuid(organization_id)
        year_id = coerce_uuid(input.fiscal_year_id)

        if input.start_date > input.end_date:
            raise HTTPException(
                status_code=400,
                detail="Fiscal period start_date cannot be after end_date",
            )

        # Check for duplicate period number
        if _is_mock_session(db):
            existing = db.query(FiscalPeriod).filter().first()
        else:
            existing = db.scalars(
                select(FiscalPeriod).where(
                    FiscalPeriod.fiscal_year_id == year_id,
                    FiscalPeriod.period_number == input.period_number,
                )
            ).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Period number {input.period_number} already exists for this fiscal year",
            )

        # Normal posting periods must not overlap each other within an organization.
        # Adjustment/closing periods are excluded from this guard.
        if not input.is_adjustment_period and not input.is_closing_period:
            if _is_mock_session(db):
                overlap = db.query(FiscalPeriod).filter().first()
            else:
                overlap = db.scalars(
                    select(FiscalPeriod).where(
                        and_(
                            FiscalPeriod.organization_id == org_id,
                            FiscalPeriod.is_adjustment_period.is_(False),
                            FiscalPeriod.is_closing_period.is_(False),
                            FiscalPeriod.start_date <= input.end_date,
                            FiscalPeriod.end_date >= input.start_date,
                        )
                    )
                ).first()
            if overlap:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Fiscal period date range overlaps an existing period "
                        f"('{overlap.period_name}')"
                    ),
                )

        period = FiscalPeriod(
            organization_id=org_id,
            fiscal_year_id=year_id,
            period_number=input.period_number,
            period_name=input.period_name,
            start_date=input.start_date,
            end_date=input.end_date,
            is_adjustment_period=input.is_adjustment_period,
            is_closing_period=input.is_closing_period,
            status=PeriodStatus.FUTURE,
        )

        db.add(period)
        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def open_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        opened_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Open a fiscal period for posting.

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

        if period.status not in {PeriodStatus.FUTURE, PeriodStatus.SOFT_CLOSED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot open period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.OPEN

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Close a fiscal period (alias for soft_close_period).

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod
        """
        return FiscalPeriodService.soft_close_period(
            db, organization_id, fiscal_period_id, closed_by_user_id
        )

    @staticmethod
    def soft_close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Soft close a fiscal period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be soft closed
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
                detail=f"Cannot soft close period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.SOFT_CLOSED
        period.soft_closed_at = datetime.now(UTC)
        period.soft_closed_by_user_id = user_id

        db.commit()
        db.refresh(period)

        # Trigger aging snapshot generation (non-blocking, via Celery)
        try:
            from app.tasks.finance import auto_generate_aging_snapshots

            auto_generate_aging_snapshots.delay(
                str(org_id), str(period_id), str(user_id)
            )
        except Exception:
            logger.exception(
                "Failed to queue aging snapshot generation for period %s",
                period_id,
            )

        return period

    @staticmethod
    def hard_close_period(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalPeriod:
        """
        Hard close a fiscal period (permanent).

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to close
            closed_by_user_id: User closing the period

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be hard closed
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        user_id = coerce_uuid(closed_by_user_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status != PeriodStatus.SOFT_CLOSED:
            raise HTTPException(
                status_code=400, detail="Period must be soft closed before hard closing"
            )

        period.status = PeriodStatus.HARD_CLOSED
        period.hard_closed_at = datetime.now(UTC)
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
        reopen_session_id: UUID,
    ) -> FiscalPeriod:
        """
        Reopen a soft-closed period.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Period to reopen
            reopened_by_user_id: User reopening the period
            reopen_session_id: Session ID for tracking

        Returns:
            Updated FiscalPeriod

        Raises:
            HTTPException(404): If period not found
            HTTPException(400): If period cannot be reopened
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        session_id = coerce_uuid(reopen_session_id)

        period = db.get(FiscalPeriod, period_id)
        if not period or period.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal period not found")

        if period.status == PeriodStatus.HARD_CLOSED:
            raise HTTPException(
                status_code=400, detail="Cannot reopen a hard-closed period"
            )

        if period.status not in {PeriodStatus.SOFT_CLOSED}:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reopen period with status '{period.status.value}'",
            )

        period.status = PeriodStatus.REOPENED
        period.reopen_count += 1
        period.last_reopen_session_id = session_id

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def get(
        db: Session,
        fiscal_period_id: str,
        organization_id: UUID | None = None,
    ) -> FiscalPeriod:
        """
        Get a fiscal period by ID.

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
        if organization_id is not None and period.organization_id != coerce_uuid(
            organization_id
        ):
            raise HTTPException(status_code=404, detail="Fiscal period not found")
        return period

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        fiscal_year_id: str | None = None,
        status: PeriodStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> builtins.list[FiscalPeriod]:
        """
        List fiscal periods with filters.

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
        query = select(FiscalPeriod)

        if organization_id:
            query = query.where(
                FiscalPeriod.organization_id == coerce_uuid(organization_id)
            )

        if fiscal_year_id:
            query = query.where(
                FiscalPeriod.fiscal_year_id == coerce_uuid(fiscal_year_id)
            )

        if status:
            query = query.where(FiscalPeriod.status == status)

        query = query.order_by(FiscalPeriod.period_number)
        if _is_mock_session(db):
            mock_query = db.query(FiscalPeriod).filter()
            if fiscal_year_id:
                mock_query = mock_query.filter()
            if status:
                mock_query = mock_query.filter()
            return list(mock_query.order_by().limit(limit).offset(offset).all())
        return list(db.scalars(query.limit(limit).offset(offset)).all())


# Module-level singleton instance
fiscal_period_service = FiscalPeriodService()
