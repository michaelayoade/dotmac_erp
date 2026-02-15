"""
Coach / Intelligence Engine Background Tasks.

Deterministic analyzers across finance, workforce, supply chain, and revenue domains.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.config import settings as app_settings
from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)


@shared_task
def generate_daily_data_quality_insights(organization_id: str | None = None) -> dict:
    """
    Generate deterministic data-quality insights (org-wide).

    Safe aggregates only; no LLM required.
    """
    if not app_settings.coach_enabled:
        return {"skipped": True, "reason": "coach_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "insights_written": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.coach.analyzers.data_quality import (
                    DataQualityAnalyzer,
                )

                analyzer = DataQualityAnalyzer(db)
                written = analyzer.upsert_daily_org_insights(org.organization_id)
                db.commit()
                results["organizations_processed"] += 1
                results["insights_written"] += int(written)
            except Exception as exc:
                logger.exception(
                    "Coach data-quality generation failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    return results


@shared_task
def generate_daily_banking_health_insights(organization_id: str | None = None) -> dict:
    """
    Generate deterministic banking health insights (org-wide).

    Safe aggregates; no LLM required.
    """
    if not app_settings.coach_enabled:
        return {"skipped": True, "reason": "coach_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "insights_written": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.coach.analyzers.banking import BankingHealthAnalyzer

                analyzer = BankingHealthAnalyzer(db)
                written = analyzer.upsert_daily_org_insights(org.organization_id)
                db.commit()
                results["organizations_processed"] += 1
                results["insights_written"] += int(written)
            except Exception as exc:
                logger.exception(
                    "Coach banking health generation failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    return results


@shared_task
def generate_daily_expense_approval_insights(
    organization_id: str | None = None,
) -> dict:
    """
    Generate deterministic expense approval backlog insights.
    """
    if not app_settings.coach_enabled:
        return {"skipped": True, "reason": "coach_disabled"}

    max_per_run = int(app_settings.coach_max_insights_per_run)
    per_org_limit = max(max_per_run, 1)

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "insights_written": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.coach.analyzers.expense import ExpenseApprovalAnalyzer

                analyzer = ExpenseApprovalAnalyzer(db)
                written = analyzer.upsert_daily_insights(
                    org.organization_id,
                    limit=min(20, per_org_limit),
                )
                db.commit()
                results["organizations_processed"] += 1
                results["insights_written"] += int(written)
            except Exception as exc:
                logger.exception(
                    "Coach expense approval generation failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    return results


@shared_task
def generate_daily_ar_overdue_insights(organization_id: str | None = None) -> dict:
    """
    Generate deterministic AR overdue receivables insights (org-wide).
    """
    if not app_settings.coach_enabled:
        return {"skipped": True, "reason": "coach_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "insights_written": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.coach.analyzers.ar_overdue import AROverdueAnalyzer

                analyzer = AROverdueAnalyzer(db)
                written = analyzer.upsert_daily_org_insights(org.organization_id)
                db.commit()
                results["organizations_processed"] += 1
                results["insights_written"] += int(written)
            except Exception as exc:
                logger.exception(
                    "Coach AR overdue generation failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    return results


@shared_task
def generate_daily_ap_due_insights(organization_id: str | None = None) -> dict:
    """
    Generate deterministic AP payables due soon/overdue insights (org-wide).
    """
    if not app_settings.coach_enabled:
        return {"skipped": True, "reason": "coach_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "insights_written": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.coach.analyzers.ap_due import (
                    APDueAnalyzer,  # pragma: allowlist secret
                )

                analyzer = APDueAnalyzer(db)
                written = analyzer.upsert_daily_org_insights(org.organization_id)
                db.commit()
                results["organizations_processed"] += 1
                results["insights_written"] += int(written)
            except Exception as exc:
                logger.exception(
                    "Coach AP due generation failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    return results


# ---------------------------------------------------------------------------
# New domain analyzers (Phase 2+)
# ---------------------------------------------------------------------------


def _run_org_analyzer(
    analyzer_path: str,
    analyzer_cls_name: str,
    task_label: str,
    organization_id: str | None = None,
) -> dict:
    """Shared boilerplate for single-analyzer daily tasks."""
    import importlib

    if not app_settings.coach_enabled:
        return {"skipped": True, "reason": "coach_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "insights_written": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                mod = importlib.import_module(analyzer_path)
                cls = getattr(mod, analyzer_cls_name)
                analyzer = cls(db)
                written = analyzer.upsert_daily_org_insights(org.organization_id)
                db.commit()
                results["organizations_processed"] += 1
                results["insights_written"] += int(written)
            except Exception as exc:
                logger.exception(
                    "Coach %s generation failed for org %s",
                    task_label,
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    return results


@shared_task
def generate_daily_cash_flow_insights(organization_id: str | None = None) -> dict:
    """Generate deterministic cash-flow health insights (DSO, DPO, CCC)."""
    return _run_org_analyzer(
        "app.services.coach.analyzers.cash_flow",
        "CashFlowAnalyzer",
        "cash-flow",
        organization_id,
    )


@shared_task
def generate_daily_compliance_insights(organization_id: str | None = None) -> dict:
    """Generate deterministic compliance / fiscal-period health insights."""
    return _run_org_analyzer(
        "app.services.coach.analyzers.compliance",
        "ComplianceAnalyzer",
        "compliance",
        organization_id,
    )


@shared_task
def generate_daily_workforce_insights(organization_id: str | None = None) -> dict:
    """Generate deterministic workforce health + leave utilization insights."""
    return _run_org_analyzer(
        "app.services.coach.analyzers.workforce",
        "WorkforceAnalyzer",
        "workforce",
        organization_id,
    )


@shared_task
def generate_daily_supply_chain_insights(organization_id: str | None = None) -> dict:
    """Generate deterministic supply-chain health insights (stockout risk, dead stock)."""
    return _run_org_analyzer(
        "app.services.coach.analyzers.supply_chain",
        "SupplyChainAnalyzer",
        "supply-chain",
        organization_id,
    )


@shared_task
def generate_daily_revenue_insights(organization_id: str | None = None) -> dict:
    """Generate deterministic revenue / pipeline health insights."""
    return _run_org_analyzer(
        "app.services.coach.analyzers.revenue",
        "RevenueAnalyzer",
        "revenue",
        organization_id,
    )


@shared_task
def generate_daily_efficiency_insights(organization_id: str | None = None) -> dict:
    """Generate deterministic operational efficiency insights (period close, leave backlog, workflow health)."""
    return _run_org_analyzer(
        "app.services.coach.analyzers.efficiency",
        "EfficiencyAnalyzer",
        "efficiency",
        organization_id,
    )


@shared_task
def generate_weekly_finance_report(organization_id: str | None = None) -> dict:
    """Generate weekly finance digest report for each organization."""
    if not app_settings.coach_enabled:
        return {"skipped": True, "reason": "coach_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "reports_written": 0,
        "errors": [],
    }

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.coach.report_generator import ReportGenerator

                generator = ReportGenerator(db)
                report = generator.generate_weekly_finance_report(org.organization_id)
                if report:
                    db.add(report)
                    db.commit()
                    results["reports_written"] += 1
                results["organizations_processed"] += 1
            except Exception as exc:
                logger.exception(
                    "Weekly finance report failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    return results
