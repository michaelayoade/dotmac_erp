"""
Analytics Background Tasks — Celery tasks for metric computation.

Refreshes pre-computed metrics in org_metric_snapshot via scheduled
computers. Runs before coach/AI tasks so they consume fresh data.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date
from typing import Any

from celery import shared_task
from sqlalchemy import select

from app.config import settings as app_settings
from app.db import SessionLocal
from app.models.finance.core_org.organization import Organization

logger = logging.getLogger(__name__)


@shared_task
def refresh_cash_flow_metrics(organization_id: str | None = None) -> dict:
    """Refresh cash-flow KPIs for all (or one) active orgs.

    Produces: cash_flow.net_position, cash_flow.inflow_30d,
    cash_flow.outflow_30d, cash_flow.net_flow_30d,
    cash_flow.monthly_summary, cash_flow.ar_overdue_total,
    cash_flow.ap_due_7d_total.
    """
    if not app_settings.analytics_enabled:
        return {"skipped": True, "reason": "analytics_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "metrics_written": 0,
        "errors": [],
    }

    today = date.today()

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.analytics.computers.cash_flow import (
                    CashFlowComputer,
                )

                computer = CashFlowComputer(db)
                written = computer.compute_for_org(org.organization_id, today)
                db.commit()
                results["organizations_processed"] += 1
                results["metrics_written"] += written
            except Exception as exc:
                logger.exception(
                    "Cash flow metric refresh failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    logger.info(
        "refresh_cash_flow_metrics complete: %d orgs, %d metrics, %d errors",
        results["organizations_processed"],
        results["metrics_written"],
        len(results["errors"]),
    )
    return results


@shared_task
def refresh_efficiency_metrics(organization_id: str | None = None) -> dict:
    """Refresh efficiency KPIs for all (or one) active orgs.

    Produces: efficiency.dso, efficiency.dpo, efficiency.ccc,
    efficiency.reconciliation_freshness_days,
    efficiency.unreconciled_account_count,
    efficiency.pending_expense_approvals.
    """
    if not app_settings.analytics_enabled:
        return {"skipped": True, "reason": "analytics_disabled"}

    results: dict[str, Any] = {
        "organizations_processed": 0,
        "metrics_written": 0,
        "errors": [],
    }

    today = date.today()

    with SessionLocal() as db:
        org_query = select(Organization).where(Organization.is_active == True)  # noqa: E712
        if organization_id:
            org_query = org_query.where(
                Organization.organization_id == uuid.UUID(organization_id)
            )

        orgs = db.scalars(org_query).all()
        for org in orgs:
            try:
                from app.services.analytics.computers.efficiency import (
                    EfficiencyComputer,
                )

                computer = EfficiencyComputer(db)
                written = computer.compute_for_org(org.organization_id, today)
                db.commit()
                results["organizations_processed"] += 1
                results["metrics_written"] += written
            except Exception as exc:
                logger.exception(
                    "Efficiency metric refresh failed for org %s",
                    org.organization_id,
                )
                db.rollback()
                results["errors"].append(
                    {"organization_id": str(org.organization_id), "error": str(exc)}
                )

    logger.info(
        "refresh_efficiency_metrics complete: %d orgs, %d metrics, %d errors",
        results["organizations_processed"],
        results["metrics_written"],
        len(results["errors"]),
    )
    return results
