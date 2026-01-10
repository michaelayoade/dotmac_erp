"""
InterestAccrualService - IFRS 9 effective interest method calculations.

Handles interest accrual, premium/discount amortization using the effective interest method.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.fin_inst.financial_instrument import (
    FinancialInstrument,
    InstrumentClassification,
    InstrumentStatus,
)
from app.models.ifrs.fin_inst.interest_accrual import InterestAccrual
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class AccrualCalculationResult:
    """Result of interest accrual calculation."""

    principal_amount: Decimal
    effective_interest_rate: Decimal
    days_in_period: int
    interest_amount: Decimal
    premium_discount_amortization: Decimal
    effective_interest_income: Decimal
    cash_interest: Decimal
    interest_receivable_movement: Decimal


@dataclass
class DayCountResult:
    """Result of day count calculation."""

    days: int
    year_basis: int
    day_count_fraction: Decimal


class InterestAccrualService(ListResponseMixin):
    """
    Service for IFRS 9 interest accrual calculations.

    Implements the effective interest method for:
    - Interest accrual
    - Premium/discount amortization
    - Cash vs accrual recognition
    """

    @staticmethod
    def calculate_day_count(
        start_date: date,
        end_date: date,
        convention: str = "ACTUAL/365",
    ) -> DayCountResult:
        """
        Calculate day count fraction based on convention.

        Supported conventions:
        - ACTUAL/365: Actual days / 365
        - ACTUAL/360: Actual days / 360
        - 30/360: 30-day months / 360
        - ACTUAL/ACTUAL: Actual days / actual days in year

        Args:
            start_date: Period start date
            end_date: Period end date
            convention: Day count convention

        Returns:
            DayCountResult with days and fraction
        """
        actual_days = (end_date - start_date).days

        if convention == "ACTUAL/365":
            return DayCountResult(
                days=actual_days,
                year_basis=365,
                day_count_fraction=Decimal(actual_days) / Decimal("365"),
            )
        elif convention == "ACTUAL/360":
            return DayCountResult(
                days=actual_days,
                year_basis=360,
                day_count_fraction=Decimal(actual_days) / Decimal("360"),
            )
        elif convention == "30/360":
            # 30/360 convention
            d1 = min(start_date.day, 30)
            d2 = min(end_date.day, 30) if d1 == 30 else end_date.day
            m1 = start_date.month
            m2 = end_date.month
            y1 = start_date.year
            y2 = end_date.year

            days_30_360 = (y2 - y1) * 360 + (m2 - m1) * 30 + (d2 - d1)
            return DayCountResult(
                days=days_30_360,
                year_basis=360,
                day_count_fraction=Decimal(days_30_360) / Decimal("360"),
            )
        else:
            # Default to ACTUAL/365
            return DayCountResult(
                days=actual_days,
                year_basis=365,
                day_count_fraction=Decimal(actual_days) / Decimal("365"),
            )

    @staticmethod
    def calculate_effective_interest(
        principal: Decimal,
        effective_rate: Decimal,
        day_count_fraction: Decimal,
    ) -> Decimal:
        """
        Calculate interest using effective interest method.

        Args:
            principal: Outstanding principal/amortized cost
            effective_rate: Annual effective interest rate
            day_count_fraction: Fraction of year

        Returns:
            Interest amount
        """
        interest = principal * effective_rate * day_count_fraction
        return interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_stated_interest(
        face_value: Decimal,
        stated_rate: Decimal,
        day_count_fraction: Decimal,
    ) -> Decimal:
        """
        Calculate stated (coupon) interest.

        Args:
            face_value: Face/par value
            stated_rate: Annual stated/coupon rate
            day_count_fraction: Fraction of year

        Returns:
            Stated interest amount
        """
        interest = face_value * stated_rate * day_count_fraction
        return interest.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_accrual(
        db: Session,
        instrument: FinancialInstrument,
        accrual_start_date: date,
        accrual_end_date: date,
    ) -> AccrualCalculationResult:
        """
        Calculate interest accrual for a period.

        Implements IFRS 9 effective interest method:
        - Effective interest income = Amortized cost * EIR * time
        - Cash interest = Face value * stated rate * time
        - Premium/discount amortization = Effective interest - Cash interest

        Args:
            db: Database session
            instrument: Financial instrument
            accrual_start_date: Start of accrual period
            accrual_end_date: End of accrual period

        Returns:
            AccrualCalculationResult with calculation details
        """
        if not instrument.is_interest_bearing:
            return AccrualCalculationResult(
                principal_amount=instrument.current_principal,
                effective_interest_rate=Decimal("0"),
                days_in_period=0,
                interest_amount=Decimal("0"),
                premium_discount_amortization=Decimal("0"),
                effective_interest_income=Decimal("0"),
                cash_interest=Decimal("0"),
                interest_receivable_movement=Decimal("0"),
            )

        # Get day count
        convention = instrument.day_count_convention or "ACTUAL/365"
        day_count = InterestAccrualService.calculate_day_count(
            accrual_start_date,
            accrual_end_date,
            convention,
        )

        # Calculate effective interest income (on amortized cost)
        effective_rate = instrument.effective_interest_rate or Decimal("0")
        effective_interest = InterestAccrualService.calculate_effective_interest(
            principal=instrument.amortized_cost,
            effective_rate=effective_rate,
            day_count_fraction=day_count.day_count_fraction,
        )

        # Calculate stated (cash) interest (on face value)
        stated_rate = instrument.stated_interest_rate or Decimal("0")
        cash_interest = InterestAccrualService.calculate_stated_interest(
            face_value=instrument.face_value,
            stated_rate=stated_rate,
            day_count_fraction=day_count.day_count_fraction,
        )

        # Premium/discount amortization is the difference
        premium_discount_amort = effective_interest - cash_interest

        # Interest receivable movement = effective interest (for accrual accounting)
        # When cash is received, this will be reversed
        interest_receivable = effective_interest

        return AccrualCalculationResult(
            principal_amount=instrument.amortized_cost,
            effective_interest_rate=effective_rate,
            days_in_period=day_count.days,
            interest_amount=effective_interest,
            premium_discount_amortization=premium_discount_amort,
            effective_interest_income=effective_interest,
            cash_interest=cash_interest,
            interest_receivable_movement=interest_receivable,
        )

    @staticmethod
    def create_accrual(
        db: Session,
        organization_id: UUID,
        instrument_id: UUID,
        fiscal_period_id: UUID,
        accrual_start_date: date,
        accrual_end_date: date,
        exchange_rate: Decimal = Decimal("1.0"),
    ) -> InterestAccrual:
        """
        Create an interest accrual record.

        Args:
            db: Database session
            organization_id: Organization scope
            instrument_id: Instrument for accrual
            fiscal_period_id: Fiscal period
            accrual_start_date: Accrual period start
            accrual_end_date: Accrual period end
            exchange_rate: Exchange rate to functional currency

        Returns:
            Created InterestAccrual
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instrument_id)
        period_id = coerce_uuid(fiscal_period_id)

        # Load instrument
        instrument = db.get(FinancialInstrument, inst_id)
        if not instrument or instrument.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Instrument not found")

        if instrument.status != InstrumentStatus.ACTIVE:
            raise HTTPException(
                status_code=400,
                detail="Cannot accrue interest on inactive instrument",
            )

        # Check for existing accrual
        existing = (
            db.query(InterestAccrual)
            .filter(
                InterestAccrual.instrument_id == inst_id,
                InterestAccrual.fiscal_period_id == period_id,
            )
            .first()
        )
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Interest accrual already exists for this period",
            )

        # Calculate accrual
        calc_result = InterestAccrualService.calculate_accrual(
            db, instrument, accrual_start_date, accrual_end_date
        )

        # Calculate functional currency amount
        functional_amount = (calc_result.effective_interest_income * exchange_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        accrual = InterestAccrual(
            instrument_id=inst_id,
            fiscal_period_id=period_id,
            accrual_start_date=accrual_start_date,
            accrual_end_date=accrual_end_date,
            days_in_period=calc_result.days_in_period,
            principal_amount=calc_result.principal_amount,
            currency_code=instrument.currency_code,
            effective_interest_rate=calc_result.effective_interest_rate,
            interest_amount=calc_result.interest_amount,
            premium_discount_amortization=calc_result.premium_discount_amortization,
            effective_interest_income=calc_result.effective_interest_income,
            exchange_rate=exchange_rate,
            functional_currency_amount=functional_amount,
            cash_interest=calc_result.cash_interest,
            interest_receivable_movement=calc_result.interest_receivable_movement,
        )

        db.add(accrual)

        # Update instrument amortized cost
        instrument.amortized_cost += calc_result.premium_discount_amortization
        instrument.premium_discount -= calc_result.premium_discount_amortization

        # Update carrying amount based on classification
        if instrument.classification == InstrumentClassification.AMORTIZED_COST:
            instrument.carrying_amount = instrument.amortized_cost - instrument.loss_allowance

        db.commit()
        db.refresh(accrual)

        return accrual

    @staticmethod
    def record_cash_receipt(
        db: Session,
        accrual_id: UUID,
        cash_amount: Decimal,
    ) -> InterestAccrual:
        """
        Record cash interest received against an accrual.

        Args:
            db: Database session
            accrual_id: Accrual to update
            cash_amount: Cash amount received

        Returns:
            Updated InterestAccrual
        """
        acc_id = coerce_uuid(accrual_id)

        accrual = db.get(InterestAccrual, acc_id)
        if not accrual:
            raise HTTPException(status_code=404, detail="Interest accrual not found")

        accrual.cash_interest = cash_amount
        accrual.interest_receivable_movement = accrual.effective_interest_income - cash_amount

        db.commit()
        db.refresh(accrual)

        return accrual

    @staticmethod
    def get_accrual(
        db: Session,
        accrual_id: str,
    ) -> InterestAccrual:
        """Get an interest accrual by ID."""
        accrual = db.get(InterestAccrual, coerce_uuid(accrual_id))
        if not accrual:
            raise HTTPException(status_code=404, detail="Interest accrual not found")
        return accrual

    @staticmethod
    def list_accruals_for_instrument(
        db: Session,
        instrument_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InterestAccrual]:
        """List all accruals for an instrument."""
        return (
            db.query(InterestAccrual)
            .filter(InterestAccrual.instrument_id == coerce_uuid(instrument_id))
            .order_by(InterestAccrual.accrual_end_date.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )

    @staticmethod
    def list_accruals_for_period(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> list[InterestAccrual]:
        """List all accruals for a fiscal period."""
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        return (
            db.query(InterestAccrual)
            .join(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                InterestAccrual.fiscal_period_id == period_id,
            )
            .all()
        )

    @staticmethod
    def get_total_interest_income(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> Decimal:
        """Get total interest income for a period."""
        from sqlalchemy import func

        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        result = (
            db.query(func.sum(InterestAccrual.functional_currency_amount))
            .join(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                FinancialInstrument.is_asset == True,
                InterestAccrual.fiscal_period_id == period_id,
            )
            .scalar()
        )

        return result or Decimal("0")

    @staticmethod
    def get_total_interest_expense(
        db: Session,
        organization_id: str,
        fiscal_period_id: str,
    ) -> Decimal:
        """Get total interest expense for a period (liabilities)."""
        from sqlalchemy import func

        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)

        result = (
            db.query(func.sum(InterestAccrual.functional_currency_amount))
            .join(FinancialInstrument)
            .filter(
                FinancialInstrument.organization_id == org_id,
                FinancialInstrument.is_asset == False,
                InterestAccrual.fiscal_period_id == period_id,
            )
            .scalar()
        )

        return result or Decimal("0")


# Module-level singleton instance
interest_accrual_service = InterestAccrualService()
