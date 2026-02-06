"""
Employee Info Change Request Service - Approval workflow for employee data updates.

Handles the workflow for approving/rejecting employee-submitted changes to
bank details, tax info, and pension info.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.people.hr.employee import Employee
from app.models.people.hr.info_change_request import (
    EmployeeInfoChangeRequest,
    InfoChangeStatus,
    InfoChangeType,
)
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.person import Gender as PersonGender
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.models.email_profile import EmailModule
from app.services.email import send_email
from app.services.notification import NotificationService

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class InfoChangeService:
    """
    Service for managing employee info change requests.

    Handles:
    - Creating pending change requests
    - Approving requests and applying changes
    - Rejecting requests
    - Querying pending requests for review
    """

    # How long a request remains valid before expiring
    DEFAULT_EXPIRY_DAYS = 30

    def __init__(self, db: Session):
        self.db = db
        self.notification_service = NotificationService()

    # =========================================================================
    # Create Change Requests
    # =========================================================================

    def submit_change_request(
        self,
        organization_id: UUID,
        employee_id: UUID,
        proposed_changes: dict[str, Any],
        *,
        requester_notes: Optional[str] = None,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> EmployeeInfoChangeRequest:
        """
        Submit a change request for employee info.

        Args:
            organization_id: Organization scope
            employee_id: Employee submitting the change
            proposed_changes: Dict of field->new_value
            requester_notes: Optional notes from employee
            expiry_days: Days until request expires

        Returns:
            Created EmployeeInfoChangeRequest

        The proposed_changes dict can contain:
        - Bank: bank_name, bank_account_number, bank_account_name, bank_branch_code
        - Tax: tin, tax_state
        - Pension: rsa_pin, pfa_code
        - NHF: nhf_number
        """
        # Get current values
        employee = self.db.get(Employee, employee_id)
        if not employee:
            raise ValueError(f"Employee {employee_id} not found")

        # Get current tax profile if exists
        tax_profile = self.db.scalar(
            select(EmployeeTaxProfile)
            .where(
                EmployeeTaxProfile.employee_id == employee_id,
                EmployeeTaxProfile.effective_to.is_(None),
            )
            .order_by(EmployeeTaxProfile.effective_from.desc())
            .limit(1)
        )

        # Determine change type and collect previous values
        change_type = self._determine_change_type(proposed_changes)
        previous_values = self._get_previous_values(
            employee, tax_profile, proposed_changes
        )

        # Create expiry time
        expires_at = datetime.now(timezone.utc) + timedelta(days=expiry_days)

        # Create the request
        request = EmployeeInfoChangeRequest(
            organization_id=organization_id,
            employee_id=employee_id,
            change_type=change_type,
            status=InfoChangeStatus.PENDING,
            proposed_changes=proposed_changes,
            previous_values=previous_values,
            requester_notes=requester_notes,
            expires_at=expires_at,
        )
        self.db.add(request)
        self.db.flush()

        logger.info(
            "Created info change request %s for employee %s (type=%s)",
            request.request_id,
            employee_id,
            change_type.value,
        )

        # Notify HR/manager about pending request
        self._notify_pending_request(request, employee)

        return request

    def _determine_change_type(self, changes: dict[str, Any]) -> InfoChangeType:
        """Determine the type of change based on fields being updated."""
        bank_fields = {
            "bank_name",
            "bank_account_number",
            "bank_account_name",
            "bank_branch_code",
        }
        tax_fields = {"tin", "tax_state"}
        pension_fields = {"rsa_pin", "pfa_code"}
        nhf_fields = {"nhf_number"}

        change_keys = set(changes.keys())

        types_found = []
        if change_keys & bank_fields:
            types_found.append(InfoChangeType.BANK_DETAILS)
        if change_keys & tax_fields:
            types_found.append(InfoChangeType.TAX_INFO)
        if change_keys & pension_fields:
            types_found.append(InfoChangeType.PENSION_INFO)
        if change_keys & nhf_fields:
            types_found.append(InfoChangeType.NHF_INFO)

        if len(types_found) > 1:
            return InfoChangeType.COMBINED
        elif len(types_found) == 1:
            return types_found[0]
        else:
            return InfoChangeType.COMBINED  # Fallback

    def _get_previous_values(
        self,
        employee: Employee,
        tax_profile: Optional[EmployeeTaxProfile],
        proposed: dict[str, Any],
    ) -> dict[str, Any]:
        """Get the current values for fields being changed."""
        previous = {}

        # Bank fields from employee
        if "bank_name" in proposed:
            previous["bank_name"] = employee.bank_name
        if "bank_account_number" in proposed:
            previous["bank_account_number"] = employee.bank_account_number
        if "bank_account_name" in proposed:
            previous["bank_account_name"] = employee.bank_account_name
        if "bank_branch_code" in proposed:
            previous["bank_branch_code"] = employee.bank_branch_code

        # Personal/contact fields from Person/Employee
        person = employee.person
        if person:
            if "phone" in proposed:
                previous["phone"] = person.phone
            if "date_of_birth" in proposed:
                previous["date_of_birth"] = (
                    person.date_of_birth.isoformat() if person.date_of_birth else None
                )
            if "gender" in proposed:
                previous["gender"] = person.gender.value if person.gender else None
            if "address_line1" in proposed:
                previous["address_line1"] = person.address_line1
            if "address_line2" in proposed:
                previous["address_line2"] = person.address_line2
            if "city" in proposed:
                previous["city"] = person.city
            if "region" in proposed:
                previous["region"] = person.region
            if "postal_code" in proposed:
                previous["postal_code"] = person.postal_code
            if "country_code" in proposed:
                previous["country_code"] = person.country_code
        if "personal_email" in proposed:
            previous["personal_email"] = employee.personal_email
        if "personal_phone" in proposed:
            previous["personal_phone"] = employee.personal_phone
        if "emergency_contact_name" in proposed:
            previous["emergency_contact_name"] = employee.emergency_contact_name
        if "emergency_contact_phone" in proposed:
            previous["emergency_contact_phone"] = employee.emergency_contact_phone

        # Tax/pension/NHF fields from tax profile
        if tax_profile:
            if "tin" in proposed:
                previous["tin"] = tax_profile.tin
            if "tax_state" in proposed:
                previous["tax_state"] = tax_profile.tax_state
            if "rsa_pin" in proposed:
                previous["rsa_pin"] = tax_profile.rsa_pin
            if "pfa_code" in proposed:
                previous["pfa_code"] = tax_profile.pfa_code
            if "nhf_number" in proposed:
                previous["nhf_number"] = tax_profile.nhf_number
        else:
            # No tax profile exists yet - previous values are None
            for field in ["tin", "tax_state", "rsa_pin", "pfa_code", "nhf_number"]:
                if field in proposed:
                    previous[field] = None

        return previous

    # =========================================================================
    # Approve/Reject Requests
    # =========================================================================

    def approve_request(
        self,
        organization_id: UUID,
        request_id: UUID,
        reviewer_id: UUID,
        *,
        reviewer_notes: Optional[str] = None,
    ) -> EmployeeInfoChangeRequest:
        """
        Approve a change request and apply the changes.

        Args:
            organization_id: Organization scope (required for multi-tenancy)
            request_id: The request to approve
            reviewer_id: Person approving the request
            reviewer_notes: Optional notes from reviewer

        Returns:
            Updated EmployeeInfoChangeRequest
        """
        request = self.db.get(EmployeeInfoChangeRequest, request_id)
        if not request or request.organization_id != organization_id:
            raise ValueError(f"Request {request_id} not found")

        if not request.is_actionable:
            raise ValueError(
                f"Request {request_id} is not actionable (status={request.status.value})"
            )

        # Apply the changes
        self._apply_changes(request)

        # Update request status
        request.status = InfoChangeStatus.APPROVED
        request.reviewer_id = reviewer_id
        request.reviewer_notes = reviewer_notes
        request.reviewed_at = datetime.now(timezone.utc)

        self.db.flush()

        logger.info(
            "Approved info change request %s for employee %s by %s",
            request_id,
            request.employee_id,
            reviewer_id,
        )

        # Notify employee of approval
        self._notify_decision(request, approved=True)

        return request

    def reject_request(
        self,
        organization_id: UUID,
        request_id: UUID,
        reviewer_id: UUID,
        *,
        reviewer_notes: Optional[str] = None,
    ) -> EmployeeInfoChangeRequest:
        """
        Reject a change request.

        Args:
            organization_id: Organization scope (required for multi-tenancy)
            request_id: The request to reject
            reviewer_id: Person rejecting the request
            reviewer_notes: Optional notes explaining rejection

        Returns:
            Updated EmployeeInfoChangeRequest
        """
        request = self.db.get(EmployeeInfoChangeRequest, request_id)
        if not request or request.organization_id != organization_id:
            raise ValueError(f"Request {request_id} not found")

        if not request.is_actionable:
            raise ValueError(
                f"Request {request_id} is not actionable (status={request.status.value})"
            )

        # Update request status
        request.status = InfoChangeStatus.REJECTED
        request.reviewer_id = reviewer_id
        request.reviewer_notes = reviewer_notes
        request.reviewed_at = datetime.now(timezone.utc)

        self.db.flush()

        logger.info(
            "Rejected info change request %s for employee %s by %s",
            request_id,
            request.employee_id,
            reviewer_id,
        )

        # Notify employee of rejection
        self._notify_decision(request, approved=False)

        return request

    def _apply_changes(self, request: EmployeeInfoChangeRequest) -> None:
        """Apply the proposed changes to employee/tax profile."""
        employee = self.db.get(Employee, request.employee_id)
        if not employee:
            raise ValueError(f"Employee {request.employee_id} not found")

        changes = request.proposed_changes

        def _clean_text(value: object) -> Optional[str]:
            if value is None:
                return None
            text = str(value).strip()
            if not text:
                return None
            if text.lower() in {"none", "null"}:
                return None
            return text

        # Apply bank changes to employee
        bank_fields = [
            "bank_name",
            "bank_account_number",
            "bank_account_name",
            "bank_branch_code",
        ]
        for field in bank_fields:
            if field in changes:
                setattr(employee, field, _clean_text(changes[field]))

        # Apply personal/contact changes
        person = employee.person or self.db.get(Person, employee.person_id)
        if person:
            person_fields = [
                "phone",
                "address_line1",
                "address_line2",
                "city",
                "region",
                "postal_code",
                "country_code",
            ]
            for field in person_fields:
                if field in changes:
                    value = _clean_text(changes[field])
                    if field == "country_code" and value:
                        value = value.upper()
                        if len(value) != 2:
                            value = None
                    setattr(person, field, value)
            if "date_of_birth" in changes:
                value = changes.get("date_of_birth")
                if isinstance(value, str) and value:
                    try:
                        from datetime import date as dt_date

                        person.date_of_birth = dt_date.fromisoformat(value)
                    except ValueError:
                        person.date_of_birth = None
                else:
                    person.date_of_birth = None
            if "gender" in changes:
                value = changes.get("gender")
                if value:
                    try:
                        person.gender = PersonGender(value)
                    except Exception:
                        person.gender = cast(Any, None)
                else:
                    person.gender = cast(Any, None)
        employee_fields = [
            "personal_email",
            "personal_phone",
            "emergency_contact_name",
            "emergency_contact_phone",
        ]
        for field in employee_fields:
            if field in changes:
                setattr(employee, field, _clean_text(changes[field]))

        # Apply tax/pension/NHF changes to tax profile
        tax_fields = ["tin", "tax_state", "rsa_pin", "pfa_code", "nhf_number"]
        tax_changes = {k: v for k, v in changes.items() if k in tax_fields}

        if tax_changes:
            # Get or create tax profile
            tax_profile = self.db.scalar(
                select(EmployeeTaxProfile)
                .where(
                    EmployeeTaxProfile.employee_id == request.employee_id,
                    EmployeeTaxProfile.effective_to.is_(None),
                )
                .order_by(EmployeeTaxProfile.effective_from.desc())
                .limit(1)
            )

            if not tax_profile:
                # Create new tax profile
                from datetime import date

                tax_profile = EmployeeTaxProfile(
                    employee_id=request.employee_id,
                    organization_id=request.organization_id,
                    effective_from=date.today(),
                )
                self.db.add(tax_profile)

        # Apply changes
        for field, value in tax_changes.items():
            setattr(tax_profile, field, _clean_text(value))

        self.db.flush()

    # =========================================================================
    # Query Methods
    # =========================================================================

    def get_pending_requests(
        self,
        organization_id: UUID,
        *,
        employee_id: Optional[UUID] = None,
        limit: int = 100,
    ) -> list[EmployeeInfoChangeRequest]:
        """
        Get pending change requests for review.

        Args:
            organization_id: Organization scope
            employee_id: Optional filter by specific employee
            limit: Max results

        Returns:
            List of pending requests
        """
        stmt = (
            select(EmployeeInfoChangeRequest)
            .where(
                EmployeeInfoChangeRequest.organization_id == organization_id,
                EmployeeInfoChangeRequest.status == InfoChangeStatus.PENDING,
            )
            .order_by(EmployeeInfoChangeRequest.created_at.asc())
            .limit(limit)
        )

        if employee_id:
            stmt = stmt.where(EmployeeInfoChangeRequest.employee_id == employee_id)

        return list(self.db.scalars(stmt).all())

    def get_request_by_id(
        self, organization_id: UUID, request_id: UUID
    ) -> Optional[EmployeeInfoChangeRequest]:
        """Get a specific request by ID within organization scope."""
        request = self.db.get(EmployeeInfoChangeRequest, request_id)
        if request and request.organization_id != organization_id:
            # Request exists but belongs to different org - treat as not found
            return None
        return request

    def get_employee_requests(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        include_resolved: bool = False,
        limit: int = 20,
    ) -> list[EmployeeInfoChangeRequest]:
        """
        Get change requests for a specific employee.

        Args:
            organization_id: Organization scope (required for multi-tenancy)
            employee_id: Employee to get requests for
            include_resolved: Include approved/rejected requests
            limit: Max results

        Returns:
            List of requests
        """
        stmt = (
            select(EmployeeInfoChangeRequest)
            .where(
                EmployeeInfoChangeRequest.organization_id == organization_id,
                EmployeeInfoChangeRequest.employee_id == employee_id,
            )
            .order_by(EmployeeInfoChangeRequest.created_at.desc())
            .limit(limit)
        )

        if not include_resolved:
            stmt = stmt.where(
                EmployeeInfoChangeRequest.status == InfoChangeStatus.PENDING
            )

        return list(self.db.scalars(stmt).all())

    def has_pending_request(self, organization_id: UUID, employee_id: UUID) -> bool:
        """Check if employee has any pending change requests."""
        from sqlalchemy import func

        count = self.db.scalar(
            select(func.count(EmployeeInfoChangeRequest.request_id)).where(
                EmployeeInfoChangeRequest.organization_id == organization_id,
                EmployeeInfoChangeRequest.employee_id == employee_id,
                EmployeeInfoChangeRequest.status == InfoChangeStatus.PENDING,
            )
        )
        return (count or 0) > 0

    # =========================================================================
    # Notifications
    # =========================================================================

    def _get_admin_recipients(self, organization_id: UUID) -> list[Person]:
        """Get active admin users for an organization."""
        stmt = (
            select(Person.id)
            .join(PersonRole, PersonRole.person_id == Person.id)
            .join(Role, PersonRole.role_id == Role.id)
            .where(
                Person.organization_id == organization_id,
                Person.is_active.is_(True),
                Role.name.in_(["admin", "hr_manager"]),
                Role.is_active.is_(True),
            )
            .distinct()
        )
        person_ids = list(self.db.scalars(stmt).all())
        if not person_ids:
            return []
        return list(
            self.db.scalars(select(Person).where(Person.id.in_(person_ids))).all()
        )

    def _build_app_url(self, path: str) -> str:
        base = settings.app_url.rstrip("/")
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}"

    def _send_email_safe(
        self,
        to_email: Optional[str],
        subject: str,
        body_html: str,
        body_text: str,
        organization_id: UUID | None,
    ) -> None:
        if not to_email:
            return
        try:
            send_email(
                self.db,
                to_email,
                subject,
                body_html,
                body_text,
                module=EmailModule.PEOPLE_PAYROLL,
                organization_id=organization_id,
            )
        except Exception as exc:
            logger.warning("Failed to send info change email to %s: %s", to_email, exc)

    def _notify_pending_request(
        self,
        request: EmployeeInfoChangeRequest,
        employee: Employee,
    ) -> None:
        """Notify HR about a pending change request."""
        action_path = f"/people/hr/info-changes/{request.request_id}"
        action_url = self._build_app_url(action_path)
        change_label = request.change_type.value.lower().replace("_", " ")
        employee_name = employee.full_name or employee.employee_code

        # Notify the employee's manager if they have one
        if employee.reports_to_id:
            manager = self.db.get(Employee, employee.reports_to_id)
            if manager and manager.person_id:
                try:
                    self.notification_service.create(
                        self.db,
                        organization_id=request.organization_id,
                        recipient_id=manager.person_id,
                        entity_type=EntityType.EMPLOYEE,
                        entity_id=request.request_id,
                        notification_type=NotificationType.SUBMITTED,
                        title="Employee Info Update Request",
                        message=(
                            f"{employee_name} has requested an update to their {change_label}. "
                            "Please review and approve/reject."
                        ),
                        channel=NotificationChannel.IN_APP,
                        action_url=action_path,
                    )
                except Exception as e:
                    logger.warning("Failed to notify manager: %s", e)

        # Notify admins
        admin_recipients = self._get_admin_recipients(request.organization_id)
        for admin in admin_recipients:
            try:
                self.notification_service.create(
                    self.db,
                    organization_id=request.organization_id,
                    recipient_id=admin.id,
                    entity_type=EntityType.EMPLOYEE,
                    entity_id=request.request_id,
                    notification_type=NotificationType.SUBMITTED,
                    title="Employee Info Update Request",
                    message=(
                        f"{employee_name} has requested an update to their {change_label}. "
                        "Please review and approve/reject."
                    ),
                    channel=NotificationChannel.IN_APP,
                    action_url=action_path,
                )
            except Exception as e:
                logger.warning("Failed to notify admin %s: %s", admin.id, e)

            subject = "Employee info change request submitted"
            body_text = (
                f"{employee_name} submitted a request to update their {change_label}.\n"
                f"Review request: {action_url}"
            )
            body_html = (
                f"<p>{employee_name} submitted a request to update their {change_label}.</p>"
                f'<p><a href="{action_url}">Review request</a></p>'
            )
            self._send_email_safe(
                admin.email,
                subject,
                body_html,
                body_text,
                request.organization_id,
            )

    def _notify_decision(
        self,
        request: EmployeeInfoChangeRequest,
        approved: bool,
    ) -> None:
        """Notify employee of approval decision."""
        employee = self.db.get(Employee, request.employee_id)
        if not employee or not employee.person_id:
            return

        status = "approved" if approved else "rejected"
        notification_type = (
            NotificationType.APPROVED if approved else NotificationType.REJECTED
        )
        action_path = "/people/self/tax-info"
        action_url = self._build_app_url(action_path)
        change_label = request.change_type.value.lower().replace("_", " ")
        employee_email = employee.work_email or employee.personal_email

        try:
            self.notification_service.create(
                self.db,
                organization_id=request.organization_id,
                recipient_id=employee.person_id,
                entity_type=EntityType.EMPLOYEE,
                entity_id=request.request_id,
                notification_type=notification_type,
                title=f"Info Update {status.title()}",
                message=(
                    f"Your request to update your {change_label} "
                    f"has been {status}."
                    + (
                        f" Reason: {request.reviewer_notes}"
                        if request.reviewer_notes
                        else ""
                    )
                ),
                channel=NotificationChannel.IN_APP,
                action_url=action_path,
            )
        except Exception as e:
            logger.warning("Failed to notify employee of decision: %s", e)

        subject = f"Your info change request was {status}"
        reason_line = (
            f"\nReason: {request.reviewer_notes}" if request.reviewer_notes else ""
        )
        body_text = (
            f"Your request to update your {change_label} was {status}."
            f"{reason_line}\n"
            f"View details: {action_url}"
        )
        body_html = (
            f"<p>Your request to update your {change_label} was {status}.</p>"
            f"{f'<p>Reason: {request.reviewer_notes}</p>' if request.reviewer_notes else ''}"
            f'<p><a href="{action_url}">View details</a></p>'
        )
        self._send_email_safe(
            employee_email,
            subject,
            body_html,
            body_text,
            request.organization_id,
        )
