"""
Enhanced Onboarding Service.

Provides comprehensive onboarding workflow management including:
- Template-based checklist instantiation
- Self-service portal token management
- Progress tracking and calculation
- Due date management and overdue detection
- Activity completion with document support
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.people.hr import Employee
from app.models.people.hr.checklist_template import (
    ChecklistTemplate,
    ChecklistTemplateType,
)
from app.models.people.hr.lifecycle import (
    ActivityStatus,
    BoardingStatus,
    EmployeeOnboarding,
    EmployeeOnboardingActivity,
)
from app.services.people.hr.errors import (
    ActivityNotFoundError,
    ChecklistTemplateNotFoundError,
    InvalidSelfServiceTokenError,
    LifecycleStatusError,
    OnboardingNotFoundError,
    ValidationError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "OnboardingService",
]


class OnboardingService:
    """
    Enhanced onboarding service with template-based workflows.

    Extends basic lifecycle management with:
    - Template instantiation to create checklists
    - Self-service portal token generation/validation
    - Progress calculation and tracking
    - Due date management
    - Reminder support
    """

    # Token validity period (30 days by default)
    TOKEN_VALIDITY_DAYS = 30

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _hash_token(token: str) -> str:
        """
        Hash a token using SHA-256.

        Tokens are hashed before storage to prevent exposure in database
        backups or compromises. The raw token is only sent to the user.
        """
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    # =========================================================================
    # Template Management
    # =========================================================================

    def get_template(self, org_id: UUID, template_id: UUID) -> ChecklistTemplate:
        """Get a checklist template by ID."""
        template = self.db.scalar(
            select(ChecklistTemplate)
            .options(joinedload(ChecklistTemplate.items))
            .where(
                ChecklistTemplate.organization_id == org_id,
                ChecklistTemplate.template_id == template_id,
            )
        )
        if not template:
            raise ChecklistTemplateNotFoundError(template_id)
        return template

    def get_default_onboarding_template(self, org_id: UUID) -> ChecklistTemplate | None:
        """Get the default active onboarding template for an organization."""
        return self.db.scalar(
            select(ChecklistTemplate)
            .options(joinedload(ChecklistTemplate.items))
            .where(
                ChecklistTemplate.organization_id == org_id,
                ChecklistTemplate.template_type == ChecklistTemplateType.ONBOARDING,
                ChecklistTemplate.is_active == True,
            )
            .order_by(ChecklistTemplate.created_at.desc())
        )

    def list_templates(
        self,
        org_id: UUID,
        *,
        template_type: ChecklistTemplateType | None = None,
        active_only: bool = True,
    ) -> list[ChecklistTemplate]:
        """List checklist templates for an organization."""
        query = select(ChecklistTemplate).where(
            ChecklistTemplate.organization_id == org_id
        )

        if template_type:
            query = query.where(ChecklistTemplate.template_type == template_type)

        if active_only:
            query = query.where(ChecklistTemplate.is_active == True)

        query = query.options(joinedload(ChecklistTemplate.items))
        query = query.order_by(ChecklistTemplate.template_name)

        return list(self.db.scalars(query).unique().all())

    # =========================================================================
    # Onboarding Creation with Template
    # =========================================================================

    def create_onboarding_from_template(
        self,
        org_id: UUID,
        *,
        employee_id: UUID,
        template_id: UUID | None = None,
        date_of_joining: date,
        department_id: UUID | None = None,
        designation_id: UUID | None = None,
        job_applicant_id: UUID | None = None,
        job_offer_id: UUID | None = None,
        buddy_employee_id: UUID | None = None,
        manager_id: UUID | None = None,
        notes: str | None = None,
        generate_self_service_token: bool = True,
    ) -> EmployeeOnboarding:
        """
        Create an onboarding record from a template.

        Instantiates all template items as activities with calculated due dates.
        Optionally generates a self-service portal token.
        """
        # Get template (may be None if no template specified and no default exists)
        template: ChecklistTemplate | None = None
        if template_id:
            template = self.get_template(org_id, template_id)
        else:
            template = self.get_default_onboarding_template(org_id)

        # Verify employee exists
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == org_id,
                Employee.is_deleted == False,
            )
        )
        if not employee:
            raise ValidationError(f"Employee {employee_id} not found")

        # Calculate expected completion date (max days_from_start + buffer)
        expected_completion = None
        if template and template.items:
            max_days = max((item.days_from_start for item in template.items), default=0)
            expected_completion = date_of_joining + timedelta(
                days=max(max_days + 7, 30)
            )

        # Create onboarding record
        onboarding = EmployeeOnboarding(
            organization_id=org_id,
            employee_id=employee_id,
            job_applicant_id=job_applicant_id,
            job_offer_id=job_offer_id,
            date_of_joining=date_of_joining,
            department_id=department_id,
            designation_id=designation_id,
            template_id=template.template_id if template else None,
            template_name=template.template_name if template else None,
            status=BoardingStatus.PENDING,
            notes=notes,
            expected_completion_date=expected_completion,
            buddy_employee_id=buddy_employee_id,
            manager_id=manager_id,
            progress_percentage=0,
        )

        # Generate self-service token if requested
        # SECURITY: Token is hashed before storage. Raw token stored in
        # _raw_self_service_token attribute for use in welcome email.
        if generate_self_service_token:
            raw_token = secrets.token_urlsafe(32)
            onboarding.self_service_token = self._hash_token(raw_token)
            onboarding.self_service_token_expires = datetime.now(
                timezone.utc
            ) + timedelta(days=self.TOKEN_VALIDITY_DAYS)
            # Store raw token temporarily (not persisted) for welcome email
            onboarding._raw_self_service_token = raw_token  # type: ignore[attr-defined]

        self.db.add(onboarding)
        self.db.flush()

        # Create activities from template items
        if template and template.items:
            for idx, item in enumerate(
                sorted(template.items, key=lambda x: x.sequence)
            ):
                due_date = date_of_joining + timedelta(days=item.days_from_start)

                activity = EmployeeOnboardingActivity(
                    onboarding_id=onboarding.onboarding_id,
                    template_item_id=item.item_id,
                    activity_name=item.item_name,
                    category=item.category,
                    assignee_role=item.default_assignee_role,
                    assigned_to_employee=item.default_assignee_role == "EMPLOYEE",
                    requires_document=item.requires_document,
                    due_date=due_date,
                    activity_status=ActivityStatus.PENDING.value,
                    sequence=item.sequence,
                    is_overdue=False,
                )
                self.db.add(activity)

        self.db.flush()

        logger.info(
            "Created onboarding %s for employee %s with %d activities",
            onboarding.onboarding_id,
            employee_id,
            len(template.items) if template else 0,
        )

        return onboarding

    # =========================================================================
    # Self-Service Portal Support
    # =========================================================================

    def get_onboarding_by_token(self, token: str) -> EmployeeOnboarding:
        """
        Get onboarding record by self-service token.

        Validates that:
        - Token exists and matches a record (via hash comparison)
        - Token is not expired
        - Organization is active (multi-tenancy validation)
        - Onboarding is not cancelled

        Security: Tokens are stored as SHA-256 hashes. The incoming token
        is hashed before comparison to prevent timing attacks and ensure
        tokens aren't exposed in database.
        """
        from app.models.finance.core_org import Organization

        # Hash the provided token for comparison
        token_hash = self._hash_token(token)

        onboarding = self.db.scalar(
            select(EmployeeOnboarding)
            .options(joinedload(EmployeeOnboarding.activities))
            .where(EmployeeOnboarding.self_service_token == token_hash)
        )

        if not onboarding:
            logger.warning("Invalid self-service token attempted")
            raise InvalidSelfServiceTokenError()

        # Validate token expiry (use timezone-aware comparison)
        if onboarding.self_service_token_expires:
            # Handle both naive and aware datetime comparisons
            expires = onboarding.self_service_token_expires
            now = datetime.now(timezone.utc)
            # If expires is naive, treat it as UTC
            if expires.tzinfo is None:
                from datetime import timezone as tz

                expires = expires.replace(tzinfo=tz.utc)
            if expires < now:
                logger.warning(
                    "Expired self-service token for onboarding %s",
                    onboarding.onboarding_id,
                )
                raise InvalidSelfServiceTokenError("Self-service token has expired")

        # Validate organization is active (multi-tenancy check)
        org = self.db.get(Organization, onboarding.organization_id)
        if not org or not getattr(org, "is_active", True):
            logger.warning(
                "Token lookup for inactive organization %s", onboarding.organization_id
            )
            raise InvalidSelfServiceTokenError("Organization is not active")

        # Validate onboarding is not cancelled
        if onboarding.status == BoardingStatus.CANCELLED:
            logger.warning(
                "Token lookup for cancelled onboarding %s", onboarding.onboarding_id
            )
            raise InvalidSelfServiceTokenError("Onboarding has been cancelled")

        logger.debug(
            "Valid token access for onboarding %s (org: %s)",
            onboarding.onboarding_id,
            onboarding.organization_id,
        )

        return onboarding

    def regenerate_self_service_token(self, org_id: UUID, onboarding_id: UUID) -> str:
        """
        Generate a new self-service token for an onboarding.

        Returns the raw token (to be sent to user). The token is stored
        as a SHA-256 hash for security.
        """
        onboarding = self.get_onboarding(org_id, onboarding_id)

        # Generate raw token and store its hash
        raw_token = secrets.token_urlsafe(32)
        onboarding.self_service_token = self._hash_token(raw_token)
        onboarding.self_service_token_expires = datetime.now(timezone.utc) + timedelta(
            days=self.TOKEN_VALIDITY_DAYS
        )
        self.db.flush()

        logger.info("Regenerated self-service token for onboarding %s", onboarding_id)

        # Return raw token (not the hash) for the URL
        return raw_token

    def mark_welcome_email_sent(self, org_id: UUID, onboarding_id: UUID) -> None:
        """Mark that the welcome email has been sent."""
        onboarding = self.get_onboarding(org_id, onboarding_id)
        onboarding.self_service_email_sent = True
        self.db.flush()

    # =========================================================================
    # Onboarding Retrieval
    # =========================================================================

    def get_onboarding(self, org_id: UUID, onboarding_id: UUID) -> EmployeeOnboarding:
        """Get an onboarding record by ID."""
        onboarding = self.db.scalar(
            select(EmployeeOnboarding)
            .options(joinedload(EmployeeOnboarding.activities))
            .where(
                EmployeeOnboarding.organization_id == org_id,
                EmployeeOnboarding.onboarding_id == onboarding_id,
            )
        )
        if not onboarding:
            raise OnboardingNotFoundError(
                message=f"Onboarding {onboarding_id} not found"
            )
        return onboarding

    def list_onboardings(
        self,
        org_id: UUID,
        *,
        status: BoardingStatus | None = None,
        employee_id: UUID | None = None,
        manager_id: UUID | None = None,
        has_overdue: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[EmployeeOnboarding], int]:
        """
        List onboardings with filters.

        Returns a tuple of (items, total_count).
        """
        query = select(EmployeeOnboarding).where(
            EmployeeOnboarding.organization_id == org_id
        )

        if status:
            query = query.where(EmployeeOnboarding.status == status)

        if employee_id:
            query = query.where(EmployeeOnboarding.employee_id == employee_id)

        if manager_id:
            query = query.where(EmployeeOnboarding.manager_id == manager_id)

        if has_overdue is True:
            # Join with activities to filter by overdue
            query = query.join(EmployeeOnboardingActivity).where(
                EmployeeOnboardingActivity.is_overdue == True
            )

        query = query.options(joinedload(EmployeeOnboarding.activities))
        query = query.order_by(EmployeeOnboarding.date_of_joining.desc())

        # Count total
        count_subq = (
            query.with_only_columns(EmployeeOnboarding.onboarding_id)
            .distinct()
            .subquery()
        )
        total = self.db.scalar(select(func.count()).select_from(count_subq)) or 0

        # Apply pagination
        query = query.offset(offset).limit(limit)

        items = list(self.db.scalars(query).unique().all())
        return items, total

    # =========================================================================
    # Activity Management
    # =========================================================================

    def get_activity(
        self, org_id: UUID, activity_id: UUID
    ) -> EmployeeOnboardingActivity:
        """Get a specific activity."""
        activity = self.db.scalar(
            select(EmployeeOnboardingActivity)
            .join(EmployeeOnboarding)
            .where(
                EmployeeOnboarding.organization_id == org_id,
                EmployeeOnboardingActivity.activity_id == activity_id,
            )
        )
        if not activity:
            raise ActivityNotFoundError(activity_id)
        return activity

    def complete_activity(
        self,
        org_id: UUID,
        activity_id: UUID,
        *,
        completed_by: UUID,
        completion_notes: str | None = None,
        document_id: UUID | None = None,
    ) -> EmployeeOnboardingActivity:
        """
        Mark an activity as completed.

        If the activity requires a document, document_id must be provided.
        """
        activity = self.get_activity(org_id, activity_id)

        # Validate document requirement
        if activity.requires_document and not document_id:
            raise ValidationError("This activity requires a document upload")

        activity.activity_status = ActivityStatus.COMPLETED.value
        activity.status = "completed"  # Legacy field
        activity.completed_on = date.today()
        activity.completed_by = completed_by
        activity.completion_notes = completion_notes
        activity.document_id = document_id
        activity.is_overdue = False

        self.db.flush()

        # Update onboarding progress
        self._update_progress(activity.onboarding_id)

        logger.info("Completed activity %s by user %s", activity_id, completed_by)

        return activity

    def skip_activity(
        self,
        org_id: UUID,
        activity_id: UUID,
        *,
        skipped_by: UUID,
        reason: str,
    ) -> EmployeeOnboardingActivity:
        """Mark an activity as skipped."""
        activity = self.get_activity(org_id, activity_id)

        activity.activity_status = ActivityStatus.SKIPPED.value
        activity.status = "skipped"
        activity.completed_by = skipped_by
        activity.completion_notes = f"Skipped: {reason}"
        activity.is_overdue = False

        self.db.flush()

        # Update onboarding progress
        self._update_progress(activity.onboarding_id)

        logger.info(
            "Skipped activity %s by user %s: %s", activity_id, skipped_by, reason
        )

        return activity

    def update_activity_assignee(
        self,
        org_id: UUID,
        activity_id: UUID,
        *,
        assignee_id: UUID | None = None,
        assignee_role: str | None = None,
    ) -> EmployeeOnboardingActivity:
        """Update the assignee for an activity."""
        activity = self.get_activity(org_id, activity_id)

        if assignee_id is not None:
            activity.assignee_id = assignee_id
        if assignee_role is not None:
            activity.assignee_role = assignee_role

        self.db.flush()
        return activity

    def get_employee_self_service_activities(
        self,
        onboarding_id: UUID,
    ) -> list[EmployeeOnboardingActivity]:
        """Get activities assigned to the employee for self-service."""
        return list(
            self.db.scalars(
                select(EmployeeOnboardingActivity)
                .where(
                    EmployeeOnboardingActivity.onboarding_id == onboarding_id,
                    EmployeeOnboardingActivity.assigned_to_employee == True,
                )
                .order_by(
                    EmployeeOnboardingActivity.due_date,
                    EmployeeOnboardingActivity.sequence,
                )
            ).all()
        )

    # =========================================================================
    # Progress Tracking
    # =========================================================================

    def _update_progress(self, onboarding_id: UUID) -> int:
        """Calculate and update the progress percentage for an onboarding."""
        onboarding = self.db.get(EmployeeOnboarding, onboarding_id)
        if not onboarding:
            return 0

        progress = self.calculate_progress(onboarding)
        onboarding.progress_percentage = progress

        # Auto-complete if all activities done
        if progress == 100 and onboarding.status != BoardingStatus.COMPLETED:
            onboarding.status = BoardingStatus.COMPLETED
            onboarding.actual_completion_date = date.today()
            logger.info("Auto-completed onboarding %s (100%% progress)", onboarding_id)

        self.db.flush()
        return progress

    @staticmethod
    def calculate_progress(onboarding: EmployeeOnboarding) -> int:
        """Calculate progress percentage for an onboarding."""
        if not onboarding.activities:
            return 0

        total = len(onboarding.activities)
        completed = sum(
            1
            for a in onboarding.activities
            if a.activity_status
            in (ActivityStatus.COMPLETED.value, ActivityStatus.SKIPPED.value)
            or a.status in ("completed", "skipped")  # Legacy support
        )

        return int((completed / total) * 100)

    # =========================================================================
    # Due Date and Overdue Management
    # =========================================================================

    def update_overdue_flags(self, org_id: UUID) -> int:
        """
        Update is_overdue flags for all activities in an organization.

        Returns the count of activities marked as overdue.
        """
        today = date.today()
        count = 0

        # Get all pending activities with past due dates
        activities = self.db.scalars(
            select(EmployeeOnboardingActivity)
            .join(EmployeeOnboarding)
            .where(
                EmployeeOnboarding.organization_id == org_id,
                EmployeeOnboardingActivity.due_date < today,
                EmployeeOnboardingActivity.activity_status.in_(
                    [
                        ActivityStatus.PENDING.value,
                        ActivityStatus.IN_PROGRESS.value,
                        ActivityStatus.AWAITING_DOCUMENT.value,
                    ]
                ),
                EmployeeOnboardingActivity.is_overdue == False,
            )
        ).all()

        for activity in activities:
            activity.is_overdue = True
            count += 1

        if count > 0:
            self.db.flush()
            logger.info("Marked %d activities as overdue in org %s", count, org_id)

        return count

    def get_overdue_activities(
        self,
        org_id: UUID,
        *,
        assignee_id: UUID | None = None,
        limit: int = 100,
    ) -> list[EmployeeOnboardingActivity]:
        """Get all overdue activities for an organization."""
        query = (
            select(EmployeeOnboardingActivity)
            .join(EmployeeOnboarding)
            .where(
                EmployeeOnboarding.organization_id == org_id,
                EmployeeOnboardingActivity.is_overdue == True,
            )
        )

        if assignee_id:
            query = query.where(EmployeeOnboardingActivity.assignee_id == assignee_id)

        query = query.order_by(EmployeeOnboardingActivity.due_date)
        query = query.limit(limit)

        return list(self.db.scalars(query).all())

    def get_activities_due_soon(
        self,
        org_id: UUID,
        *,
        days_ahead: int = 7,
        limit: int = 100,
    ) -> list[EmployeeOnboardingActivity]:
        """Get activities due within the specified number of days."""
        today = date.today()
        cutoff = today + timedelta(days=days_ahead)

        return list(
            self.db.scalars(
                select(EmployeeOnboardingActivity)
                .join(EmployeeOnboarding)
                .where(
                    EmployeeOnboarding.organization_id == org_id,
                    EmployeeOnboardingActivity.due_date.between(today, cutoff),
                    EmployeeOnboardingActivity.activity_status.in_(
                        [
                            ActivityStatus.PENDING.value,
                            ActivityStatus.IN_PROGRESS.value,
                        ]
                    ),
                )
                .order_by(EmployeeOnboardingActivity.due_date)
                .limit(limit)
            ).all()
        )

    # =========================================================================
    # Reminder Tracking
    # =========================================================================

    def mark_reminder_sent(self, activity_id: UUID) -> None:
        """Mark that a reminder was sent for an activity."""
        activity = self.db.get(EmployeeOnboardingActivity, activity_id)
        if activity:
            activity.reminder_sent_at = datetime.now(timezone.utc)
            self.db.flush()

    def get_activities_needing_reminder(
        self,
        org_id: UUID,
        *,
        days_before_due: int = 2,
        remind_if_overdue: bool = True,
        hours_since_last_reminder: int = 24,
    ) -> list[EmployeeOnboardingActivity]:
        """
        Get activities that need reminder notifications.

        Returns activities that:
        - Are due within days_before_due days, OR
        - Are overdue (if remind_if_overdue=True)
        - Haven't been reminded in the last hours_since_last_reminder hours
        """
        today = date.today()
        due_cutoff = today + timedelta(days=days_before_due)
        reminder_cutoff = datetime.now(timezone.utc) - timedelta(
            hours=hours_since_last_reminder
        )

        conditions = [
            EmployeeOnboarding.organization_id == org_id,
            EmployeeOnboardingActivity.activity_status.in_(
                [
                    ActivityStatus.PENDING.value,
                    ActivityStatus.IN_PROGRESS.value,
                    ActivityStatus.AWAITING_DOCUMENT.value,
                ]
            ),
            or_(
                EmployeeOnboardingActivity.reminder_sent_at.is_(None),
                EmployeeOnboardingActivity.reminder_sent_at < reminder_cutoff,
            ),
        ]

        # Due soon OR overdue
        if remind_if_overdue:
            conditions.append(
                or_(
                    and_(
                        EmployeeOnboardingActivity.due_date <= due_cutoff,
                        EmployeeOnboardingActivity.due_date >= today,
                    ),
                    EmployeeOnboardingActivity.is_overdue == True,
                )
            )
        else:
            conditions.append(
                and_(
                    EmployeeOnboardingActivity.due_date <= due_cutoff,
                    EmployeeOnboardingActivity.due_date >= today,
                )
            )

        return list(
            self.db.scalars(
                select(EmployeeOnboardingActivity)
                .join(EmployeeOnboarding)
                .where(*conditions)
                .order_by(EmployeeOnboardingActivity.due_date)
            ).all()
        )

    # =========================================================================
    # Status Transitions
    # =========================================================================

    def start_onboarding(self, org_id: UUID, onboarding_id: UUID) -> EmployeeOnboarding:
        """Transition onboarding to IN_PROGRESS status."""
        onboarding = self.get_onboarding(org_id, onboarding_id)

        if onboarding.status != BoardingStatus.PENDING:
            raise LifecycleStatusError(onboarding.status.value, "start onboarding")

        onboarding.status = BoardingStatus.IN_PROGRESS
        self.db.flush()

        logger.info("Started onboarding %s", onboarding_id)

        return onboarding

    def complete_onboarding(
        self,
        org_id: UUID,
        onboarding_id: UUID,
        *,
        force: bool = False,
    ) -> EmployeeOnboarding:
        """
        Complete an onboarding.

        If force=False, validates that all required activities are done.
        """
        onboarding = self.get_onboarding(org_id, onboarding_id)

        if onboarding.status == BoardingStatus.COMPLETED:
            return onboarding

        if onboarding.status not in (
            BoardingStatus.PENDING,
            BoardingStatus.IN_PROGRESS,
        ):
            raise LifecycleStatusError(onboarding.status.value, "complete onboarding")

        if not force:
            # Check for incomplete required activities
            incomplete = [
                a
                for a in onboarding.activities
                if a.activity_status
                not in (ActivityStatus.COMPLETED.value, ActivityStatus.SKIPPED.value)
                and a.status not in ("completed", "skipped")
            ]
            if incomplete:
                raise ValidationError(
                    f"Cannot complete onboarding - {len(incomplete)} activities still pending"
                )

        onboarding.status = BoardingStatus.COMPLETED
        onboarding.actual_completion_date = date.today()
        onboarding.progress_percentage = 100

        self.db.flush()

        logger.info("Completed onboarding %s", onboarding_id)

        return onboarding
