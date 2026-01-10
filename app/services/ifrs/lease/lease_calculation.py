"""
LeaseCalculationService - IFRS 16 lease calculations.

Handles present value calculations, amortization schedules, and interest accrual.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.lease.lease_contract import LeaseContract, LeaseClassification
from app.models.ifrs.lease.lease_liability import LeaseLiability
from app.models.ifrs.lease.lease_asset import LeaseAsset
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class LiabilityCalculationResult:
    """Result of initial liability calculation."""

    total_liability: Decimal
    pv_fixed_payments: Decimal
    pv_variable_payments: Decimal
    pv_residual_guarantee: Decimal
    pv_purchase_option: Decimal
    current_portion: Decimal
    non_current_portion: Decimal
    total_payments: Decimal
    total_interest: Decimal


@dataclass
class PaymentScheduleEntry:
    """Single entry in a lease payment schedule."""

    period: int
    payment_date: date
    opening_balance: Decimal
    payment_amount: Decimal
    interest_expense: Decimal
    principal_reduction: Decimal
    closing_balance: Decimal


@dataclass
class InterestAccrualResult:
    """Result of interest accrual calculation."""

    interest_amount: Decimal
    opening_balance: Decimal
    closing_balance: Decimal
    accrual_date: date


class LeaseCalculationService(ListResponseMixin):
    """
    Service for IFRS 16 lease calculations.

    Provides present value calculations, amortization schedules,
    and periodic interest accrual.
    """

    @staticmethod
    def calculate_pv(
        payment: Decimal,
        rate: Decimal,
        periods: int,
        payment_timing: str = "ADVANCE",
    ) -> Decimal:
        """
        Calculate present value of an annuity.

        Args:
            payment: Periodic payment amount
            rate: Periodic discount rate (annual rate / periods per year)
            periods: Number of payment periods
            payment_timing: ADVANCE (beginning) or ARREARS (end)

        Returns:
            Present value of payments
        """
        if periods <= 0 or payment == Decimal("0"):
            return Decimal("0")

        if rate == Decimal("0"):
            return payment * Decimal(periods)

        # PV of ordinary annuity
        pv_factor = (Decimal("1") - (Decimal("1") + rate) ** (-periods)) / rate
        pv = payment * pv_factor

        # Adjust for payment timing
        if payment_timing == "ADVANCE":
            # Annuity due - multiply by (1 + r)
            pv = pv * (Decimal("1") + rate)

        return pv.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def calculate_pv_single(
        amount: Decimal,
        rate: Decimal,
        periods: int,
    ) -> Decimal:
        """
        Calculate present value of a single future amount.

        Args:
            amount: Future amount
            rate: Periodic discount rate
            periods: Number of periods until payment

        Returns:
            Present value
        """
        if periods <= 0:
            return amount

        if rate == Decimal("0"):
            return amount

        pv = amount / ((Decimal("1") + rate) ** periods)
        return pv.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @staticmethod
    def get_periods_per_year(frequency: str) -> int:
        """Get number of payment periods per year."""
        frequencies = {
            "MONTHLY": 12,
            "QUARTERLY": 4,
            "SEMI_ANNUAL": 2,
            "ANNUAL": 1,
        }
        return frequencies.get(frequency.upper(), 12)

    @staticmethod
    def calculate_initial_liability(
        db: Session,
        contract: LeaseContract,
    ) -> LiabilityCalculationResult:
        """
        Calculate initial lease liability per IFRS 16.

        Components:
        1. PV of fixed lease payments
        2. PV of variable payments based on index/rate
        3. PV of residual value guarantees
        4. PV of purchase option (if reasonably certain)

        Args:
            db: Database session
            contract: Lease contract

        Returns:
            LiabilityCalculationResult with PV components
        """
        # Determine periodic rate
        periods_per_year = LeaseCalculationService.get_periods_per_year(
            contract.payment_frequency
        )
        periodic_rate = contract.discount_rate_used / Decimal(periods_per_year)

        # Total number of payment periods
        total_periods = contract.lease_term_months // (12 // periods_per_year)

        # 1. PV of fixed payments
        pv_fixed = LeaseCalculationService.calculate_pv(
            payment=contract.base_payment_amount,
            rate=periodic_rate,
            periods=total_periods,
            payment_timing=contract.payment_timing,
        )

        # 2. PV of variable payments (index-linked)
        # For initial measurement, use current index values
        pv_variable = Decimal("0")

        # 3. PV of residual value guarantee
        pv_residual = Decimal("0")
        if contract.residual_value_guarantee > 0:
            pv_residual = LeaseCalculationService.calculate_pv_single(
                amount=contract.residual_value_guarantee,
                rate=periodic_rate,
                periods=total_periods,
            )

        # 4. PV of purchase option (if reasonably certain)
        pv_purchase = Decimal("0")
        if contract.purchase_reasonably_certain and contract.purchase_option_price:
            pv_purchase = LeaseCalculationService.calculate_pv_single(
                amount=contract.purchase_option_price,
                rate=periodic_rate,
                periods=total_periods,
            )

        total_liability = pv_fixed + pv_variable + pv_residual + pv_purchase

        # Calculate total undiscounted payments
        total_payments = contract.base_payment_amount * Decimal(total_periods)
        if contract.residual_value_guarantee:
            total_payments += contract.residual_value_guarantee
        if contract.purchase_reasonably_certain and contract.purchase_option_price:
            total_payments += contract.purchase_option_price

        total_interest = total_payments - total_liability

        # Calculate current vs non-current portion
        # Current portion = payments due within 12 months
        periods_in_year = min(periods_per_year, total_periods)
        first_year_payments = contract.base_payment_amount * Decimal(periods_in_year)

        # Rough estimate of current portion (principal portion of first year payments)
        # More accurate would require full amortization schedule
        if total_periods > 0:
            avg_principal_per_period = total_liability / Decimal(total_periods)
            current_portion = min(
                avg_principal_per_period * Decimal(periods_in_year),
                total_liability,
            )
        else:
            current_portion = total_liability

        non_current_portion = total_liability - current_portion

        return LiabilityCalculationResult(
            total_liability=total_liability,
            pv_fixed_payments=pv_fixed,
            pv_variable_payments=pv_variable,
            pv_residual_guarantee=pv_residual,
            pv_purchase_option=pv_purchase,
            current_portion=current_portion,
            non_current_portion=non_current_portion,
            total_payments=total_payments,
            total_interest=total_interest,
        )

    @staticmethod
    def generate_amortization_schedule(
        db: Session,
        lease_id: UUID,
    ) -> list[PaymentScheduleEntry]:
        """
        Generate full amortization schedule for a lease.

        Args:
            db: Database session
            lease_id: Lease to generate schedule for

        Returns:
            List of PaymentScheduleEntry for each payment period
        """
        from dateutil.relativedelta import relativedelta

        ls_id = coerce_uuid(lease_id)

        contract = db.get(LeaseContract, ls_id)
        if not contract:
            raise HTTPException(status_code=404, detail="Lease contract not found")

        liability = (
            db.query(LeaseLiability)
            .filter(LeaseLiability.lease_id == ls_id)
            .first()
        )

        if not liability:
            raise HTTPException(status_code=404, detail="Lease liability not found")

        # Calculate schedule parameters
        periods_per_year = LeaseCalculationService.get_periods_per_year(
            contract.payment_frequency
        )
        periodic_rate = contract.discount_rate_used / Decimal(periods_per_year)
        total_periods = contract.lease_term_months // (12 // periods_per_year)

        # Determine period increment
        if contract.payment_frequency == "MONTHLY":
            period_delta = relativedelta(months=1)
        elif contract.payment_frequency == "QUARTERLY":
            period_delta = relativedelta(months=3)
        elif contract.payment_frequency == "SEMI_ANNUAL":
            period_delta = relativedelta(months=6)
        else:
            period_delta = relativedelta(years=1)

        schedule = []
        opening_balance = liability.initial_liability_amount
        payment_date = contract.commencement_date

        for period in range(1, total_periods + 1):
            # For advance payments, first payment reduces principal immediately
            if contract.payment_timing == "ADVANCE" and period == 1:
                interest = Decimal("0")
            else:
                interest = (opening_balance * periodic_rate).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

            principal = contract.base_payment_amount - interest
            closing_balance = opening_balance - principal

            # Handle rounding on final payment
            if period == total_periods:
                principal = opening_balance
                closing_balance = Decimal("0")

            entry = PaymentScheduleEntry(
                period=period,
                payment_date=payment_date,
                opening_balance=opening_balance,
                payment_amount=contract.base_payment_amount,
                interest_expense=interest,
                principal_reduction=principal,
                closing_balance=max(Decimal("0"), closing_balance),
            )
            schedule.append(entry)

            opening_balance = closing_balance
            payment_date = payment_date + period_delta

        return schedule

    @staticmethod
    def calculate_interest_accrual(
        db: Session,
        lease_id: UUID,
        accrual_date: date,
    ) -> InterestAccrualResult:
        """
        Calculate interest accrual for a period.

        Args:
            db: Database session
            lease_id: Lease to accrue interest for
            accrual_date: Date of accrual

        Returns:
            InterestAccrualResult with interest calculation
        """
        ls_id = coerce_uuid(lease_id)

        contract = db.get(LeaseContract, ls_id)
        if not contract:
            raise HTTPException(status_code=404, detail="Lease contract not found")

        liability = (
            db.query(LeaseLiability)
            .filter(LeaseLiability.lease_id == ls_id)
            .first()
        )

        if not liability:
            raise HTTPException(status_code=404, detail="Lease liability not found")

        # Calculate monthly rate
        monthly_rate = contract.discount_rate_used / Decimal("12")

        # Calculate interest on current balance
        interest = (liability.current_liability_balance * monthly_rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        closing_balance = liability.current_liability_balance + interest

        return InterestAccrualResult(
            interest_amount=interest,
            opening_balance=liability.current_liability_balance,
            closing_balance=closing_balance,
            accrual_date=accrual_date,
        )

    @staticmethod
    def calculate_rou_depreciation(
        db: Session,
        lease_id: UUID,
        periods: int = 1,
    ) -> Decimal:
        """
        Calculate ROU asset depreciation for period(s).

        Args:
            db: Database session
            lease_id: Lease to depreciate
            periods: Number of months to depreciate

        Returns:
            Depreciation amount
        """
        ls_id = coerce_uuid(lease_id)

        asset = (
            db.query(LeaseAsset)
            .filter(LeaseAsset.lease_id == ls_id)
            .first()
        )

        if not asset:
            raise HTTPException(status_code=404, detail="Lease asset not found")

        if asset.useful_life_months <= 0:
            return Decimal("0")

        # Straight-line depreciation
        depreciable_amount = asset.initial_rou_asset_value - asset.residual_value
        monthly_depreciation = depreciable_amount / Decimal(asset.useful_life_months)

        depreciation = (monthly_depreciation * Decimal(periods)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Don't exceed remaining depreciable amount
        max_depreciation = asset.carrying_amount - asset.residual_value
        if max_depreciation <= 0:
            return Decimal("0")

        return min(depreciation, max_depreciation)


# Module-level singleton instance
lease_calculation_service = LeaseCalculationService()
