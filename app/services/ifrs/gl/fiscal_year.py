"""
FiscalYearService - Fiscal year management.

Manages fiscal years including creation, closing, and queries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.gl.fiscal_year import FiscalYear
from app.models.ifrs.gl.fiscal_period import FiscalPeriod, PeriodStatus
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class FiscalYearInput:
    """Input for creating a fiscal year."""

    year_code: str
    year_name: str
    start_date: date
    end_date: date
    is_adjustment_year: bool = False
    retained_earnings_account_id: Optional[UUID] = None


class FiscalYearService(ListResponseMixin):
    """
    Service for fiscal year management.

    Manages year creation, closing, and queries.
    """

    @staticmethod
    def create_year(
        db: Session,
        organization_id: UUID,
        input: FiscalYearInput,
    ) -> FiscalYear:
        """
        Create a new fiscal year.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Year input data

        Returns:
            Created FiscalYear

        Raises:
            HTTPException(400): If year code already exists
        """
        org_id = coerce_uuid(organization_id)

        # Check for duplicate year code
        existing = (
            db.query(FiscalYear)
            .filter(
                FiscalYear.organization_id == org_id,
                FiscalYear.year_code == input.year_code,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Fiscal year '{input.year_code}' already exists"
            )

        year = FiscalYear(
            organization_id=org_id,
            year_code=input.year_code,
            year_name=input.year_name,
            start_date=input.start_date,
            end_date=input.end_date,
            is_adjustment_year=input.is_adjustment_year,
            retained_earnings_account_id=(
                coerce_uuid(input.retained_earnings_account_id)
                if input.retained_earnings_account_id else None
            ),
        )

        db.add(year)
        db.commit()
        db.refresh(year)

        return year

    @staticmethod
    def create_year_with_periods(
        db: Session,
        organization_id: UUID,
        input: FiscalYearInput,
        period_count: int = 12,
    ) -> FiscalYear:
        """
        Create a fiscal year with automatic period generation.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Year input data
            period_count: Number of periods to create (default 12)

        Returns:
            Created FiscalYear with periods

        Raises:
            HTTPException(400): If year code already exists
        """
        from dateutil.relativedelta import relativedelta

        org_id = coerce_uuid(organization_id)

        # Create the year
        year = FiscalYearService.create_year(db, org_id, input)

        # Generate periods
        period_start = input.start_date
        for i in range(period_count):
            period_end = period_start + relativedelta(months=1) - relativedelta(days=1)
            if period_end > input.end_date:
                period_end = input.end_date

            period = FiscalPeriod(
                organization_id=org_id,
                fiscal_year_id=year.fiscal_year_id,
                period_number=i + 1,
                period_name=f"Period {i + 1}",
                start_date=period_start,
                end_date=period_end,
                status=PeriodStatus.FUTURE,
            )
            db.add(period)

            period_start = period_end + relativedelta(days=1)
            if period_start > input.end_date:
                break

        db.commit()
        db.refresh(year)

        return year

    @staticmethod
    def close_year(
        db: Session,
        organization_id: UUID,
        fiscal_year_id: UUID,
        closed_by_user_id: UUID,
    ) -> FiscalYear:
        """
        Close a fiscal year.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_year_id: Year to close
            closed_by_user_id: User closing the year

        Returns:
            Updated FiscalYear

        Raises:
            HTTPException(404): If year not found
            HTTPException(400): If year cannot be closed
        """
        org_id = coerce_uuid(organization_id)
        year_id = coerce_uuid(fiscal_year_id)
        user_id = coerce_uuid(closed_by_user_id)

        year = db.get(FiscalYear, year_id)
        if not year or year.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Fiscal year not found")

        if year.is_closed:
            raise HTTPException(status_code=400, detail="Fiscal year is already closed")

        # Check all periods are hard closed
        open_periods = (
            db.query(FiscalPeriod)
            .filter(
                FiscalPeriod.fiscal_year_id == year_id,
                FiscalPeriod.status != PeriodStatus.HARD_CLOSED,
            )
            .count()
        )

        if open_periods > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot close year: {open_periods} periods are not hard closed"
            )

        year.is_closed = True
        year.closed_at = datetime.now(timezone.utc)
        year.closed_by_user_id = user_id

        db.commit()
        db.refresh(year)

        return year

    @staticmethod
    def get(
        db: Session,
        fiscal_year_id: str,
    ) -> FiscalYear:
        """
        Get a fiscal year by ID.

        Args:
            db: Database session
            fiscal_year_id: Year ID

        Returns:
            FiscalYear

        Raises:
            HTTPException(404): If not found
        """
        year = db.get(FiscalYear, coerce_uuid(fiscal_year_id))
        if not year:
            raise HTTPException(status_code=404, detail="Fiscal year not found")
        return year

    @staticmethod
    def get_by_code(
        db: Session,
        organization_id: UUID,
        year_code: str,
    ) -> FiscalYear:
        """
        Get a fiscal year by code.

        Args:
            db: Database session
            organization_id: Organization scope
            year_code: Year code

        Returns:
            FiscalYear

        Raises:
            HTTPException(404): If not found
        """
        org_id = coerce_uuid(organization_id)

        year = (
            db.query(FiscalYear)
            .filter(
                FiscalYear.organization_id == org_id,
                FiscalYear.year_code == year_code,
            )
            .first()
        )
        if not year:
            raise HTTPException(status_code=404, detail="Fiscal year not found")
        return year

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        is_closed: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[FiscalYear]:
        """
        List fiscal years with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            is_closed: Filter by closed status
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of FiscalYear objects
        """
        query = db.query(FiscalYear)

        if organization_id:
            query = query.filter(
                FiscalYear.organization_id == coerce_uuid(organization_id)
            )

        if is_closed is not None:
            query = query.filter(FiscalYear.is_closed == is_closed)

        query = query.order_by(FiscalYear.start_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
fiscal_year_service = FiscalYearService()
