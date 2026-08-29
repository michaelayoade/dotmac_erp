"""Quarantined pre-activation bootstrap for Employment Type.

This service is reachable only from ``scripts/bootstrap_people_employment_types.py``.
It deliberately does not replace an ERP reader or writer: legacy
``hr.employment_type`` remains the source, and the released ``dotmac-people``
reconciliation API is only a target-side bootstrap mechanism.

The module import is intentionally lazy so application-process startup does not
activate this path. ERP pins the immutable a2 artifact, and execution still
checks the installed version and reviewed public names so environment drift
fails closed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.people.hr import EmploymentType
from app.services.people.hr.replacement_projection import (
    people_projection_fingerprint,
)
from app.tenancy import OrganizationTenantContext

PEOPLE_BOOTSTRAP_VERSION = "0.1.0a2"


class EmploymentTypeBootstrapError(RuntimeError):
    """The bootstrap cannot produce trustworthy cutover evidence."""


class DotmacPeopleA2RequiredError(EmploymentTypeBootstrapError):
    """The exact reviewed bootstrap API is not installed."""


class BootstrapMode(str, Enum):
    """The three explicit and mutually exclusive operator intents."""

    DRY_RUN = "dry-run"
    COMMIT = "commit"
    REPLAY = "replay"


@dataclass(frozen=True, slots=True)
class EmploymentTypeSourceRecord:
    source_id: UUID
    source_fingerprint: str
    source_created_at: datetime
    source_updated_at: datetime | None
    code: str
    name: str
    description: str | None
    is_active: bool


@dataclass(frozen=True, slots=True)
class EmploymentTypeTargetRecord:
    target_id: UUID
    target_fingerprint: str


@dataclass(frozen=True, slots=True)
class EmploymentTypeReconcileResult:
    action: str
    target_id: UUID
    source_fingerprint: str
    target_fingerprint: str


@dataclass(frozen=True, slots=True)
class EmploymentTypeBootstrapResult:
    organization_id: UUID
    tenant_id: UUID
    mode: BootstrapMode
    source_count: int
    target_before_count: int
    target_after_count: int
    created: int
    updated: int
    unchanged: int
    source_fingerprint_set_digest: str
    target_before_fingerprint_set_digest: str
    target_after_fingerprint_set_digest: str


class EmploymentTypeSource(Protocol):
    def fence(self) -> None: ...

    def scan(self, *, page_size: int) -> tuple[EmploymentTypeSourceRecord, ...]: ...


class EmploymentTypeTarget(Protocol):
    def scan(self, *, page_size: int) -> tuple[EmploymentTypeTargetRecord, ...]: ...

    def reconcile(
        self, source: EmploymentTypeSourceRecord
    ) -> EmploymentTypeReconcileResult: ...


def _framed(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def fingerprint_set_digest(*, kind: str, items: Iterable[tuple[UUID, str]]) -> str:
    """Hash one order-independent UUID/fingerprint set with explicit framing."""
    if kind not in {"source", "target"}:
        raise ValueError("fingerprint set kind must be 'source' or 'target'")
    ordered = sorted(items, key=lambda item: item[0].bytes)
    seen: set[UUID] = set()
    payload = bytearray(
        _framed(b"dotmac-erp/employment-type-bootstrap/fingerprint-set/v1")
    )
    payload.extend(_framed(kind.encode("ascii")))
    payload.extend(len(ordered).to_bytes(8, "big"))
    for record_id, fingerprint in ordered:
        if not isinstance(record_id, UUID):
            raise TypeError("fingerprint-set ids must be UUIDs")
        if record_id in seen:
            raise EmploymentTypeBootstrapError(
                f"duplicate {kind} Employment Type id {record_id}"
            )
        seen.add(record_id)
        if not isinstance(fingerprint, str):
            raise EmploymentTypeBootstrapError(
                f"{kind} Employment Type {record_id} has an invalid fingerprint"
            )
        expected_prefix = "" if kind == "source" else "et1:"
        digest = fingerprint.removeprefix(expected_prefix)
        if (
            not fingerprint.startswith(expected_prefix)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise EmploymentTypeBootstrapError(
                f"{kind} Employment Type {record_id} has an invalid fingerprint"
            )
        payload.extend(_framed(record_id.bytes))
        payload.extend(_framed(fingerprint.encode("ascii")))
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _source_index(
    records: tuple[EmploymentTypeSourceRecord, ...],
) -> dict[UUID, EmploymentTypeSourceRecord]:
    indexed: dict[UUID, EmploymentTypeSourceRecord] = {}
    for record in records:
        if record.source_id in indexed:
            raise EmploymentTypeBootstrapError(
                f"duplicate source Employment Type id {record.source_id}"
            )
        indexed[record.source_id] = record
    return indexed


def _target_index(
    records: tuple[EmploymentTypeTargetRecord, ...],
) -> dict[UUID, EmploymentTypeTargetRecord]:
    indexed: dict[UUID, EmploymentTypeTargetRecord] = {}
    for record in records:
        if record.target_id in indexed:
            raise EmploymentTypeBootstrapError(
                f"duplicate target Employment Type id {record.target_id}"
            )
        indexed[record.target_id] = record
    return indexed


class LegacyEmploymentTypeSource:
    """Complete, keyset-paged reads from ERP's still-authoritative table."""

    def __init__(self, db: Session, *, organization_id: UUID):
        self._db = db
        self._organization_id = organization_id

    def fence(self) -> None:
        """Prevent legacy DML until the bootstrap transaction finishes.

        ``app_user`` deliberately has no legacy UPDATE/DELETE/TRUNCATE privilege,
        so PostgreSQL will not let it acquire SHARE directly.  The migration's
        fixed-search-path SECURITY DEFINER function exposes only this lock and
        keeps the resulting table lock transaction-scoped through the CLI's
        commit or rollback.
        """
        if self._db.get_bind().dialect.name != "postgresql":
            return
        self._db.execute(text("SELECT hr.lock_employment_type_bootstrap()"))

    def scan(self, *, page_size: int) -> tuple[EmploymentTypeSourceRecord, ...]:
        after: UUID | None = None
        records: list[EmploymentTypeSourceRecord] = []
        while True:
            statement = (
                select(
                    EmploymentType.employment_type_id,
                    EmploymentType.created_at,
                    EmploymentType.updated_at,
                    EmploymentType.type_code,
                    EmploymentType.type_name,
                    EmploymentType.description,
                    EmploymentType.is_active,
                )
                .where(EmploymentType.organization_id == self._organization_id)
                .order_by(EmploymentType.employment_type_id)
                .limit(page_size)
            )
            if after is not None:
                statement = statement.where(EmploymentType.employment_type_id > after)
            rows = self._db.execute(statement).all()
            for row in rows:
                payload: dict[str, object] = {
                    "code": row.type_code,
                    "name": row.type_name,
                    "description": row.description,
                    "is_active": row.is_active,
                }
                records.append(
                    EmploymentTypeSourceRecord(
                        source_id=row.employment_type_id,
                        source_fingerprint=people_projection_fingerprint(payload),
                        source_created_at=row.created_at,
                        source_updated_at=row.updated_at,
                        code=row.type_code,
                        name=row.type_name,
                        description=row.description,
                        is_active=row.is_active,
                    )
                )
            if len(rows) < page_size:
                break
            after = rows[-1].employment_type_id
        return tuple(records)


