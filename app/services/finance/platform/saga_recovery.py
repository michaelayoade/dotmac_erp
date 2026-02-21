"""
SagaRecoveryService - Recovery and monitoring for stuck sagas.

Handles detection and recovery of sagas that may have become stuck
due to process crashes or network failures.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, delete, func, select
from sqlalchemy.orm import Session

from app.models.finance.platform.saga_execution import (
    SagaExecution,
    SagaStatus,
    SagaStep,
    StepStatus,
)
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


class SagaRecoveryService:
    """
    Service for recovering stuck sagas.

    Sagas can become stuck if the process crashes during execution.
    This service detects and recovers them.
    """

    # Time after which an EXECUTING saga is considered stuck
    STUCK_THRESHOLD_MINUTES: int = 30

    # Maximum compensation retries before marking as FAILED
    MAX_COMPENSATION_RETRIES: int = 3

    @staticmethod
    def find_stuck_sagas(
        db: Session,
        organization_id: UUID | None = None,
        threshold_minutes: int | None = None,
    ) -> list[SagaExecution]:
        """
        Find sagas that appear to be stuck.

        A saga is considered stuck if it has been in EXECUTING or
        COMPENSATING status for longer than the threshold.

        Args:
            db: Database session
            organization_id: Optional filter by organization
            threshold_minutes: Override default threshold

        Returns:
            List of stuck sagas
        """
        threshold = threshold_minutes or SagaRecoveryService.STUCK_THRESHOLD_MINUTES
        cutoff_time = datetime.now(UTC) - timedelta(minutes=threshold)

        stmt = select(SagaExecution).where(
            and_(
                SagaExecution.status.in_(
                    [
                        SagaStatus.EXECUTING,
                        SagaStatus.COMPENSATING,
                    ]
                ),
                SagaExecution.started_at < cutoff_time,
            )
        )

        if organization_id:
            stmt = stmt.where(
                SagaExecution.organization_id == coerce_uuid(organization_id)
            )

        stuck = list(db.scalars(stmt))

        if stuck:
            logger.warning(
                "Found %d stuck sagas older than %d minutes", len(stuck), threshold
            )

        return stuck

    @staticmethod
    def recover_saga(
        db: Session,
        saga_id: UUID,
        force_compensate: bool = False,
    ) -> bool:
        """
        Attempt to recover a stuck saga.

        If force_compensate is True, the saga will be compensated.
        Otherwise, it will attempt to resume from the current step.

        Args:
            db: Database session
            saga_id: Saga to recover
            force_compensate: Force compensation instead of resume

        Returns:
            True if recovery succeeded
        """
        from app.services.finance.platform.saga_factory import saga_factory

        saga = db.get(SagaExecution, coerce_uuid(saga_id))
        if not saga:
            logger.error("Saga %s not found for recovery", saga_id)
            return False

        if saga.status in {
            SagaStatus.COMPLETED,
            SagaStatus.COMPENSATED,
            SagaStatus.FAILED,
        }:
            logger.info(
                "Saga %s already in terminal state: %s", saga_id, saga.status.value
            )
            return True

        logger.info(
            "Recovering saga %s type=%s status=%s current_step=%d",
            saga_id,
            saga.saga_type,
            saga.status.value,
            saga.current_step,
        )

        # Get the orchestrator for this saga type
        orchestrator = saga_factory.get_orchestrator(saga.saga_type)
        if not orchestrator:
            logger.error("No orchestrator registered for saga type: %s", saga.saga_type)
            return False

        if force_compensate or saga.status == SagaStatus.COMPENSATING:
            # Force compensation
            result = orchestrator._compensate_steps(db, saga)
            return bool(result.was_compensated)
        else:
            # Attempt to resume
            result = orchestrator._execute_steps(db, saga)
            return bool(result.success)

    @staticmethod
    def mark_failed(
        db: Session,
        saga_id: UUID,
        reason: str,
    ) -> SagaExecution:
        """
        Manually mark a saga as failed.

        Use this when manual intervention is required and the saga
        cannot be recovered automatically.

        Args:
            db: Database session
            saga_id: Saga to mark as failed
            reason: Reason for failure

        Returns:
            Updated saga
        """
        saga = db.get(SagaExecution, coerce_uuid(saga_id))
        if not saga:
            raise ValueError(f"Saga {saga_id} not found")

        saga.status = SagaStatus.FAILED
        saga.error_message = reason
        saga.completed_at = datetime.now(UTC)

        db.commit()
        db.refresh(saga)

        logger.warning("Manually marked saga %s as FAILED: %s", saga_id, reason)

        return saga

    @staticmethod
    def get_saga_health_summary(
        db: Session,
        organization_id: UUID | None = None,
    ) -> dict:
        """
        Get health summary of sagas.

        Args:
            db: Database session
            organization_id: Optional filter by organization

        Returns:
            Dictionary with status counts and stuck saga info
        """
        stmt = select(
            SagaExecution.status,
            func.count(SagaExecution.saga_id).label("count"),
        )

        if organization_id:
            stmt = stmt.where(
                SagaExecution.organization_id == coerce_uuid(organization_id)
            )

        status_counts: dict[SagaStatus, int] = {
            status: count
            for status, count in db.execute(stmt.group_by(SagaExecution.status)).all()
        }

        stuck = SagaRecoveryService.find_stuck_sagas(db, organization_id)

        return {
            "status_counts": {s.value: c for s, c in status_counts.items()},
            "stuck_count": len(stuck),
            "stuck_sagas": [
                {
                    "saga_id": str(s.saga_id),
                    "saga_type": s.saga_type,
                    "status": s.status.value,
                    "current_step": s.current_step,
                    "started_at": s.started_at.isoformat(),
                }
                for s in stuck[:10]  # Limit to 10 for summary
            ],
        }

    @staticmethod
    def retry_compensation(
        db: Session,
        saga_id: UUID,
    ) -> bool:
        """
        Retry compensation for a saga that failed during rollback.

        Args:
            db: Database session
            saga_id: Saga to retry

        Returns:
            True if compensation completed successfully
        """
        from app.services.finance.platform.saga_factory import saga_factory

        saga = db.get(SagaExecution, coerce_uuid(saga_id))
        if not saga:
            raise ValueError(f"Saga {saga_id} not found")

        if saga.status != SagaStatus.FAILED:
            logger.info(
                "Saga %s is not in FAILED status, cannot retry compensation", saga_id
            )
            return False

        # Check if any steps need compensation
        steps_to_compensate = (
            db.scalar(
                select(func.count())
                .select_from(SagaStep)
                .where(
                    SagaStep.saga_id == saga_id,
                    SagaStep.status == StepStatus.COMPLETED,
                )
            )
            or 0
        )

        if steps_to_compensate == 0:
            logger.info("Saga %s has no steps to compensate", saga_id)
            saga.status = SagaStatus.COMPENSATED
            db.commit()
            return True

        orchestrator = saga_factory.get_orchestrator(saga.saga_type)
        if not orchestrator:
            raise ValueError(f"No orchestrator for saga type: {saga.saga_type}")

        saga.status = SagaStatus.COMPENSATING
        db.commit()

        result = orchestrator._compensate_steps(db, saga)
        return bool(result.was_compensated)

    @staticmethod
    def cleanup_old_completed_sagas(
        db: Session,
        days_to_keep: int = 90,
        batch_size: int = 1000,
    ) -> int:
        """
        Clean up old completed sagas.

        Deletes sagas in terminal states (COMPLETED, COMPENSATED, FAILED)
        that are older than the retention period.

        Args:
            db: Database session
            days_to_keep: Retention period in days
            batch_size: Maximum records to delete per call

        Returns:
            Number of deleted sagas
        """
        cutoff_date = datetime.now(UTC) - timedelta(days=days_to_keep)

        # Get IDs to delete
        saga_ids = list(
            db.scalars(
                select(SagaExecution.saga_id)
                .where(
                    SagaExecution.status.in_(
                        [
                            SagaStatus.COMPLETED,
                            SagaStatus.COMPENSATED,
                            SagaStatus.FAILED,
                        ]
                    ),
                    SagaExecution.completed_at < cutoff_date,
                )
                .limit(batch_size)
            )
        )

        if not saga_ids:
            return 0

        ids_to_delete = list(saga_ids)

        # Delete in batches (steps cascade)
        deleted = (
            db.execute(
                delete(SagaExecution).where(SagaExecution.saga_id.in_(ids_to_delete))
            ).rowcount
            or 0
        )

        db.commit()

        logger.info("Cleaned up %d old sagas", deleted)
        return deleted


# Module-level singleton
saga_recovery_service = SagaRecoveryService()
