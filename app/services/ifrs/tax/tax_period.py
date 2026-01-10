"""
TaxPeriodService - Tax period management.

Manages tax reporting periods for various jurisdictions and tax types.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from dateutil.relativedelta import relativedelta
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.tax.tax_period import (
    TaxPeriod,
    TaxPeriodStatus,
    TaxPeriodFrequency,
)
from app.models.ifrs.tax.tax_jurisdiction import TaxJurisdiction
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class TaxPeriodInput:
    """Input for creating a tax period."""

    jurisdiction_id: UUID
    period_name: str
    frequency: TaxPeriodFrequency
    start_date: date
    end_date: date
    due_date: date
    fiscal_period_id: Optional[UUID] = None


class TaxPeriodService(ListResponseMixin):
    """
    Service for tax period management.

    Handles creation, status tracking, and lifecycle of tax periods.
    """

    @staticmethod
    def create_period(
        db: Session,
        organization_id: UUID,
        input: TaxPeriodInput,
    ) -> TaxPeriod:
        """
        Create a new tax period.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Period input data

        Returns:
            Created TaxPeriod
        """
        org_id = coerce_uuid(organization_id)
        jurisdiction_id = coerce_uuid(input.jurisdiction_id)

        # Validate jurisdiction exists
        jurisdiction = db.query(TaxJurisdiction).filter(
            TaxJurisdiction.jurisdiction_id == jurisdiction_id,
            TaxJurisdiction.organization_id == org_id,
        ).first()

        if not jurisdiction:
            raise HTTPException(status_code=404, detail="Tax jurisdiction not found")

        # Validate dates
        if input.end_date < input.start_date:
            raise HTTPException(
                status_code=400,
                detail="End date must be after start date"
            )

        # Check for overlapping periods
        existing = db.query(TaxPeriod).filter(
            TaxPeriod.organization_id == org_id,
            TaxPeriod.jurisdiction_id == jurisdiction_id,
            TaxPeriod.start_date <= input.end_date,
            TaxPeriod.end_date >= input.start_date,
        ).first()

        if existing:
            raise HTTPException(
                status_code=400,
                detail=f"Period overlaps with existing period: {existing.period_name}"
            )

        period = TaxPeriod(
            organization_id=org_id,
            jurisdiction_id=jurisdiction_id,
            fiscal_period_id=coerce_uuid(input.fiscal_period_id) if input.fiscal_period_id else None,
            period_name=input.period_name,
            frequency=input.frequency,
            start_date=input.start_date,
            end_date=input.end_date,
            due_date=input.due_date,
            status=TaxPeriodStatus.OPEN,
        )

        db.add(period)
        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def generate_periods(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID,
        year: int,
        frequency: TaxPeriodFrequency,
        due_date_offset_days: int = 30,
    ) -> list[TaxPeriod]:
        """
        Generate tax periods for a year.

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Tax jurisdiction
            year: Year to generate periods for
            frequency: Period frequency
            due_date_offset_days: Days after period end for due date

        Returns:
            List of created TaxPeriod objects
        """
        org_id = coerce_uuid(organization_id)
        jurisdiction_id = coerce_uuid(jurisdiction_id)

        periods = []

        if frequency == TaxPeriodFrequency.MONTHLY:
            for month in range(1, 13):
                start = date(year, month, 1)
                end = start + relativedelta(months=1) - relativedelta(days=1)
                due = end + relativedelta(days=due_date_offset_days)

                period_input = TaxPeriodInput(
                    jurisdiction_id=jurisdiction_id,
                    period_name=f"{year}-{month:02d}",
                    frequency=frequency,
                    start_date=start,
                    end_date=end,
                    due_date=due,
                )
                period = TaxPeriodService.create_period(db, org_id, period_input)
                periods.append(period)

        elif frequency == TaxPeriodFrequency.QUARTERLY:
            for quarter in range(1, 5):
                start_month = (quarter - 1) * 3 + 1
                start = date(year, start_month, 1)
                end = start + relativedelta(months=3) - relativedelta(days=1)
                due = end + relativedelta(days=due_date_offset_days)

                period_input = TaxPeriodInput(
                    jurisdiction_id=jurisdiction_id,
                    period_name=f"{year}-Q{quarter}",
                    frequency=frequency,
                    start_date=start,
                    end_date=end,
                    due_date=due,
                )
                period = TaxPeriodService.create_period(db, org_id, period_input)
                periods.append(period)

        elif frequency == TaxPeriodFrequency.ANNUAL:
            start = date(year, 1, 1)
            end = date(year, 12, 31)
            due = end + relativedelta(days=due_date_offset_days)

            period_input = TaxPeriodInput(
                jurisdiction_id=jurisdiction_id,
                period_name=f"{year}",
                frequency=frequency,
                start_date=start,
                end_date=end,
                due_date=due,
            )
            period = TaxPeriodService.create_period(db, org_id, period_input)
            periods.append(period)

        return periods

    @staticmethod
    def file_extension(
        db: Session,
        organization_id: UUID,
        period_id: UUID,
        extended_due_date: date,
    ) -> TaxPeriod:
        """
        File an extension for a tax period.

        Args:
            db: Database session
            organization_id: Organization scope
            period_id: Period to extend
            extended_due_date: New due date

        Returns:
            Updated TaxPeriod
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(period_id)

        period = db.query(TaxPeriod).filter(
            TaxPeriod.period_id == period_id,
            TaxPeriod.organization_id == org_id,
        ).first()

        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")

        if period.status != TaxPeriodStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot file extension for period in {period.status.value} status"
            )

        if extended_due_date <= period.due_date:
            raise HTTPException(
                status_code=400,
                detail="Extended due date must be after original due date"
            )

        period.is_extension_filed = True
        period.extended_due_date = extended_due_date

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def mark_filed(
        db: Session,
        organization_id: UUID,
        period_id: UUID,
    ) -> TaxPeriod:
        """
        Mark a tax period as filed.

        Args:
            db: Database session
            organization_id: Organization scope
            period_id: Period to mark filed

        Returns:
            Updated TaxPeriod
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(period_id)

        period = db.query(TaxPeriod).filter(
            TaxPeriod.period_id == period_id,
            TaxPeriod.organization_id == org_id,
        ).first()

        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")

        if period.status != TaxPeriodStatus.OPEN:
            raise HTTPException(
                status_code=400,
                detail=f"Period is already in {period.status.value} status"
            )

        period.status = TaxPeriodStatus.FILED

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def mark_paid(
        db: Session,
        organization_id: UUID,
        period_id: UUID,
    ) -> TaxPeriod:
        """
        Mark a tax period as paid.

        Args:
            db: Database session
            organization_id: Organization scope
            period_id: Period to mark paid

        Returns:
            Updated TaxPeriod
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(period_id)

        period = db.query(TaxPeriod).filter(
            TaxPeriod.period_id == period_id,
            TaxPeriod.organization_id == org_id,
        ).first()

        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")

        period.status = TaxPeriodStatus.PAID

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def close_period(
        db: Session,
        organization_id: UUID,
        period_id: UUID,
    ) -> TaxPeriod:
        """
        Close a tax period.

        Args:
            db: Database session
            organization_id: Organization scope
            period_id: Period to close

        Returns:
            Updated TaxPeriod
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(period_id)

        period = db.query(TaxPeriod).filter(
            TaxPeriod.period_id == period_id,
            TaxPeriod.organization_id == org_id,
        ).first()

        if not period:
            raise HTTPException(status_code=404, detail="Tax period not found")

        period.status = TaxPeriodStatus.CLOSED

        db.commit()
        db.refresh(period)

        return period

    @staticmethod
    def get_current_period(
        db: Session,
        organization_id: UUID,
        jurisdiction_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> Optional[TaxPeriod]:
        """
        Get the current open tax period.

        Args:
            db: Database session
            organization_id: Organization scope
            jurisdiction_id: Tax jurisdiction
            as_of_date: Date to find period for (defaults to today)

        Returns:
            TaxPeriod if found, None otherwise
        """
        org_id = coerce_uuid(organization_id)
        jurisdiction_id = coerce_uuid(jurisdiction_id)
        check_date = as_of_date or date.today()

        return db.query(TaxPeriod).filter(
            TaxPeriod.organization_id == org_id,
            TaxPeriod.jurisdiction_id == jurisdiction_id,
            TaxPeriod.start_date <= check_date,
            TaxPeriod.end_date >= check_date,
        ).first()

    @staticmethod
    def get_overdue_periods(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> list[TaxPeriod]:
        """
        Get tax periods that are past due.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date to check against (defaults to today)

        Returns:
            List of overdue TaxPeriod objects
        """
        org_id = coerce_uuid(organization_id)
        check_date = as_of_date or date.today()

        return db.query(TaxPeriod).filter(
            TaxPeriod.organization_id == org_id,
            TaxPeriod.status == TaxPeriodStatus.OPEN,
            TaxPeriod.due_date < check_date,
            TaxPeriod.is_extension_filed == False,
        ).order_by(TaxPeriod.due_date).all()

    @staticmethod
    def get(db: Session, period_id: str) -> Optional[TaxPeriod]:
        """Get a tax period by ID."""
        return db.query(TaxPeriod).filter(
            TaxPeriod.period_id == coerce_uuid(period_id)
        ).first()

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        jurisdiction_id: Optional[str] = None,
        status: Optional[TaxPeriodStatus] = None,
        frequency: Optional[TaxPeriodFrequency] = None,
        year: Optional[int] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TaxPeriod]:
        """
        List tax periods with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            jurisdiction_id: Filter by jurisdiction
            status: Filter by status
            frequency: Filter by frequency
            year: Filter by year
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of TaxPeriod objects
        """
        query = db.query(TaxPeriod)

        if organization_id:
            query = query.filter(
                TaxPeriod.organization_id == coerce_uuid(organization_id)
            )

        if jurisdiction_id:
            query = query.filter(
                TaxPeriod.jurisdiction_id == coerce_uuid(jurisdiction_id)
            )

        if status:
            query = query.filter(TaxPeriod.status == status)

        if frequency:
            query = query.filter(TaxPeriod.frequency == frequency)

        if year:
            query = query.filter(
                TaxPeriod.start_date >= date(year, 1, 1),
                TaxPeriod.end_date <= date(year, 12, 31),
            )

        return query.order_by(TaxPeriod.start_date.desc()).offset(offset).limit(limit).all()


# Module-level instance
tax_period_service = TaxPeriodService()
