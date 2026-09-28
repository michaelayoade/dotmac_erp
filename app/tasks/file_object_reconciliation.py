"""Explicit, tenant-scoped dry-run entry point for files object reports."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from celery import shared_task
from dotmac_files import list_objects

from app.db.session_context import session_for_org
from app.services.file_object_cleanup import (
    DEFAULT_MAX_DELETIONS,
    MIN_APPLY_GRACE_HOURS,
    authorize_apply,
    plan_orphan_cleanup,
)
from app.services.file_object_reconciliation import report_file_objects
from app.services.storage import (
    delete_reviewed_file_orphans,
    get_dotmac_files_read_provider,
)
from app.tenancy import OrganizationTenantContext

logger = logging.getLogger(__name__)


@shared_task(name="app.tasks.file_object_reconciliation.report_tenant_file_objects")
def report_tenant_file_objects(
    organization_id: str, *, grace_hours: int = 24
) -> dict[str, object]:
    """Operator-invoked report for one org; no object or metadata mutation."""
    if grace_hours < 1:
        raise ValueError("grace_hours must be at least one")
    org_id = UUID(organization_id)
    scope = OrganizationTenantContext.for_organization(org_id).tenant_scope
    provider = get_dotmac_files_read_provider()
    observed_at = datetime.now(timezone.utc)
    observations = list_objects(provider, scope=scope)
    with session_for_org(org_id) as db:
        report = report_file_objects(
            db,
            scope=scope,
            provider_code=provider.code,
            observations=observations,
            observed_at=observed_at,
            grace_period=timedelta(hours=grace_hours),
        )
    summary = report.safe_summary()
    logger.info("Managed file object report: %s", summary)
    return summary


@shared_task(name="app.tasks.file_object_reconciliation.clean_tenant_file_objects")
def clean_tenant_file_objects(
    organization_id: str,
    *,
    grace_hours: int = MIN_APPLY_GRACE_HOURS,
    max_deletions: int = DEFAULT_MAX_DELETIONS,
    apply: bool = False,
    expected_plan_digest: str | None = None,
) -> dict[str, object]:
    """Operator-invoked orphan cleanup: dry-run plan by default.

    ``apply=False`` (the default) only builds and returns the plan's safe
    summary; nothing is deleted. ``apply=True`` requires an
    ``expected_plan_digest`` naming exactly the plan an operator reviewed
    and a ``grace_hours`` of at least :data:`MIN_APPLY_GRACE_HOURS`; it
    recomputes the plan from this call's own fresh listing and session
    (never a cached one), authorizes it, and only then deletes through the
    sole storage seam, ``app.services.storage.delete_reviewed_file_orphans``.
    """
    if grace_hours < 1:
        raise ValueError("grace_hours must be at least one")
    if apply:
        if grace_hours < MIN_APPLY_GRACE_HOURS:
            raise ValueError(f"apply requires grace_hours >= {MIN_APPLY_GRACE_HOURS}")
        if not expected_plan_digest:
            raise ValueError("apply requires expected_plan_digest")
    org_id = UUID(organization_id)
    scope = OrganizationTenantContext.for_organization(org_id).tenant_scope
    provider = get_dotmac_files_read_provider()
    observed_at = datetime.now(timezone.utc)
    observations = list_objects(provider, scope=scope)
    with session_for_org(org_id) as db:
        report = report_file_objects(
            db,
            scope=scope,
            provider_code=provider.code,
            observations=observations,
            observed_at=observed_at,
            grace_period=timedelta(hours=grace_hours),
        )
    plan = plan_orphan_cleanup(report, max_deletions=max_deletions)

    if not apply:
        summary = plan.safe_summary()
        logger.info("Orphan cleanup plan (dry run): %s", summary)
        return summary

    assert expected_plan_digest is not None  # enforced above when apply=True
    authorize_apply(plan, expected_plan_digest=expected_plan_digest)
    deleted = delete_reviewed_file_orphans(scope=plan.scope, keys=plan.candidate_keys)
    summary = {**plan.safe_summary(), "dry_run": False, "deleted": deleted}
    logger.info("Orphan cleanup applied: %s", summary)
    return summary


__all__ = ["clean_tenant_file_objects", "report_tenant_file_objects"]
