"""Weekly / monthly report generation for Coach.

Collects data from analyzers and MetricStore, then produces a
``CoachReport`` record. LLM narration is optional — the service produces
a structured deterministic report even without LLM connectivity.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.coach.insight import CoachInsight
from app.models.coach.report import CoachReport

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates weekly digest reports by aggregating recent insights."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def generate_weekly_finance_report(
        self, organization_id: UUID
    ) -> CoachReport | None:
        """Create a weekly finance digest for the given org.

        Returns None if there are no insights to report.
        """
        period_end = date.today()
        period_start = period_end - timedelta(days=7)

        # Collect recent finance insights
        finance_insights = self._recent_insights(
            organization_id,
            period_start,
            audience="FINANCE",
        )

        if not finance_insights:
            logger.debug(
                "No finance insights for org %s in past 7d, skipping report",
                organization_id,
            )
            return None

        sections = self._build_finance_sections(finance_insights)
        key_metrics = self._extract_key_metrics(finance_insights)
        recommendations = self._build_recommendations(finance_insights)

        executive_summary = self._build_executive_summary(finance_insights, "finance")

        return CoachReport(
            report_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="FINANCE",
            target_employee_id=None,
            report_type="WEEKLY_DIGEST",
            period_start=period_start,
            period_end=period_end,
            title=f"Weekly Finance Digest — {period_start:%d %b} to {period_end:%d %b %Y}",
            executive_summary=executive_summary,
            sections=sections,
            key_metrics=key_metrics,
            recommendations=recommendations,
            model_used=None,  # Deterministic — no LLM
            tokens_used=0,
            generation_time_ms=0,
            created_at=datetime.now(UTC),
            sent_at=None,
        )

    def generate_weekly_hr_report(self, organization_id: UUID) -> CoachReport | None:
        """Create a weekly HR digest for the given org."""
        period_end = date.today()
        period_start = period_end - timedelta(days=7)

        hr_insights = self._recent_insights(
            organization_id,
            period_start,
            audience="HR",
        )

        if not hr_insights:
            return None

        sections = self._build_hr_sections(hr_insights)
        key_metrics = self._extract_key_metrics(hr_insights)
        recommendations = self._build_recommendations(hr_insights)
        executive_summary = self._build_executive_summary(hr_insights, "HR")

        return CoachReport(
            report_id=uuid.uuid4(),
            organization_id=organization_id,
            audience="HR",
            target_employee_id=None,
            report_type="WEEKLY_DIGEST",
            period_start=period_start,
            period_end=period_end,
            title=f"Weekly HR Digest — {period_start:%d %b} to {period_end:%d %b %Y}",
            executive_summary=executive_summary,
            sections=sections,
            key_metrics=key_metrics,
            recommendations=recommendations,
            model_used=None,
            tokens_used=0,
            generation_time_ms=0,
            created_at=datetime.now(UTC),
            sent_at=None,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _recent_insights(
        self,
        organization_id: UUID,
        since: date,
        *,
        audience: str | None = None,
    ) -> list[CoachInsight]:
        stmt = (
            select(CoachInsight)
            .where(
                CoachInsight.organization_id == organization_id,
                func.date(CoachInsight.created_at) >= since,
            )
            .order_by(CoachInsight.created_at.desc())
        )
        if audience:
            stmt = stmt.where(CoachInsight.audience == audience)
        return list(self.db.scalars(stmt).all())

    def _build_finance_sections(
        self, insights: list[CoachInsight]
    ) -> list[dict[str, Any]]:
        """Group finance insights into report sections by category."""
        category_order = [
            "CASH_FLOW",
            "REVENUE",
            "COMPLIANCE",
            "EFFICIENCY",
        ]
        sections: list[dict[str, Any]] = []
        by_category: dict[str, list[CoachInsight]] = {}
        for i in insights:
            by_category.setdefault(i.category, []).append(i)

        for cat in category_order:
            items = by_category.get(cat, [])
            if not items:
                continue
            sections.append(
                {
                    "category": cat,
                    "title": cat.replace("_", " ").title(),
                    "insights": [
                        {
                            "title": i.title,
                            "summary": i.summary,
                            "severity": i.severity,
                            "coaching_action": i.coaching_action,
                        }
                        for i in items
                    ],
                }
            )

        # Any remaining categories
        for cat, items in by_category.items():
            if cat not in category_order:
                sections.append(
                    {
                        "category": cat,
                        "title": cat.replace("_", " ").title(),
                        "insights": [
                            {
                                "title": i.title,
                                "summary": i.summary,
                                "severity": i.severity,
                                "coaching_action": i.coaching_action,
                            }
                            for i in items
                        ],
                    }
                )
        return sections

    def _build_hr_sections(self, insights: list[CoachInsight]) -> list[dict[str, Any]]:
        category_order = ["WORKFORCE", "DATA_QUALITY"]
        sections: list[dict[str, Any]] = []
        by_category: dict[str, list[CoachInsight]] = {}
        for i in insights:
            by_category.setdefault(i.category, []).append(i)

        for cat in category_order:
            items = by_category.get(cat, [])
            if not items:
                continue
            sections.append(
                {
                    "category": cat,
                    "title": cat.replace("_", " ").title(),
                    "insights": [
                        {
                            "title": i.title,
                            "summary": i.summary,
                            "severity": i.severity,
                            "coaching_action": i.coaching_action,
                        }
                        for i in items
                    ],
                }
            )
        return sections

    def _extract_key_metrics(
        self, insights: list[CoachInsight]
    ) -> list[dict[str, Any]]:
        """Pull numeric evidence values as key_metrics for the report."""
        metrics: list[dict[str, Any]] = []
        seen: set[str] = set()

        for insight in insights:
            evidence = insight.evidence or {}
            for key, val in evidence.items():
                if key in seen or key in (
                    "top_overdue_customers",
                    "top_due_suppliers",
                ):
                    continue
                seen.add(key)
                metrics.append(
                    {
                        "metric": key.replace("_", " ").title(),
                        "value": val,
                        "source": insight.category,
                    }
                )

        return metrics[:20]  # Cap at 20

    def _build_recommendations(self, insights: list[CoachInsight]) -> list[str]:
        """Collect coaching actions as report recommendations."""
        recs: list[str] = []
        seen: set[str] = set()
        # Prioritize by severity
        severity_order = {"URGENT": 0, "WARNING": 1, "ATTENTION": 2, "INFO": 3}
        sorted_insights = sorted(
            insights, key=lambda i: severity_order.get(i.severity, 99)
        )
        for insight in sorted_insights:
            if insight.coaching_action and insight.coaching_action not in seen:
                recs.append(insight.coaching_action)
                seen.add(insight.coaching_action)
        return recs[:5]  # Top 5

    def _build_executive_summary(
        self, insights: list[CoachInsight], domain: str
    ) -> str:
        """Create a deterministic executive summary from insight data."""
        total = len(insights)
        by_severity: dict[str, int] = {}
        for i in insights:
            by_severity[i.severity] = by_severity.get(i.severity, 0) + 1

        parts = [f"This week's {domain} analysis produced {total} insight(s)."]
        if by_severity.get("WARNING") or by_severity.get("URGENT"):
            urgent = by_severity.get("URGENT", 0)
            warning = by_severity.get("WARNING", 0)
            parts.append(
                f"{urgent + warning} require(s) immediate attention "
                f"({urgent} urgent, {warning} warning)."
            )

        categories = {i.category for i in insights}
        parts.append(
            f"Areas covered: {', '.join(sorted(c.replace('_', ' ').title() for c in categories))}."
        )

        return " ".join(parts)
