"""
ECLService - IFRS 9 Expected Credit Loss Calculation.

Manages ECL calculations for accounts receivable using simplified and general approaches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID
import uuid as uuid_lib

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.finance.ar.expected_credit_loss import (
    ExpectedCreditLoss,
    ECLMethodology,
    ECLStage,
)
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.ar.customer import Customer
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


# Default aging buckets and loss rates for simplified approach
DEFAULT_AGING_BUCKETS = {
    "CURRENT": {"days_from": 0, "days_to": 0, "default_rate": Decimal("0.005")},
    "1_30_DAYS": {"days_from": 1, "days_to": 30, "default_rate": Decimal("0.01")},
    "31_60_DAYS": {"days_from": 31, "days_to": 60, "default_rate": Decimal("0.025")},
    "61_90_DAYS": {"days_from": 61, "days_to": 90, "default_rate": Decimal("0.05")},
    "91_120_DAYS": {"days_from": 91, "days_to": 120, "default_rate": Decimal("0.10")},
    "OVER_120_DAYS": {"days_from": 121, "days_to": 9999, "default_rate": Decimal("0.25")},
}


@dataclass
class ECLAgingBucket:
    """Aging bucket for ECL calculation."""

    bucket_name: str
    gross_carrying_amount: Decimal
    loss_rate: Decimal
    ecl_amount: Decimal
    invoice_count: int = 0


@dataclass
class ECLCalculationInput:
    """Input for ECL calculation."""

    calculation_date: date
    fiscal_period_id: UUID
    methodology: ECLMethodology = ECLMethodology.SIMPLIFIED
    customer_id: Optional[UUID] = None
    portfolio_segment: Optional[str] = None
    forward_looking_adjustment: Decimal = Decimal("0")
    custom_loss_rates: Optional[dict[str, Decimal]] = None


@dataclass
class GeneralApproachInput:
    """Input for general approach ECL calculation."""

    calculation_date: date
    fiscal_period_id: UUID
    customer_id: UUID
    probability_of_default: Decimal
    loss_given_default: Decimal
    exposure_at_default: Decimal
    credit_risk_rating: str
    significant_increase_indicator: bool = False


@dataclass
class ECLResult:
    """Result of ECL calculation."""

    total_gross_carrying_amount: Decimal
    total_provision: Decimal
    provision_movement: Decimal
    buckets: list[ECLAgingBucket]
    calculation_date: date
    methodology: ECLMethodology
    ecl_records: list[ExpectedCreditLoss]


class ECLService(ListResponseMixin):
    """
    Service for IFRS 9 Expected Credit Loss calculations.

    Supports both simplified approach (provision matrix) and
    general approach (PD/LGD/EAD model).
    """

    @staticmethod
    def calculate_simplified(
        db: Session,
        organization_id: UUID,
        input: ECLCalculationInput,
        created_by_user_id: UUID,
    ) -> ECLResult:
        """
        Calculate ECL using the simplified approach (provision matrix).

        This approach is suitable for trade receivables without
        a significant financing component.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Calculation input
            created_by_user_id: User performing calculation

        Returns:
            ECLResult with calculated provisions
        """
        org_id = coerce_uuid(organization_id)
        fiscal_period_id = coerce_uuid(input.fiscal_period_id)

        # Get loss rates (custom or default)
        loss_rates = input.custom_loss_rates or {
            bucket: info["default_rate"]
            for bucket, info in DEFAULT_AGING_BUCKETS.items()
        }

        # Apply forward-looking adjustment
        for bucket in loss_rates:
            loss_rates[bucket] = loss_rates[bucket] * (
                Decimal("1") + input.forward_looking_adjustment
            )

        # Build query for outstanding invoices
        # Note: balance_due is a @property, so we compute it inline for SQL
        query = db.query(Invoice).filter(
            Invoice.organization_id == org_id,
            Invoice.status.in_([InvoiceStatus.POSTED, InvoiceStatus.PARTIALLY_PAID]),
            (Invoice.total_amount - Invoice.amount_paid) > 0,
        )

        if input.customer_id:
            query = query.filter(Invoice.customer_id == coerce_uuid(input.customer_id))

        invoices = query.all()

        # Calculate aging and group into buckets
        bucket_totals: dict[str, ECLAgingBucket] = {}
        for bucket_name in DEFAULT_AGING_BUCKETS:
            bucket_totals[bucket_name] = ECLAgingBucket(
                bucket_name=bucket_name,
                gross_carrying_amount=Decimal("0"),
                loss_rate=loss_rates.get(bucket_name, Decimal("0")),
                ecl_amount=Decimal("0"),
                invoice_count=0,
            )

        for invoice in invoices:
            days_past_due = (input.calculation_date - invoice.due_date).days
            if days_past_due < 0:
                days_past_due = 0

            # Determine bucket
            bucket_name = "CURRENT"
            for name, info in DEFAULT_AGING_BUCKETS.items():
                if info["days_from"] <= days_past_due <= info["days_to"]:
                    bucket_name = name
                    break

            bucket = bucket_totals[bucket_name]
            bucket.gross_carrying_amount += invoice.balance_due
            bucket.invoice_count += 1

        # Calculate ECL for each bucket
        ecl_records = []
        total_gross = Decimal("0")
        total_provision = Decimal("0")

        for bucket_name, bucket in bucket_totals.items():
            if bucket.gross_carrying_amount > 0:
                bucket.ecl_amount = bucket.gross_carrying_amount * bucket.loss_rate
                total_gross += bucket.gross_carrying_amount
                total_provision += bucket.ecl_amount

                # Create ECL record
                ecl_record = ExpectedCreditLoss(
                    organization_id=org_id,
                    calculation_date=input.calculation_date,
                    fiscal_period_id=fiscal_period_id,
                    methodology=ECLMethodology.SIMPLIFIED,
                    customer_id=coerce_uuid(input.customer_id) if input.customer_id else None,
                    portfolio_segment=input.portfolio_segment,
                    aging_bucket=bucket_name,
                    gross_carrying_amount=bucket.gross_carrying_amount,
                    historical_loss_rate=bucket.loss_rate,
                    forward_looking_adjustment=input.forward_looking_adjustment,
                    ecl_stage=ECLService._determine_stage(bucket_name),
                    provision_amount=bucket.ecl_amount,
                    calculation_details={
                        "invoice_count": bucket.invoice_count,
                        "loss_rate": str(bucket.loss_rate),
                        "fla": str(input.forward_looking_adjustment),
                    },
                )
                db.add(ecl_record)
                ecl_records.append(ecl_record)

        # Calculate provision movement (compare to prior period)
        prior_provision = ECLService._get_prior_provision(
            db, org_id, input.calculation_date, input.customer_id
        )
        provision_movement = total_provision - prior_provision

        db.commit()

        # Refresh records to get IDs
        for record in ecl_records:
            db.refresh(record)

        return ECLResult(
            total_gross_carrying_amount=total_gross,
            total_provision=total_provision,
            provision_movement=provision_movement,
            buckets=list(bucket_totals.values()),
            calculation_date=input.calculation_date,
            methodology=ECLMethodology.SIMPLIFIED,
            ecl_records=ecl_records,
        )

    @staticmethod
    def calculate_general(
        db: Session,
        organization_id: UUID,
        input: GeneralApproachInput,
        created_by_user_id: UUID,
    ) -> ExpectedCreditLoss:
        """
        Calculate ECL using the general approach (PD/LGD/EAD model).

        Uses three-stage impairment model based on credit risk changes.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Calculation input with PD/LGD/EAD
            created_by_user_id: User performing calculation

        Returns:
            Created ExpectedCreditLoss record
        """
        org_id = coerce_uuid(organization_id)
        customer_id = coerce_uuid(input.customer_id)
        fiscal_period_id = coerce_uuid(input.fiscal_period_id)

        # Verify customer exists
        customer = db.query(Customer).filter(
            Customer.customer_id == customer_id,
            Customer.organization_id == org_id,
        ).first()

        if not customer:
            raise HTTPException(status_code=404, detail="Customer not found")

        # Determine ECL stage based on SICR
        if input.significant_increase_indicator:
            # Stage 2 or 3 based on whether credit-impaired
            ecl_stage = ECLStage.STAGE_3 if input.probability_of_default > Decimal("0.5") else ECLStage.STAGE_2
        else:
            ecl_stage = ECLStage.STAGE_1

        # Calculate 12-month and lifetime ECL
        # ECL = PD × LGD × EAD
        ecl_12_month = input.probability_of_default * input.loss_given_default * input.exposure_at_default
        ecl_lifetime = ecl_12_month  # Simplified - in practice, would use lifetime PD

        # For Stage 2 and 3, use lifetime ECL
        if ecl_stage in [ECLStage.STAGE_2, ECLStage.STAGE_3]:
            provision_amount = ecl_lifetime
        else:
            provision_amount = ecl_12_month

        # Get prior provision for movement calculation
        prior = ECLService._get_prior_customer_provision(db, org_id, customer_id, input.calculation_date)
        provision_movement = provision_amount - prior

        ecl_record = ExpectedCreditLoss(
            organization_id=org_id,
            calculation_date=input.calculation_date,
            fiscal_period_id=fiscal_period_id,
            methodology=ECLMethodology.GENERAL,
            customer_id=customer_id,
            gross_carrying_amount=input.exposure_at_default,
            probability_of_default=input.probability_of_default,
            loss_given_default=input.loss_given_default,
            exposure_at_default=input.exposure_at_default,
            ecl_12_month=ecl_12_month,
            ecl_lifetime=ecl_lifetime,
            ecl_stage=ecl_stage,
            credit_risk_rating=input.credit_risk_rating,
            significant_increase_indicator=input.significant_increase_indicator,
            provision_amount=provision_amount,
            provision_movement=provision_movement,
            calculation_details={
                "pd": str(input.probability_of_default),
                "lgd": str(input.loss_given_default),
                "ead": str(input.exposure_at_default),
                "stage": ecl_stage.value,
            },
        )

        db.add(ecl_record)
        db.commit()
        db.refresh(ecl_record)

        return ecl_record

    @staticmethod
    def _determine_stage(aging_bucket: str) -> ECLStage:
        """Determine ECL stage based on aging bucket."""
        if aging_bucket in ["CURRENT", "1_30_DAYS"]:
            return ECLStage.STAGE_1
        elif aging_bucket in ["31_60_DAYS", "61_90_DAYS"]:
            return ECLStage.STAGE_2
        else:
            return ECLStage.STAGE_3

    @staticmethod
    def _get_prior_provision(
        db: Session,
        organization_id: UUID,
        current_date: date,
        customer_id: Optional[UUID] = None,
    ) -> Decimal:
        """Get the most recent prior provision amount."""
        query = db.query(func.sum(ExpectedCreditLoss.provision_amount)).filter(
            ExpectedCreditLoss.organization_id == organization_id,
            ExpectedCreditLoss.calculation_date < current_date,
        )

        if customer_id:
            query = query.filter(ExpectedCreditLoss.customer_id == coerce_uuid(customer_id))

        # Get the most recent calculation date
        subquery = db.query(func.max(ExpectedCreditLoss.calculation_date)).filter(
            ExpectedCreditLoss.organization_id == organization_id,
            ExpectedCreditLoss.calculation_date < current_date,
        )
        if customer_id:
            subquery = subquery.filter(ExpectedCreditLoss.customer_id == coerce_uuid(customer_id))

        max_date = subquery.scalar()
        if not max_date:
            return Decimal("0")

        result = db.query(func.sum(ExpectedCreditLoss.provision_amount)).filter(
            ExpectedCreditLoss.organization_id == organization_id,
            ExpectedCreditLoss.calculation_date == max_date,
        )
        if customer_id:
            result = result.filter(ExpectedCreditLoss.customer_id == coerce_uuid(customer_id))

        return result.scalar() or Decimal("0")

    @staticmethod
    def _get_prior_customer_provision(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
        current_date: date,
    ) -> Decimal:
        """Get the most recent prior provision for a specific customer."""
        result = db.query(ExpectedCreditLoss).filter(
            ExpectedCreditLoss.organization_id == organization_id,
            ExpectedCreditLoss.customer_id == customer_id,
            ExpectedCreditLoss.calculation_date < current_date,
        ).order_by(ExpectedCreditLoss.calculation_date.desc()).first()

        return result.provision_amount if result else Decimal("0")

    @staticmethod
    def get_provision_summary(
        db: Session,
        organization_id: UUID,
        as_of_date: date,
    ) -> dict[str, Any]:
        """
        Get a summary of ECL provisions as of a specific date.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date for summary

        Returns:
            Summary dictionary with stage breakdowns
        """
        org_id = coerce_uuid(organization_id)

        # Get most recent calculation on or before as_of_date
        max_date = db.query(func.max(ExpectedCreditLoss.calculation_date)).filter(
            ExpectedCreditLoss.organization_id == org_id,
            ExpectedCreditLoss.calculation_date <= as_of_date,
        ).scalar()

        if not max_date:
            return {
                "as_of_date": as_of_date.isoformat(),
                "total_gross_carrying_amount": "0",
                "total_provision": "0",
                "stage_breakdown": {},
            }

        # Get provisions by stage
        stage_data = db.query(
            ExpectedCreditLoss.ecl_stage,
            func.sum(ExpectedCreditLoss.gross_carrying_amount).label("gross"),
            func.sum(ExpectedCreditLoss.provision_amount).label("provision"),
        ).filter(
            ExpectedCreditLoss.organization_id == org_id,
            ExpectedCreditLoss.calculation_date == max_date,
        ).group_by(ExpectedCreditLoss.ecl_stage).all()

        stage_breakdown = {}
        total_gross = Decimal("0")
        total_provision = Decimal("0")

        for row in stage_data:
            stage_breakdown[row.ecl_stage.value] = {
                "gross_carrying_amount": str(row.gross),
                "provision": str(row.provision),
                "coverage_ratio": str(row.provision / row.gross if row.gross > 0 else 0),
            }
            total_gross += row.gross
            total_provision += row.provision

        return {
            "as_of_date": as_of_date.isoformat(),
            "calculation_date": max_date.isoformat(),
            "total_gross_carrying_amount": str(total_gross),
            "total_provision": str(total_provision),
            "coverage_ratio": str(total_provision / total_gross if total_gross > 0 else 0),
            "stage_breakdown": stage_breakdown,
        }

    @staticmethod
    def get(db: Session, ecl_id: str) -> Optional[ExpectedCreditLoss]:
        """Get an ECL record by ID."""
        return db.query(ExpectedCreditLoss).filter(
            ExpectedCreditLoss.ecl_id == coerce_uuid(ecl_id)
        ).first()

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        fiscal_period_id: Optional[str] = None,
        ecl_stage: Optional[ECLStage] = None,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ExpectedCreditLoss]:
        """
        List ECL records with filters.

        Args:
            db: Database session
            organization_id: Filter by organization
            customer_id: Filter by customer
            fiscal_period_id: Filter by fiscal period
            ecl_stage: Filter by stage
            from_date: Filter by calculation date from
            to_date: Filter by calculation date to
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of ExpectedCreditLoss records
        """
        query = db.query(ExpectedCreditLoss)

        if organization_id:
            query = query.filter(
                ExpectedCreditLoss.organization_id == coerce_uuid(organization_id)
            )

        if customer_id:
            query = query.filter(
                ExpectedCreditLoss.customer_id == coerce_uuid(customer_id)
            )

        if fiscal_period_id:
            query = query.filter(
                ExpectedCreditLoss.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        if ecl_stage:
            query = query.filter(ExpectedCreditLoss.ecl_stage == ecl_stage)

        if from_date:
            query = query.filter(ExpectedCreditLoss.calculation_date >= from_date)

        if to_date:
            query = query.filter(ExpectedCreditLoss.calculation_date <= to_date)

        return query.order_by(
            ExpectedCreditLoss.calculation_date.desc()
        ).offset(offset).limit(limit).all()


# Module-level instance
ecl_service = ECLService()
