"""
SagaOrchestrator - Base class for saga pattern implementation.

Provides the orchestration logic for executing multi-step distributed
transactions with automatic compensation on failure.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Generic, Optional, TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.finance.platform.saga_execution import (
    SagaExecution,
    SagaStatus,
    SagaStep,
    StepStatus,
)
from app.services.common import coerce_uuid

logger = logging.getLogger(__name__)


@dataclass
class StepResult:
    """Result of executing a saga step."""
    success: bool
    output_data: dict[str, Any] = field(default_factory=dict)
    compensation_data: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class SagaResult:
    """Result of saga execution."""
    success: bool
    saga_id: Optional[UUID] = None
    result: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    was_compensated: bool = False


T = TypeVar('T')


@dataclass
class SagaStepDefinition(Generic[T]):
    """Definition of a saga step."""
    name: str
    execute: Callable[[Session, dict[str, Any], dict[str, Any]], StepResult]
    compensate: Optional[Callable[[Session, dict[str, Any], dict[str, Any]], bool]] = None
    description: str = ""


class SagaOrchestrator(ABC):
    """
    Abstract base class for saga orchestration.

    Subclasses define their steps and the orchestrator handles:
    - Step execution in order
    - State persistence
    - Compensation (rollback) on failure
    - Idempotency
    """

    @property
    @abstractmethod
    def saga_type(self) -> str:
        """Unique identifier for this saga type."""
        pass

    @property
    @abstractmethod
    def steps(self) -> list[SagaStepDefinition]:
        """
        List of steps in execution order.

        Each step should be atomic and have a compensation function
        if it modifies state.
        """
        pass

    def execute(
        self,
        db: Session,
        organization_id: UUID,
        payload: dict[str, Any],
        idempotency_key: str,
        created_by_user_id: UUID,
        correlation_id: Optional[str] = None,
    ) -> SagaResult:
        """
        Execute the saga.

        Args:
            db: Database session
            organization_id: Organization scope
            payload: Input parameters for the saga
            idempotency_key: Unique key for deduplication
            created_by_user_id: User initiating the saga
            correlation_id: Optional correlation ID for tracing

        Returns:
            SagaResult with outcome
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Check for existing saga with same idempotency key
        existing = (
            db.query(SagaExecution)
            .filter(SagaExecution.idempotency_key == idempotency_key)
            .first()
        )

        if existing:
            return self._handle_existing_saga(db, existing)

        # Create new saga
        saga = SagaExecution(
            organization_id=org_id,
            saga_type=self.saga_type,
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            status=SagaStatus.PENDING,
            current_step=0,
            payload=payload,
            context={},
            created_by_user_id=user_id,
        )
        db.add(saga)
        db.flush()

        # Create step records
        for i, step_def in enumerate(self.steps):
            step = SagaStep(
                saga_id=saga.saga_id,
                step_number=i,
                step_name=step_def.name,
                status=StepStatus.PENDING,
            )
            db.add(step)

        db.commit()
        db.refresh(saga)

        logger.info(
            "Created saga %s type=%s idempotency=%s",
            saga.saga_id, self.saga_type, idempotency_key
        )

        # Execute steps
        return self._execute_steps(db, saga)

    def _handle_existing_saga(
        self,
        db: Session,
        saga: SagaExecution,
    ) -> SagaResult:
        """Handle an existing saga with the same idempotency key."""
        if saga.status == SagaStatus.COMPLETED:
            # Already completed - return cached result
            logger.info("Saga %s already completed (idempotent)", saga.saga_id)
            return SagaResult(
                success=True,
                saga_id=saga.saga_id,
                result=saga.result or {},
            )

        if saga.status == SagaStatus.COMPENSATED:
            # Was rolled back - treat as failure
            return SagaResult(
                success=False,
                saga_id=saga.saga_id,
                error=saga.error_message or "Saga was compensated",
                was_compensated=True,
            )

        if saga.status == SagaStatus.FAILED:
            # Permanently failed
            return SagaResult(
                success=False,
                saga_id=saga.saga_id,
                error=saga.error_message or "Saga failed",
            )

        if saga.status in {SagaStatus.EXECUTING, SagaStatus.COMPENSATING}:
            # In progress - try to resume
            logger.info("Resuming saga %s in status %s", saga.saga_id, saga.status.value)
            if saga.status == SagaStatus.COMPENSATING:
                return self._compensate_steps(db, saga)
            return self._execute_steps(db, saga)

        # PENDING - start execution
        return self._execute_steps(db, saga)

    def _execute_steps(
        self,
        db: Session,
        saga: SagaExecution,
    ) -> SagaResult:
        """Execute saga steps from current position."""
        saga.status = SagaStatus.EXECUTING
        db.commit()

        context = dict(saga.context)
        step_defs = self.steps

        for i in range(saga.current_step, len(step_defs)):
            step_def = step_defs[i]
            step = self._get_step(db, saga.saga_id, i)

            if step.status == StepStatus.COMPLETED:
                # Already done - merge output to context
                if step.output_data:
                    context.update(step.output_data)
                continue

            logger.info(
                "Executing saga %s step %d: %s",
                saga.saga_id, i, step_def.name
            )

            # Mark step as executing
            step.status = StepStatus.EXECUTING
            step.started_at = datetime.now(timezone.utc)
            step.input_data = {"payload": saga.payload, "context": context}
            db.commit()

            try:
                result = step_def.execute(db, saga.payload, context)
            except Exception as e:
                logger.exception(
                    "Saga %s step %d failed with exception",
                    saga.saga_id, i
                )
                result = StepResult(
                    success=False,
                    error=str(e),
                )

            if result.success:
                # Step succeeded
                step.status = StepStatus.COMPLETED
                step.completed_at = datetime.now(timezone.utc)
                step.output_data = result.output_data
                step.compensation_data = result.compensation_data

                # Update context with step output
                context.update(result.output_data)
                saga.context = context
                saga.current_step = i + 1
                db.commit()

                logger.info(
                    "Saga %s step %d completed successfully",
                    saga.saga_id, i
                )
            else:
                # Step failed - start compensation
                step.status = StepStatus.FAILED
                step.completed_at = datetime.now(timezone.utc)
                step.error_message = result.error
                saga.error_message = f"Step '{step_def.name}' failed: {result.error}"
                db.commit()

                logger.warning(
                    "Saga %s step %d failed: %s - starting compensation",
                    saga.saga_id, i, result.error
                )

                return self._compensate_steps(db, saga)

        # All steps completed
        saga.status = SagaStatus.COMPLETED
        saga.completed_at = datetime.now(timezone.utc)
        saga.result = self._build_result(saga.payload, context)
        db.commit()

        logger.info("Saga %s completed successfully", saga.saga_id)

        return SagaResult(
            success=True,
            saga_id=saga.saga_id,
            result=saga.result or {},
        )

    def _compensate_steps(
        self,
        db: Session,
        saga: SagaExecution,
    ) -> SagaResult:
        """Compensate (rollback) completed steps in reverse order."""
        saga.status = SagaStatus.COMPENSATING
        db.commit()

        step_defs = self.steps
        compensation_failed = False

        # Compensate completed steps in reverse order
        for i in range(saga.current_step - 1, -1, -1):
            step_def = step_defs[i]
            step = self._get_step(db, saga.saga_id, i)

            if step.status == StepStatus.COMPENSATED:
                continue

            if step.status != StepStatus.COMPLETED:
                # Step wasn't completed - nothing to compensate
                continue

            if step_def.compensate is None:
                # No compensation defined (read-only step)
                step.status = StepStatus.COMPENSATED
                step.completed_at = datetime.now(timezone.utc)
                db.commit()
                continue

            logger.info(
                "Compensating saga %s step %d: %s",
                saga.saga_id, i, step_def.name
            )

            step.status = StepStatus.COMPENSATING
            db.commit()

            try:
                success = step_def.compensate(
                    db,
                    saga.payload,
                    step.compensation_data or {},
                )
            except Exception as e:
                logger.exception(
                    "Saga %s step %d compensation failed",
                    saga.saga_id, i
                )
                success = False
                step.error_message = f"Compensation failed: {e}"

            if success:
                step.status = StepStatus.COMPENSATED
                step.completed_at = datetime.now(timezone.utc)
                db.commit()

                logger.info(
                    "Saga %s step %d compensated",
                    saga.saga_id, i
                )
            else:
                compensation_failed = True
                step.retry_count += 1
                db.commit()

                logger.error(
                    "Saga %s step %d compensation failed",
                    saga.saga_id, i
                )

        if compensation_failed:
            saga.status = SagaStatus.FAILED
            saga.error_message = saga.error_message or "Compensation failed"
        else:
            saga.status = SagaStatus.COMPENSATED

        saga.completed_at = datetime.now(timezone.utc)
        db.commit()

        return SagaResult(
            success=False,
            saga_id=saga.saga_id,
            error=saga.error_message,
            was_compensated=saga.status == SagaStatus.COMPENSATED,
        )

    def _get_step(
        self,
        db: Session,
        saga_id: UUID,
        step_number: int,
    ) -> SagaStep:
        """Get a saga step by number."""
        return (
            db.query(SagaStep)
            .filter(
                SagaStep.saga_id == saga_id,
                SagaStep.step_number == step_number,
            )
            .one()
        )

    def _build_result(
        self,
        payload: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Build the final saga result.

        Override in subclasses to customize the result structure.
        """
        return context

    @staticmethod
    def get_saga(
        db: Session,
        saga_id: UUID,
    ) -> Optional[SagaExecution]:
        """Get a saga by ID."""
        return db.get(SagaExecution, coerce_uuid(saga_id))

    @staticmethod
    def get_saga_by_idempotency_key(
        db: Session,
        idempotency_key: str,
    ) -> Optional[SagaExecution]:
        """Get a saga by idempotency key."""
        return (
            db.query(SagaExecution)
            .filter(SagaExecution.idempotency_key == idempotency_key)
            .first()
        )

    @staticmethod
    def list_sagas(
        db: Session,
        organization_id: Optional[UUID] = None,
        saga_type: Optional[str] = None,
        status: Optional[SagaStatus] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[SagaExecution]:
        """List sagas with optional filters."""
        query = db.query(SagaExecution)

        if organization_id:
            query = query.filter(
                SagaExecution.organization_id == coerce_uuid(organization_id)
            )
        if saga_type:
            query = query.filter(SagaExecution.saga_type == saga_type)
        if status:
            query = query.filter(SagaExecution.status == status)

        return (
            query.order_by(SagaExecution.started_at.desc())
            .limit(limit)
            .offset(offset)
            .all()
        )