@dataclass(frozen=True, slots=True)
class _PeopleA2Api:
    EmploymentTypeQuery: Any
    ReconcileEmploymentType: Any
    employment_type_fingerprint: Any
    list_employment_types: Any
    reconcile_employment_type: Any


def _load_people_a2() -> _PeopleA2Api:
    # This is the one reviewed bootstrap seam permitted by the composition
    # guard. Keep it direct so the AST guard can see it; do not hide it behind
    # importlib or a provider-name string.
    import dotmac_people

    observed = getattr(dotmac_people, "__version__", None)
    if observed != PEOPLE_BOOTSTRAP_VERSION:
        raise DotmacPeopleA2RequiredError(
            "Employment Type bootstrap requires the immutable "
            f"dotmac-people=={PEOPLE_BOOTSTRAP_VERSION} API; installed version is "
            f"{observed!r}. Publish a2 first, then update ERP's exact pin and lock."
        )
    names = (
        "EmploymentTypeQuery",
        "ReconcileEmploymentType",
        "employment_type_fingerprint",
        "list_employment_types",
        "reconcile_employment_type",
    )
    public = set(getattr(dotmac_people, "__all__", ()))
    missing = sorted(name for name in names if name not in public)
    if missing:
        raise DotmacPeopleA2RequiredError(
            f"dotmac-people=={PEOPLE_BOOTSTRAP_VERSION} is missing reviewed public "
            f"bootstrap names: {missing}"
        )
    return _PeopleA2Api(**{name: getattr(dotmac_people, name) for name in names})


