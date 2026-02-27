"""
Shift Swap Request Service.

Handles shift swap requests and approval workflow.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.people.hr.department import Department
from app.models.people.scheduling import (
    ScheduleStatus,
    ShiftSchedule,
    ShiftSwapRequest,
    SwapRequestStatus,
)
from app.services.common import PaginatedResult, PaginationParams
from app.services.notification import NotificationService

logger = logging.getLogger(__name__)

# Singleton notification service
_notification_service = NotificationService()


class SwapServiceError(Exception):
    """Base error for swap service."""

    pass


class SwapRequestNotFoundError(SwapServiceError):
    """Swap request not found."""

    def __init__(self, request_id: UUID):
        self.request_id = request_id
        super().__init__(f"Swap request {request_id} not found")


class InvalidSwapTransitionError(SwapServiceError):
    """Invalid status transition for swap request."""

    def __init__(self, current: SwapRequestStatus, target: str):
        self.current = current
        self.target = target
        super().__init__(f"Cannot {target} swap request in {current.value} status")


class SwapService:
    """
    Service for shift swap request operations.

    Handles:
    - Creating swap requests
    - Target employee acceptance
    - Manager approval/rejection
    - Executing swaps
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_swap_requests(
        self,
        org_id: UUID,
        *,
        status: SwapRequestStatus | None = None,
        requester_id: UUID | None = None,
        target_employee_id: UUID | None = None,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftSwapRequest]:
        """List swap requests."""
        query = (
            select(ShiftSwapRequest)
            .where(ShiftSwapRequest.organization_id == org_id)
            .options(
                joinedload(ShiftSwapRequest.requester),
                joinedload(ShiftSwapRequest.target_employee),
                joinedload(ShiftSwapRequest.requester_schedule).joinedload(
                    ShiftSchedule.shift_type
                ),
                joinedload(ShiftSwapRequest.target_schedule).joinedload(
                    ShiftSchedule.shift_type
                ),
            )
        )

        if status:
            query = query.where(ShiftSwapRequest.status == status)

        if requester_id:
            query = query.where(ShiftSwapRequest.requester_id == requester_id)

        if target_employee_id:
            query = query.where(
                ShiftSwapRequest.target_employee_id == target_employee_id
            )

        query = query.order_by(ShiftSwapRequest.created_at.desc())

        # Count total
        count_query = select(func.count()).select_from(query.subquery())
        total = self.db.scalar(count_query) or 0

        if pagination:
            query = query.offset(pagination.offset).limit(pagination.limit)

        items = list(self.db.scalars(query).unique().all())

        return PaginatedResult(
            items=items,
            total=total,
            offset=pagination.offset if pagination else 0,
            limit=pagination.limit if pagination else len(items),
        )

    def get_my_requests(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftSwapRequest]:
        """Get swap requests created by an employee."""
        return self.list_swap_requests(
            org_id, requester_id=employee_id, pagination=pagination
        )

    def get_pending_acceptance(
        self,
        org_id: UUID,
        employee_id: UUID,
        *,
        pagination: PaginationParams | None = None,
    ) -> PaginatedResult[ShiftSwapRequest]:
        """Get swap requests waiting for an employee's acceptance."""
        return self.list_swap_requests(
            org_id,
            target_employee_id=employee_id,
            status=SwapRequestStatus.PENDING,
            pagination=pagination,
        )

    def get_swap_request(self, org_id: UUID, request_id: UUID) -> ShiftSwapRequest:
        """Get a swap request by ID."""
        request = self.db.scalar(
            select(ShiftSwapRequest)
            .options(
                joinedload(ShiftSwapRequest.requester),
                joinedload(ShiftSwapRequest.target_employee),
                joinedload(ShiftSwapRequest.requester_schedule).joinedload(
                    ShiftSchedule.shift_type
                ),
                joinedload(ShiftSwapRequest.target_schedule).joinedload(
                    ShiftSchedule.shift_type
                ),
                joinedload(ShiftSwapRequest.reviewed_by),
            )
            .where(
                ShiftSwapRequest.swap_request_id == request_id,
                ShiftSwapRequest.organization_id == org_id,
            )
        )
        if not request:
            raise SwapRequestNotFoundError(request_id)
        return request

    def create_swap_request(
        self,
        org_id: UUID,
        *,
        requester_id: UUID,
        requester_schedule_id: UUID,
        target_schedule_id: UUID,
        reason: str | None = None,
    ) -> ShiftSwapRequest:
        """
        Create a new swap request.

        Args:
            org_id: Organization ID
            requester_id: Employee creating the request
            requester_schedule_id: Requester's schedule entry to swap
            target_schedule_id: Target employee's schedule entry to swap with
            reason: Optional reason for the swap

        Returns:
            The created swap request
        """
        # Validate schedules exist and are PUBLISHED
        requester_schedule = self._get_schedule(org_id, requester_schedule_id)
        target_schedule = self._get_schedule(org_id, target_schedule_id)

        # Verify requester owns their schedule
        if requester_schedule.employee_id != requester_id:
            raise SwapServiceError(
                "You can only create swap requests for your own schedule"
            )

        # Verify schedules are from the same organization and are PUBLISHED
        if requester_schedule.status != ScheduleStatus.PUBLISHED:
            raise SwapServiceError("Can only swap published schedules")
        if target_schedule.status != ScheduleStatus.PUBLISHED:
            raise SwapServiceError("Target schedule must be published")

        # Verify they are swapping for the same date (or can be different - depends on business rules)
        # For now, allow swapping different dates

        # Check for existing pending requests for the same schedules
        existing = self.db.scalar(
            select(ShiftSwapRequest).where(
                ShiftSwapRequest.organization_id == org_id,
                ShiftSwapRequest.status.in_(
                    [
                        SwapRequestStatus.PENDING,
                        SwapRequestStatus.TARGET_ACCEPTED,
                    ]
                ),
                or_(
                    ShiftSwapRequest.requester_schedule_id == requester_schedule_id,
                    ShiftSwapRequest.target_schedule_id == requester_schedule_id,
                    ShiftSwapRequest.requester_schedule_id == target_schedule_id,
                    ShiftSwapRequest.target_schedule_id == target_schedule_id,
                ),
            )
        )
        if existing:
            raise SwapServiceError(
                "A pending swap request already exists involving one of these schedules"
            )

        swap_request = ShiftSwapRequest(
            organization_id=org_id,
            requester_id=requester_id,
            requester_schedule_id=requester_schedule_id,
            target_schedule_id=target_schedule_id,
            target_employee_id=target_schedule.employee_id,
            reason=reason,
            status=SwapRequestStatus.PENDING,
        )

        self.db.add(swap_request)
        self.db.flush()

        logger.info(
            "Created swap request: %s -> %s",
            requester_id,
            target_schedule.employee_id,
        )

        # Notify target employee
        self._notify_swap_request_created(org_id, swap_request, target_schedule)

        return swap_request

    def _notify_swap_request_created(
        self,
        org_id: UUID,
        swap_request: ShiftSwapRequest,
        target_schedule: ShiftSchedule,
    ) -> None:
        """Notify target employee of a new swap request."""
        from app.models.people.hr.employee import Employee

        target_emp = self.db.get(Employee, target_schedule.employee_id)
        if not target_emp or not target_emp.person_id:
            return

        try:
            _notification_service.create(
                self.db,
                organization_id=org_id,
                recipient_id=target_emp.person_id,
                entity_type=EntityType.SYSTEM,
                entity_id=swap_request.swap_request_id,
                notification_type=NotificationType.INFO,
                title="Shift Swap Request",
                message=f"You have a new shift swap request for {target_schedule.shift_date}. Please review and accept or decline.",
                channel=NotificationChannel.BOTH,
                action_url="/people/self/scheduling/swaps",
            )
        except Exception as e:
            logger.warning("Failed to send swap request notification: %s", e)

    def accept_swap_request(
        self,
        org_id: UUID,
        request_id: UUID,
        accepting_employee_id: UUID,
    ) -> ShiftSwapRequest:
        """
        Target employee accepts the swap request.

        Moves status from PENDING to TARGET_ACCEPTED.
        """
        request = self.get_swap_request(org_id, request_id)

        if request.status != SwapRequestStatus.PENDING:
            raise InvalidSwapTransitionError(request.status, "accept")

        if request.target_employee_id != accepting_employee_id:
            raise SwapServiceError(
                "Only the target employee can accept this swap request"
            )

        request.status = SwapRequestStatus.TARGET_ACCEPTED
        request.target_accepted_at = datetime.now(UTC)

        self.db.flush()

        logger.info(
            "Swap request %s accepted by target employee",
            request_id,
        )

        # Notify requester that target accepted
        self._notify_swap_accepted(org_id, request)

        return request

    def _notify_swap_accepted(self, org_id: UUID, request: ShiftSwapRequest) -> None:
        """Notify requester and department manager that target employee accepted."""
        from app.models.people.hr.employee import Employee

        # Notify requester
        requester = self.db.get(Employee, request.requester_id)
        if requester and requester.person_id:
            try:
                _notification_service.create(
                    self.db,
                    organization_id=org_id,
                    recipient_id=requester.person_id,
                    entity_type=EntityType.SYSTEM,
                    entity_id=request.swap_request_id,
                    notification_type=NotificationType.STATUS_CHANGE,
                    title="Swap Request Accepted",
                    message="Your shift swap request has been accepted and is pending manager approval.",
                    channel=NotificationChannel.BOTH,
                    action_url="/people/self/scheduling/swaps",
                )
            except Exception as e:
                logger.warning(
                    "Failed to send swap accepted notification to requester: %s", e
                )

        # Notify department manager for approval
        self._notify_manager_for_approval(org_id, request)

    def _notify_manager_for_approval(
        self, org_id: UUID, request: ShiftSwapRequest
    ) -> None:
        """Notify department manager that a swap request needs approval."""
        from app.models.people.hr.employee import Employee

        # Get department head from requester's schedule department
        department_id = request.requester_schedule.department_id
        department = self.db.scalar(
            select(Department).where(
                Department.department_id == department_id,
                Department.organization_id == org_id,
            )
        )
        if not department or not department.head_id:
            logger.debug("No department head found for department %s", department_id)
            return

        # Get the department head's person_id
        head_employee = self.db.get(Employee, department.head_id)
        if not head_employee or not head_employee.person_id:
            logger.debug("Department head employee not found or has no person_id")
            return

        requester_name = (
            request.requester.full_name if request.requester else "An employee"
        )
        target_name = (
            request.target_employee.full_name
            if request.target_employee
            else "another employee"
        )

        try:
            _notification_service.create(
                self.db,
                organization_id=org_id,
                recipient_id=head_employee.person_id,
                entity_type=EntityType.SYSTEM,
                entity_id=request.swap_request_id,
                notification_type=NotificationType.SUBMITTED,
                title="Shift Swap Request Pending Approval",
                message=f"{requester_name} and {target_name} have agreed to swap shifts and need your approval.",
                channel=NotificationChannel.BOTH,
                action_url="/people/scheduling/swaps",
            )
        except Exception as e:
            logger.warning(
                "Failed to send swap approval notification to manager: %s", e
            )

    def approve_swap_request(
        self,
        org_id: UUID,
        request_id: UUID,
        manager_id: UUID,
        notes: str | None = None,
    ) -> ShiftSwapRequest:
        """
        Manager approves the swap request and executes the swap.

        Moves status from TARGET_ACCEPTED to APPROVED.
        """
        request = self.get_swap_request(org_id, request_id)

        if request.status != SwapRequestStatus.TARGET_ACCEPTED:
            raise InvalidSwapTransitionError(request.status, "approve")

        # Verify manager is authorized for the department
        department_id = request.requester_schedule.department_id
        self._verify_manager_authorization(org_id, manager_id, department_id)

        # Execute the swap - exchange shift types between the two schedules
        requester_schedule = request.requester_schedule
        target_schedule = request.target_schedule

        # Swap the shift types
        requester_shift_type_id = requester_schedule.shift_type_id
        target_shift_type_id = target_schedule.shift_type_id

        requester_schedule.shift_type_id = target_shift_type_id
        target_schedule.shift_type_id = requester_shift_type_id

        # Update request status
        request.status = SwapRequestStatus.APPROVED
        request.reviewed_by_id = manager_id
        request.reviewed_at = datetime.now(UTC)
        request.review_notes = notes

        self.db.flush()

        logger.info(
            "Swap request %s approved by %s. Shift types swapped.",
            request_id,
            manager_id,
        )

        # Notify both parties
        self._notify_swap_decision(org_id, request, approved=True)

        return request

    def _notify_swap_decision(
        self,
        org_id: UUID,
        request: ShiftSwapRequest,
        approved: bool,
    ) -> None:
        """Notify requester and target of swap decision."""
        from app.models.people.hr.employee import Employee

        status_text = "approved" if approved else "rejected"
        title = f"Swap Request {status_text.title()}"
        message = f"Your shift swap request has been {status_text}."
        notif_type = (
            NotificationType.APPROVED if approved else NotificationType.REJECTED
        )

        # Notify both employees
        for emp_id in [request.requester_id, request.target_employee_id]:
            emp = self.db.get(Employee, emp_id)
            if not emp or not emp.person_id:
                continue

            try:
                _notification_service.create(
                    self.db,
                    organization_id=org_id,
                    recipient_id=emp.person_id,
                    entity_type=EntityType.SYSTEM,
                    entity_id=request.swap_request_id,
                    notification_type=notif_type,
                    title=title,
                    message=message,
                    channel=NotificationChannel.BOTH,
                    action_url="/people/self/scheduling/swaps",
                )
            except Exception as e:
                logger.warning("Failed to send swap decision notification: %s", e)

    def reject_swap_request(
        self,
        org_id: UUID,
        request_id: UUID,
        manager_id: UUID,
        notes: str | None = None,
    ) -> ShiftSwapRequest:
        """
        Manager rejects the swap request.

        Can reject from PENDING or TARGET_ACCEPTED status.
        """
        request = self.get_swap_request(org_id, request_id)

        if request.status not in [
            SwapRequestStatus.PENDING,
            SwapRequestStatus.TARGET_ACCEPTED,
        ]:
            raise InvalidSwapTransitionError(request.status, "reject")

        # Verify manager is authorized for the department
        department_id = request.requester_schedule.department_id
        self._verify_manager_authorization(org_id, manager_id, department_id)

        request.status = SwapRequestStatus.REJECTED
        request.reviewed_by_id = manager_id
        request.reviewed_at = datetime.now(UTC)
        request.review_notes = notes

        self.db.flush()

        logger.info(
            "Swap request %s rejected by %s",
            request_id,
            manager_id,
        )

        # Notify both parties
        self._notify_swap_decision(org_id, request, approved=False)

        return request

    def cancel_swap_request(
        self,
        org_id: UUID,
        request_id: UUID,
        requester_id: UUID,
    ) -> ShiftSwapRequest:
        """
        Requester cancels their swap request.

        Can only cancel PENDING or TARGET_ACCEPTED requests.
        """
        request = self.get_swap_request(org_id, request_id)

        if request.requester_id != requester_id:
            raise SwapServiceError("Only the requester can cancel this swap request")

        if request.status not in [
            SwapRequestStatus.PENDING,
            SwapRequestStatus.TARGET_ACCEPTED,
        ]:
            raise InvalidSwapTransitionError(request.status, "cancel")

        request.status = SwapRequestStatus.CANCELLED

        self.db.flush()

        logger.info(
            "Swap request %s cancelled by requester",
            request_id,
        )

        return request

    def decline_swap_request(
        self,
        org_id: UUID,
        request_id: UUID,
        declining_employee_id: UUID,
        reason: str | None = None,
    ) -> ShiftSwapRequest:
        """
        Target employee declines the swap request.

        Can only decline PENDING requests. This is different from a manager
        rejection - this is the target employee refusing to swap.
        """
        request = self.get_swap_request(org_id, request_id)

        if request.target_employee_id != declining_employee_id:
            raise SwapServiceError(
                "Only the target employee can decline this swap request"
            )

        if request.status != SwapRequestStatus.PENDING:
            raise InvalidSwapTransitionError(request.status, "decline")

        request.status = SwapRequestStatus.REJECTED
        request.review_notes = (
            f"Declined by target employee: {reason}"
            if reason
            else "Declined by target employee"
        )

        self.db.flush()

        logger.info(
            "Swap request %s declined by target employee %s",
            request_id,
            declining_employee_id,
        )

        # Notify requester that target declined
        self._notify_swap_declined(org_id, request)

        return request

    def _notify_swap_declined(self, org_id: UUID, request: ShiftSwapRequest) -> None:
        """Notify requester that target employee declined."""
        from app.models.people.hr.employee import Employee

        requester = self.db.get(Employee, request.requester_id)
        if not requester or not requester.person_id:
            return

        target_name = (
            request.target_employee.full_name
            if request.target_employee
            else "The target employee"
        )

        try:
            _notification_service.create(
                self.db,
                organization_id=org_id,
                recipient_id=requester.person_id,
                entity_type=EntityType.SYSTEM,
                entity_id=request.swap_request_id,
                notification_type=NotificationType.REJECTED,
                title="Swap Request Declined",
                message=f"{target_name} has declined your shift swap request.",
                channel=NotificationChannel.BOTH,
                action_url="/people/self/scheduling/swaps",
            )
        except Exception as e:
            logger.warning("Failed to send swap declined notification: %s", e)

    def _get_schedule(self, org_id: UUID, schedule_id: UUID) -> ShiftSchedule:
        """Get a schedule by ID, raising error if not found."""
        schedule = self.db.scalar(
            select(ShiftSchedule).where(
                ShiftSchedule.shift_schedule_id == schedule_id,
                ShiftSchedule.organization_id == org_id,
            )
        )
        if not schedule:
            raise SwapServiceError(f"Schedule {schedule_id} not found")
        return schedule

    def _verify_manager_authorization(
        self,
        org_id: UUID,
        manager_id: UUID,
        department_id: UUID,
    ) -> None:
        """
        Verify that the manager is authorized to approve swaps for this department.

        A manager is authorized if they are:
        1. The department head of the schedule's department
        2. The head of a parent department in the hierarchy

        Raises SwapServiceError if not authorized.
        """
        # Check department hierarchy (up to 3 levels for safety)
        dept_id = department_id
        for _ in range(3):
            department = self.db.scalar(
                select(Department).where(
                    Department.department_id == dept_id,
                    Department.organization_id == org_id,
                )
            )
            if not department:
                break

            # Check if manager is the head of this department
            if department.head_id == manager_id:
                return  # Authorized

            # Move up to parent department
            if department.parent_department_id:
                dept_id = department.parent_department_id
            else:
                break

        raise SwapServiceError(
            "You are not authorized to approve swap requests for this department"
        )
