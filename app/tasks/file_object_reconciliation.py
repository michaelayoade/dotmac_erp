"""Explicit, tenant-scoped dry-run entry point for files object reports."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from celery import shared_task
from dotmac_files import ObjectInfo, TenantStoredFile, list_objects
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session_context import session_for_org
from app.services.file_object_cleanup import (
    DEFAULT_MAX_DELETIONS,
    MIN_APPLY_GRACE_HOURS,
    CleanupPlanDrift,
    OrphanCleanupPlan,
    authorize_apply,
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


def _is_still_referenced(db: Session, *, tenant_id: UUID, key: str) -> bool:
    """Whether ANY TenantStoredFile row now claims this key.

    Checked across every provider code and every lifecycle state — a
    candidate is only safe to delete if NOTHING claims its key, not merely
    nothing in the ``erp_s3``/``available`` slice this report compared
    against.
    """
    return (
        db.execute(
            select(TenantStoredFile.id).where(
                TenantStoredFile.tenant_id == tenant_id,
                TenantStoredFile.storage_key == key,
            )
        ).first()
        is not None
    )


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

    Each iteration: (i) opens a short, separate ``session_for_org`` to check
    for ANY referencing row and closes it before touching storage — deletion
    must happen outside a database transaction; (ii) re-observes the live
    object; (iii) deletes through the sole single-key seam. Stops at the
    first delete failure so a partial batch is never silently swallowed.
    """
    outcomes: dict[str, list[str]] = {name: [] for name in _RECHECK_OUTCOMES}
    if plan.scope_kind != "tenant" or plan.tenant_id is None:
        raise ValueError("orphan cleanup apply requires a tenant scope")
    tenant_id = UUID(plan.tenant_id)

    for key in plan.candidate_keys:
        with session_for_org(org_id) as db:
            referenced = _is_still_referenced(db, tenant_id=tenant_id, key=key)
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

        try:
            delete_reviewed_file_orphan(
                scope=plan.scope, key=key, expected_provider_code=plan.provider_code
            )
        except Exception:
            outcomes["failed"].append(key)
            logger.exception("Orphan delete failed; stopping the recheck loop")
            break
        outcomes["deleted"].append(key)

    result: dict[str, object] = {}
    for name in _RECHECK_OUTCOMES:
        keys = outcomes[name]
        result[f"{name}_count"] = len(keys)
        result[f"{name}_key_digests"] = [
            hashlib.sha256(k.encode("utf-8")).hexdigest()
            for k in keys[:_MAX_OUTCOME_EVIDENCE]
        ]
        result[f"{name}_evidence_omitted"] = max(0, len(keys) - _MAX_OUTCOME_EVIDENCE)
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
    and authorizes it against the digest, the cap, plan expiry, and the
    reference-safety checks in ``authorize_apply``. Only then does the
    per-object recheck-and-delete loop run, one key at a time, through the
    sole storage seam, ``app.services.storage.delete_reviewed_file_orphan``.
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
    authorize_apply(plan, expected_plan_digest=expected_plan_digest, now=observed_at)
    recheck_summary = _recheck_and_delete(org_id, plan, provider)
    summary = {
        **plan.safe_summary(),
        **recheck_summary,
        "dry_run": False,
        "deleted": recheck_summary["deleted_count"],
    }
    logger.info("Orphan cleanup applied: %s", summary)
    return summary


__all__ = ["clean_tenant_file_objects", "report_tenant_file_objects"]
