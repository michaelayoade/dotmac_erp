"""Project SLA periodic tasks."""

from __future__ import annotations

import logging
from typing import TypedDict
from uuid import UUID

from celery import shared_task

from app.db.session_context import session_for_org
from app.services.pm.sla_service import ProjectSLAService
from app.tenant_catalog import organization_ids

logger = logging.getLogger(__name__)


class ProjectSLABreachResult(TypedDict):
    organizations: int
    projects: int
    breaches: int


def _list_organization_ids() -> list[UUID]:
    """Every tenant, deactivated ones included.

    The scan this replaced took distinct ``Project.organization_id`` values with
    no organization-active predicate, so a deactivated tenant's open projects
    were still scanned for breaches. Enumerating only active organizations would
    change which tenants are scanned, which is not what this change is for.
    """
    return organization_ids(include_inactive=True)


@shared_task
def process_project_sla_breaches() -> ProjectSLABreachResult:
    """Scan active projects and generate SLA breach notifications.

    ``organizations`` counts the tenants scanned. It previously counted the
    tenants that owned at least one project row, which was a by-product of the
    cross-tenant pre-scan rather than a fact anyone asked for; the projects and
    breaches counts, which are the ones that carry meaning, are unchanged,
    because a tenant with no projects contributes zero to both.
    """
    result: ProjectSLABreachResult = {"organizations": 0, "projects": 0, "breaches": 0}

    for org_id in _list_organization_ids():
        result["organizations"] += 1
        with session_for_org(org_id) as db:
            stats = ProjectSLAService(db, org_id).process_breaches()
            db.commit()
        result["projects"] += int(stats.get("projects", 0))
        result["breaches"] += int(stats.get("breaches", 0))

    if result["breaches"] > 0:
        logger.info(
            "Project SLA breach scan: orgs=%d projects=%d breaches=%d",
            result["organizations"],
            result["projects"],
            result["breaches"],
        )

    return result
