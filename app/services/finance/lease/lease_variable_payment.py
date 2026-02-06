"""
LeaseVariablePaymentService - IFRS 16 Variable Payments and Index Adjustments.

Manages variable lease payments, index/rate adjustments, and related remeasurements.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.lease.lease_asset import LeaseAsset
from app.models.finance.lease.lease_contract import LeaseContract, LeaseStatus
from app.models.finance.lease.lease_liability import LeaseLiability
from app.models.finance.lease.lease_payment_schedule import (
    LeasePaymentSchedule,
    PaymentStatus,
)
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class VariablePaymentInput:
    """Input for recording a variable payment."""

    schedule_id: UUID
    variable_amount: Decimal
    description: Optional[str] = None


@dataclass
class IndexAdjustmentInput:
    """Input for applying an index adjustment."""

    lease_id: UUID
    adjustment_date: date
    fiscal_period_id: UUID
    new_index_value: Decimal
    base_index_value: Decimal
    description: Optional[str] = None


@dataclass
class IndexAdjustmentResult:
    """Result of an index adjustment."""

    success: bool
    payments_adjusted: int = 0
    liability_adjustment: Decimal = Decimal("0")
    asset_adjustment: Decimal = Decimal("0")
    message: str = ""


class LeaseVariablePaymentService(ListResponseMixin):
    """
    Service for IFRS 16 variable payments and index adjustments.

    Handles:
    - Variable lease payments that depend on an index or rate (IFRS 16.42)
    - Remeasurement when there is a change in the index (IFRS 16.43)
    - Payments that depend on sales/usage (expensed as incurred)
    """

    @staticmethod
    def record_variable_payment(
        db: Session,
        organization_id: UUID,
        input: VariablePaymentInput,
    ) -> LeasePaymentSchedule:
        """
        Record a variable payment on a scheduled payment.

        Variable payments that do not depend on an index/rate
        are recognized in profit or loss as incurred (IFRS 16.38(b)).

        Args:
            db: Database session
            organization_id: Organization scope
            input: Variable payment input

        Returns:
            Updated LeasePaymentSchedule
        """
        org_id = coerce_uuid(organization_id)
        schedule_id = coerce_uuid(input.schedule_id)

        schedule = (
            db.query(LeasePaymentSchedule)
            .filter(LeasePaymentSchedule.schedule_id == schedule_id)
            .first()
        )

        if not schedule:
            raise HTTPException(status_code=404, detail="Payment schedule not found")

        if schedule.status == PaymentStatus.PAID:
            raise HTTPException(
                status_code=400,
                detail="Cannot add variable payment to already paid schedule",
            )

        schedule.variable_payment = input.variable_amount
        schedule.total_payment = (
            schedule.principal_portion
            + schedule.interest_portion
            + input.variable_amount
        )

        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def apply_index_adjustment(
        db: Session,
        organization_id: UUID,
        input: IndexAdjustmentInput,
        adjusted_by_user_id: UUID,
    ) -> IndexAdjustmentResult:
        """
        Apply an index/rate adjustment to a lease.

        Per IFRS 16.42, when payments are adjusted for changes in an index
        or rate, the lease liability is remeasured by discounting the
        revised lease payments.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Index adjustment input
            adjusted_by_user_id: User making adjustment

        Returns:
            IndexAdjustmentResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        lease_id = coerce_uuid(input.lease_id)

        # Load contract
        contract = (
            db.query(LeaseContract)
            .filter(
                LeaseContract.lease_id == lease_id,
                LeaseContract.organization_id == org_id,
            )
            .first()
        )

        if not contract:
            return IndexAdjustmentResult(
                success=False, message="Lease contract not found"
            )

        if contract.status != LeaseStatus.ACTIVE:
            return IndexAdjustmentResult(
                success=False,
                message=f"Cannot adjust index for lease in {contract.status.value} status",
            )

        # Load liability and asset
        liability = (
            db.query(LeaseLiability).filter(LeaseLiability.lease_id == lease_id).first()
        )

        asset = db.query(LeaseAsset).filter(LeaseAsset.lease_id == lease_id).first()

        if not liability or not asset:
            return IndexAdjustmentResult(
                success=False, message="Lease liability and asset not found"
            )

        # Calculate index adjustment ratio
        adjustment_ratio = input.new_index_value / input.base_index_value

        # Get future scheduled payments
        future_payments = (
            db.query(LeasePaymentSchedule)
            .filter(
                LeasePaymentSchedule.lease_id == lease_id,
                LeasePaymentSchedule.payment_date >= input.adjustment_date,
                LeasePaymentSchedule.status != PaymentStatus.PAID,
            )
            .all()
        )

        if not future_payments:
            return IndexAdjustmentResult(
                success=False, message="No future payments to adjust"
            )

        # Liability balance before adjustment
        liability_before = liability.current_liability_balance

        # Adjust each future payment
        total_adjustment = Decimal("0")
        for payment in future_payments:
            old_total = payment.total_payment
            new_total = (
                payment.principal_portion + payment.interest_portion
            ) * adjustment_ratio

            payment.total_payment = new_total
            payment.index_adjustment_amount = new_total - old_total
            payment.is_index_adjusted = True

            total_adjustment += payment.index_adjustment_amount

        # Recalculate liability (PV of revised payments)
        # Simplified: adjust by the same ratio
        new_liability = liability_before * adjustment_ratio
        liability_adjustment = new_liability - liability_before

        # Update liability balance
        liability.current_liability_balance = new_liability

        # Adjust ROU asset by the same amount (IFRS 16.42)
        asset.carrying_amount += liability_adjustment
        asset.modification_adjustments += liability_adjustment

        db.commit()

        return IndexAdjustmentResult(
            success=True,
            payments_adjusted=len(future_payments),
            liability_adjustment=liability_adjustment,
            asset_adjustment=liability_adjustment,
            message=f"Index adjustment applied to {len(future_payments)} payments",
        )

    @staticmethod
    def get_scheduled_payments(
        db: Session,
        lease_id: UUID,
        include_paid: bool = False,
    ) -> list[LeasePaymentSchedule]:
        """
        Get scheduled payments for a lease.

        Args:
            db: Database session
            lease_id: Lease ID
            include_paid: Include paid payments

        Returns:
            List of LeasePaymentSchedule
        """
        lease_id = coerce_uuid(lease_id)

        query = db.query(LeasePaymentSchedule).filter(
            LeasePaymentSchedule.lease_id == lease_id
        )

        if not include_paid:
            query = query.filter(LeasePaymentSchedule.status != PaymentStatus.PAID)

        return query.order_by(LeasePaymentSchedule.payment_number).all()

    @staticmethod
    def get_variable_payment_summary(
        db: Session,
        organization_id: UUID,
        lease_id: Optional[UUID] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
    ) -> dict:
        """
        Get summary of variable payments.

        Args:
            db: Database session
            organization_id: Organization scope
            lease_id: Optional filter by lease
            from_date: Optional start date
            to_date: Optional end date

        Returns:
            Summary dictionary
        """
        org_id = coerce_uuid(organization_id)

        query = (
            db.query(LeasePaymentSchedule)
            .join(
                LeaseContract, LeasePaymentSchedule.lease_id == LeaseContract.lease_id
            )
            .filter(
                LeaseContract.organization_id == org_id,
                LeasePaymentSchedule.variable_payment > 0,
            )
        )

        if lease_id:
            query = query.filter(LeasePaymentSchedule.lease_id == coerce_uuid(lease_id))

        if from_date:
            query = query.filter(LeasePaymentSchedule.payment_date >= from_date)

        if to_date:
            query = query.filter(LeasePaymentSchedule.payment_date <= to_date)

        payments = query.all()

        total_variable = sum(p.variable_payment for p in payments)
        total_index_adjustment = sum(p.index_adjustment_amount for p in payments)

        return {
            "payment_count": len(payments),
            "total_variable_payments": str(total_variable),
            "total_index_adjustments": str(total_index_adjustment),
            "combined_total": str(total_variable + total_index_adjustment),
        }

    @staticmethod
    def mark_payment_paid(
        db: Session,
        schedule_id: UUID,
        actual_payment_date: date,
        actual_payment_amount: Decimal,
        payment_reference: Optional[UUID] = None,
    ) -> LeasePaymentSchedule:
        """
        Mark a scheduled payment as paid.

        Args:
            db: Database session
            schedule_id: Schedule ID
            actual_payment_date: Date paid
            actual_payment_amount: Amount paid
            payment_reference: Reference to payment record

        Returns:
            Updated LeasePaymentSchedule
        """
        schedule_id = coerce_uuid(schedule_id)

        schedule = (
            db.query(LeasePaymentSchedule)
            .filter(LeasePaymentSchedule.schedule_id == schedule_id)
            .first()
        )

        if not schedule:
            raise HTTPException(status_code=404, detail="Payment schedule not found")

        if schedule.status == PaymentStatus.PAID:
            raise HTTPException(
                status_code=400, detail="Payment is already marked as paid"
            )

        schedule.status = PaymentStatus.PAID
        schedule.actual_payment_date = actual_payment_date
        schedule.actual_payment_amount = actual_payment_amount
        schedule.payment_reference = payment_reference

        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def get_overdue_payments(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> list[LeasePaymentSchedule]:
        """
        Get overdue lease payments.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date to check against (defaults to today)

        Returns:
            List of overdue LeasePaymentSchedule
        """
        org_id = coerce_uuid(organization_id)
        check_date = as_of_date or date.today()

        # Mark overdue payments
        db.query(LeasePaymentSchedule).filter(
            LeasePaymentSchedule.payment_date < check_date,
            LeasePaymentSchedule.status == PaymentStatus.SCHEDULED,
        ).update({LeasePaymentSchedule.status: PaymentStatus.OVERDUE})
        db.commit()

        # Return overdue payments
        return (
            db.query(LeasePaymentSchedule)
            .join(
                LeaseContract, LeasePaymentSchedule.lease_id == LeaseContract.lease_id
            )
            .filter(
                LeaseContract.organization_id == org_id,
                LeasePaymentSchedule.status == PaymentStatus.OVERDUE,
            )
            .order_by(LeasePaymentSchedule.payment_date)
            .all()
        )


# Module-level instance
lease_variable_payment_service = LeaseVariablePaymentService()
