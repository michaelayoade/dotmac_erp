"""Revenue & pipeline health analyzer (deterministic, no LLM required).

Monitors quote-to-cash conversion, customer concentration risk,
and sales pipeline health.
"""

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
from app.models.finance.ar.quote import Quote, QuoteStatus
from app.models.finance.ar.sales_order import SalesOrder, SOStatus

logger = logging.getLogger(__name__)

_ZERO = Decimal("0")


@dataclass(frozen=True)
class PipelineHealthSummary:
    open_quotes: int
    open_quote_value: Decimal
    expired_quotes: int
    conversion_rate_pct: Decimal  # quotes converted / total resolved
    open_sales_orders: int
    open_so_value: Decimal
    currency_code: str


@dataclass(frozen=True)
class CustomerConcentrationSummary:
    total_revenue_90d: Decimal
    top_customer_name: str | None
    top_customer_revenue: Decimal
    top_customer_pct: Decimal  # % of total
    top_3_pct: Decimal  # top 3 customers as % of total
    active_customer_count: int
    currency_code: str


def _severity_for_pipeline(expired: int, conversion_pct: Decimal) -> str:
    if expired >= 10:
        return "WARNING"
    if conversion_pct < 20:
        return "ATTENTION"
    return "INFO"


def _severity_for_concentration(top_customer_pct: Decimal) -> str:
    if top_customer_pct >= 50:
        return "WARNING"
    if top_customer_pct >= 30:
        return "ATTENTION"
    return "INFO"