class DotmacPeopleEmploymentTypeTarget:
    """Narrow adapter over the exact published a2 reconciliation surface."""

    def __init__(self, db: Session, *, context: OrganizationTenantContext):
        self._db = db
        self._scope = context.tenant_scope
        self._api = _load_people_a2()

    def scan(self, *, page_size: int) -> tuple[EmploymentTypeTargetRecord, ...]:
        offset = 0
        expected_total: int | None = None
        records: list[EmploymentTypeTargetRecord] = []
        while expected_total is None or offset < expected_total:
            page = self._api.list_employment_types(
                self._db,
                scope=self._scope,
                query=self._api.EmploymentTypeQuery(offset=offset, limit=page_size),
            )
            if expected_total is None:
                expected_total = page.total
            elif page.total != expected_total:
                raise EmploymentTypeBootstrapError(
                    "dotmac-people Employment Type target changed during a complete scan"
                )
            if not page.items and offset < expected_total:
                raise EmploymentTypeBootstrapError(
                    "dotmac-people Employment Type target scan ended before its total"
                )
            for record in page.items:
                records.append(
                    EmploymentTypeTargetRecord(
                        target_id=record.id,
                        target_fingerprint=self._api.employment_type_fingerprint(
                            scope=self._scope,
                            employment_type_id=record.id,
                            code=record.code,
                            name=record.name,
                            description=record.description,
                            is_active=record.is_active,
                        ),
                    )
                )
            offset += len(page.items)
        return tuple(records)

    def reconcile(
        self, source: EmploymentTypeSourceRecord
    ) -> EmploymentTypeReconcileResult:
        outcome = self._api.reconcile_employment_type(
            self._db,
            scope=self._scope,
            command=self._api.ReconcileEmploymentType(
                source_id=source.source_id,
                source_fingerprint=source.source_fingerprint,
                source_created_at=source.source_created_at,
                source_updated_at=source.source_updated_at,
                code=source.code,
                name=source.name,
                description=source.description,
                is_active=source.is_active,
            ),
        )
        return EmploymentTypeReconcileResult(
            action=str(outcome.action.value),
            target_id=outcome.record.id,
            source_fingerprint=outcome.source_fingerprint,
            target_fingerprint=outcome.target_fingerprint,
        )


