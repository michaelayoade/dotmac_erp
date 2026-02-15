"""
Coach / Intelligence Engine Background Tasks.

Initial Phase 1 implementation: deterministic data-quality insights.
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