class RevenueAnalyzer:
    """Deterministic revenue & pipeline health analyzer.

    Generates org-wide Finance/Executive insights for pipeline health
    and customer concentration risk.
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # MetricStore fast-path
    # ------------------------------------------------------------------
    def _quick_check_from_store(self, organization_id: UUID) -> bool:
        """Return True if MetricStore shows zero pipeline value and zero revenue."""
        from app.services.coach.analyzers import metric_is_fresh

        fresh_p, p_val = metric_is_fresh(
            self.db, organization_id, "revenue.pipeline_value"
        )
        fresh_r, r_val = metric_is_fresh(self.db, organization_id, "revenue.ytd_total")
        if fresh_p and fresh_r and (p_val or _ZERO) <= 0 and (r_val or _ZERO) <= 0:
            logger.debug("Revenue fast-path: zero pipeline and revenue, skipping")
            return True
        return False

    # ------------------------------------------------------------------
    # Core computations
    # ------------------------------------------------------------------
    def pipeline_health(self, organization_id: UUID) -> PipelineHealthSummary:
        from app.models.finance.core_org.organization import Organization

        org = self.db.scalar(
            select(Organization).where(Organization.organization_id == organization_id)
        )
        currency_code = org.functional_currency_code if org else "NGN"

        # Open quotes
        open_q = (
            select(
                func.count().label("cnt"),
                func.coalesce(func.sum(Quote.total_amount), 0).label("val"),
            )
            .select_from(Quote)
            .where(
                Quote.organization_id == organization_id,
                Quote.status.in_((QuoteStatus.DRAFT, QuoteStatus.SENT)),
            )
        )
        oq = self.db.execute(open_q).one()
        open_quotes = int(oq.cnt or 0)
        open_quote_value = Decimal(str(oq.val or "0"))

        # Expired quotes
        expired_q = (
            select(func.count())
            .select_from(Quote)
            .where(
                Quote.organization_id == organization_id,
                Quote.status == QuoteStatus.EXPIRED,
            )
        )
        expired_quotes = int(self.db.scalar(expired_q) or 0)

        # Conversion rate: converted / (converted + rejected + expired)
        resolved_statuses = (
            QuoteStatus.CONVERTED,
            QuoteStatus.REJECTED,
            QuoteStatus.EXPIRED,
        )
        resolved_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(Quote)
                .where(
                    Quote.organization_id == organization_id,
                    Quote.status.in_(resolved_statuses),
                )
            )
            or 0
        )
        converted_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(Quote)
                .where(
                    Quote.organization_id == organization_id,
                    Quote.status == QuoteStatus.CONVERTED,
                )
            )
            or 0
        )
        conversion_rate = (
            round(Decimal(str(converted_count)) / Decimal(str(resolved_count)) * 100, 1)
            if resolved_count > 0
            else _ZERO
        )

        # Open sales orders
        open_so_statuses = (
            SOStatus.APPROVED,
            SOStatus.CONFIRMED,
            SOStatus.IN_PROGRESS,
        )
        so_q = (
            select(
                func.count().label("cnt"),
                func.coalesce(func.sum(SalesOrder.total_amount), 0).label("val"),
            )
            .select_from(SalesOrder)
            .where(
                SalesOrder.organization_id == organization_id,
                SalesOrder.status.in_(open_so_statuses),
            )
        )
        sor = self.db.execute(so_q).one()

        return PipelineHealthSummary(
            open_quotes=open_quotes,
            open_quote_value=open_quote_value,
            expired_quotes=expired_quotes,
            conversion_rate_pct=conversion_rate,
            open_sales_orders=int(sor.cnt or 0),
            open_so_value=Decimal(str(sor.val or "0")),
            currency_code=currency_code,
        )

    def customer_concentration(
        self, organization_id: UUID
    ) -> CustomerConcentrationSummary:
        from app.models.finance.core_org.organization import Organization

        org = self.db.scalar(
            select(Organization).where(Organization.organization_id == organization_id)
        )
        currency_code = org.functional_currency_code if org else "NGN"

        cutoff_90d = date.today() - timedelta(days=90)

        # Revenue by customer (last 90 days, paid invoices)
        rev_by_cust = (
            select(
                Invoice.customer_id,
                func.coalesce(Customer.trading_name, Customer.legal_name).label("name"),
                func.sum(Invoice.functional_currency_amount).label("rev"),
            )
            .join(Customer, Customer.customer_id == Invoice.customer_id)
            .where(
                Invoice.organization_id == organization_id,
                Customer.organization_id == organization_id,
                Invoice.status == InvoiceStatus.PAID,
                Invoice.invoice_date >= cutoff_90d,
            )
            .group_by(Invoice.customer_id, Customer.trading_name, Customer.legal_name)
            .order_by(func.sum(Invoice.functional_currency_amount).desc())
        )
        rows = self.db.execute(rev_by_cust).all()

        total_rev: Decimal = sum((Decimal(str(r.rev or "0")) for r in rows), _ZERO)
        active_count = len(rows)

        top_name: str | None = None
        top_rev: Decimal = _ZERO
        top_pct: Decimal = _ZERO
        top_3_pct: Decimal = _ZERO

        if rows and total_rev > 0:
            top_name = str(rows[0].name)
            top_rev = Decimal(str(rows[0].rev or "0"))
            top_pct = Decimal(str(round(top_rev / total_rev * 100, 1)))
            top_3_rev: Decimal = sum(
                (Decimal(str(r.rev or "0")) for r in rows[:3]), _ZERO
            )
            top_3_pct = Decimal(str(round(top_3_rev / total_rev * 100, 1)))

        return CustomerConcentrationSummary(
            total_revenue_90d=total_rev,
            top_customer_name=top_name,
            top_customer_revenue=top_rev,
            top_customer_pct=top_pct,
            top_3_pct=top_3_pct,
            active_customer_count=active_count,
            currency_code=currency_code,
        )

    # ------------------------------------------------------------------
    # Insight generation
    # ------------------------------------------------------------------
    def generate_pipeline_health_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        if self._quick_check_from_store(organization_id):
            return None

        pipeline = self.pipeline_health(organization_id)
        if pipeline.open_quotes == 0 and pipeline.open_sales_orders == 0:
            return None

        severity = _severity_for_pipeline(
            pipeline.expired_quotes, pipeline.conversion_rate_pct
        )
        title = "Sales pipeline health"
        summary_text = (
            f"Open quotes: {pipeline.open_quotes} "
            f"({pipeline.currency_code} {pipeline.open_quote_value:,.2f}). "
            f"Expired: {pipeline.expired_quotes}. "
            f"Conversion rate: {pipeline.conversion_rate_pct}%. "
            f"Open SOs: {pipeline.open_sales_orders} "
            f"({pipeline.currency_code} {pipeline.open_so_value:,.2f})."
        )
        coaching_action = (
            "Follow up on open quotes before expiry. If conversion rate is below 30%, "
            "review pricing strategy and quote follow-up process. "
            "Prioritize fulfilling open sales orders to convert pipeline to revenue."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            category="REVENUE",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.85,
            data_sources={
                "ar.quote": pipeline.open_quotes + pipeline.expired_quotes,
                "ar.sales_order": pipeline.open_sales_orders,
            },
            evidence={
                "currency_code": pipeline.currency_code,
                "open_quotes": pipeline.open_quotes,
                "open_quote_value": str(pipeline.open_quote_value),
                "expired_quotes": pipeline.expired_quotes,
                "conversion_rate_pct": str(pipeline.conversion_rate_pct),
                "open_sales_orders": pipeline.open_sales_orders,
                "open_so_value": str(pipeline.open_so_value),
            },
            status="GENERATED",
            delivered_at=None,
            read_at=None,
            dismissed_at=None,
            feedback=None,
            valid_until=date.today() + timedelta(days=1),
            created_at=datetime.now(UTC),
        )

    def generate_customer_concentration_insight(
        self, organization_id: UUID
    ) -> CoachInsight | None:
        conc = self.customer_concentration(organization_id)
        if conc.total_revenue_90d <= 0:
            return None
        if conc.top_customer_pct < 25:
            # Well-diversified — no insight needed
            return None

        severity = _severity_for_concentration(conc.top_customer_pct)
        title = "Customer concentration risk"
        summary_text = (
            f"Top customer ({conc.top_customer_name}) accounts for "
            f"{conc.top_customer_pct}% of 90-day revenue. "
            f"Top 3 customers: {conc.top_3_pct}%. "
            f"Active customers: {conc.active_customer_count}."
        )
        coaching_action = (
            "High customer concentration increases revenue risk if a key "
            "account churns. Diversify by expanding the customer base and "
            "reducing dependency on any single account above 25%."
        )

        return CoachInsight(
            insight_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="EXECUTIVE",
            target_employee_id=None,
            category="REVENUE",
            severity=severity,
            title=title,
            summary=summary_text,
            detail=None,
            coaching_action=coaching_action,
            confidence=0.85,
            data_sources={"ar.invoice": conc.active_customer_count},
            evidence={
                "currency_code": conc.currency_code,
                "total_revenue_90d": str(conc.total_revenue_90d),
                "top_customer_name": conc.top_customer_name,
                "top_customer_revenue": str(conc.top_customer_revenue),
                "top_customer_pct": str(conc.top_customer_pct),
                "top_3_pct": str(conc.top_3_pct),
                "active_customer_count": conc.active_customer_count,
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
        written = 0

        self.db.execute(
            delete(CoachInsight).where(
                CoachInsight.organization_id == organization_id,
                CoachInsight.target_employee_id.is_(None),
                CoachInsight.category == "REVENUE",
                func.date(CoachInsight.created_at) == today,
                CoachInsight.title.in_(
                    ["Sales pipeline health", "Customer concentration risk"]
                ),
            )
        )

        for gen in (
            self.generate_pipeline_health_insight,
            self.generate_customer_concentration_insight,
        ):
            insight = gen(organization_id)
            if insight:
                self.db.add(insight)
                written += 1

        if written:
            self.db.flush()
        return written
