"""
ReportSchedulerService - Scheduled report management.

Manages report schedules and automated report generation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.finance.rpt.report_schedule import ReportSchedule, ScheduleFrequency
from app.models.finance.rpt.report_definition import ReportDefinition
from app.services.common import coerce_uuid
from app.services.response import ListResponseMixin


@dataclass
class ScheduleInput:
    """Input for creating a report schedule."""

    report_def_id: UUID
    schedule_name: str
    frequency: ScheduleFrequency
    output_format: str = "PDF"
    description: Optional[str] = None
    cron_expression: Optional[str] = None
    day_of_week: Optional[int] = None
    day_of_month: Optional[int] = None
    time_of_day: Optional[str] = None
    timezone: str = "UTC"
    report_parameters: Optional[dict] = None
    email_recipients: Optional[list] = None
    storage_path: Optional[str] = None
    retention_days: Optional[int] = None


@dataclass
class ScheduleExecution:
    """Schedule execution details."""

    schedule_id: UUID
    schedule_name: str
    report_code: str
    last_run: Optional[datetime]
    next_run: Optional[datetime]
    is_active: bool


class ReportSchedulerService(ListResponseMixin):
    """
    Service for report schedule management.

    Handles:
    - Schedule CRUD
    - Next run calculation
    - Due schedule identification
    - Distribution configuration
    """

    @staticmethod
    def create_schedule(
        db: Session,
        organization_id: UUID,
        input: ScheduleInput,
        created_by_user_id: UUID,
    ) -> ReportSchedule:
        """
        Create a report schedule.

        Args:
            db: Database session
            organization_id: Organization scope
            input: Schedule input data
            created_by_user_id: User creating the schedule

        Returns:
            Created ReportSchedule
        """
        org_id = coerce_uuid(organization_id)
        user_id = coerce_uuid(created_by_user_id)

        # Validate report definition
        definition = db.get(ReportDefinition, input.report_def_id)
        if not definition or definition.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Report definition not found")

        if not definition.is_active:
            raise HTTPException(
                status_code=400,
                detail="Cannot schedule inactive report",
            )

        # Validate output format
        if definition.supported_formats and input.output_format not in definition.supported_formats:
            raise HTTPException(
                status_code=400,
                detail=f"Output format {input.output_format} not supported",
            )

        # Calculate next run
        next_run = ReportSchedulerService._calculate_next_run(
            frequency=input.frequency,
            cron_expression=input.cron_expression,
            day_of_week=input.day_of_week,
            day_of_month=input.day_of_month,
            time_of_day=input.time_of_day,
            tz=input.timezone,
        )

        schedule = ReportSchedule(
            report_def_id=input.report_def_id,
            organization_id=org_id,
            schedule_name=input.schedule_name,
            description=input.description,
            frequency=input.frequency,
            cron_expression=input.cron_expression,
            day_of_week=input.day_of_week,
            day_of_month=input.day_of_month,
            time_of_day=input.time_of_day,
            timezone=input.timezone,
            report_parameters=input.report_parameters,
            output_format=input.output_format,
            email_recipients=input.email_recipients,
            storage_path=input.storage_path,
            retention_days=input.retention_days,
            is_active=True,
            next_run_at=next_run,
            created_by_user_id=user_id,
        )

        db.add(schedule)
        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def update_schedule(
        db: Session,
        organization_id: UUID,
        schedule_id: UUID,
        schedule_name: Optional[str] = None,
        description: Optional[str] = None,
        output_format: Optional[str] = None,
        email_recipients: Optional[list] = None,
        storage_path: Optional[str] = None,
        retention_days: Optional[int] = None,
    ) -> ReportSchedule:
        """
        Update a report schedule.

        Args:
            db: Database session
            organization_id: Organization scope
            schedule_id: Schedule to update
            schedule_name: New name
            description: New description
            output_format: New output format
            email_recipients: New recipients
            storage_path: New storage path
            retention_days: New retention

        Returns:
            Updated ReportSchedule
        """
        org_id = coerce_uuid(organization_id)
        sch_id = coerce_uuid(schedule_id)

        schedule = db.get(ReportSchedule, sch_id)
        if not schedule or schedule.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        if schedule_name is not None:
            schedule.schedule_name = schedule_name
        if description is not None:
            schedule.description = description
        if output_format is not None:
            schedule.output_format = output_format
        if email_recipients is not None:
            schedule.email_recipients = email_recipients
        if storage_path is not None:
            schedule.storage_path = storage_path
        if retention_days is not None:
            schedule.retention_days = retention_days

        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def update_timing(
        db: Session,
        organization_id: UUID,
        schedule_id: UUID,
        frequency: ScheduleFrequency,
        cron_expression: Optional[str] = None,
        day_of_week: Optional[int] = None,
        day_of_month: Optional[int] = None,
        time_of_day: Optional[str] = None,
        tz: str = "UTC",
    ) -> ReportSchedule:
        """
        Update schedule timing.

        Args:
            db: Database session
            organization_id: Organization scope
            schedule_id: Schedule to update
            frequency: New frequency
            cron_expression: New cron expression
            day_of_week: Day of week (0-6)
            day_of_month: Day of month (1-31)
            time_of_day: Time in HH:MM format
            tz: Timezone

        Returns:
            Updated ReportSchedule
        """
        org_id = coerce_uuid(organization_id)
        sch_id = coerce_uuid(schedule_id)

        schedule = db.get(ReportSchedule, sch_id)
        if not schedule or schedule.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        schedule.frequency = frequency
        schedule.cron_expression = cron_expression
        schedule.day_of_week = day_of_week
        schedule.day_of_month = day_of_month
        schedule.time_of_day = time_of_day
        schedule.timezone = tz

        # Recalculate next run
        schedule.next_run_at = ReportSchedulerService._calculate_next_run(
            frequency=frequency,
            cron_expression=cron_expression,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
            time_of_day=time_of_day,
            tz=tz,
        )

        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def activate(
        db: Session,
        organization_id: UUID,
        schedule_id: UUID,
    ) -> ReportSchedule:
        """Activate a schedule."""
        org_id = coerce_uuid(organization_id)
        sch_id = coerce_uuid(schedule_id)

        schedule = db.get(ReportSchedule, sch_id)
        if not schedule or schedule.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        schedule.is_active = True

        # Recalculate next run
        schedule.next_run_at = ReportSchedulerService._calculate_next_run(
            frequency=schedule.frequency,
            cron_expression=schedule.cron_expression,
            day_of_week=schedule.day_of_week,
            day_of_month=schedule.day_of_month,
            time_of_day=schedule.time_of_day,
            tz=schedule.timezone,
        )

        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def deactivate(
        db: Session,
        organization_id: UUID,
        schedule_id: UUID,
    ) -> ReportSchedule:
        """Deactivate a schedule."""
        org_id = coerce_uuid(organization_id)
        sch_id = coerce_uuid(schedule_id)

        schedule = db.get(ReportSchedule, sch_id)
        if not schedule or schedule.organization_id != org_id:
            raise HTTPException(status_code=404, detail="Schedule not found")

        schedule.is_active = False
        schedule.next_run_at = None

        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def record_execution(
        db: Session,
        schedule_id: UUID,
    ) -> ReportSchedule:
        """
        Record schedule execution and calculate next run.

        Args:
            db: Database session
            schedule_id: Schedule that was executed

        Returns:
            Updated ReportSchedule
        """
        sch_id = coerce_uuid(schedule_id)

        schedule = db.get(ReportSchedule, sch_id)
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        now = datetime.now(timezone.utc)
        schedule.last_run_at = now

        if schedule.is_active:
            schedule.next_run_at = ReportSchedulerService._calculate_next_run(
                frequency=schedule.frequency,
                cron_expression=schedule.cron_expression,
                day_of_week=schedule.day_of_week,
                day_of_month=schedule.day_of_month,
                time_of_day=schedule.time_of_day,
                tz=schedule.timezone,
                from_time=now,
            )

        db.commit()
        db.refresh(schedule)

        return schedule

    @staticmethod
    def get_due_schedules(
        db: Session,
        organization_id: Optional[str] = None,
    ) -> List[ReportSchedule]:
        """
        Get schedules due for execution.

        Args:
            db: Database session
            organization_id: Optional org filter

        Returns:
            List of due schedules
        """
        now = datetime.now(timezone.utc)

        query = db.query(ReportSchedule).filter(
            ReportSchedule.is_active == True,
            ReportSchedule.next_run_at <= now,
        )

        if organization_id:
            query = query.filter(
                ReportSchedule.organization_id == coerce_uuid(organization_id)
            )

        return query.order_by(ReportSchedule.next_run_at).all()

    @staticmethod
    def get_upcoming_schedules(
        db: Session,
        organization_id: str,
        hours_ahead: int = 24,
    ) -> list[ScheduleExecution]:
        """
        Get schedules running in next N hours.

        Args:
            db: Database session
            organization_id: Organization scope
            hours_ahead: Hours to look ahead

        Returns:
            List of upcoming schedule executions
        """
        org_id = coerce_uuid(organization_id)
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=hours_ahead)

        schedules = (
            db.query(ReportSchedule)
            .join(ReportDefinition)
            .filter(
                ReportSchedule.organization_id == org_id,
                ReportSchedule.is_active == True,
                ReportSchedule.next_run_at <= cutoff,
            )
            .all()
        )

        executions = []
        for schedule in schedules:
            definition = db.get(ReportDefinition, schedule.report_def_id)
            executions.append(
                ScheduleExecution(
                    schedule_id=schedule.schedule_id,
                    schedule_name=schedule.schedule_name,
                    report_code=definition.report_code if definition else "Unknown",
                    last_run=schedule.last_run_at,
                    next_run=schedule.next_run_at,
                    is_active=schedule.is_active,
                )
            )

        return executions

    @staticmethod
    def _calculate_next_run(
        frequency: ScheduleFrequency,
        cron_expression: Optional[str],
        day_of_week: Optional[int],
        day_of_month: Optional[int],
        time_of_day: Optional[str],
        tz: str,
        from_time: Optional[datetime] = None,
    ) -> Optional[datetime]:
        """Calculate next run time based on schedule configuration."""
        if frequency == ScheduleFrequency.ON_DEMAND:
            return None

        now = from_time or datetime.now(timezone.utc)

        # Parse time of day
        run_hour, run_minute = 0, 0
        if time_of_day:
            parts = time_of_day.split(":")
            run_hour = int(parts[0])
            run_minute = int(parts[1]) if len(parts) > 1 else 0

        if frequency == ScheduleFrequency.DAILY:
            next_run = now.replace(hour=run_hour, minute=run_minute, second=0, microsecond=0)
            if next_run <= now:
                next_run += timedelta(days=1)
            return next_run

        elif frequency == ScheduleFrequency.WEEKLY:
            target_day = day_of_week or 0  # Monday by default
            days_ahead = target_day - now.weekday()
            if days_ahead <= 0:
                days_ahead += 7
            next_run = now + timedelta(days=days_ahead)
            next_run = next_run.replace(hour=run_hour, minute=run_minute, second=0, microsecond=0)
            return next_run

        elif frequency == ScheduleFrequency.MONTHLY:
            target_day = day_of_month or 1
            next_month = now.month + 1 if now.day >= target_day else now.month
            next_year = now.year + 1 if next_month > 12 else now.year
            next_month = next_month if next_month <= 12 else 1
            try:
                next_run = now.replace(
                    year=next_year,
                    month=next_month,
                    day=target_day,
                    hour=run_hour,
                    minute=run_minute,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                # Handle invalid day (e.g., Feb 30)
                next_run = now.replace(
                    year=next_year,
                    month=next_month,
                    day=28,
                    hour=run_hour,
                    minute=run_minute,
                    second=0,
                    microsecond=0,
                )
            return next_run

        elif frequency == ScheduleFrequency.QUARTERLY:
            # Next quarter start
            current_quarter = (now.month - 1) // 3
            next_quarter = current_quarter + 1
            next_quarter_month = (next_quarter % 4) * 3 + 1
            next_year = now.year + 1 if next_quarter >= 4 else now.year
            target_day = day_of_month or 1
            try:
                next_run = now.replace(
                    year=next_year,
                    month=next_quarter_month,
                    day=target_day,
                    hour=run_hour,
                    minute=run_minute,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                next_run = now.replace(
                    year=next_year,
                    month=next_quarter_month,
                    day=28,
                    hour=run_hour,
                    minute=run_minute,
                    second=0,
                    microsecond=0,
                )
            return next_run

        elif frequency == ScheduleFrequency.ANNUALLY:
            target_day = day_of_month or 1
            next_year = now.year + 1
            try:
                next_run = now.replace(
                    year=next_year,
                    month=1,
                    day=target_day,
                    hour=run_hour,
                    minute=run_minute,
                    second=0,
                    microsecond=0,
                )
            except ValueError:
                next_run = now.replace(
                    year=next_year,
                    month=1,
                    day=28,
                    hour=run_hour,
                    minute=run_minute,
                    second=0,
                    microsecond=0,
                )
            return next_run

        elif frequency == ScheduleFrequency.PERIOD_END:
            # Requires external trigger
            return None

        return None

    @staticmethod
    def get(
        db: Session,
        schedule_id: str,
    ) -> ReportSchedule:
        """Get a schedule by ID."""
        schedule = db.get(ReportSchedule, coerce_uuid(schedule_id))
        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")
        return schedule

    @staticmethod
    def list(
        db: Session,
        organization_id: Optional[str] = None,
        report_def_id: Optional[str] = None,
        frequency: Optional[ScheduleFrequency] = None,
        is_active: Optional[bool] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ReportSchedule]:
        """List schedules with optional filters."""
        query = db.query(ReportSchedule)

        if organization_id:
            query = query.filter(
                ReportSchedule.organization_id == coerce_uuid(organization_id)
            )

        if report_def_id:
            query = query.filter(
                ReportSchedule.report_def_id == coerce_uuid(report_def_id)
            )

        if frequency:
            query = query.filter(ReportSchedule.frequency == frequency)

        if is_active is not None:
            query = query.filter(ReportSchedule.is_active == is_active)

        query = query.order_by(ReportSchedule.schedule_name)
        return query.limit(limit).offset(offset).all()


# Module-level singleton instance
report_scheduler_service = ReportSchedulerService()
