"""
APAgingService - Accounts Payable aging analysis.

Generates aging snapshots and provides aging analysis for AP management.
"""

from __future__ import annotations

import builtins
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models.finance.ap.ap_aging_snapshot import APAgingSnapshot
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.services.common import coerce_uuid
from app.services.finance.common.aging_helper import (
    AGING_BUCKETS,
    BUCKET_ATTRS,
    compute_aging_totals,
)
from app.services.finance.platform.org_context import org_context_service
from app.services.response import ListResponseMixin

logger = logging.getLogger(__name__)


@dataclass
class SupplierAgingSummary:
    """Aging summary for a single supplier."""

    supplier_id: UUID
    supplier_code: str
    supplier_name: str
    currency_code: str
    current: Decimal  # 0-30 days
    days_31_60: Decimal
    days_61_90: Decimal
    over_90: Decimal
    total_outstanding: Decimal
    invoice_count: int


@dataclass
class OrganizationAgingSummary:
    """Aging summary for the entire organization."""

    as_of_date: date
    currency_code: str
    current: Decimal
    days_31_60: Decimal
    days_61_90: Decimal
    over_90: Decimal
    total_outstanding: Decimal
    supplier_count: int
    invoice_count: int


class APAgingService(ListResponseMixin):
    """
    Service for AP aging analysis and snapshot generation.

    Provides aging reports by supplier, bucket analysis, and
    point-in-time snapshots for historical reporting.
    """

    AGING_BUCKETS = AGING_BUCKETS

    @staticmethod
    def calculate_supplier_aging(
        db: Session,
        organization_id: UUID,
        supplier_id: UUID,
        as_of_date: date | None = None,
    ) -> SupplierAgingSummary:
        """
        Calculate aging for a single supplier.

        Args:
            db: Database session
            organization_id: Organization scope
            supplier_id: Supplier to analyze
            as_of_date: Date for aging calculation (default: today)

        Returns:
            SupplierAgingSummary with bucket totals
        """
        org_id = coerce_uuid(organization_id)
        sup_id = coerce_uuid(supplier_id)
        ref_date = as_of_date or date.today()

        supplier = db.get(Supplier, sup_id)
        if not supplier or supplier.organization_id != org_id:
            raise ValueError("Supplier not found")

        # Get outstanding invoices
        outstanding_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        invoices = list(
            db.scalars(
                select(SupplierInvoice).where(
                    and_(
                        SupplierInvoice.supplier_id == sup_id,
                        SupplierInvoice.organization_id == org_id,
                        SupplierInvoice.status.in_(outstanding_statuses),
                    )
                )
            ).all()
        )

        current, days_31_60, days_61_90, over_90 = compute_aging_totals(
            invoices,
            ref_date,
            due_date=lambda inv: inv.due_date,
            balance=lambda inv: inv.balance_due,
        )

        return SupplierAgingSummary(
            supplier_id=supplier.supplier_id,
            supplier_code=supplier.supplier_code,
            supplier_name=supplier.legal_name,
            currency_code=supplier.currency_code,
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
        as_of_date: date | None = None,
    ) -> OrganizationAgingSummary:
        """
        Calculate aging summary for entire organization.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date for aging calculation

        Returns:
            OrganizationAgingSummary with totals
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()

        outstanding_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        invoices = list(
            db.scalars(
                select(SupplierInvoice).where(
                    and_(
                        SupplierInvoice.organization_id == org_id,
                        SupplierInvoice.status.in_(outstanding_statuses),
                    )
                )
            ).all()
        )

        supplier_ids = {inv.supplier_id for inv in invoices}
        current, days_31_60, days_61_90, over_90 = compute_aging_totals(
            invoices,
            ref_date,
            due_date=lambda inv: inv.due_date,
            balance=lambda inv: inv.balance_due,
        )

        # Get organization's functional currency
        functional_currency = org_context_service.get_functional_currency(db, org_id)

        return OrganizationAgingSummary(
            as_of_date=ref_date,
            currency_code=functional_currency,
            current=current,
            days_31_60=days_31_60,
            days_61_90=days_61_90,
            over_90=over_90,
            total_outstanding=current + days_31_60 + days_61_90 + over_90,
            supplier_count=len(supplier_ids),
            invoice_count=len(invoices),
        )

    @staticmethod
    def get_aging_by_supplier(
        db: Session,
        organization_id: UUID,
        as_of_date: date | None = None,
        min_balance: Decimal | None = None,
    ) -> list[SupplierAgingSummary]:
        """
        Get aging breakdown by supplier.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Date for aging calculation
            min_balance: Only include suppliers with balance >= this amount

        Returns:
            List of SupplierAgingSummary sorted by total outstanding
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()

        # Get all active suppliers with outstanding invoices
        outstanding_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        supplier_ids = list(
            db.scalars(
                select(SupplierInvoice.supplier_id)
                .where(
                    and_(
                        SupplierInvoice.organization_id == org_id,
                        SupplierInvoice.status.in_(outstanding_statuses),
                    )
                )
                .distinct()
            ).all()
        )

        results = []
        for sup_id in supplier_ids:
            try:
                summary = APAgingService.calculate_supplier_aging(
                    db, org_id, sup_id, ref_date
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
        as_of_date: date | None = None,
        created_by_user_id: UUID | None = None,
    ) -> builtins.list[APAgingSnapshot]:
        """
        Create point-in-time aging snapshot for all suppliers.

        Args:
            db: Database session
            organization_id: Organization scope
            fiscal_period_id: Fiscal period for the snapshot
            as_of_date: Date for snapshot
            created_by_user_id: User creating snapshot

        Returns:
            List of created APAgingSnapshot records
        """
        org_id = coerce_uuid(organization_id)
        period_id = coerce_uuid(fiscal_period_id)
        ref_date = as_of_date or date.today()

        # Get aging by supplier
        supplier_aging = APAgingService.get_aging_by_supplier(db, org_id, ref_date)

        snapshots = []
        bucket_mapping = BUCKET_ATTRS

        for aging in supplier_aging:
            # Create one snapshot record per aging bucket per supplier
            for bucket_name, attr_name in bucket_mapping:
                amount = getattr(aging, attr_name)
                if amount > 0:
                    snapshot = APAgingSnapshot(
                        organization_id=org_id,
                        fiscal_period_id=period_id,
                        snapshot_date=ref_date,
                        supplier_id=aging.supplier_id,
                        aging_bucket=bucket_name,
                        amount_functional=amount,
                        invoice_count=aging.invoice_count
                        if bucket_name == "Current"
                        else 0,
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
        as_of_date: date | None = None,
        min_days_overdue: int = 1,
        supplier_id: UUID | None = None,
    ) -> list[SupplierInvoice]:
        """
        Get overdue invoices.

        Args:
            db: Database session
            organization_id: Organization scope
            as_of_date: Reference date for overdue calculation
            min_days_overdue: Minimum days overdue to include
            supplier_id: Filter by specific supplier

        Returns:
            List of overdue SupplierInvoice objects
        """
        org_id = coerce_uuid(organization_id)
        ref_date = as_of_date or date.today()
        cutoff_date = ref_date

        outstanding_statuses = [
            SupplierInvoiceStatus.POSTED,
            SupplierInvoiceStatus.PARTIALLY_PAID,
        ]

        stmt = select(SupplierInvoice).where(
            and_(
                SupplierInvoice.organization_id == org_id,
                SupplierInvoice.status.in_(outstanding_statuses),
                SupplierInvoice.due_date < cutoff_date,
            )
        )

        if supplier_id:
            stmt = stmt.where(SupplierInvoice.supplier_id == coerce_uuid(supplier_id))

        invoices = list(db.scalars(stmt.order_by(SupplierInvoice.due_date)).all())

        # Filter by min days overdue
        result = []
        for inv in invoices:
            days_overdue = (ref_date - inv.due_date).days
            if days_overdue >= min_days_overdue:
                result.append(inv)

        return result

    @staticmethod
    def list(
        db: Session,
        organization_id: str | None = None,
        supplier_id: str | None = None,
        snapshot_date: date | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[APAgingSnapshot]:
        """
        List aging snapshots.

        Args:
            db: Database session
            organization_id: Filter by organization
            supplier_id: Filter by supplier
            snapshot_date: Filter by snapshot date
            limit: Maximum results
            offset: Pagination offset

        Returns:
            List of APAgingSnapshot objects
        """
        stmt = select(APAgingSnapshot)

        if organization_id:
            stmt = stmt.where(
                APAgingSnapshot.organization_id == coerce_uuid(organization_id)
            )

        if supplier_id:
            stmt = stmt.where(APAgingSnapshot.supplier_id == coerce_uuid(supplier_id))

        if snapshot_date:
            stmt = stmt.where(APAgingSnapshot.snapshot_date == snapshot_date)

        stmt = stmt.order_by(APAgingSnapshot.snapshot_date.desc())
        return list(db.scalars(stmt.limit(limit).offset(offset)).all())


# Module-level singleton instance
ap_aging_service = APAgingService()
