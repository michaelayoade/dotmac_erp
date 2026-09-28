"""Explicit, tenant-scoped dry-run entry point for files object reports."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from celery import shared_task
from dotmac_files import ObjectInfo, list_objects

from app.db.session_context import session_for_org
from app.services.file_object_cleanup import (
    DEFAULT_MAX_DELETIONS,
    MIN_APPLY_GRACE_HOURS,
    CleanupPartialFailure,
    OrphanCleanupPlan,
    authorize_apply,
    is_storage_key_referenced,
    plan_orphan_cleanup,
)
from app.services.file_object_reconciliation import report_file_objects
from app.services.storage import (
    DotmacFilesS3Provider,
    delete_reviewed_file_orphan,
    get_dotmac_files_read_provider,
)
from app.tenancy import OrganizationTenantContext

logger = logging.getLogger(__name__)

_MAX_OUTCOME_EVIDENCE = 100
_RECHECK_OUTCOMES = (
    "deleted",
    "rechecked_referenced",
    "already_absent",
    "rechecked_too_new",
    "failed",
)


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


def _reobserve(provider: DotmacFilesS3Provider, key: str) -> ObjectInfo | None:
    """Re-observe one key's live presence and freshness immediately before delete.

    ``dotmac_files.observe_object`` does not fit this recheck: its a4
    signature is ``observe_object(provider, *, target: StoredObjectRef) ->
    bool`` — it requires an EXISTING metadata row and returns presence only,
    never a modification time. An orphan candidate has no such row by
    definition (that is what makes it an orphan), so that primitive cannot
    be given a target here. This instead calls the provider's own ``list``
    (the exact public primitive ``dotmac_files.list_objects`` itself calls),
    scoped to the single key, to recover both presence and ``last_modified``
    in one provider round trip without holding a database transaction.
    """
    for info in provider.list(key):
        if info.key == key:
            return info
    return None


def _recheck_and_delete(
    org_id: UUID, plan: OrphanCleanupPlan, provider: DotmacFilesS3Provider
) -> dict[str, object]:
    """Recheck and delete one candidate key at a time, in plan order.

    Each key's ENTIRE body — the reference recheck, the re-observation, and
    the delete — is wrapped: any exception anywhere in that body records the
    key as ``failed`` (with the exception's class name) and stops the loop.
    Logs one line per successfully deleted key's digest as it happens (never
    the raw key), and always logs the final summary before returning.
    """
    outcomes: dict[str, list[str]] = {name: [] for name in _RECHECK_OUTCOMES}
    failure_reasons: dict[str, str] = {}
    if plan.scope_kind != "tenant" or plan.tenant_id is None:
        raise ValueError("orphan cleanup apply requires a tenant scope")
    tenant_id = UUID(plan.tenant_id)

    for key in plan.candidate_keys:
        try:
            with session_for_org(org_id) as db:
                referenced = is_storage_key_referenced(
                    db, tenant_id=tenant_id, storage_key=key
                )
            if referenced:
                outcomes["rechecked_referenced"].append(key)
                continue

            info = _reobserve(provider, key)
            if info is None:
                outcomes["already_absent"].append(key)
                continue
            if not info.last_modified < plan.older_than:
                outcomes["rechecked_too_new"].append(key)
                continue

            delete_reviewed_file_orphan(
                scope=plan.scope, key=key, expected_provider_code=plan.provider_code
            )
        except Exception as exc:
            key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
            outcomes["failed"].append(key)
            failure_reasons[key] = type(exc).__name__
            logger.exception(
                "Orphan delete failed for key digest %s (%s); stopping the "
                "recheck loop",
                key_digest,
                type(exc).__name__,
            )
            break
        else:
            outcomes["deleted"].append(key)
            logger.info(
                "Orphan deleted: key digest %s",
                hashlib.sha256(key.encode("utf-8")).hexdigest(),
            )

    result: dict[str, object] = {}
    for name in _RECHECK_OUTCOMES:
        keys = outcomes[name]
        result[f"{name}_count"] = len(keys)
        result[f"{name}_key_digests"] = [
            hashlib.sha256(k.encode("utf-8")).hexdigest()
            for k in keys[:_MAX_OUTCOME_EVIDENCE]
        ]
        result[f"{name}_evidence_omitted"] = max(0, len(keys) - _MAX_OUTCOME_EVIDENCE)
    if failure_reasons:
        result["failure_exception_types"] = sorted(set(failure_reasons.values()))
    return result


@shared_task(name="app.tasks.file_object_reconciliation.clean_tenant_file_objects")
def clean_tenant_file_objects(
    organization_id: str,
    *,
    grace_hours: int = MIN_APPLY_GRACE_HOURS,
    max_deletions: int = DEFAULT_MAX_DELETIONS,
    apply: bool = False,
    expected_plan_digest: str | None = None,
    reviewed_older_than: str | None = None,
    reviewed_plan_observed_at: str | None = None,
) -> dict[str, object]:
    """Operator-invoked orphan cleanup: dry-run plan by default.

    ``apply=False`` (the default) uses ``grace_hours`` (default
    :data:`MIN_APPLY_GRACE_HOURS`) and this run's own real observation time
    to build and return the plan's safe summary only; nothing is deleted.
    The summary's ``older_than``, ``plan_observed_at``, and ``plan_digest``
    are exactly what an operator must pass back to apply.

    ``apply=True`` requires `expected_plan_digest`, `reviewed_older_than`,
    and `reviewed_plan_observed_at` (all copied verbatim from the dry-run
    summary). `reviewed_older_than` must equal
    `reviewed_plan_observed_at - MIN_APPLY_GRACE_HOURS` — a self-consistency
    check on the two reviewed values, independent of any I/O. The FRESH
    rebuild then re-lists and re-queries at REAL wall-clock time, but labels
    the resulting report with `observed_at=reviewed_plan_observed_at` and a
    FIXED `MIN_APPLY_GRACE_HOURS` grace — never a relative `now() - cutoff`
    computation, which could never reproduce the reviewed digest and which
    also could not bound the true age of the review (see
    ``app.services.file_object_cleanup`` for why an apply-side relative
    grace both destabilizes the digest and defeats plan expiry). Only a
    change to data OLDER than that fixed cutoff can therefore ever cause
    ``CleanupPlanDrift`` — a fresh upload or a fresh metadata row does not.
    `authorize_apply` then separately compares REAL current time against the
    plan's `plan_observed_at` (which equals `reviewed_plan_observed_at`
    exactly) for the unforgeable 24-hour plan-expiry check. Only after every
    check in `authorize_apply` passes does the per-object recheck-and-delete
    loop run, one key at a time, through the sole storage seam,
    ``app.services.storage.delete_reviewed_file_orphan``.
    """
    if grace_hours < 1:
        raise ValueError("grace_hours must be at least one")

    reviewed_cutoff: datetime | None = None
    reviewed_observed_at: datetime | None = None
    if apply:
        if not expected_plan_digest:
            raise ValueError("apply requires expected_plan_digest")
        if not reviewed_older_than:
            raise ValueError("apply requires reviewed_older_than")
        if not reviewed_plan_observed_at:
            raise ValueError("apply requires reviewed_plan_observed_at")
        reviewed_cutoff = datetime.fromisoformat(reviewed_older_than)
        if reviewed_cutoff.tzinfo is None or reviewed_cutoff.utcoffset() is None:
            raise ValueError("reviewed_older_than must be timezone-aware")
        reviewed_observed_at = datetime.fromisoformat(reviewed_plan_observed_at)
        if (
            reviewed_observed_at.tzinfo is None
            or reviewed_observed_at.utcoffset() is None
        ):
            raise ValueError("reviewed_plan_observed_at must be timezone-aware")
        if reviewed_cutoff != reviewed_observed_at - timedelta(
            hours=MIN_APPLY_GRACE_HOURS
        ):
            raise ValueError(
                "reviewed_older_than is inconsistent with reviewed_plan_observed_at"
            )

    org_id = UUID(organization_id)
    scope = OrganizationTenantContext.for_organization(org_id).tenant_scope
    provider = get_dotmac_files_read_provider()
    real_now = datetime.now(timezone.utc)
    observations = list_objects(provider, scope=scope)

    if apply:
        if reviewed_observed_at is None:
            raise ValueError("apply requires reviewed_plan_observed_at")
        plan_observed_at = reviewed_observed_at
        grace_period = timedelta(hours=MIN_APPLY_GRACE_HOURS)
    else:
        plan_observed_at = real_now
        grace_period = timedelta(hours=grace_hours)

    with session_for_org(org_id) as db:
        report = report_file_objects(
            db,
            scope=scope,
            provider_code=provider.code,
            observations=observations,
            observed_at=plan_observed_at,
            grace_period=grace_period,
        )

    plan = plan_orphan_cleanup(report, plan_observed_at, max_deletions=max_deletions)

    if not apply:
        summary = plan.safe_summary()
        logger.info("Orphan cleanup plan (dry run): %s", summary)
        return summary

    if expected_plan_digest is None:
        raise ValueError("apply requires expected_plan_digest")
    authorize_apply(plan, expected_plan_digest=expected_plan_digest, now=real_now)
    recheck_summary = _recheck_and_delete(org_id, plan, provider)
    summary = {
        **plan.safe_summary(),
        **recheck_summary,
        "dry_run": False,
        "deleted": recheck_summary["deleted_count"],
    }
    logger.info("Orphan cleanup applied: %s", summary)
    if recheck_summary["failed_count"]:
        raise CleanupPartialFailure(
            "orphan cleanup stopped after a per-object delete failure",
            summary=summary,
        )
    return summary


__all__ = ["clean_tenant_file_objects", "report_tenant_file_objects"]
