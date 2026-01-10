"""
ARAgingService - Accounts Receivable aging analysis.

Generates aging snapshots and provides aging analysis for AR management.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional
from uuid import UUID
import uuid as uuid_lib

from sqlalchemy import and_, func
from sqlalchemy.orm import Session

from app.models.ifrs.ar.customer import Customer
from app.models.ifrs.ar.invoice import (
    Invoice,
    InvoiceStatus,
)
from app.models.ifrs.ar.ar_aging_snapshot import ARAgingSnapshot
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin
from app.services.ifrs.platform.org_context import org_context_service


@dataclass
class AgingBucket:
    """Represents an aging bucket."""

    bucket_name: str
    min_days: int
    max_days: Optional[int]  # None for 90+ bucket
    amount: Decimal = Decimal("0")
    invoice_count: int = 0


@dataclass
class CustomerAgingSummary:
    """Aging summary for a single customer."""

    customer_id: UUID
    customer_code: str
    customer_name: str
    currency_code: str
    current: Decimal  # 0-30 days
    days_31_60: Decimal
    days_61_90: Decimal
    over_90: Decimal
    total_outstanding: Decimal
    invoice_count: int


@dataclass
class OrganizationARAgingSummary:
    """AR Aging summary for the entire organization."""

    as_of_date: date
    currency_code: str
    current: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    over_90: Decimal
    total_outstanding: Decimal
    customer_count: int
    invoice_count: int


class ARAgingService(ListResponseMixin):
    """
    Service for AR aging analysis and snapshot generation.

    Provides aging reports by customer, bucket analysis, and
    point-in-time snapshots for historical reporting.
    """

    # Standard aging buckets
    AGING_BUCKETS = [
        AgingBucket("Current", 0, 30),
        AgingBucket("31-60 Days", 31, 60),
        AgingBucket("61-90 Days", 61, 90),
        AgingBucket("Over 90 Days", 91, None),
    ]

    @staticmethod
    def calculate_customer_aging(
        db: Session,
        organization_id: UUID,
        customer_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> CustomerAgingSummary:
        """
        Calculate aging for a single customer.

        Args:
            db: Database session
            organization_id: Organization scope
            customer_id: Customer to analyze
            as_of_date: Date for aging calculation (default: today)

        Returns:
            CustomerAgingSummary with bucket totals
        """
        org_id = coerce_uuid(organization_id)
        cust_id = coerce_uuid(customer_id)
        ref_date = as_of_date or date.today()

        customer = db.get(Customer, cust_id)
        if not customer or customer.organization_id != org_id:
            raise ValueError("Customer not found")

        # Get outstanding invoices
        outstanding_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        invoices = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.customer_id == cust_id,
                    Invoice.organization_id == org_id,
                    Invoice.status.in_(outstanding_statuses),
                )
            )
            .all()
        )

        current = Decimal("0")
        days_31_60 = Decimal("0")
        days_61_90 = Decimal("0")
        over_90 = Decimal("0")

        for inv in invoices:
            days_overdue = (ref_date - inv.due_date).days
            balance = inv.balance_due

            if days_overdue <= 30:
                current += balance
            elif days_overdue <= 60:
                days_31_60 += balance
            elif days_overdue <= 90:
                days_61_90 += balance
            else:
                over_90 += balance

        return CustomerAgingSummary(
            customer_id=customer.customer_id,
            customer_code=customer.customer_code,
            customer_name=customer.legal_name,
            currency_code=customer.currency_code,
            current=current,
            days_31_60=days_31_60,
            days_61_90=days_61_90,
            over_90=over_90,
            total_outstanding=current + days_31_60 + days_61_90 + over_90,
            invoice_count=len(invoices),
        )

    @staticmethod
    def calculate_organization_aging(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
    ) -> OrganizationARAgingSummary:
        """
        Calculate aging summary for entire organization.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date for aging calculation

        Returns:
            OrganizationARAgingSummary with totals
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()

        outstanding_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        invoices = (
            db.query(Invoice)
            .filter(
                and_(
                    Invoice.organization_id == org_id,
                    Invoice.status.in_(outstanding_statuses),
                )
            )
            .all()
        )

        current = Decimal("0")
        days_31_60 = Decimal("0")
        days_61_90 = Decimal("0")
        over_90 = Decimal("0")
        customer_ids = set()

        for inv in invoices:
            days_overdue = (ref_date - inv.due_date).days
            balance = inv.balance_due
            customer_ids.add(inv.customer_id)

            if days_overdue <= 30:
                current += balance
            elif days_overdue <= 60:
                days_31_60 += balance
            elif days_overdue <= 90:
                days_61_90 += balance
            else:
                over_90 += balance

        # Get organization's functional currency
        functional_currency = org_context_service.get_functional_currency(db, org_id)

        return OrganizationARAgingSummary(
            as_of_date=ref_date,
            currency_code=functional_currency,
            current=current,
            days_31_60=days_31_60,
            days_61_90=days_61_90,
            over_90=over_90,
            total_outstanding=current + days_31_60 + days_61_90 + over_90,
            customer_count=len(customer_ids),
            invoice_count=len(invoices),
        )

    @staticmethod
    def get_aging_by_customer(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
        min_balance: Optional[Decimal] = None,
    ) -> list[CustomerAgingSummary]:
        """
        Get aging breakdown by customer.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date for aging calculation
            min_balance: Only include customers with balance >= this amount

        Returns:
            List of CustomerAgingSummary sorted by total outstanding
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()

        # Get all customers with outstanding invoices
        outstanding_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        customer_ids = (
            db.query(Invoice.customer_id)
            .filter(
                and_(
                    Invoice.organization_id == org_id,
                    Invoice.status.in_(outstanding_statuses),
                )
            )
            .distinct()
            .all()
        )

        results = []
        for (cust_id,) in customer_ids:
            try:
                summary = ARAgingService.calculate_customer_aging(
                    db, org_id, cust_id, ref_date
                )
                if min_balance is None or summary.total_outstanding >= min_balance:
                    results.append(summary)
            except ValueError:
                continue

        # Sort by total outstanding descending
        results.sort(key=lambda x: x.total_outstanding, reverse=True)
        return results

    @staticmethod
    def create_aging_snapshot(
        db: Session,
        organization_id: UUID,
        fiscal_period_id: UUID,
        as_of_date: Optional[date] = None,
        created_by_user_id: Optional[UUID] = None,
    ) -> list[ARAgingSnapshot]:
        """
        Create point-in-time aging snapshot for all customers.

        The AR aging snapshot model stores each bucket as a separate record.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period for the snapshot
            as_of_date: Date for snapshot
            created_by_user_id: User creating snapshot

        Returns:
            List of created ARAgingSnapshot records
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        ref_date = as_of_date or date.today()

        # Get aging by customer
        customer_aging = ARAgingService.get_aging_by_customer(
            db, org_id, ref_date
        )

        snapshots = []
        bucket_configs = [
            ("Current", "current"),
            ("31-60 Days", "days_31_60"),
            ("61-90 Days", "days_61_90"),
            ("Over 90 Days", "over_90"),
        ]

        for aging in customer_aging:
            for bucket_name, attr_name in bucket_configs:
                amount = getattr(aging, attr_name)
                if amount > Decimal("0"):
                    snapshot = ARAgingSnapshot(
                        organization_id=org_id,
                        fiscal_period_id=period_id,
                        snapshot_date=ref_date,
                        customer_id=aging.customer_id,
                        aging_bucket=bucket_name,
                        amount_functional=amount,
                        invoice_count=aging.invoice_count if bucket_name == "Current" else 0,
                        currency_code=aging.currency_code,
                        amount_original_currency=amount,
                    )
                    db.add(snapshot)
                    snapshots.append(snapshot)

        db.commit()
        return snapshots

    @staticmethod
    def get_overdue_invoices(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
        min_days_overdue: int = 1,
        customer_id: Optional[UUID] = None,
    ) -> list[Invoice]:
        """
        Get overdue invoices.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Reference date for overdue calculation
            min_days_overdue: Minimum days overdue to include
            customer_id: Filter by specific customer

        Returns:
            List of overdue Invoice objects
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()
        cutoff_date = ref_date

        outstanding_statuses = [
            InvoiceStatus.POSTED,
            InvoiceStatus.PARTIALLY_PAID,
            InvoiceStatus.OVERDUE,
        ]

        query = db.query(Invoice).filter(
            and_(
                Invoice.organization_id == org_id,
                Invoice.status.in_(outstanding_statuses),
                Invoice.due_date < cutoff_date,
            )
        )

        if customer_id:
            query = query.filter(
                Invoice.customer_id == coerce_uuid(customer_id)
            )

        invoices = query.order_by(Invoice.due_date).all()

        # Filter by min days overdue
        result = []
        for inv in invoices:
            days_overdue = (ref_date - inv.due_date).days
            if days_overdue >= min_days_overdue:
                result.append(inv)

        return result

    @staticmethod
    def get_high_risk_customers(
        db: Session,
        organization_id: UUID,
        as_of_date: Optional[date] = None,
        min_overdue_days: int = 60,
        min_overdue_amount: Optional[Decimal] = None,
    ) -> list[CustomerAgingSummary]:
        """
        Get customers with high-risk aging profiles.

        High risk is defined as having significant amounts in older buckets.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date for aging calculation
            min_overdue_days: Minimum days to consider high risk (default 60)
            min_overdue_amount: Minimum amount in overdue buckets

        Returns:
            List of CustomerAgingSummary for high-risk customers
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()

        all_customers = ARAgingService.get_aging_by_customer(db, org_id, ref_date)

        high_risk = []
        for aging in all_customers:
            # Calculate amount in older buckets
            if min_overdue_days <= 60:
                overdue_amount = aging.days_31_60 + aging.days_61_90 + aging.over_90
            elif min_overdue_days <= 90:
                overdue_amount = aging.days_61_90 + aging.over_90
            else:
                overdue_amount = aging.over_90

            if min_overdue_amount is None or overdue_amount >= min_overdue_amount:
                if overdue_amount > Decimal("0"):
                    high_risk.append(aging)

        # Sort by over_90 amount descending (highest risk first)
        high_risk.sort(key=lambda x: x.over_90, reverse=True)
        return high_risk

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        customer_id: Optional[str] = None,
        snapshot_date: Optional[date] = None,
        aging_bucket: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ARAgingSnapshot]:
        """
        List aging snapshots.

        Args:
            db: Database session
            organization_id: Filter by organization
            customer_id: Filter by customer
            snapshot_date: Filter by snapshot date
            aging_bucket: Filter by aging bucket name
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of ARAgingSnapshot objects
        """
        query = db.query(ARAgingSnapshot)

        if organization_id:
            query = query.filter(
                ARAgingSnapshot.organization_id == coerce_uuid(organization_id)
            )

        if customer_id:
            query = query.filter(
                ARAgingSnapshot.customer_id == coerce_uuid(customer_id)
            )

        if snapshot_date:
            query = query.filter(ARAgingSnapshot.snapshot_date == snapshot_date)

        if aging_bucket:
            query = query.filter(ARAgingSnapshot.aging_bucket == aging_bucket)

        query = query.order_by(ARAgingSnapshot.snapshot_date.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
ar_aging_service = ARAgingService()
