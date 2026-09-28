"""Decision code for managed file-object orphan cleanup.

This module turns a read-only :class:`ObjectReconciliationReport` into a
reviewable, digest-bound :class:`OrphanCleanupPlan`, and refuses to authorize
an apply unless the digest presented for review still matches a freshly
recomputed plan, the reference view looks trustworthy, and the review is
still fresh. It performs no PROVIDER I/O and holds no open database
transaction of its own; ``is_storage_key_referenced`` accepts an
already-open, short-lived session from its caller for exactly one read — the
same "adapters query nothing themselves" discipline the rest of the codebase
applies to router/web thin wrappers, applied here to the Celery task that
would otherwise embed this query directly.

The digest binds only values computed over objects OLDER than the reviewed
cutoff (``older_than``): a fresh upload or a fresh metadata row appearing
between a dry-run and its apply must never itself cause ``CleanupPlanDrift``
— only a change to the OLD, orphan-eligible picture may. Raw, all-age counts
(``managed_objects``, ``referenced_objects``, ``in_flight_unreferenced``)
stay in the summary for operator visibility but are deliberately excluded
from the digest.

The caller (the Celery task) is responsible for producing a fresh report,
for the per-object recheck immediately before each delete, and for the one
single-key deletion seam, ``app.services.storage.delete_reviewed_file_orphan``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from dotmac_files import TenantStoredFile
from dotmac_kernel.cache import PlatformScope, TenantScope
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.file_object_reconciliation import ObjectReconciliationReport

_MAX_SUMMARY_EVIDENCE = 100

#: Default authorized deletion cap for one apply. A caller may raise this
#: (e.g. for a known large backlog) up to, but never past, the hard ceiling.
DEFAULT_MAX_DELETIONS = 100

#: Hard ceiling on the deletion cap itself. A caller requesting more than
#: this is refused outright — this is not the "too many candidates" refusal,
#: which is ``CleanupCapExceeded`` raised from ``authorize_apply``.
MAX_DELETIONS_CEILING = 1000

#: The minimum retention an APPLY may use, and the dry-run default: an
#: object must be at least this old before it may ever be deleted. Decided
#: 2026-09-28 (Michael): 7 days. Dry-run may still use the report's own
#: one-hour minimum for exploration; only apply is bound to this floor.
MIN_APPLY_GRACE_HOURS = 168

#: The maximum age of the reviewed dry-run itself. A plan older than this is
#: refused even if its digest still matches — a stale review is not
#: sufficient authorization for a live delete. Decided 2026-09-28 (Michael).
#: Bound to the plan's REAL observed_at (never derived from reviewed data —
#: see ``OrphanCleanupPlan.plan_observed_at``), so it cannot be defeated by
#: choosing a smaller dry-run ``grace_hours`` to manufacture a
#: later-looking, still-"fresh" cutoff.
MAX_PLAN_AGE_HOURS = 24


class OrphanCleanupError(Exception):
    """Base for a refused cleanup authorization."""


class CleanupPlanDrift(OrphanCleanupError):
    """The digest presented for apply no longer matches a fresh plan."""


class CleanupCapExceeded(OrphanCleanupError):
    """The reviewed key count exceeds the authorized cap, or the requested
    cap itself exceeds :data:`MAX_DELETIONS_CEILING`."""


class CleanupUnsafeReferenceView(OrphanCleanupError):
    """The reference view underlying this plan cannot be trusted.

    Raised when every OLD (past-cutoff) managed object looks unreferenced
    while candidates exist (indistinguishable from a hidden-rows or RLS
    failure that makes a healthy tenant look fully orphaned), or when any
    OLD metadata row was found outside its declared scope prefix
    (``boundary_drift``) — a sign the plan's picture of "what is real"
    cannot be trusted enough to delete against.
    """


class CleanupPlanExpired(OrphanCleanupError):
    """The reviewed dry-run is older than :data:`MAX_PLAN_AGE_HOURS`."""


class CleanupPartialFailure(OrphanCleanupError):
    """A per-object delete failed partway through the recheck-and-delete loop.

    Carries the full outcome summary (``summary`` attribute) so the caller
    can report exactly what happened before the loop stopped.
    """

    def __init__(self, message: str, *, summary: dict[str, object]) -> None:
        super().__init__(message)
        self.summary = summary


def is_storage_key_referenced(
    db: Session, *, tenant_id: UUID, storage_key: str
) -> bool:
    """Whether ANY ``TenantStoredFile`` row now claims this key.

    Checked across every provider code and every lifecycle state — a
    candidate is only safe to delete if NOTHING claims its key, not merely
    nothing in the slice a report previously compared against. Accepts an
    already-open, caller-owned session for exactly one read; opens and
    closes nothing itself.
    """
    return (
        db.execute(
            select(TenantStoredFile.id).where(
                TenantStoredFile.tenant_id == tenant_id,
                TenantStoredFile.storage_key == storage_key,
            )
        ).first()
        is not None
    )


def _scope_kind_and_tenant(scope: object) -> tuple[str, str | None]:
    """Return the plan-safe scope kind and tenant id, or refuse a stranger.

    Deliberately checks the concrete ``dotmac_kernel.cache`` scope types
    (the same pattern ``dotmac_files.physical.scope_prefix`` uses) rather
    than defaulting an unrecognized scope to "platform" — an unclassified
    scope is a bug to surface, not treat as the platform plane.
    """
    if isinstance(scope, TenantScope):
        return "tenant", str(scope.tenant_id)
    if isinstance(scope, PlatformScope):
        return "platform", None
    raise TypeError(f"unsupported file scope type {type(scope).__name__}")


def _plan_digest(
    *,
    scope_kind: str,
    tenant_id: str | None,
    provider_code: str,
    older_than: datetime,
    candidate_keys: tuple[str, ...],
    old_managed_objects: int,
    old_referenced_objects: int,
    missing_references: int,
    boundary_drift: int,
    plan_observed_at: datetime,
) -> str:
    """Digest payload covers ONLY values derived from objects OLDER than
    ``older_than`` (plus the plan's identity and its real observation time).
    A fresh upload or a fresh, not-yet-past-grace metadata row must never
    change any of these — see the module docstring.
    """
    payload = json.dumps(
        {
            "scope_kind": scope_kind,
            "tenant_id": tenant_id,
            "provider_code": provider_code,
            "older_than": older_than.isoformat(),
            "candidate_keys": list(candidate_keys),
            "old_managed_objects": old_managed_objects,
            "old_referenced_objects": old_referenced_objects,
            "missing_references": missing_references,
            "boundary_drift": boundary_drift,
            "plan_observed_at": plan_observed_at.isoformat(),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class OrphanCleanupPlan:
    """A reviewed, digest-bound set of orphan candidate keys.

    ``scope`` is the raw scope object from the report (needed by the caller
    to invoke the storage deletion seam); ``scope_kind``/``tenant_id`` are
    its safe, loggable projection. ``plan_observed_at`` is the REAL moment
    this plan's underlying report was observed — supplied by the caller,
    never derived from ``older_than`` — which is what makes plan expiry
    unforgeable (see :data:`MAX_PLAN_AGE_HOURS`).

    ``managed_objects``/``referenced_objects``/``in_flight_unreferenced`` are
    ALL-AGE counts kept for operator visibility only; the digest instead
    binds ``old_managed_objects``/``old_referenced_objects`` (objects at or
    past ``older_than``), so a fresh upload cannot destabilize it.
    """

    scope: object
    scope_kind: str
    tenant_id: str | None
    provider_code: str
    older_than: datetime
    candidate_keys: tuple[str, ...]
    max_deletions: int
    managed_objects: int
    referenced_objects: int
    in_flight_unreferenced: int
    old_managed_objects: int
    old_referenced_objects: int
    missing_references: int
    boundary_drift: int
    plan_observed_at: datetime
    plan_digest: str

    def safe_summary(self) -> dict[str, object]:
        """Bounded operational output without raw keys or customer metadata."""
        return {
            "scope": self.scope_kind,
            "tenant_id": self.tenant_id,
            "provider_code": self.provider_code,
            "older_than": self.older_than.isoformat(),
            "orphan_candidates": len(self.candidate_keys),
            "max_deletions": self.max_deletions,
            "managed_objects": self.managed_objects,
            "referenced_objects": self.referenced_objects,
            "in_flight_unreferenced": self.in_flight_unreferenced,
            "old_managed_objects": self.old_managed_objects,
            "old_referenced_objects": self.old_referenced_objects,
            "missing_references": self.missing_references,
            "boundary_drift": self.boundary_drift,
            "plan_observed_at": self.plan_observed_at.isoformat(),
            "candidate_key_digests": [
                hashlib.sha256(key.encode("utf-8")).hexdigest()
                for key in self.candidate_keys[:_MAX_SUMMARY_EVIDENCE]
            ],
            "candidate_evidence_omitted": max(
                0, len(self.candidate_keys) - _MAX_SUMMARY_EVIDENCE
            ),
            "plan_digest": self.plan_digest,
            "dry_run": True,
        }


def plan_orphan_cleanup(
    report: ObjectReconciliationReport,
    observed_at: datetime,
    *,
    max_deletions: int = DEFAULT_MAX_DELETIONS,
) -> OrphanCleanupPlan:
    """Build a reviewable, digest-bound cleanup plan from a read-only report.

    ``observed_at`` must be the REAL moment this report's underlying listing
    and metadata query were taken — for a dry-run, the task's own fresh
    ``datetime.now(UTC)``; for an apply rebuild, the operator-reviewed
    ``reviewed_plan_observed_at`` (so the rebuilt plan's digest can match the
    original). This function never derives it from ``older_than`` itself.

    Refuses outright (``CleanupCapExceeded``) if the requested cap itself is
    non-positive or exceeds :data:`MAX_DELETIONS_CEILING`; this does not
    check the candidate count against the cap — that check is deferred to
    ``authorize_apply``, since a dry-run plan is informational and must be
    reviewable even when it exceeds the cap. Likewise, an unsafe reference
    view or an expired plan is only refused at ``authorize_apply`` — a
    dry-run must still be able to SHOW an operator why it is unsafe.
    """
    if max_deletions < 1 or max_deletions > MAX_DELETIONS_CEILING:
        raise CleanupCapExceeded(
            f"max_deletions must be between 1 and {MAX_DELETIONS_CEILING}, "
            f"got {max_deletions}"
        )
    scope_kind, tenant_id = _scope_kind_and_tenant(report.scope)
    candidate_keys = tuple(sorted(report.candidate_keys))
    managed_objects = len(report.objects)
    referenced_objects = sum(1 for item in report.objects if item.referenced)
    in_flight_unreferenced = sum(
        1 for item in report.objects if not item.referenced and not item.old_enough
    )
    old_managed_objects = sum(1 for item in report.objects if item.old_enough)
    old_referenced_objects = sum(
        1 for item in report.objects if item.referenced and item.old_enough
    )
    missing_references = len(report.missing_references)
    boundary_drift = len(report.boundary_drift)
    plan_digest = _plan_digest(
        scope_kind=scope_kind,
        tenant_id=tenant_id,
        provider_code=report.provider_code,
        older_than=report.older_than,
        candidate_keys=candidate_keys,
        old_managed_objects=old_managed_objects,
        old_referenced_objects=old_referenced_objects,
        missing_references=missing_references,
        boundary_drift=boundary_drift,
        plan_observed_at=observed_at,
    )
    return OrphanCleanupPlan(
        scope=report.scope,
        scope_kind=scope_kind,
        tenant_id=tenant_id,
        provider_code=report.provider_code,
        older_than=report.older_than,
        candidate_keys=candidate_keys,
        max_deletions=max_deletions,
        managed_objects=managed_objects,
        referenced_objects=referenced_objects,
        in_flight_unreferenced=in_flight_unreferenced,
        old_managed_objects=old_managed_objects,
        old_referenced_objects=old_referenced_objects,
        missing_references=missing_references,
        boundary_drift=boundary_drift,
        plan_observed_at=observed_at,
        plan_digest=plan_digest,
    )


def authorize_apply(
    plan: OrphanCleanupPlan, *, expected_plan_digest: str, now: datetime
) -> None:
    """Authorize a deletion apply against a freshly recomputed plan.

    ``now`` must be the REAL current wall-clock time (never a reviewed
    value) — it is compared against ``plan.plan_observed_at``, which for an
    apply rebuild equals the operator-supplied ``reviewed_plan_observed_at``,
    to enforce :data:`MAX_PLAN_AGE_HOURS` using two independently
    unforgeable timestamps.

    Raises ``CleanupPlanDrift`` if the reviewed digest no longer matches this
    plan; ``CleanupCapExceeded`` if the candidate count exceeds the plan's
    own authorized cap; ``CleanupPlanExpired`` if the reviewed dry-run is
    older than :data:`MAX_PLAN_AGE_HOURS`; and ``CleanupUnsafeReferenceView``
    if the OLD-object reference view looks like a hidden-rows/RLS failure or
    any OLD boundary drift was found. Refuses outright rather than deleting a
    partial or unsafely-derived set.
    """
    if plan.plan_digest != expected_plan_digest:
        raise CleanupPlanDrift(
            "the reviewed plan digest no longer matches the current plan"
        )
    if len(plan.candidate_keys) > plan.max_deletions:
        raise CleanupCapExceeded(
            f"{len(plan.candidate_keys)} candidate keys exceed the "
            f"authorized cap of {plan.max_deletions}"
        )
    if now - plan.plan_observed_at > timedelta(hours=MAX_PLAN_AGE_HOURS):
        raise CleanupPlanExpired(
            f"the reviewed plan is older than {MAX_PLAN_AGE_HOURS} hours"
        )
    if (
        plan.old_managed_objects > 0
        and plan.old_referenced_objects == 0
        and plan.candidate_keys
    ):
        raise CleanupUnsafeReferenceView(
            "every old managed object appears unreferenced while candidates "
            "exist — this is indistinguishable from a hidden-rows or RLS "
            "failure and refuses rather than risk deleting a healthy tenant"
        )
    if plan.boundary_drift > 0:
        raise CleanupUnsafeReferenceView(
            "old metadata rows exist outside their declared scope prefix; "
            "the reference view cannot be trusted for deletion"
        )


__all__ = [
    "DEFAULT_MAX_DELETIONS",
    "MAX_DELETIONS_CEILING",
    "MAX_PLAN_AGE_HOURS",
    "MIN_APPLY_GRACE_HOURS",
    "CleanupCapExceeded",
    "CleanupPartialFailure",
    "CleanupPlanDrift",
    "CleanupPlanExpired",
    "CleanupUnsafeReferenceView",
    "OrphanCleanupError",
    "OrphanCleanupPlan",
    "authorize_apply",
    "is_storage_key_referenced",
    "plan_orphan_cleanup",
]
