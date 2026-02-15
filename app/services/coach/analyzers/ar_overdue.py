from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.finance.ar.customer import Customer
from app.models.finance.ar.invoice import Invoice, InvoiceStatus
from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OverdueReceivablesSummary:
    overdue_invoice_count: int
    overdue_balance_fc: Decimal
    max_days_overdue: int
    currency_code: str


def _severity_for_overdue_receivables(
    overdue_invoice_count: int,
    overdue_balance_fc: Decimal,
    max_days_overdue: int,
) -> str:
    if overdue_invoice_count <= 0:
        return "INFO"
    if max_days_overdue >= 60:
        return "WARNING"
    if overdue_balance_fc >= Decimal("10000000"):
        # Heuristic: large functional-currency exposure.
        return "WARNING"
    return "ATTENTION"


class AROverdueAnalyzer:
    """
    Deterministic AR overdue analyzer (no LLM required).

    Produces an org-wide Finance insight for overdue receivables based on invoices.
    """

    OUTSTANDING_STATUSES: tuple[InvoiceStatus, ...] = (
        InvoiceStatus.POSTED,
        InvoiceStatus.PARTIALLY_PAID,
        InvoiceStatus.OVERDUE,
        InvoiceStatus.DISPUTED,
    )

    def __init__(self, db: Session) -> None:
        self.db = db

    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero overdue AR (nothing to report)."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh, value = metric_is_fresh(
            self.db, organization_id, "cash_flow.ar_overdue_total"
        )
        if fresh and value is not None and value <= 0:
            logger.debug(
                "AR fast-path: MetricStore shows zero overdue AR, skipping detail query"
            )
            return True
        return False

    def overdue_summary(self, organization_id: UUID) -> OverdueReceivablesSummary:
        org = self.db.scalar(
            select(Organization).where(Organization.organization_id == organization_id)
        )
        currency_code = org.functional_currency_code if org else "NGN"

        today = date.today()
        # PostgreSQL: date - date returns integer days natively
        days_overdue_expr = func.extract(
            "day", func.age(func.current_date(), Invoice.due_date)
        )
        balance_due = Invoice.total_amount - Invoice.amount_paid
        ratio = balance_due / func.nullif(Invoice.total_amount, 0)
        balance_due_fc = Invoice.functional_currency_amount * func.coalesce(ratio, 0)

        stmt = (
            select(
                func.count().label("cnt"),
                func.coalesce(func.sum(balance_due_fc), 0).label("sum_fc"),
                func.coalesce(func.max(days_overdue_expr), 0).label("max_days"),
            )
            .where(
                Invoice.organization_id == organization_id,
                Invoice.due_date < today,
                Invoice.status.in_(self.OUTSTANDING_STATUSES),
                balance_due > 0,
            )
            .select_from(Invoice)
        )
        row = self.db.execute(stmt).one()

        overdue_invoice_count = int(row.cnt or 0)
        overdue_balance_fc = Decimal(str(row.sum_fc or "0"))
        max_days_overdue = int(row.max_days or 0)

        return OverdueReceivablesSummary(
            overdue_invoice_count=overdue_invoice_count,
            overdue_balance_fc=overdue_balance_fc,
            max_days_overdue=max_days_overdue,
            currency_code=currency_code,
        )

    def top_overdue_customers(
        self,
        organization_id: UUID,
        limit: int = 5,
    ) -> list[dict]:
        today = date.today()
        balance_due = Invoice.total_amount - Invoice.amount_paid
        ratio = balance_due / func.nullif(Invoice.total_amount, 0)
        balance_due_fc = Invoice.functional_currency_amount * func.coalesce(ratio, 0)

        stmt = (
            select(
                Customer.customer_id,
                Customer.customer_code,
                func.coalesce(Customer.trading_name, Customer.legal_name).label("name"),
                func.coalesce(func.sum(balance_due_fc), 0).label("sum_fc"),
                func.count().label("cnt"),
                func.min(Invoice.due_date).label("oldest_due"),
            )
            .join(Customer, Customer.customer_id == Invoice.customer_id)
            .where(
                Invoice.organization_id == organization_id,
                Customer.organization_id == organization_id,
                Invoice.due_date < today,
                Invoice.status.in_(self.OUTSTANDING_STATUSES),
                balance_due > 0,
            )
            .group_by(
                Customer.customer_id,
                Customer.customer_code,
                Customer.trading_name,
                Customer.legal_name,
            )
            .order_by(func.sum(balance_due_fc).desc())
            .limit(limit)
        )
        rows = self.db.execute(stmt).all()
        out: list[dict] = []
        for customer_id, customer_code, name, sum_fc, cnt, oldest_due in rows:
            out.append(
                {
                    "customer_id": str(customer_id),
                    "customer_code": str(customer_code),
                    "name": str(name),
                    "overdue_balance_fc": str(sum_fc),
                    "overdue_invoice_count": int(cnt or 0),
                    "oldest_due_date": oldest_due.isoformat() if oldest_due else None,
                }
            )
        return out

    def generate_overdue_receivables_insight(
        self,
        organization_id: UUID,
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None
        summary = self.overdue_summary(organization_id)
        if summary.overdue_invoice_count <= 0:
            return None

        severity = _severity_for_overdue_receivables(
            summary.overdue_invoice_count,
            summary.overdue_balance_fc,
            summary.max_days_overdue,
        )
        top_customers = self.top_overdue_customers(organization_id, limit=5)

        title = "Overdue receivables risk"
        summary_text = (
            f"{summary.overdue_invoice_count} overdue invoice(s) with an estimated "
            f"outstanding balance of {summary.currency_code} {summary.overdue_balance_fc}. "
            f"Oldest is {summary.max_days_overdue} day(s) overdue."
        )
        coaching_action = (
            "Focus collections on the top overdue customers and resolve any disputed invoices. "
            "If needed, enforce credit holds on repeat late payers."
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
            data_sources={"ar.invoice": summary.overdue_invoice_count},
            evidence={
                "currency_code": summary.currency_code,
                "overdue_invoice_count": summary.overdue_invoice_count,
                "overdue_balance_fc": str(summary.overdue_balance_fc),
                "max_days_overdue": summary.max_days_overdue,
                "top_overdue_customers": top_customers,
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
                CoachInsight.title == "Overdue receivables risk",
            )
        )

        insight = self.generate_overdue_receivables_insight(organization_id)
        if not insight:
            return 0
        self.db.add(insight)
        self.db.flush()
        return 1
