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
    CleanupPlanDrift,
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
    reviewed_older_than: str | None = None,
) -> dict[str, object]:
    """Operator-invoked orphan cleanup: dry-run plan by default.

    ``apply=False`` (the default) uses ``grace_hours`` (default
    :data:`MIN_APPLY_GRACE_HOURS`) to build and return the plan's safe
    summary only; nothing is deleted. The summary's ``older_than`` and
    ``plan_digest`` are exactly what an operator must pass back to apply.

    ``apply=True`` requires BOTH ``expected_plan_digest`` and
    ``reviewed_older_than`` (the dry-run summary's own ``older_than``, a
    timezone-aware ISO-8601 string). The task never reuses ``now()`` as the
    apply cutoff — a fresh ``now()`` on every run would make the dry-run and
    apply plan digests permanently unequal (their ``older_than`` values
    would never match), so every apply would fail-safe but nothing could
    ever be deleted. Instead the apply run derives its grace period from
    THIS run's fresh observation time and the reviewed fixed cutoff, refuses
    unless that cutoff is still at least :data:`MIN_APPLY_GRACE_HOURS` old,
    recomputes the plan from a fresh listing and session (never a cached
    one), confirms the fresh report's cutoff still matches the reviewed one,
    authorizes it against the digest, and only then deletes through the sole
    storage seam, ``app.services.storage.delete_reviewed_file_orphans``.
    """
    if grace_hours < 1:
        raise ValueError("grace_hours must be at least one")

    reviewed_cutoff: datetime | None = None
    if apply:
        if not expected_plan_digest:
            raise ValueError("apply requires expected_plan_digest")
        if not reviewed_older_than:
            raise ValueError("apply requires reviewed_older_than")
        reviewed_cutoff = datetime.fromisoformat(reviewed_older_than)
        if reviewed_cutoff.tzinfo is None or reviewed_cutoff.utcoffset() is None:
            raise ValueError("reviewed_older_than must be timezone-aware")

    org_id = UUID(organization_id)
    scope = OrganizationTenantContext.for_organization(org_id).tenant_scope
    provider = get_dotmac_files_read_provider()
    observed_at = datetime.now(timezone.utc)
    observations = list_objects(provider, scope=scope)

    if apply:
        if reviewed_cutoff is None:
            raise ValueError("apply requires reviewed_older_than")
        grace_period = observed_at - reviewed_cutoff
        if grace_period < timedelta(hours=MIN_APPLY_GRACE_HOURS):
            raise ValueError(
                "reviewed_older_than must be at least "
                f"{MIN_APPLY_GRACE_HOURS} hours before now"
            )
    else:
        grace_period = timedelta(hours=grace_hours)

    with session_for_org(org_id) as db:
        report = report_file_objects(
            db,
            scope=scope,
            provider_code=provider.code,
            observations=observations,
            observed_at=observed_at,
            grace_period=grace_period,
        )

    if apply and report.older_than != reviewed_cutoff:
        raise CleanupPlanDrift(
            "the fresh report's cutoff no longer matches reviewed_older_than"
        )

    plan = plan_orphan_cleanup(report, max_deletions=max_deletions)

    if not apply:
        summary = plan.safe_summary()
        logger.info("Orphan cleanup plan (dry run): %s", summary)
        return summary

    if expected_plan_digest is None:
        raise ValueError("apply requires expected_plan_digest")
    authorize_apply(plan, expected_plan_digest=expected_plan_digest)
    deleted = delete_reviewed_file_orphans(scope=plan.scope, keys=plan.candidate_keys)
    summary = {**plan.safe_summary(), "dry_run": False, "deleted": deleted}
    logger.info("Orphan cleanup applied: %s", summary)
    return summary


__all__ = ["clean_tenant_file_objects", "report_tenant_file_objects"]
