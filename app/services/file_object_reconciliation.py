"""Read-only reconciliation of objects owned by the composed files module.

This service only observes one explicit provider and one explicit files scope.
Legacy ERP object prefixes are outside that scope and have separate owners.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol, cast, runtime_checkable
from uuid import UUID

from dotmac_files import (
    FileState,
    ObjectInfo,
    PlatformStoredFile,
    TenantStoredFile,
    find_orphan_keys,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

_MAX_SUMMARY_EVIDENCE = 100


@runtime_checkable
class _TenantFileScope(Protocol):
    """Structural view of the tenant scope created by ``app.tenancy``."""

    tenant_id: UUID


@dataclass(frozen=True, slots=True)
class ObjectEvidence:
    """One managed object; keys are opaque module-generated identifiers."""

    key: str
    size_bytes: int
    last_modified: datetime
    referenced: bool
    old_enough: bool

    @property
    def key_digest(self) -> str:
        return hashlib.sha256(self.key.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MissingReference:
    """A live metadata row with no object in the provider listing."""

    key_digest: str
    state: FileState


@dataclass(frozen=True, slots=True)
class BoundaryDrift:
    """A metadata key outside the scope declared by its row plane."""

    key_digest: str
    state: FileState


@dataclass(frozen=True, slots=True)
class ObjectReconciliationReport:
    scope: object
    provider_code: str
    older_than: datetime
    objects: tuple[ObjectEvidence, ...]
    candidate_keys: tuple[str, ...]
    missing_references: tuple[MissingReference, ...]
    boundary_drift: tuple[BoundaryDrift, ...]

    def safe_summary(self) -> dict[str, object]:
        """Bounded operational output without raw keys or customer metadata."""
        return {
            "scope": (
                "tenant" if isinstance(self.scope, _TenantFileScope) else "platform"
            ),
            "tenant_id": (
                str(self.scope.tenant_id)
                if isinstance(self.scope, _TenantFileScope)
                else None
            ),
            "provider_code": self.provider_code,
            "older_than": self.older_than.isoformat(),
            "managed_objects": len(self.objects),
            "referenced_objects": sum(item.referenced for item in self.objects),
            "in_flight_unreferenced": sum(
                not item.referenced and not item.old_enough for item in self.objects
            ),
            "orphan_candidates": len(self.candidate_keys),
            "missing_references": len(self.missing_references),
            "boundary_drift": len(self.boundary_drift),
            "candidate_key_digests": [
                hashlib.sha256(key.encode("utf-8")).hexdigest()
                for key in self.candidate_keys[:_MAX_SUMMARY_EVIDENCE]
            ],
            "missing_key_digests": [
                item.key_digest
                for item in self.missing_references[:_MAX_SUMMARY_EVIDENCE]
            ],
            "boundary_drift_key_digests": [
                item.key_digest for item in self.boundary_drift[:_MAX_SUMMARY_EVIDENCE]
            ],
            "candidate_evidence_omitted": max(
                0, len(self.candidate_keys) - _MAX_SUMMARY_EVIDENCE
            ),
            "missing_evidence_omitted": max(
                0, len(self.missing_references) - _MAX_SUMMARY_EVIDENCE
            ),
            "boundary_drift_evidence_omitted": max(
                0, len(self.boundary_drift) - _MAX_SUMMARY_EVIDENCE
            ),
            "dry_run": True,
        }


def _managed_scope_prefix(scope: object) -> str:
    """ERP's pinned a4 managed-key contract, checked against public listing."""
    if isinstance(scope, _TenantFileScope):
        return f"tenants/{scope.tenant_id}/files/"
    return "platform/files/"


def report_file_objects(
    db: Session,
    *,
    scope: object,
    provider_code: str,
    observations: tuple[ObjectInfo, ...],
    observed_at: datetime,
    grace_period: timedelta = timedelta(hours=24),
) -> ObjectReconciliationReport:
    """Compare prelisted objects with the authoritative row plane, without provider I/O.

    The caller lists with public ``dotmac_files.list_objects`` before opening
    the session. The grace period covers uploads and metadata rows that may not
    yet appear in both observations. This report is never deletion authority.
    """
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("observed_at must be timezone-aware")
    if grace_period < timedelta(hours=1):
        raise ValueError("grace_period must be at least one hour")
    prefix = _managed_scope_prefix(scope)
    older_than = observed_at - grace_period
    for item in observations:
        if item.last_modified.tzinfo is None or item.last_modified.utcoffset() is None:
            raise ValueError("object modification time must be timezone-aware")
        if item.size_bytes < 0:
            raise ValueError("object size must be nonnegative")
    observed_keys = {item.key for item in observations}
    if len(observed_keys) != len(observations):
        raise ValueError("object listing contains duplicate keys")

    # This public module service validates the provider code and every
    # observation against the scope prefix before our comparison uses them.
    candidates = find_orphan_keys(
        db,
        scope=cast(Any, scope),
        provider_code=provider_code,
        observations=observations,
        older_than=older_than,
    )
    if isinstance(scope, _TenantFileScope):
        query = select(
            TenantStoredFile.storage_key,
            TenantStoredFile.state,
            (TenantStoredFile.created_at < older_than).label("past_grace"),
        ).where(
            TenantStoredFile.tenant_id == scope.tenant_id,
            TenantStoredFile.provider_code == provider_code,
        )
    else:
        query = select(
            PlatformStoredFile.storage_key,
            PlatformStoredFile.state,
            (PlatformStoredFile.created_at < older_than).label("past_grace"),
        ).where(PlatformStoredFile.provider_code == provider_code)
    rows = tuple(db.execute(query).all())
    referenced_keys = {key for key, _state, _past_grace in rows}
    objects = tuple(
        ObjectEvidence(
            key=item.key,
            size_bytes=item.size_bytes,
            last_modified=item.last_modified,
            referenced=item.key in referenced_keys,
            old_enough=item.last_modified < older_than,
        )
        for item in observations
    )
    missing = tuple(
        MissingReference(
            key_digest=hashlib.sha256(key.encode("utf-8")).hexdigest(),
            state=FileState(state),
        )
        for key, state, past_grace in rows
        if key.startswith(prefix)
        and key not in observed_keys
        and past_grace
        and FileState(state) in {FileState.AVAILABLE, FileState.MISSING}
    )
    drift = tuple(
        BoundaryDrift(
            key_digest=hashlib.sha256(key.encode("utf-8")).hexdigest(),
            state=FileState(state),
        )
        for key, state, past_grace in rows
        if not key.startswith(prefix) and past_grace
    )
    return ObjectReconciliationReport(
        scope=scope,
        provider_code=provider_code,
        older_than=older_than,
        objects=objects,
        candidate_keys=candidates,
        missing_references=missing,
        boundary_drift=drift,
    )


__all__ = [
    "BoundaryDrift",
    "MissingReference",
    "ObjectEvidence",
    "ObjectReconciliationReport",
    "report_file_objects",
]
