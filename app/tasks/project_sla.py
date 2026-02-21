"""Project SLA periodic tasks."""

from __future__ import annotations

import logging
from typing import TypedDict

from celery import shared_task
from sqlalchemy import select

from app.db import SessionLocal
from app.models.finance.core_org.project import Project
from app.services.pm.sla_service import ProjectSLAService

logger = logging.getLogger(__name__)


class ProjectSLABreachResult(TypedDict):
    organizations: int
    projects: int
    breaches: int


@shared_task
def process_project_sla_breaches() -> ProjectSLABreachResult:
    """Scan active projects and generate SLA breach notifications."""
    result: ProjectSLABreachResult = {"organizations": 0, "projects": 0, "breaches": 0}

    with SessionLocal() as db:
        org_ids = list(db.scalars(select(Project.organization_id).distinct()).all())
        for org_id in org_ids:
            result["organizations"] += 1
            stats = ProjectSLAService(db, org_id).process_breaches()
            result["projects"] += int(stats.get("projects", 0))
            result["breaches"] += int(stats.get("breaches", 0))

        db.commit()

    if result["breaches"] > 0:
        logger.info(
            "Project SLA breach scan: orgs=%d projects=%d breaches=%d",
            result["organizations"],
            result["projects"],
            result["breaches"],
        )

    return result
