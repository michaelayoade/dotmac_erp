"""
ReportInstanceService - Report generation and instance management.

Manages report generation, execution, and output storage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.ifrs.rpt.report_instance import ReportInstance, ReportStatus
from app.models.ifrs.rpt.report_definition import ReportDefinition
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class ReportGenerationRequest:
    """Request to generate a report."""

    report_def_id: UUID
    output_format: str = "PDF"
    fiscal_period_id: Optional[UUID] = None
    parameters: Optional[dict] = None
    schedule_id: Optional[UUID] = None


@dataclass
class ReportGenerationResult:
    """Result of report generation."""

    instance_id: UUID
    status: ReportStatus
    output_file_path: Optional[str] = None
    generation_time_ms: Optional[int] = None
    error_message: Optional[str] = None


class ReportInstanceService(ListResponseMixin):
    """
    Service for report instance management.

    Handles:
    - Report generation requests
    - Execution status tracking
    - Output file management
    - Instance history
    """

    @staticmethod
    def queue_report(
        db: Session,
        organization_id: UUID,
        request: ReportGenerationRequest,
        requested_by_user_id: UUID,
    ) -> ReportInstance:
        """
        Queue a report for generation.

        Args:
            db: Database session
            organization_id: Organization scope
            request: Generation request
            requested_by_user_id: User requesting the report

        Returns:
            Created ReportInstance in QUEUED status
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(requested_by_user_id)

        # Validate report definition
        definition = db.get(ReportDefinition, request.report_def_id)
        if not definition or definition.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report definition not found")

        if not definition.is_active:
            raise HTTPException(
                status_code=400,
                detail="Report definition is not active",
            )

        # Validate output format
        if definition.supported_formats and request.output_format not in definition.supported_formats:
            raise HTTPException(
                status_code=400,
                detail=f"Output format {request.output_format} not supported for this report",
            )

        instance = ReportInstance(
            report_def_id=request.report_def_id,
            organization_id=org_id,
            schedule_id=request.schedule_id,
            fiscal_period_id=request.fiscal_period_id,
            parameters_used=request.parameters,
            output_format=request.output_format,
            status=ReportStatus.QUEUED,
            generated_by_user_id=user_id,
        )

        db.add(instance)
        db.commit()
        db.refresh(instance)

        return instance

    @staticmethod
    def start_generation(
        db: Session,
        instance_id: UUID,
    ) -> ReportInstance:
        """
        Mark report generation as started.

        Args:
            db: Database session
            instance_id: Instance to start

        Returns:
            Updated ReportInstance
        """
        inst_id = coerce_uuid(instance_id)

        instance = db.get(ReportInstance, inst_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Report instance not found")

        if instance.status != ReportStatus.QUEUED:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot start report in {instance.status} status",
            )

        instance.status = ReportStatus.GENERATING
        instance.started_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(instance)

        return instance

    @staticmethod
    def complete_generation(
        db: Session,
        instance_id: UUID,
        output_file_path: str,
        output_size_bytes: int,
    ) -> ReportInstance:
        """
        Mark report generation as completed.

        Args:
            db: Database session
            instance_id: Instance to complete
            output_file_path: Path to generated file
            output_size_bytes: Size of output file

        Returns:
            Updated ReportInstance
        """
        inst_id = coerce_uuid(instance_id)

        instance = db.get(ReportInstance, inst_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Report instance not found")

        if instance.status != ReportStatus.GENERATING:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot complete report in {instance.status} status",
            )

        now = datetime.now(timezone.utc)
        generation_time_ms = None
        if instance.started_at:
            delta = now - instance.started_at
            generation_time_ms = int(delta.total_seconds() * 1000)

        instance.status = ReportStatus.COMPLETED
        instance.completed_at = now
        instance.output_file_path = output_file_path
        instance.output_size_bytes = output_size_bytes
        instance.generation_time_ms = generation_time_ms

        db.commit()
        db.refresh(instance)

        return instance

    @staticmethod
    def fail_generation(
        db: Session,
        instance_id: UUID,
        error_message: str,
    ) -> ReportInstance:
        """
        Mark report generation as failed.

        Args:
            db: Database session
            instance_id: Instance that failed
            error_message: Error description

        Returns:
            Updated ReportInstance
        """
        inst_id = coerce_uuid(instance_id)

        instance = db.get(ReportInstance, inst_id)
        if not instance:
            raise HTTPException(status_code=404, detail="Report instance not found")

        now = datetime.now(timezone.utc)
        generation_time_ms = None
        if instance.started_at:
            delta = now - instance.started_at
            generation_time_ms = int(delta.total_seconds() * 1000)

        instance.status = ReportStatus.FAILED
        instance.completed_at = now
        instance.error_message = error_message
        instance.generation_time_ms = generation_time_ms

        db.commit()
        db.refresh(instance)

        return instance

    @staticmethod
    def cancel_report(
        db: Session,
        organization_id: UUID,
        instance_id: UUID,
    ) -> ReportInstance:
        """
        Cancel a queued report.

        Args:
            db: Database session
            organization_id: Organization scope
            instance_id: Instance to cancel

        Returns:
            Updated ReportInstance
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instance_id)

        instance = db.get(ReportInstance, inst_id)
        if not instance or instance.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report instance not found")

        if instance.status != ReportStatus.QUEUED:
            raise HTTPException(
                status_code=400,
                detail=f"Can only cancel queued reports, current status: {instance.status}",
            )

        instance.status = ReportStatus.CANCELLED
        instance.completed_at = datetime.now(timezone.utc)

        db.commit()
        db.refresh(instance)

        return instance

    @staticmethod
    def get_queued_reports(
        db: Session,
        organization_id: Optional[str] = None,
        limit: int = 50,
    ) -> list[ReportInstance]:
        """
        Get queued reports awaiting generation.

        Args:
            db: Database session
            organization_id: Optional org filter
            limit: Maximum reports to return

        Returns:
            List of queued report instances
        """
        query = db.query(ReportInstance).filter(
            ReportInstance.status == ReportStatus.QUEUED,
        )

        if organization_id:
            query = query.filter(
                ReportInstance.organization_id == coerce_uuid(organization_id)
            )

        return query.order_by(ReportInstance.queued_at).limit(limit).all()

    @staticmethod
    def get_generation_statistics(
        db: Session,
        organization_id: str,
        report_def_id: Optional[str] = None,
    ) -> dict:
        """
        Get report generation statistics.

        Args:
            db: Database session
            organization_id: Organization scope
            report_def_id: Optional filter by report definition

        Returns:
            Dict with statistics
        """
        org_id = coerce_uuid(organization_id)

        query = db.query(ReportInstance).filter(
            ReportInstance.organization_id == org_id,
        )

        if report_def_id:
            query = query.filter(
                ReportInstance.report_def_id == coerce_uuid(report_def_id)
            )

        instances = query.all()

        total = len(instances)
        completed = len([i for i in instances if i.status == ReportStatus.COMPLETED])
        failed = len([i for i in instances if i.status == ReportStatus.FAILED])
        queued = len([i for i in instances if i.status == ReportStatus.QUEUED])
        generating = len([i for i in instances if i.status == ReportStatus.GENERATING])

        generation_times = [
            i.generation_time_ms for i in instances
            if i.generation_time_ms is not None
        ]
        avg_generation_time = (
            sum(generation_times) / len(generation_times) if generation_times else 0
        )

        return {
            "total": total,
            "completed": completed,
            "failed": failed,
            "queued": queued,
            "generating": generating,
            "success_rate": completed / total if total > 0 else 0,
            "avg_generation_time_ms": avg_generation_time,
        }

    @staticmethod
    def cleanup_old_instances(
        db: Session,
        organization_id: UUID,
        retention_days: int,
    ) -> int:
        """
        Clean up old report instances.

        Args:
            db: Database session
            organization_id: Organization scope
            retention_days: Days to retain

        Returns:
            Number of instances deleted
        """
        from datetime import timedelta

        org_id = coerce_uuid(organization_id)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)

        instances = (
            db.query(ReportInstance)
            .filter(
                ReportInstance.organization_id == org_id,
                ReportInstance.generated_at < cutoff_date,
                ReportInstance.status.in_([
                    ReportStatus.COMPLETED,
                    ReportStatus.FAILED,
                    ReportStatus.CANCELLED,
                ]),
            )
            .all()
        )

        count = len(instances)
        for instance in instances:
            db.delete(instance)

        db.commit()
        return count

    @staticmethod
    def regenerate_report(
        db: Session,
        organization_id: UUID,
        instance_id: UUID,
        requested_by_user_id: UUID,
    ) -> ReportInstance:
        """
        Regenerate a previously run report.

        Args:
            db: Database session
            organization_id: Organization scope
            instance_id: Instance to regenerate
            requested_by_user_id: User requesting regeneration

        Returns:
            New ReportInstance
        """
        org_id = coerce_uuid(organization_id)
        inst_id = coerce_uuid(instance_id)
        user_id = coerce_uuid(requested_by_user_id)

        original = db.get(ReportInstance, inst_id)
        if not original or original.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report instance not found")

        request = ReportGenerationRequest(
            report_def_id=original.report_def_id,
            output_format=original.output_format,
            fiscal_period_id=original.fiscal_period_id,
            parameters=original.parameters_used,
            schedule_id=original.schedule_id,
        )

        return ReportInstanceService.queue_report(
            db=db,
            organization_id=organization_id,
            request=request,
            requested_by_user_id=user_id,
        )

    @staticmethod
    def get(
        db: Session,
        instance_id: str,
    ) -> ReportInstance:
        """Get a report instance by ID."""
        instance = db.get(ReportInstance, coerce_uuid(instance_id))
        if not instance:
            raise HTTPException(status_code=404, detail="Report instance not found")
        return instance

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        report_def_id: Optional[str] = None,
        status: Optional[ReportStatus] = None,
        fiscal_period_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ReportInstance]:
        """List report instances with optional filters."""
        query = db.query(ReportInstance)

        if organization_id:
            query = query.filter(
                ReportInstance.organization_id == coerce_uuid(organization_id)
            )

        if report_def_id:
            query = query.filter(
                ReportInstance.report_def_id == coerce_uuid(report_def_id)
            )

        if status:
            query = query.filter(ReportInstance.status == status)

        if fiscal_period_id:
            query = query.filter(
                ReportInstance.fiscal_period_id == coerce_uuid(fiscal_period_id)
            )

        query = query.order_by(ReportInstance.generated_at.desc())
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
report_instance_service = ReportInstanceService()