class EmploymentTypeBootstrapService:
    """Run one sealed bootstrap transaction for a single organization."""

    def __init__(
        self,
        db: Session,
        *,
        organization_id: UUID,
        source: EmploymentTypeSource | None = None,
        target: EmploymentTypeTarget | None = None,
    ):
        context = OrganizationTenantContext.for_organization(organization_id)
        if db.info.get("organization_id") != context.organization_id:
            raise EmploymentTypeBootstrapError(
                "bootstrap session is not primed for the requested organization"
            )
        if db.info.get("tenant_id") != context.tenant_id:
            raise EmploymentTypeBootstrapError(
                "bootstrap session is not primed for the mapped People tenant"
            )
        self._db = db
        self._context = context
        self._source = source or LegacyEmploymentTypeSource(
            db, organization_id=organization_id
        )
        self._target = target or DotmacPeopleEmploymentTypeTarget(db, context=context)

    def execute(
        self, *, mode: BootstrapMode, page_size: int = 200
    ) -> EmploymentTypeBootstrapResult:
        if not isinstance(mode, BootstrapMode):
            raise TypeError("bootstrap mode must be a BootstrapMode")
        if not 1 <= page_size <= 200:
            raise ValueError("bootstrap page size must be between 1 and 200")

        # Seal the legacy source before the first page is read. PostgreSQL holds
        # the SHARE lock through the adapter-owned commit/rollback, so both full
        # scans describe one DML-stable source rather than two best-effort reads.
        self._source.fence()
        source_first = _source_index(self._source.scan(page_size=page_size))
        target_before = _target_index(self._target.scan(page_size=page_size))
        extras = sorted(
            set(target_before) - set(source_first), key=lambda value: value.bytes
        )
        if extras:
            raise EmploymentTypeBootstrapError(
                "target contains Employment Type ids absent from the complete legacy "
                f"source scan: {[str(value) for value in extras]}"
            )
        if mode is BootstrapMode.COMMIT and target_before:
            raise EmploymentTypeBootstrapError(
                "--commit is initial-bootstrap only and requires an empty target; "
                "use --replay for an established target"
            )
        if mode is BootstrapMode.REPLAY and not target_before and source_first:
            raise EmploymentTypeBootstrapError(
                "--replay requires an established non-empty target when the legacy "
                "source is non-empty; use --commit for the initial bootstrap"
            )

        outcomes: dict[UUID, EmploymentTypeReconcileResult] = {}
        counts = {"CREATED": 0, "UPDATED": 0, "UNCHANGED": 0}
        for source_record in sorted(
            source_first.values(), key=lambda record: record.source_id.bytes
        ):
            outcome = self._target.reconcile(source_record)
            if outcome.target_id != source_record.source_id:
                raise EmploymentTypeBootstrapError(
                    f"target changed source id {source_record.source_id} to "
                    f"{outcome.target_id}"
                )
            if outcome.source_fingerprint != source_record.source_fingerprint:
                raise EmploymentTypeBootstrapError(
                    f"target did not preserve source fingerprint for "
                    f"{source_record.source_id}"
                )
            if outcome.action not in counts:
                raise EmploymentTypeBootstrapError(
                    f"unknown reconciliation action {outcome.action!r}"
                )
            outcomes[outcome.target_id] = outcome
            counts[outcome.action] += 1

        # This is deliberately a second COMPLETE source scan, after every
        # target mutation and before the CLI is allowed to commit. Comparing
        # the full immutable records catches timestamp-only and payload changes
        # even when a faulty fingerprint implementation would not.
        source_second = _source_index(self._source.scan(page_size=page_size))
        if source_second != source_first:
            raise EmploymentTypeBootstrapError(
                "legacy Employment Type source changed between the two complete "
                "scans; refusing to commit partial evidence"
            )

        target_after = _target_index(self._target.scan(page_size=page_size))
        if set(target_after) != set(source_second):
            missing = sorted(
                set(source_second) - set(target_after), key=lambda v: v.bytes
            )
            extras = sorted(
                set(target_after) - set(source_second), key=lambda v: v.bytes
            )
            raise EmploymentTypeBootstrapError(
                "target/source id sets differ after reconciliation: "
                f"missing={[str(v) for v in missing]} extras={[str(v) for v in extras]}"
            )
        for target_id, outcome in outcomes.items():
            if target_after[target_id].target_fingerprint != outcome.target_fingerprint:
                raise EmploymentTypeBootstrapError(
                    f"target fingerprint changed after reconciliation for {target_id}"
                )

        source_digest = fingerprint_set_digest(
            kind="source",
            items=(
                (record.source_id, record.source_fingerprint)
                for record in source_second.values()
            ),
        )
        before_digest = fingerprint_set_digest(
            kind="target",
            items=(
                (record.target_id, record.target_fingerprint)
                for record in target_before.values()
            ),
        )
        after_digest = fingerprint_set_digest(
            kind="target",
            items=(
                (record.target_id, record.target_fingerprint)
                for record in target_after.values()
            ),
        )
        return EmploymentTypeBootstrapResult(
            organization_id=self._context.organization_id,
            tenant_id=self._context.tenant_id,
            mode=mode,
            source_count=len(source_second),
            target_before_count=len(target_before),
            target_after_count=len(target_after),
            created=counts["CREATED"],
            updated=counts["UPDATED"],
            unchanged=counts["UNCHANGED"],
            source_fingerprint_set_digest=source_digest,
            target_before_fingerprint_set_digest=before_digest,
            target_after_fingerprint_set_digest=after_digest,
        )


__all__ = [
    "BootstrapMode",
    "DotmacPeopleA2RequiredError",
    "EmploymentTypeBootstrapError",
    "EmploymentTypeBootstrapResult",
    "EmploymentTypeBootstrapService",
    "EmploymentTypeReconcileResult",
    "EmploymentTypeSourceRecord",
    "EmploymentTypeTargetRecord",
    "PEOPLE_BOOTSTRAP_VERSION",
    "fingerprint_set_digest",
]
