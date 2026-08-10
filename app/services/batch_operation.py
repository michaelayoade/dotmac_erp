"""The owner of batch-run records: who ran what, when, over which org.

`BatchOperation` (model, table and migration) shipped in January 2026 and was
never used — no service, no route, no template, no caller. Meanwhile 100+
scripts under `scripts/` did the work it was designed to record, leaving no
trace of who ran them, against which organization, or what they touched.

This module is the writer. Everything that performs a batch operation — a
Celery task, an admin-triggered action, a break-glass CLI run — opens one of
these, and the row is what the admin UI reads.

## Why the record is committed before the work

An audit record that vanishes with the failure it was recording is worse than
no record: the run that most needs explaining is exactly the one that leaves
nothing behind. So `batch_operation()`:

1. INSERTs the RUNNING row and **commits it immediately**, before the body
   runs. A process killed mid-flight therefore leaves a RUNNING row that is
   visibly stuck, rather than silence.
2. Runs the body inside the caller's own transaction, which the caller owns
   and which this module does not touch.
3. On success, marks the row COMPLETED and commits that.
4. On failure, rolls the *work* back, then records FAILED with the error in a
   fresh transaction, then re-raises.

Step 4 is the whole point and the reason this cannot be a plain
`try/finally` around a single transaction: the rollback that undoes the work
would also undo the record of it having been attempted.

`db.commit()` here is deliberate and scoped to the journal row. It is the
same shape as an outbox or an audit event — a record whose durability must
not be conditional on the success of the thing it describes.

## Scope

`organization_id` is required and never inferred. These runs are the ones
that historically executed with no tenant scope at all (see
`scripts/check_session_context.py`), so the owner asks for it explicitly
rather than reading ambient state.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.batch_operation import (
    BatchOperation,
    BatchOperationStatus,
    BatchOperationType,
)

logger = logging.getLogger(__name__)


@dataclass
class BatchTally:
    """Mutable counters the body fills in as it works."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0
    entities: dict[str, list[str]] = field(default_factory=dict)

    def track(self, entity_type: str, entity_id: uuid.UUID) -> None:
        self.entities.setdefault(entity_type, []).append(str(entity_id))


class BatchOperationService:
    """Creates, completes and fails batch-run records."""

    @staticmethod
    def start(
        db: Session,
        *,
        organization_id: uuid.UUID,
        operation_type: BatchOperationType,
        operation_name: str,
        started_by_id: uuid.UUID,
        description: str | None = None,
        source_file: str | None = None,
    ) -> BatchOperation:
        """Record a run as RUNNING and commit it, so it survives a later failure."""
        record = BatchOperation(
            organization_id=organization_id,
            operation_type=operation_type,
            operation_name=operation_name,
            started_by_id=started_by_id,
            description=description,
            source_file=source_file,
            status=BatchOperationStatus.RUNNING,
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        logger.info(
            "batch %s started: %s (org=%s)", record.id, operation_name, organization_id
        )
        return record

    @staticmethod
    def complete(db: Session, record: BatchOperation, tally: BatchTally) -> None:
        record.mark_completed(
            created=tally.created,
            updated=tally.updated,
            skipped=tally.skipped,
            failed=tally.failed,
        )
        if tally.entities:
            record.created_entity_ids = tally.entities
        db.commit()
        logger.info(
            "batch %s completed: +%d ~%d =%d !%d",
            record.id,
            tally.created,
            tally.updated,
            tally.skipped,
            tally.failed,
        )

    @staticmethod
    def fail(db: Session, record: BatchOperation, error: str) -> None:
        """Record a failure in a FRESH transaction.

        The caller's work has just been rolled back; without the rollback
        first, this commit would persist the very changes the failure should
        have undone.
        """
        db.rollback()
        db.add(record)
        record.mark_failed(error)
        db.commit()
        logger.warning("batch %s failed: %s", record.id, error)

    @staticmethod
    def recent(
        db: Session, *, organization_id: uuid.UUID, limit: int = 50
    ) -> list[BatchOperation]:
        stmt = (
            select(BatchOperation)
            .where(BatchOperation.organization_id == organization_id)
            .order_by(desc(BatchOperation.started_at))
            .limit(limit)
        )
        return list(db.scalars(stmt).all())

    @staticmethod
    def get(
        db: Session, *, organization_id: uuid.UUID, operation_id: uuid.UUID
    ) -> BatchOperation | None:
        stmt = select(BatchOperation).where(
            BatchOperation.id == operation_id,
            BatchOperation.organization_id == organization_id,
        )
        return db.scalar(stmt)


batch_operation_service = BatchOperationService()


@contextmanager
def batch_operation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    operation_type: BatchOperationType,
    operation_name: str,
    started_by_id: uuid.UUID,
    description: str | None = None,
    source_file: str | None = None,
) -> Iterator[BatchTally]:
    """Run a batch operation, recording it whether it succeeds or not.

        with batch_operation(
            db,
            organization_id=org_id,
            operation_type=BatchOperationType.SCRIPT,
            operation_name="reconcile_invoice_amount_paid",
            started_by_id=actor_id,
        ) as tally:
            ...
            tally.updated += 1

    Yields a `BatchTally` for the body to fill in. On success the counts are
    written to the record; on failure the error is recorded and the exception
    re-raised unchanged — this never swallows.
    """
    record = BatchOperationService.start(
        db,
        organization_id=organization_id,
        operation_type=operation_type,
        operation_name=operation_name,
        started_by_id=started_by_id,
        description=description,
        source_file=source_file,
    )
    tally = BatchTally()
    try:
        yield tally
    except Exception as exc:
        BatchOperationService.fail(db, record, f"{type(exc).__name__}: {exc}")
        raise
    BatchOperationService.complete(db, record, tally)
