from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.coach.insight import CoachInsight
from app.models.finance.ap.supplier import Supplier
from app.models.finance.ap.supplier_invoice import (
    SupplierInvoice,
    SupplierInvoiceStatus,
)
from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PayablesDueSummary:
    due_7d_invoice_count: int
    due_7d_balance_fc: Decimal
    overdue_invoice_count: int
    overdue_balance_fc: Decimal
    currency_code: str


def _severity_for_payables(overdue_count: int, overdue_fc: Decimal) -> str:
    if overdue_count <= 0:
        return "ATTENTION"
    if overdue_fc >= Decimal("10000000") or overdue_count >= 20:
        return "WARNING"
    return "ATTENTION"


class APDueAnalyzer:
    """
    Deterministic AP analyzer: payables due soon + overdue.
    """

    OUTSTANDING_STATUSES: tuple[SupplierInvoiceStatus, ...] = (
        SupplierInvoiceStatus.POSTED,
        SupplierInvoiceStatus.PARTIALLY_PAID,
        SupplierInvoiceStatus.APPROVED,
        SupplierInvoiceStatus.DISPUTED,
        SupplierInvoiceStatus.ON_HOLD,
    )

    def __init__(self, db: Session) -> None:
        self.db = db

    def _currency_code(self, organization_id: UUID) -> str:
        org = self.db.scalar(
            select(Organization).where(Organization.organization_id == organization_id)
        )
        return (
            org.functional_currency_code
            if org
            else settings.default_functional_currency_code
        )

    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore says AP due 7d total is zero (nothing to report)."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "cash_flow.ap_due_7d_total"
        )
        if fresh and value is not None and value <= 0:
            logger.debug(
                "AP fast-path: MetricStore shows zero AP due, skipping detail query"
            )
            return True
        return False

    def due_summary(self, organization_id: UUID) -> PayablesDueSummary:
        currency_code = self._currency_code(organization_id)
        today = date.today()
        due_7 = today + timedelta(days=7)

        balance_due = SupplierInvoice.total_amount - SupplierInvoice.amount_paid
        ratio = balance_due / func.nullif(SupplierInvoice.total_amount, 0)
        balance_due_fc = SupplierInvoice.functional_currency_amount * func.coalesce(
            ratio, 0
        )

        due_stmt = (
            select(
                func.count().label("cnt"),
                func.coalesce(func.sum(balance_due_fc), 0).label("sum_fc"),
            )
            .where(
                SupplierInvoice.organization_id == organization_id,
                SupplierInvoice.status.in_(self.OUTSTANDING_STATUSES),
                SupplierInvoice.due_date >= today,
                SupplierInvoice.due_date <= due_7,
                balance_due > 0,
            )
            .select_from(SupplierInvoice)
        )
        due_row = self.db.execute(due_stmt).one()

        overdue_stmt = (
            select(
                func.count().label("cnt"),
                func.coalesce(func.sum(balance_due_fc), 0).label("sum_fc"),
            )
            .where(
                SupplierInvoice.organization_id == organization_id,
                SupplierInvoice.status.in_(self.OUTSTANDING_STATUSES),
                SupplierInvoice.due_date < today,
                balance_due > 0,
            )
            .select_from(SupplierInvoice)
        )
        over_row = self.db.execute(overdue_stmt).one()

        return PayablesDueSummary(
            due_7d_invoice_count=int(due_row.cnt or 0),
            due_7d_balance_fc=Decimal(str(due_row.sum_fc or "0")),
            overdue_invoice_count=int(over_row.cnt or 0),
            overdue_balance_fc=Decimal(str(over_row.sum_fc or "0")),
            currency_code=currency_code,
        )

    def top_due_suppliers(self, organization_id: UUID, limit: int = 5) -> list[dict]:
        today = date.today()
        due_7 = today + timedelta(days=7)

        balance_due = SupplierInvoice.total_amount - SupplierInvoice.amount_paid
        ratio = balance_due / func.nullif(SupplierInvoice.total_amount, 0)
        balance_due_fc = SupplierInvoice.functional_currency_amount * func.coalesce(
            ratio, 0
        )

        stmt = (
            select(
                Supplier.supplier_id,
                Supplier.supplier_code,
                func.coalesce(Supplier.trading_name, Supplier.legal_name).label("name"),
                func.coalesce(func.sum(balance_due_fc), 0).label("sum_fc"),
                func.count().label("cnt"),
                func.min(SupplierInvoice.due_date).label("nearest_due"),
            )
            .join(Supplier, Supplier.supplier_id == SupplierInvoice.supplier_id)
            .where(
                SupplierInvoice.organization_id == organization_id,
                Supplier.organization_id == organization_id,
                SupplierInvoice.status.in_(self.OUTSTANDING_STATUSES),
                SupplierInvoice.due_date >= today,
                SupplierInvoice.due_date <= due_7,
                balance_due > 0,
            )
            .group_by(
                Supplier.supplier_id,
                Supplier.supplier_code,
                Supplier.trading_name,
                Supplier.legal_name,
            )
            .order_by(func.sum(balance_due_fc).desc())
            .limit(limit)
        )
        rows = self.db.execute(stmt).all()
        out: list[dict] = []
        for supplier_id, supplier_code, name, sum_fc, cnt, nearest_due in rows:
            out.append(
                {
                    "supplier_id": str(supplier_id),
                    "supplier_code": str(supplier_code),
                    "name": str(name),
                    "due_balance_fc": str(sum_fc),
                    "due_invoice_count": int(cnt or 0),
                    "nearest_due_date": nearest_due.isoformat()
                    if nearest_due
                    else None,
                }
            )
        return out

    def generate_payables_due_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None
        summary = self.due_summary(organization_id)
        if summary.due_7d_invoice_count <= 0 and summary.overdue_invoice_count <= 0:
            return None

        severity = _severity_for_payables(
            summary.overdue_invoice_count, summary.overdue_balance_fc
        )
        top_suppliers = self.top_due_suppliers(organization_id, limit=5)

        title = "Payables due soon / overdue risk"
        summary_text = (
            f"Due in next 7 days: {summary.due_7d_invoice_count} invoice(s), "
            f"{summary.currency_code} {summary.due_7d_balance_fc}. "
            f"Overdue: {summary.overdue_invoice_count} invoice(s), "
            f"{summary.currency_code} {summary.overdue_balance_fc}."
        )
        coaching_action = (
            "Plan payments for the next 7 days to avoid penalties and supply disruption. "
            "Prioritize overdue items and critical suppliers."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="CASH_FLOW",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.9,
            data_sources={"ap.supplier_invoice": summary.due_7d_invoice_count},
            evidence={
                "currency_code": summary.currency_code,
                "due_7d_invoice_count": summary.due_7d_invoice_count,
                "due_7d_balance_fc": str(summary.due_7d_balance_fc),
                "overdue_invoice_count": summary.overdue_invoice_count,
                "overdue_balance_fc": str(summary.overdue_balance_fc),
                "top_due_suppliers": top_suppliers,
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def upsert_daily_org_insights(self, organization_id: UUID) -> int:
        today = date.today()
        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "CASH_FLOW",
                CoachInsight.audience == "FINANCE",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title == "Payables due soon / overdue risk",
            )
        )

        insight = self.generate_payables_due_insight(organization_id)
        if not insight:
            return 0
        self.db.add(insight)
        self.db.flush()
        return 1
