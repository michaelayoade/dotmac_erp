"""
Employee Info Change Request Service - Approval workflow for employee data updates.

Handles the workflow for approving/rejecting employee-submitted changes to
bank details, tax info, and pension info.
"""

from __future__ import annotations

import html
import logging
from collections.abc import AsyncIterable, Iterable
from enum import Enum
from pathlib import Path
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse
from uuid import UUID

try:
    from datetime import UTC  # type: ignore
except ImportError:  # pragma: no cover
    UTC = timezone.utc

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import settings
from app.models.email_profile import EmailModule
from app.models.notification import EntityType, NotificationChannel, NotificationType
from app.models.people.hr.employee import Employee, EmployeeStatus
from app.models.people.hr.employee_extended import (
    DocumentType,
    EmployeeCertification,
    EmployeeDependent,
    EmployeeQualification,
    EmployeeSkill,
    Gender as DependentGender,
    QualificationType,
    RelationshipType,
)
from app.models.people.hr.info_change_request import (
    EmployeeInfoChangeBatch,
    EmployeeInfoChangeRequest,
    InfoChangeOperation,
    InfoChangeStatus,
    InfoChangeType,
)
from app.models.people.payroll.employee_tax_profile import EmployeeTaxProfile
from app.models.person import Gender as PersonGender
from app.models.person import Person
from app.models.rbac import PersonRole, Role
from app.services.file_upload import get_employee_document_upload
from app.services.email import employee_can_receive_email, send_email
from app.services.notification import NotificationService
from app.services.people.hr.employee_extended import (
    EmployeeCertificationService,
    EmployeeDependentService,
    EmployeeDocumentService,
    EmployeeQualificationService,
    EmployeeSkillService,
    SkillService,
)
from app.services.people.hr.org_resolver import OrgResolver
from app.services.storage import get_storage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


def _json_safe_value(value: Any) -> Any:
    """Convert validated workflow data into values accepted by JSON columns."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return value


@dataclass(frozen=True)
class PendingEvidence:
    """Pending evidence metadata stored on an info-change request."""

    path: str
    file_name: str
    file_size: int
    mime_type: str | None
    checksum: str | None = None


@dataclass(frozen=True)
class ExtendedBatchItemInput:
    """Normalized extended-profile batch item."""

    proposed_changes: dict[str, Any]
    previous_values: dict[str, Any]
    operation: InfoChangeOperation = InfoChangeOperation.CREATE
    target_record_id: UUID | None = None
    pending_evidence: PendingEvidence | None = None


@dataclass(frozen=True)
class DocumentBatchItemInput:
    """Normalized document batch item."""

    proposed_changes: dict[str, Any]
    pending_evidence: PendingEvidence


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
    MY_INFO_CHANGE_TYPES = (
        InfoChangeType.BANK_DETAILS,
        InfoChangeType.TAX_INFO,
        InfoChangeType.PENSION_INFO,
        InfoChangeType.NHF_INFO,
        InfoChangeType.COMBINED,
    )
    EXTENDED_CHANGE_TYPES = (
        InfoChangeType.QUALIFICATION,
        InfoChangeType.CERTIFICATION,
        InfoChangeType.SKILL,
        InfoChangeType.DEPENDENT,
    )
    DOCUMENT_CHANGE_TYPES = (InfoChangeType.DOCUMENT,)
    MAX_BATCH_ITEMS = 20
    MAX_BATCH_TOTAL_UPLOAD_BYTES = 50 * 1024 * 1024
    SELF_SERVICE_DOCUMENT_TYPES = frozenset(
        {
            DocumentType.ID_PROOF,
            DocumentType.PASSPORT,
            DocumentType.VISA,
            DocumentType.WORK_PERMIT,
            DocumentType.EDUCATIONAL,
            DocumentType.PROFESSIONAL,
            DocumentType.MEDICAL,
            DocumentType.TAX_FORM,
            DocumentType.BANK_DETAILS,
            DocumentType.OTHER,
        }
    )

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
        requester_notes: str | None = None,
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
        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == employee_id,
                Employee.organization_id == organization_id,
            )
        )
        if not employee:
            raise ValueError(f"Employee {employee_id} not found")

        # Get current tax profile if exists
        tax_profile = self.db.scalar(
            select(EmployeeTaxProfile)
            .where(
                EmployeeTaxProfile.employee_id == employee_id,
                EmployeeTaxProfile.organization_id == organization_id,
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
        expires_at = datetime.now(UTC) + timedelta(days=expiry_days)

        # Create the request
        request = EmployeeInfoChangeRequest(
            organization_id=organization_id,
            employee_id=employee_id,
            change_type=change_type,
            status=InfoChangeStatus.PENDING,
            proposed_changes=_json_safe_value(proposed_changes),
            previous_values=_json_safe_value(previous_values),
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

    def submit_extended_change_request(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        change_type: InfoChangeType,
        operation: InfoChangeOperation,
        proposed_changes: dict[str, Any],
        previous_values: dict[str, Any],
        requester_notes: str | None = None,
        target_record_id: UUID | None = None,
        pending_evidence: PendingEvidence | None = None,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> EmployeeInfoChangeRequest:
        """Submit an extended-profile change request for approval."""
        batch = self.submit_extended_change_batch(
            organization_id,
            employee_id,
            change_type=change_type,
            items=[
                ExtendedBatchItemInput(
                    proposed_changes=proposed_changes,
                    previous_values=previous_values,
                    operation=operation,
                    target_record_id=target_record_id,
                    pending_evidence=pending_evidence,
                )
            ],
            requester_notes=requester_notes,
            expiry_days=expiry_days,
        )
        return batch.items[0]

    def submit_document_change_request(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        proposed_changes: dict[str, Any],
        requester_notes: str | None = None,
        pending_evidence: PendingEvidence,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> EmployeeInfoChangeRequest:
        """Submit a self-service employee document upload for HR approval."""
        batch = self.submit_document_change_batch(
            organization_id,
            employee_id,
            items=[
                DocumentBatchItemInput(
                    proposed_changes=proposed_changes,
                    pending_evidence=pending_evidence,
                )
            ],
            requester_notes=requester_notes,
            expiry_days=expiry_days,
        )
        return batch.items[0]

    def submit_extended_change_batch(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        change_type: InfoChangeType,
        items: list[ExtendedBatchItemInput],
        requester_notes: str | None = None,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> EmployeeInfoChangeBatch:
        """Submit a repeatable extended-profile batch for approval."""
        if change_type not in self.EXTENDED_CHANGE_TYPES:
            raise ValueError("Unsupported extended profile change type")
        employee = self._get_employee_or_raise(organization_id, employee_id)
        normalized_items = self._prepare_extended_batch_items(
            organization_id,
            employee_id,
            change_type=change_type,
            items=items,
        )
        batch = self._create_batch_record(
            organization_id=organization_id,
            employee_id=employee_id,
            change_type=change_type,
            requester_notes=requester_notes,
            expiry_days=expiry_days,
        )
        for index, item in enumerate(normalized_items, start=1):
            self._append_batch_request(
                batch=batch,
                employee_id=employee_id,
                change_type=change_type,
                operation=item.operation,
                proposed_changes=item.proposed_changes,
                previous_values=item.previous_values,
                requester_notes=requester_notes,
                target_record_id=item.target_record_id,
                batch_item_order=index,
                pending_evidence=item.pending_evidence,
            )
        self.db.flush()
        self._notify_pending_batch(batch, employee)
        return batch

    def submit_document_change_batch(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        items: list[DocumentBatchItemInput],
        requester_notes: str | None = None,
        expiry_days: int = DEFAULT_EXPIRY_DAYS,
    ) -> EmployeeInfoChangeBatch:
        """Submit a repeatable document batch for approval."""
        employee = self._get_employee_or_raise(organization_id, employee_id)
        normalized_items = self._prepare_document_batch_items(
            organization_id,
            employee_id,
            items=items,
        )
        batch = self._create_batch_record(
            organization_id=organization_id,
            employee_id=employee_id,
            change_type=InfoChangeType.DOCUMENT,
            requester_notes=requester_notes,
            expiry_days=expiry_days,
        )
        for index, item in enumerate(normalized_items, start=1):
            self._append_batch_request(
                batch=batch,
                employee_id=employee_id,
                change_type=InfoChangeType.DOCUMENT,
                operation=InfoChangeOperation.CREATE,
                proposed_changes=item.proposed_changes,
                previous_values={},
                requester_notes=requester_notes,
                target_record_id=None,
                batch_item_order=index,
                pending_evidence=item.pending_evidence,
            )
        self.db.flush()
        self._notify_pending_batch(batch, employee)
        return batch

    def _create_batch_record(
        self,
        *,
        organization_id: UUID,
        employee_id: UUID,
        change_type: InfoChangeType,
        requester_notes: str | None,
        expiry_days: int,
    ) -> EmployeeInfoChangeBatch:
        expires_at = datetime.now(UTC) + timedelta(days=expiry_days)
        batch = EmployeeInfoChangeBatch(
            organization_id=organization_id,
            employee_id=employee_id,
            change_type=change_type,
            requester_notes=requester_notes,
            expires_at=expires_at,
        )
        self.db.add(batch)
        self.db.flush()
        return batch

    def _append_batch_request(
        self,
        *,
        batch: EmployeeInfoChangeBatch,
        employee_id: UUID,
        change_type: InfoChangeType,
        operation: InfoChangeOperation,
        proposed_changes: dict[str, Any],
        previous_values: dict[str, Any],
        requester_notes: str | None,
        target_record_id: UUID | None,
        batch_item_order: int,
        pending_evidence: PendingEvidence | None,
    ) -> EmployeeInfoChangeRequest:
        request = EmployeeInfoChangeRequest(
            organization_id=batch.organization_id,
            employee_id=employee_id,
            batch=batch,
            batch_item_order=batch_item_order,
            change_type=change_type,
            operation=operation,
            target_record_id=target_record_id,
            status=InfoChangeStatus.PENDING,
            proposed_changes=_json_safe_value(proposed_changes),
            previous_values=_json_safe_value(previous_values),
            requester_notes=requester_notes,
            expires_at=batch.expires_at,
            pending_document_path=pending_evidence.path if pending_evidence else None,
            pending_document_name=pending_evidence.file_name
            if pending_evidence
            else None,
            pending_document_size=pending_evidence.file_size
            if pending_evidence
            else None,
            pending_document_mime_type=pending_evidence.mime_type
            if pending_evidence
            else None,
            pending_document_checksum=pending_evidence.checksum
            if pending_evidence
            else None,
        )
        self.db.add(request)
        self.db.flush()
        return request

    def expire_requests(
        self,
        organization_id: UUID,
        *,
        employee_id: UUID | None = None,
        change_type: InfoChangeType | None = None,
    ) -> int:
        """Expire pending requests whose expiry timestamp has passed."""
        now_utc = datetime.now(UTC)
        stmt = select(EmployeeInfoChangeRequest).where(
            EmployeeInfoChangeRequest.organization_id == organization_id,
            EmployeeInfoChangeRequest.status == InfoChangeStatus.PENDING,
            EmployeeInfoChangeRequest.expires_at.is_not(None),
            EmployeeInfoChangeRequest.expires_at < now_utc,
        )
        if employee_id:
            stmt = stmt.where(EmployeeInfoChangeRequest.employee_id == employee_id)
        if change_type:
            stmt = stmt.where(EmployeeInfoChangeRequest.change_type == change_type)

        expired = list(self.db.scalars(stmt).all())
        for request in expired:
            request.status = InfoChangeStatus.EXPIRED
            request.reviewed_at = now_utc
            request.reviewer_notes = (
                request.reviewer_notes or "Request expired before review"
            )
            self._cleanup_pending_evidence(request)
        if expired:
            self.db.flush()
        return len(expired)

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
        tax_profile: EmployeeTaxProfile | None,
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

    def _get_employee_or_raise(
        self,
        organization_id: UUID,
        employee_id: UUID,
    ) -> Employee:
        employee = self.db.scalar(
            select(Employee)
            .options(joinedload(Employee.person))
            .where(
                Employee.organization_id == organization_id,
                Employee.employee_id == employee_id,
            )
        )
        if not employee:
            raise ValueError(f"Employee {employee_id} not found")
        if getattr(employee, "status", None) == EmployeeStatus.TERMINATED:
            raise ValueError(
                "Terminated employees cannot submit profile change requests"
            )
        return employee

    def _cleanup_pending_evidence(self, request: EmployeeInfoChangeRequest) -> None:
        """Delete pending evidence for a request if present."""
        if not request.pending_document_path:
            return
        upload_service = get_employee_document_upload()
        try:
            upload_service.delete(request.pending_document_path)
        except Exception:
            logger.exception(
                "Failed to delete pending evidence for info change request %s",
                request.request_id,
            )
        request.pending_document_path = None
        request.pending_document_name = None
        request.pending_document_size = None
        request.pending_document_mime_type = None
        request.pending_document_checksum = None

    def _validate_batch_size(
        self,
        items_count: int,
        *,
        total_upload_bytes: int = 0,
    ) -> None:
        if items_count <= 0:
            raise ValueError("At least one non-blank row is required")
        if items_count > self.MAX_BATCH_ITEMS:
            raise ValueError(
                f"You can submit at most {self.MAX_BATCH_ITEMS} items at once"
            )
        if total_upload_bytes > self.MAX_BATCH_TOTAL_UPLOAD_BYTES:
            raise ValueError("Combined uploads exceed the total request size limit")

    def _prepare_extended_batch_items(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        change_type: InfoChangeType,
        items: list[ExtendedBatchItemInput],
    ) -> list[ExtendedBatchItemInput]:
        total_upload_bytes = sum(
            item.pending_evidence.file_size for item in items if item.pending_evidence
        )
        self._validate_batch_size(len(items), total_upload_bytes=total_upload_bytes)
        prepared: list[ExtendedBatchItemInput] = []
        seen_keys: set[str] = set()
        for item in items:
            if (
                item.operation == InfoChangeOperation.UPDATE
                and item.target_record_id is None
            ):
                raise ValueError("Update requests require a target record")
            if (
                item.operation == InfoChangeOperation.CREATE
                and item.target_record_id is not None
            ):
                raise ValueError("Create requests cannot specify a target record")
            normalized = self._validate_extended_payload(
                organization_id,
                employee_id,
                change_type=change_type,
                payload=item.proposed_changes,
                target_record_id=item.target_record_id,
            )
            key = self._dedupe_key_for_change(change_type, normalized)
            if item.operation == InfoChangeOperation.CREATE and key in seen_keys:
                raise ValueError(
                    "Duplicate rows are not allowed in the same submission"
                )
            if item.operation == InfoChangeOperation.CREATE:
                seen_keys.add(key)
            self._assert_extended_conflicts(
                organization_id,
                employee_id,
                change_type=change_type,
                operation=item.operation,
                proposed_changes=normalized,
                target_record_id=item.target_record_id,
            )
            prepared.append(
                ExtendedBatchItemInput(
                    proposed_changes=normalized,
                    previous_values=item.previous_values,
                    operation=item.operation,
                    target_record_id=item.target_record_id,
                    pending_evidence=item.pending_evidence,
                )
            )
        return prepared

    def _prepare_document_batch_items(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        items: list[DocumentBatchItemInput],
    ) -> list[DocumentBatchItemInput]:
        total_upload_bytes = sum(item.pending_evidence.file_size for item in items)
        self._validate_batch_size(len(items), total_upload_bytes=total_upload_bytes)
        prepared: list[DocumentBatchItemInput] = []
        seen_keys: set[str] = set()
        for item in items:
            normalized = self._validate_document_payload(item.proposed_changes)
            duplicate = self._find_duplicate_pending_document_request(
                organization_id,
                employee_id,
                proposed_changes=normalized,
                original_filename=item.pending_evidence.file_name,
                checksum=item.pending_evidence.checksum,
            )
            if duplicate:
                raise ValueError("A matching document upload is already pending review")
            duplicate_key = self._document_duplicate_key(
                normalized,
                item.pending_evidence.file_name,
                item.pending_evidence.checksum,
            )
            if duplicate_key in seen_keys:
                raise ValueError("Duplicate document rows are not allowed")
            seen_keys.add(duplicate_key)
            self._assert_document_not_already_uploaded(
                organization_id,
                employee_id,
                normalized,
                item.pending_evidence.file_name,
                item.pending_evidence.checksum,
            )
            prepared.append(
                DocumentBatchItemInput(
                    proposed_changes={
                        **normalized,
                        "pending_original_filename": item.pending_evidence.file_name,
                        "pending_file_size": item.pending_evidence.file_size,
                        "pending_mime_type": item.pending_evidence.mime_type,
                        "pending_checksum": item.pending_evidence.checksum,
                    },
                    pending_evidence=item.pending_evidence,
                )
            )
        return prepared

    def _validate_extended_payload(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        change_type: InfoChangeType,
        payload: dict[str, Any],
        target_record_id: UUID | None,
    ) -> dict[str, Any]:
        if change_type == InfoChangeType.QUALIFICATION:
            return self._validate_qualification_payload(payload)
        if change_type == InfoChangeType.CERTIFICATION:
            return self._validate_certification_payload(payload)
        if change_type == InfoChangeType.SKILL:
            return self._validate_skill_payload(
                organization_id,
                employee_id,
                payload,
                target_employee_skill_id=target_record_id,
            )
        if change_type == InfoChangeType.DEPENDENT:
            return self._validate_dependent_payload(payload)
        raise ValueError("Unsupported extended profile change type")

    def _assert_extended_conflicts(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        change_type: InfoChangeType,
        operation: InfoChangeOperation,
        proposed_changes: dict[str, Any],
        target_record_id: UUID | None,
    ) -> None:
        self.expire_requests(
            organization_id,
            employee_id=employee_id,
            change_type=change_type,
        )
        pending = self.get_pending_requests(
            organization_id,
            employee_id=employee_id,
            change_type=change_type,
            limit=200,
        )
        if operation == InfoChangeOperation.UPDATE:
            if target_record_id is None:
                raise ValueError("Update requests require a target record")
            for request in pending:
                if (
                    request.operation == InfoChangeOperation.UPDATE
                    and request.target_record_id == target_record_id
                ):
                    raise ValueError("A pending update already exists for this record")
            return

        duplicate_key = self._dedupe_key_for_change(change_type, proposed_changes)
        for request in pending:
            if request.operation != InfoChangeOperation.CREATE:
                continue
            if (
                self._dedupe_key_for_change(change_type, request.proposed_changes)
                == duplicate_key
            ):
                raise ValueError("A matching request is already pending review")
        self._assert_no_approved_duplicate(
            organization_id,
            employee_id,
            change_type=change_type,
            proposed_changes=proposed_changes,
        )

    def _dedupe_key_for_change(
        self,
        change_type: InfoChangeType,
        proposed_changes: dict[str, Any],
    ) -> str:
        if change_type == InfoChangeType.QUALIFICATION:
            return "|".join(
                [
                    self._normalized_token(proposed_changes.get("qualification_type")),
                    self._normalized_token(proposed_changes.get("qualification_name")),
                    self._normalized_token(proposed_changes.get("institution_name")),
                    self._normalized_token(proposed_changes.get("start_date")),
                    self._normalized_token(proposed_changes.get("end_date")),
                ]
            )
        if change_type == InfoChangeType.CERTIFICATION:
            return "|".join(
                [
                    self._normalized_token(proposed_changes.get("certification_name")),
                    self._normalized_token(proposed_changes.get("issuing_authority")),
                    self._normalized_token(proposed_changes.get("credential_id")),
                ]
            )
        if change_type == InfoChangeType.SKILL:
            return self._normalized_token(proposed_changes.get("skill_id"))
        if change_type == InfoChangeType.DEPENDENT:
            return "|".join(
                [
                    self._normalized_token(proposed_changes.get("full_name")),
                    self._normalized_token(proposed_changes.get("relationship")),
                    self._normalized_token(proposed_changes.get("date_of_birth")),
                ]
            )
        if change_type == InfoChangeType.DOCUMENT:
            return self._document_duplicate_key(
                proposed_changes,
                proposed_changes.get("pending_original_filename"),
                proposed_changes.get("pending_checksum"),
            )
        raise ValueError("Unsupported change type")

    @staticmethod
    def _normalized_token(value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().lower()

    def _assert_no_approved_duplicate(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        change_type: InfoChangeType,
        proposed_changes: dict[str, Any],
    ) -> None:
        if change_type == InfoChangeType.QUALIFICATION:
            qualifications = EmployeeQualificationService(
                self.db, organization_id
            ).list_qualifications(employee_id)
            candidate = self._dedupe_key_for_change(change_type, proposed_changes)
            if any(
                self._dedupe_key_for_change(
                    change_type, self._qualification_snapshot(item)
                )
                == candidate
                for item in qualifications
            ):
                raise ValueError("This qualification already exists")
            return
        if change_type == InfoChangeType.CERTIFICATION:
            certifications = EmployeeCertificationService(
                self.db, organization_id
            ).list_certifications(employee_id)
            candidate = self._dedupe_key_for_change(change_type, proposed_changes)
            if any(
                self._dedupe_key_for_change(
                    change_type, self._certification_snapshot(item)
                )
                == candidate
                for item in certifications
            ):
                raise ValueError("This certification already exists")
            return
        if change_type == InfoChangeType.SKILL:
            candidate = self._dedupe_key_for_change(change_type, proposed_changes)
            skills = EmployeeSkillService(
                self.db, organization_id
            ).list_employee_skills(employee_id)
            if any(
                self._dedupe_key_for_change(change_type, self._skill_snapshot(item))
                == candidate
                for item in skills
            ):
                raise ValueError("This skill is already assigned to the employee")
            return
        if change_type == InfoChangeType.DEPENDENT:
            dependants = EmployeeDependentService(
                self.db, organization_id
            ).list_dependents(employee_id)
            candidate = self._dedupe_key_for_change(change_type, proposed_changes)
            if any(
                self._dedupe_key_for_change(change_type, self._dependent_snapshot(item))
                == candidate
                for item in dependants
            ):
                raise ValueError("This dependant already exists")

    def _document_duplicate_key(
        self,
        proposed_changes: dict[str, Any],
        original_filename: str | None,
        checksum: str | None,
    ) -> str:
        return "|".join(
            [
                self._document_metadata_key(proposed_changes),
                self._normalized_token(original_filename),
                self._normalized_token(checksum),
            ]
        )

    def _document_metadata_key(
        self,
        proposed_changes: dict[str, Any],
    ) -> str:
        return "|".join(
            [
                self._normalized_token(proposed_changes.get("document_type")),
                self._normalized_token(proposed_changes.get("document_name")),
                self._normalized_token(proposed_changes.get("description")),
                self._normalized_token(proposed_changes.get("issue_date")),
                self._normalized_token(proposed_changes.get("expiry_date")),
            ]
        )

    def _assert_document_not_already_uploaded(
        self,
        organization_id: UUID,
        employee_id: UUID,
        proposed_changes: dict[str, Any],
        original_filename: str,
        checksum: str | None,
    ) -> None:
        documents = EmployeeDocumentService(self.db, organization_id).list_documents(
            employee_id
        )
        candidate_checksum = self._normalized_token(checksum)
        candidate_metadata = self._document_metadata_key(proposed_changes)
        candidate_filename = self._normalized_token(original_filename)
        for item in documents:
            metadata = {
                "document_type": item.document_type.value,
                "document_name": item.document_name,
                "description": item.description,
                "issue_date": item.issue_date.isoformat() if item.issue_date else None,
                "expiry_date": item.expiry_date.isoformat()
                if item.expiry_date
                else None,
            }
            same_metadata = self._document_metadata_key(metadata) == candidate_metadata
            same_filename = self._normalized_token(item.file_name) == candidate_filename
            same_checksum = (
                bool(candidate_checksum)
                and self._normalized_token(item.content_checksum) == candidate_checksum
            )
            if (same_checksum and same_metadata) or (same_metadata and same_filename):
                raise ValueError("A matching approved document already exists")

    # =========================================================================
    # Approve/Reject Requests
    # =========================================================================

    def approve_request(
        self,
        organization_id: UUID,
        request_id: UUID,
        reviewer_id: UUID,
        *,
        reviewer_notes: str | None = None,
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
        self.expire_requests(organization_id)
        request = self.get_request_by_id(organization_id, request_id)
        if not request:
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
        request.reviewed_at = datetime.now(UTC)

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
        reviewer_notes: str | None = None,
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
        self.expire_requests(organization_id)
        request = self.get_request_by_id(organization_id, request_id)
        if not request:
            raise ValueError(f"Request {request_id} not found")

        if not request.is_actionable:
            raise ValueError(
                f"Request {request_id} is not actionable (status={request.status.value})"
            )

        # Update request status
        request.status = InfoChangeStatus.REJECTED
        request.reviewer_id = reviewer_id
        request.reviewer_notes = reviewer_notes
        request.reviewed_at = datetime.now(UTC)
        self._cleanup_pending_evidence(request)

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

    def approve_batch(
        self,
        organization_id: UUID,
        batch_id: UUID,
        reviewer_id: UUID,
        *,
        reviewer_notes: str | None = None,
    ) -> EmployeeInfoChangeBatch:
        """Approve all actionable items in a batch atomically."""
        batch = self._get_batch_for_update(organization_id, batch_id)
        if not batch:
            raise ValueError(f"Batch {batch_id} not found")
        actionable = [item for item in batch.items if item.is_actionable]
        if not actionable:
            raise ValueError("Batch has no actionable items")
        for item in actionable:
            self._revalidate_actionable_batch_item(item)
        for item in actionable:
            self._apply_changes(item)
        reviewed_at = datetime.now(UTC)
        for item in actionable:
            item.status = InfoChangeStatus.APPROVED
            item.reviewer_id = reviewer_id
            item.reviewer_notes = reviewer_notes
            item.reviewed_at = reviewed_at
        self.db.flush()
        self._notify_batch_decision(batch, actionable, approved=True)
        return batch

    def reject_batch(
        self,
        organization_id: UUID,
        batch_id: UUID,
        reviewer_id: UUID,
        *,
        reviewer_notes: str | None = None,
    ) -> EmployeeInfoChangeBatch:
        """Reject all actionable items in a batch."""
        batch = self._get_batch_for_update(organization_id, batch_id)
        if not batch:
            raise ValueError(f"Batch {batch_id} not found")
        actionable = [item for item in batch.items if item.is_actionable]
        if not actionable:
            raise ValueError("Batch has no actionable items")
        reviewed_at = datetime.now(UTC)
        for item in actionable:
            item.status = InfoChangeStatus.REJECTED
            item.reviewer_id = reviewer_id
            item.reviewer_notes = reviewer_notes
            item.reviewed_at = reviewed_at
            self._cleanup_pending_evidence(item)
        self.db.flush()
        self._notify_batch_decision(batch, actionable, approved=False)
        return batch

    def _get_batch_for_update(
        self,
        organization_id: UUID,
        batch_id: UUID,
    ) -> EmployeeInfoChangeBatch | None:
        batch = self.db.scalar(
            select(EmployeeInfoChangeBatch)
            .options(
                joinedload(EmployeeInfoChangeBatch.employee),
                joinedload(EmployeeInfoChangeBatch.items).joinedload(
                    EmployeeInfoChangeRequest.employee
                ),
            )
            .where(
                EmployeeInfoChangeBatch.organization_id == organization_id,
                EmployeeInfoChangeBatch.batch_id == batch_id,
            )
            .with_for_update()
        )
        if batch is None:
            return None
        self.db.scalars(
            select(EmployeeInfoChangeRequest)
            .where(
                EmployeeInfoChangeRequest.organization_id == organization_id,
                EmployeeInfoChangeRequest.batch_id == batch_id,
            )
            .with_for_update()
        ).all()
        return batch

    def _revalidate_actionable_batch_item(
        self,
        request: EmployeeInfoChangeRequest,
    ) -> None:
        if request.change_type == InfoChangeType.DOCUMENT:
            self._assert_document_request_still_unique(request)
            return
        if request.change_type in self.EXTENDED_CHANGE_TYPES:
            self._assert_extended_request_still_valid(request)

    def _assert_extended_request_still_valid(
        self,
        request: EmployeeInfoChangeRequest,
    ) -> None:
        if request.operation == InfoChangeOperation.CREATE:
            self._assert_no_approved_duplicate(
                request.organization_id,
                request.employee_id,
                change_type=request.change_type,
                proposed_changes=request.proposed_changes,
            )
            return
        if request.target_record_id is None:
            raise ValueError("Update request is missing a target record")
        employee = self._get_employee_or_raise(
            request.organization_id,
            request.employee_id,
        )
        if request.change_type == InfoChangeType.QUALIFICATION:
            qualification = EmployeeQualificationService(
                self.db, request.organization_id
            ).get_qualification(request.target_record_id)
            if qualification.employee_id != employee.employee_id:
                raise ValueError("Qualification not found")
            self._assert_snapshot_matches(
                self._qualification_snapshot(qualification),
                request.previous_values,
                entity_name="Qualification",
            )
            return
        if request.change_type == InfoChangeType.CERTIFICATION:
            certification = EmployeeCertificationService(
                self.db, request.organization_id
            ).get_certification(request.target_record_id)
            if certification.employee_id != employee.employee_id:
                raise ValueError("Certification not found")
            self._assert_snapshot_matches(
                self._certification_snapshot(certification),
                request.previous_values,
                entity_name="Certification",
            )
            return
        if request.change_type == InfoChangeType.SKILL:
            skill = EmployeeSkillService(
                self.db, request.organization_id
            ).get_employee_skill(request.target_record_id)
            if skill.employee_id != employee.employee_id:
                raise ValueError("Skill not found")
            self._assert_snapshot_matches(
                self._skill_snapshot(skill),
                request.previous_values,
                entity_name="Skill",
            )
            return
        if request.change_type == InfoChangeType.DEPENDENT:
            dependant = EmployeeDependentService(
                self.db, request.organization_id
            ).get_dependent(request.target_record_id)
            if dependant.employee_id != employee.employee_id:
                raise ValueError("Dependant not found")
            self._assert_snapshot_matches(
                self._dependent_snapshot(dependant),
                request.previous_values,
                entity_name="Dependent",
            )

    def _assert_document_request_still_unique(
        self,
        request: EmployeeInfoChangeRequest,
    ) -> None:
        original_filename = request.pending_document_name or ""
        checksum = request.pending_document_checksum
        self._assert_document_not_already_uploaded(
            request.organization_id,
            request.employee_id,
            request.proposed_changes,
            original_filename,
            checksum,
        )

    def _apply_changes(self, request: EmployeeInfoChangeRequest) -> None:
        """Apply the proposed changes to employee/tax profile."""
        if request.change_type == InfoChangeType.DOCUMENT:
            self._apply_document_change(request)
            return
        if request.change_type in self.EXTENDED_CHANGE_TYPES:
            self._apply_extended_changes(request)
            return

        employee = self.db.scalar(
            select(Employee).where(
                Employee.employee_id == request.employee_id,
                Employee.organization_id == request.organization_id,
            )
        )
        if not employee:
            raise ValueError(f"Employee {request.employee_id} not found")

        changes = request.proposed_changes

        def _clean_text(value: object) -> str | None:
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
        person = employee.person or self.db.scalar(
            select(Person).where(
                Person.id == employee.person_id,
                Person.organization_id == request.organization_id,
            )
        )
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
                    except (ValueError, KeyError):
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
                    EmployeeTaxProfile.organization_id == request.organization_id,
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

    def _apply_extended_changes(self, request: EmployeeInfoChangeRequest) -> None:
        """Apply approved extended-profile changes via owning services."""
        employee = self._get_employee_or_raise(
            request.organization_id,
            request.employee_id,
        )
        if request.change_type == InfoChangeType.QUALIFICATION:
            self._apply_qualification_change(request, employee)
            return
        if request.change_type == InfoChangeType.CERTIFICATION:
            self._apply_certification_change(request, employee)
            return
        if request.change_type == InfoChangeType.SKILL:
            self._apply_skill_change(request, employee)
            return
        if request.change_type == InfoChangeType.DEPENDENT:
            self._apply_dependent_change(request, employee)
            return
        raise ValueError("Unsupported change type")

    @staticmethod
    def _clean_text(value: object | None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or text.lower() in {"none", "null"}:
            return None
        return text

    @staticmethod
    def _coerce_date(value: object | None, field_name: str) -> date | None:
        if value is None or value == "":
            return None
        if isinstance(value, date):
            return value
        try:
            return date.fromisoformat(str(value))
        except ValueError as exc:
            raise ValueError(f"{field_name} must use YYYY-MM-DD format") from exc

    @staticmethod
    def _coerce_decimal(value: object | None, field_name: str) -> Decimal | None:
        if value is None or value == "":
            return None
        try:
            return Decimal(str(value))
        except (InvalidOperation, TypeError) as exc:
            raise ValueError(f"{field_name} must be numeric") from exc

    @staticmethod
    def _require_length(value: str | None, field_name: str, max_length: int) -> None:
        if value and len(value) > max_length:
            raise ValueError(f"{field_name} must be {max_length} characters or fewer")

    @staticmethod
    def _validate_url(value: str | None, field_name: str) -> None:
        if not value:
            return
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{field_name} must be a valid http(s) URL")

    def _create_approved_document(
        self,
        request: EmployeeInfoChangeRequest,
        *,
        document_type: DocumentType,
        document_name: str,
        description: str | None = None,
        issue_date: date | None = None,
        expiry_date: date | None = None,
    ) -> UUID | None:
        if not request.pending_document_path or not request.pending_document_name:
            return None
        document_service = EmployeeDocumentService(self.db, request.organization_id)
        document = document_service._create_document_record(
            employee_id=request.employee_id,
            document_type=document_type,
            document_name=document_name,
            file_path=request.pending_document_path,
            file_name=request.pending_document_name,
            file_size=request.pending_document_size,
            mime_type=request.pending_document_mime_type,
            content_checksum=request.pending_document_checksum,
            description=description,
            issue_date=issue_date,
            expiry_date=expiry_date,
        )
        request.pending_document_path = None
        request.pending_document_name = None
        request.pending_document_size = None
        request.pending_document_mime_type = None
        request.pending_document_checksum = None
        return document.document_id

    def _validate_document_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        document_type_raw = self._clean_text(payload.get("document_type"))
        document_name = self._clean_text(payload.get("document_name"))
        description = self._clean_text(payload.get("description"))
        if not document_type_raw or not document_name:
            raise ValueError("Document type and document name are required")
        try:
            document_type = DocumentType(document_type_raw)
        except ValueError as exc:
            raise ValueError("Invalid document type") from exc
        if document_type not in self.SELF_SERVICE_DOCUMENT_TYPES:
            raise ValueError("That document type cannot be submitted in self-service")
        self._require_length(document_name, "document_name", 255)
        self._require_length(description, "description", 2000)
        issue_date = self._coerce_date(payload.get("issue_date"), "issue_date")
        expiry_date = self._coerce_date(payload.get("expiry_date"), "expiry_date")
        if issue_date and expiry_date and expiry_date < issue_date:
            raise ValueError("expiry_date cannot be earlier than issue_date")
        return {
            "document_type": document_type.value,
            "document_name": document_name,
            "description": description,
            "issue_date": issue_date.isoformat() if issue_date else None,
            "expiry_date": expiry_date.isoformat() if expiry_date else None,
        }

    def _find_duplicate_pending_document_request(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        proposed_changes: dict[str, Any],
        original_filename: str,
        checksum: str | None,
    ) -> EmployeeInfoChangeRequest | None:
        pending_requests = self.get_pending_requests(
            organization_id,
            employee_id=employee_id,
            change_type=InfoChangeType.DOCUMENT,
            limit=200,
        )
        for request in pending_requests:
            if request.operation != InfoChangeOperation.CREATE:
                continue
            if request.proposed_changes.get("document_type") != proposed_changes.get(
                "document_type"
            ):
                continue
            if request.proposed_changes.get("document_name") != proposed_changes.get(
                "document_name"
            ):
                continue
            same_filename = request.pending_document_name == original_filename
            same_checksum = checksum and request.pending_document_checksum == checksum
            if same_filename or same_checksum:
                return request
        return None

    def _apply_document_change(self, request: EmployeeInfoChangeRequest) -> None:
        if request.operation != InfoChangeOperation.CREATE:
            raise ValueError("Document requests only support create operations")
        validated = self._validate_document_payload(request.proposed_changes)
        document_id = self._create_approved_document(
            request,
            document_type=DocumentType(validated["document_type"]),
            document_name=validated["document_name"],
            description=validated.get("description"),
            issue_date=self._coerce_date(validated.get("issue_date"), "issue_date"),
            expiry_date=self._coerce_date(validated.get("expiry_date"), "expiry_date"),
        )
        if not document_id:
            raise ValueError("Pending document evidence is missing")

    def _qualification_snapshot(
        self,
        record: EmployeeQualification,
    ) -> dict[str, Any]:
        return {
            "qualification_type": record.qualification_type.value,
            "qualification_name": record.qualification_name,
            "field_of_study": record.field_of_study,
            "institution_name": record.institution_name,
            "institution_location": record.institution_location,
            "start_date": record.start_date.isoformat() if record.start_date else None,
            "end_date": record.end_date.isoformat() if record.end_date else None,
            "is_ongoing": bool(record.is_ongoing),
            "grade": record.grade,
            "score": str(record.score) if record.score is not None else None,
            "max_score": str(record.max_score)
            if record.max_score is not None
            else None,
            "notes": record.notes,
            "document_id": str(record.document_id) if record.document_id else None,
        }

    def _validate_qualification_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        qualification_type_input = payload.get("qualification_type")
        qualification_type_raw = self._clean_text(qualification_type_input)
        qualification_name = self._clean_text(payload.get("qualification_name"))
        institution_name = self._clean_text(payload.get("institution_name"))
        if not qualification_type_raw or not qualification_name or not institution_name:
            raise ValueError(
                "Qualification type, qualification name, and institution name are required"
            )
        if isinstance(qualification_type_input, QualificationType):
            qualification_type = qualification_type_input
        else:
            try:
                qualification_type = QualificationType(qualification_type_raw)
            except ValueError as exc:
                raise ValueError("Invalid qualification type") from exc
        start_date = self._coerce_date(payload.get("start_date"), "start_date")
        end_date = self._coerce_date(payload.get("end_date"), "end_date")
        is_ongoing = bool(payload.get("is_ongoing"))
        if is_ongoing and end_date is not None:
            raise ValueError("Ongoing qualifications cannot include an end date")
        if start_date and end_date and end_date < start_date:
            raise ValueError("Qualification end date cannot be before start date")
        score = self._coerce_decimal(payload.get("score"), "score")
        max_score = self._coerce_decimal(payload.get("max_score"), "max_score")
        if score is not None and score < 0:
            raise ValueError("Score cannot be negative")
        if max_score is not None and max_score <= 0:
            raise ValueError("Maximum score must be greater than zero")
        if score is not None and max_score is not None and score > max_score:
            raise ValueError("Score cannot exceed maximum score")
        for field_name, value, max_length in (
            ("qualification_name", qualification_name, 200),
            ("field_of_study", self._clean_text(payload.get("field_of_study")), 200),
            ("institution_name", institution_name, 255),
            (
                "institution_location",
                self._clean_text(payload.get("institution_location")),
                200,
            ),
            ("grade", self._clean_text(payload.get("grade")), 50),
        ):
            self._require_length(value, field_name, max_length)
        return {
            "qualification_type": qualification_type,
            "qualification_name": qualification_name,
            "field_of_study": self._clean_text(payload.get("field_of_study")),
            "institution_name": institution_name,
            "institution_location": self._clean_text(
                payload.get("institution_location")
            ),
            "start_date": start_date,
            "end_date": end_date,
            "is_ongoing": is_ongoing,
            "grade": self._clean_text(payload.get("grade")),
            "score": float(score) if score is not None else None,
            "max_score": float(max_score) if max_score is not None else None,
            "notes": self._clean_text(payload.get("notes")),
        }

    def _assert_snapshot_matches(
        self,
        current: dict[str, Any],
        expected: dict[str, Any],
        *,
        entity_name: str,
    ) -> None:
        for key, expected_value in expected.items():
            if current.get(key) != expected_value:
                raise ValueError(
                    f"{entity_name} changed after submission; refresh before approval"
                )

    def _apply_qualification_change(
        self,
        request: EmployeeInfoChangeRequest,
        employee: Employee,
    ) -> None:
        service = EmployeeQualificationService(self.db, request.organization_id)
        validated = self._validate_qualification_payload(request.proposed_changes)
        if request.operation == InfoChangeOperation.CREATE:
            document_id = self._create_approved_document(
                request,
                document_type=DocumentType.EDUCATIONAL,
                document_name=validated["qualification_name"],
            )
            if document_id:
                validated["document_id"] = document_id
            service.create_qualification(employee.employee_id, **validated)
            return
        if request.target_record_id is None:
            raise ValueError("Qualification update is missing a target record")
        qualification = service.get_qualification(request.target_record_id)
        if qualification.employee_id != employee.employee_id:
            raise ValueError("Qualification not found")
        self._assert_snapshot_matches(
            self._qualification_snapshot(qualification),
            request.previous_values,
            entity_name="Qualification",
        )
        document_id = self._create_approved_document(
            request,
            document_type=DocumentType.EDUCATIONAL,
            document_name=validated["qualification_name"],
        )
        if document_id:
            validated["document_id"] = document_id
        service.update_qualification(qualification.qualification_id, **validated)

    def _certification_snapshot(
        self,
        record: EmployeeCertification,
    ) -> dict[str, Any]:
        return {
            "certification_name": record.certification_name,
            "issuing_authority": record.issuing_authority,
            "issue_date": record.issue_date.isoformat() if record.issue_date else None,
            "expiry_date": record.expiry_date.isoformat()
            if record.expiry_date
            else None,
            "does_not_expire": bool(record.does_not_expire),
            "credential_id": record.credential_id,
            "credential_url": record.credential_url,
            "notes": record.notes,
            "document_id": str(record.document_id) if record.document_id else None,
        }

    def _validate_certification_payload(
        self, payload: dict[str, Any]
    ) -> dict[str, Any]:
        certification_name = self._clean_text(payload.get("certification_name"))
        issuing_authority = self._clean_text(payload.get("issuing_authority"))
        issue_date = self._coerce_date(payload.get("issue_date"), "issue_date")
        if not certification_name or not issuing_authority or not issue_date:
            raise ValueError(
                "Certification name, issuing authority, and issue date are required"
            )
        does_not_expire = bool(payload.get("does_not_expire"))
        expiry_date = self._coerce_date(payload.get("expiry_date"), "expiry_date")
        if does_not_expire:
            expiry_date = None
        elif expiry_date and expiry_date < issue_date:
            raise ValueError("Certification expiry date cannot be before issue date")
        credential_url = self._clean_text(payload.get("credential_url"))
        self._validate_url(credential_url, "credential_url")
        for field_name, value, max_length in (
            ("certification_name", certification_name, 255),
            ("issuing_authority", issuing_authority, 255),
            ("credential_id", self._clean_text(payload.get("credential_id")), 100),
        ):
            self._require_length(value, field_name, max_length)
        return {
            "certification_name": certification_name,
            "issuing_authority": issuing_authority,
            "issue_date": issue_date,
            "expiry_date": expiry_date,
            "does_not_expire": does_not_expire,
            "credential_id": self._clean_text(payload.get("credential_id")),
            "credential_url": credential_url,
            "notes": self._clean_text(payload.get("notes")),
        }

    def _apply_certification_change(
        self,
        request: EmployeeInfoChangeRequest,
        employee: Employee,
    ) -> None:
        service = EmployeeCertificationService(self.db, request.organization_id)
        validated = self._validate_certification_payload(request.proposed_changes)
        if request.operation == InfoChangeOperation.CREATE:
            document_id = self._create_approved_document(
                request,
                document_type=DocumentType.PROFESSIONAL,
                document_name=validated["certification_name"],
                issue_date=validated["issue_date"],
                expiry_date=validated["expiry_date"],
            )
            if document_id:
                validated["document_id"] = document_id
            service.create_certification(employee.employee_id, **validated)
            return
        if request.target_record_id is None:
            raise ValueError("Certification update is missing a target record")
        certification = service.get_certification(request.target_record_id)
        if certification.employee_id != employee.employee_id:
            raise ValueError("Certification not found")
        self._assert_snapshot_matches(
            self._certification_snapshot(certification),
            request.previous_values,
            entity_name="Certification",
        )
        document_id = self._create_approved_document(
            request,
            document_type=DocumentType.PROFESSIONAL,
            document_name=validated["certification_name"],
            issue_date=validated["issue_date"],
            expiry_date=validated["expiry_date"],
        )
        if document_id:
            validated["document_id"] = document_id
        service.update_certification(certification.certification_id, **validated)

    def _skill_snapshot(self, record: EmployeeSkill) -> dict[str, Any]:
        return {
            "skill_id": str(record.skill_id),
            "proficiency_level": record.proficiency_level,
            "years_experience": str(record.years_experience)
            if record.years_experience is not None
            else None,
            "last_used_date": record.last_used_date.isoformat()
            if record.last_used_date
            else None,
            "is_primary": bool(record.is_primary),
            "notes": record.notes,
        }

    def _validate_skill_payload(
        self,
        organization_id: UUID,
        employee_id: UUID,
        payload: dict[str, Any],
        *,
        target_employee_skill_id: UUID | None = None,
    ) -> dict[str, Any]:
        skill_id_raw = self._clean_text(payload.get("skill_id"))
        if not skill_id_raw:
            raise ValueError("Skill selection is required")
        skill_id = UUID(skill_id_raw)
        SkillService(self.db, organization_id).get_skill(skill_id)
        proficiency_level = int(payload.get("proficiency_level") or 0)
        if not 1 <= proficiency_level <= 5:
            raise ValueError("Proficiency level must be between 1 and 5")
        years_experience = self._coerce_decimal(
            payload.get("years_experience"),
            "years_experience",
        )
        if years_experience is not None and years_experience < 0:
            raise ValueError("Years of experience cannot be negative")
        last_used_date = self._coerce_date(
            payload.get("last_used_date"), "last_used_date"
        )
        duplicate_stmt = select(func.count(EmployeeSkill.employee_skill_id)).where(
            EmployeeSkill.organization_id == organization_id,
            EmployeeSkill.employee_id == employee_id,
            EmployeeSkill.skill_id == skill_id,
        )
        if target_employee_skill_id:
            duplicate_stmt = duplicate_stmt.where(
                EmployeeSkill.employee_skill_id != target_employee_skill_id
            )
        if (self.db.scalar(duplicate_stmt) or 0) > 0:
            raise ValueError("This skill is already assigned to the employee")
        return {
            "skill_id": skill_id,
            "proficiency_level": proficiency_level,
            "years_experience": float(years_experience)
            if years_experience is not None
            else None,
            "last_used_date": last_used_date,
            "is_primary": bool(payload.get("is_primary")),
            "notes": self._clean_text(payload.get("notes")),
            "is_self_assessed": True,
            "assessed_by_id": None,
        }

    def _apply_skill_change(
        self,
        request: EmployeeInfoChangeRequest,
        employee: Employee,
    ) -> None:
        service = EmployeeSkillService(self.db, request.organization_id)
        validated = self._validate_skill_payload(
            request.organization_id,
            employee.employee_id,
            request.proposed_changes,
            target_employee_skill_id=request.target_record_id,
        )
        if request.operation == InfoChangeOperation.CREATE:
            service.add_skill(employee.employee_id, **validated)
            return
        if request.target_record_id is None:
            raise ValueError("Skill update is missing a target record")
        employee_skill = service.get_employee_skill(request.target_record_id)
        if employee_skill.employee_id != employee.employee_id:
            raise ValueError("Employee skill not found")
        self._assert_snapshot_matches(
            self._skill_snapshot(employee_skill),
            request.previous_values,
            entity_name="Skill",
        )
        service.update_employee_skill(employee_skill.employee_skill_id, **validated)
        employee_skill.is_self_assessed = True
        employee_skill.assessed_by_id = None
        employee_skill.assessed_at = None

    def _dependent_snapshot(self, record: EmployeeDependent) -> dict[str, Any]:
        return {
            "full_name": record.full_name,
            "relationship": record.relation_type.value,
            "date_of_birth": record.date_of_birth.isoformat()
            if record.date_of_birth
            else None,
            "gender": record.gender.value if record.gender else None,
            "phone": record.phone,
            "email": record.email,
            "address": record.address,
            "is_emergency_contact": bool(record.is_emergency_contact),
            "emergency_contact_priority": record.emergency_contact_priority,
            "is_beneficiary": bool(record.is_beneficiary),
            "beneficiary_percentage": str(record.beneficiary_percentage)
            if record.beneficiary_percentage is not None
            else None,
            "notes": record.notes,
        }

    def _validate_dependent_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        full_name = self._clean_text(payload.get("full_name"))
        relationship_raw = self._clean_text(payload.get("relationship"))
        if not full_name or not relationship_raw:
            raise ValueError("Full name and relationship are required")
        self._require_length(full_name, "full_name", 200)
        try:
            relationship = RelationshipType(relationship_raw)
        except ValueError as exc:
            raise ValueError("Invalid relationship") from exc
        gender_raw = self._clean_text(payload.get("gender"))
        gender = DependentGender(gender_raw) if gender_raw else None
        emergency_priority_raw = self._clean_text(
            payload.get("emergency_contact_priority")
        )
        emergency_priority = (
            int(emergency_priority_raw) if emergency_priority_raw is not None else None
        )
        if emergency_priority is not None and emergency_priority < 1:
            raise ValueError("Emergency contact priority must be at least 1")
        beneficiary_percentage = self._coerce_decimal(
            payload.get("beneficiary_percentage"),
            "beneficiary_percentage",
        )
        if beneficiary_percentage is not None and not (
            Decimal("0") <= beneficiary_percentage <= Decimal("100")
        ):
            raise ValueError("Beneficiary percentage must be between 0 and 100")
        email_value = self._clean_text(payload.get("email"))
        if email_value and "@" not in email_value:
            raise ValueError("Email must be valid")
        return {
            "full_name": full_name,
            "relationship": relationship,
            "date_of_birth": self._coerce_date(
                payload.get("date_of_birth"), "date_of_birth"
            ),
            "gender": gender.value if gender else None,
            "phone": self._clean_text(payload.get("phone")),
            "email": email_value,
            "address": self._clean_text(payload.get("address")),
            "is_emergency_contact": bool(payload.get("is_emergency_contact")),
            "emergency_contact_priority": emergency_priority,
            "is_beneficiary": bool(payload.get("is_beneficiary")),
            "beneficiary_percentage": float(beneficiary_percentage)
            if beneficiary_percentage is not None
            else None,
            "notes": self._clean_text(payload.get("notes")),
        }

    def _apply_dependent_change(
        self,
        request: EmployeeInfoChangeRequest,
        employee: Employee,
    ) -> None:
        service = EmployeeDependentService(self.db, request.organization_id)
        validated = self._validate_dependent_payload(request.proposed_changes)
        if request.operation == InfoChangeOperation.CREATE:
            service.create_dependent(employee.employee_id, **validated)
            return
        if request.target_record_id is None:
            raise ValueError("Dependent update is missing a target record")
        dependent = service.get_dependent(request.target_record_id)
        if dependent.employee_id != employee.employee_id:
            raise ValueError("Dependent not found")
        self._assert_snapshot_matches(
            self._dependent_snapshot(dependent),
            request.previous_values,
            entity_name="Dependent",
        )
        service.update_dependent(dependent.dependent_id, **validated)

    def resolve_pending_evidence_download(
        self,
        organization_id: UUID,
        request_id: UUID,
        *,
        employee_id: UUID | None = None,
    ) -> tuple[
        AsyncIterable[str | bytes] | Iterable[str | bytes],
        str,
        int | None,
        str,
    ]:
        """Resolve pending request evidence for secure download."""
        request = self.get_request_by_id(organization_id, request_id)
        if not request or not request.pending_document_path:
            raise ValueError("Evidence not found")
        if employee_id is not None and request.employee_id != employee_id:
            raise ValueError("Evidence not found")
        storage = get_storage()
        s3_key = request.pending_document_path
        if not s3_key.startswith("employee_documents/"):
            s3_key = f"employee_documents/{s3_key}"
        if not storage.exists(s3_key):
            raise ValueError("Evidence not found")
        chunks, content_type, content_length = storage.stream(s3_key)
        filename = Path(request.pending_document_name or "evidence").name or "evidence"
        return (
            chunks,
            content_type or "application/octet-stream",
            content_length,
            filename,
        )

    # =========================================================================
    # Query Methods
    # =========================================================================

    def list_requests(
        self,
        organization_id: UUID,
        *,
        status: InfoChangeStatus | None = None,
        change_type: InfoChangeType | None = None,
        employee_id: UUID | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[EmployeeInfoChangeRequest]:
        """List info change requests with optional filters and eager-loaded employee."""
        self.expire_requests(
            organization_id, employee_id=employee_id, change_type=change_type
        )
        stmt = (
            select(EmployeeInfoChangeRequest)
            .options(
                joinedload(EmployeeInfoChangeRequest.employee),
                joinedload(EmployeeInfoChangeRequest.batch),
            )
            .where(EmployeeInfoChangeRequest.organization_id == organization_id)
            .order_by(EmployeeInfoChangeRequest.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        if employee_id:
            stmt = stmt.where(EmployeeInfoChangeRequest.employee_id == employee_id)
        if status:
            stmt = stmt.where(EmployeeInfoChangeRequest.status == status)
        if change_type:
            stmt = stmt.where(EmployeeInfoChangeRequest.change_type == change_type)
        return list(self.db.scalars(stmt).all())

    def get_request_detail(
        self,
        organization_id: UUID,
        request_id: UUID,
    ) -> EmployeeInfoChangeRequest | None:
        """Get a single info change request with eager-loaded employee."""
        self.expire_requests(organization_id)
        return self.db.scalar(
            select(EmployeeInfoChangeRequest)
            .options(
                joinedload(EmployeeInfoChangeRequest.employee),
                joinedload(EmployeeInfoChangeRequest.batch),
            )
            .where(
                EmployeeInfoChangeRequest.request_id == request_id,
                EmployeeInfoChangeRequest.organization_id == organization_id,
            )
        )

    def get_pending_requests(
        self,
        organization_id: UUID,
        *,
        employee_id: UUID | None = None,
        change_type: InfoChangeType | None = None,
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
        self.expire_requests(
            organization_id, employee_id=employee_id, change_type=change_type
        )
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
        if change_type:
            stmt = stmt.where(EmployeeInfoChangeRequest.change_type == change_type)

        return list(self.db.scalars(stmt).all())

    def get_request_by_id(
        self, organization_id: UUID, request_id: UUID
    ) -> EmployeeInfoChangeRequest | None:
        """Get a specific request by ID within organization scope."""
        self.expire_requests(organization_id)
        return self.db.scalar(
            select(EmployeeInfoChangeRequest)
            .options(
                joinedload(EmployeeInfoChangeRequest.employee),
                joinedload(EmployeeInfoChangeRequest.batch),
            )
            .where(
                EmployeeInfoChangeRequest.request_id == request_id,
                EmployeeInfoChangeRequest.organization_id == organization_id,
            )
        )

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
        self.expire_requests(organization_id, employee_id=employee_id)
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

    def get_batch_detail(
        self,
        organization_id: UUID,
        batch_id: UUID,
    ) -> EmployeeInfoChangeBatch | None:
        """Get a single batch with ordered child requests."""
        self.expire_requests(organization_id)
        return self.db.scalar(
            select(EmployeeInfoChangeBatch)
            .options(
                joinedload(EmployeeInfoChangeBatch.employee),
                joinedload(EmployeeInfoChangeBatch.items).joinedload(
                    EmployeeInfoChangeRequest.employee
                ),
            )
            .where(
                EmployeeInfoChangeBatch.organization_id == organization_id,
                EmployeeInfoChangeBatch.batch_id == batch_id,
            )
        )

    def has_pending_request(
        self,
        organization_id: UUID,
        employee_id: UUID,
        *,
        change_type: InfoChangeType | None = None,
    ) -> bool:
        """Check if employee has a pending change request in the requested scope."""
        self.expire_requests(
            organization_id, employee_id=employee_id, change_type=change_type
        )
        count = self.db.scalar(
            select(func.count(EmployeeInfoChangeRequest.request_id)).where(
                EmployeeInfoChangeRequest.organization_id == organization_id,
                EmployeeInfoChangeRequest.employee_id == employee_id,
                EmployeeInfoChangeRequest.status == InfoChangeStatus.PENDING,
            )
        )
        if change_type is not None:
            count = self.db.scalar(
                select(func.count(EmployeeInfoChangeRequest.request_id)).where(
                    EmployeeInfoChangeRequest.organization_id == organization_id,
                    EmployeeInfoChangeRequest.employee_id == employee_id,
                    EmployeeInfoChangeRequest.status == InfoChangeStatus.PENDING,
                    EmployeeInfoChangeRequest.change_type == change_type,
                )
            )
        else:
            count = self.db.scalar(
                select(func.count(EmployeeInfoChangeRequest.request_id)).where(
                    EmployeeInfoChangeRequest.organization_id == organization_id,
                    EmployeeInfoChangeRequest.employee_id == employee_id,
                    EmployeeInfoChangeRequest.status == InfoChangeStatus.PENDING,
                    EmployeeInfoChangeRequest.change_type.in_(
                        self.MY_INFO_CHANGE_TYPES
                    ),
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
        to_email: str | None,
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

        # Notify the employee's position manager if they have one.
        resolver = OrgResolver(self.db)
        manager = resolver.get_manager(
            employee.employee_id,
            request.organization_id,
        )
        resolver.notify_hr_for_vacancy_routing_alerts(request.organization_id)
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
            safe_name = html.escape(employee_name)
            body_html = (
                f"<p>{safe_name} submitted a request to update their {change_label}.</p>"
                f'<p><a href="{action_url}">Review request</a></p>'
            )
            self._send_email_safe(
                admin.email,
                subject,
                body_html,
                body_text,
                request.organization_id,
            )

    def _notify_pending_batch(
        self,
        batch: EmployeeInfoChangeBatch,
        employee: Employee,
    ) -> None:
        """Send one notification for a newly submitted batch."""
        first_item = batch.items[0] if batch.items else None
        if first_item is None:
            return
        action_path = f"/people/hr/info-changes/batches/{batch.batch_id}"
        action_url = self._build_app_url(action_path)
        change_label = batch.change_type.value.lower().replace("_", " ")
        employee_name = employee.full_name or employee.employee_code
        item_count = len(batch.items)

        resolver = OrgResolver(self.db)
        manager = resolver.get_manager(employee.employee_id, batch.organization_id)
        resolver.notify_hr_for_vacancy_routing_alerts(batch.organization_id)
        recipients: list[Person] = []
        if manager and manager.person_id:
            manager_person = self.db.get(Person, manager.person_id)
            if manager_person is not None:
                recipients.append(manager_person)
        recipients.extend(self._get_admin_recipients(batch.organization_id))
        seen_recipient_ids: set[UUID] = set()
        for recipient in recipients:
            if recipient.id in seen_recipient_ids:
                continue
            seen_recipient_ids.add(recipient.id)
            try:
                self.notification_service.create(
                    self.db,
                    organization_id=batch.organization_id,
                    recipient_id=recipient.id,
                    entity_type=EntityType.EMPLOYEE,
                    entity_id=batch.batch_id,
                    notification_type=NotificationType.SUBMITTED,
                    title="Employee Info Update Batch",
                    message=(
                        f"{employee_name} submitted {item_count} {change_label} item(s) "
                        "for review."
                    ),
                    channel=NotificationChannel.IN_APP,
                    action_url=action_path,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to notify batch recipient %s: %s", recipient.id, exc
                )
            subject = "Employee info change batch submitted"
            body_text = (
                f"{employee_name} submitted {item_count} {change_label} item(s) for review.\n"
                f"Review batch: {action_url}"
            )
            body_html = (
                f"<p>{html.escape(employee_name)} submitted {item_count} "
                f"{html.escape(change_label)} item(s) for review.</p>"
                f'<p><a href="{action_url}">Review batch</a></p>'
            )
            self._send_email_safe(
                recipient.email,
                subject,
                body_html,
                body_text,
                batch.organization_id,
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
        action_path = self._decision_action_path(request)
        action_url = self._build_app_url(action_path)
        change_label = request.change_type.value.lower().replace("_", " ")
        employee_email = employee.work_email or employee.personal_email
        if not employee_can_receive_email(employee):
            logger.info(
                "Skipping info change decision email for inactive employee %s",
                employee.employee_id,
            )
            return

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
        safe_notes = (
            html.escape(request.reviewer_notes) if request.reviewer_notes else ""
        )
        body_html = (
            f"<p>Your request to update your {change_label} was {status}.</p>"
            f"{f'<p>Reason: {safe_notes}</p>' if safe_notes else ''}"
            f'<p><a href="{action_url}">View details</a></p>'
        )
        self._send_email_safe(
            employee_email,
            subject,
            body_html,
            body_text,
            request.organization_id,
        )

    def _notify_batch_decision(
        self,
        batch: EmployeeInfoChangeBatch,
        items: list[EmployeeInfoChangeRequest],
        *,
        approved: bool,
    ) -> None:
        employee = self.db.get(Employee, batch.employee_id)
        if not employee or not employee.person_id:
            return
        if not employee_can_receive_email(employee):
            return
        status = "approved" if approved else "rejected"
        count = len(items) or len(batch.items)
        action_path = self._decision_action_path(items[0] if items else batch.items[0])
        action_url = self._build_app_url(action_path)
        change_label = batch.change_type.value.lower().replace("_", " ")
        notes = next(
            (item.reviewer_notes for item in items if item.reviewer_notes),
            None,
        )
        try:
            self.notification_service.create(
                self.db,
                organization_id=batch.organization_id,
                recipient_id=employee.person_id,
                entity_type=EntityType.EMPLOYEE,
                entity_id=batch.batch_id,
                notification_type=(
                    NotificationType.APPROVED if approved else NotificationType.REJECTED
                ),
                title=f"Info Update Batch {status.title()}",
                message=(
                    f"Your {count} {change_label} item(s) were {status}."
                    + (f" Reason: {notes}" if notes else "")
                ),
                channel=NotificationChannel.IN_APP,
                action_url=action_path,
            )
        except Exception as exc:
            logger.warning("Failed to notify employee of batch decision: %s", exc)

        subject = f"Your info change batch was {status}"
        body_text = (
            f"Your {count} {change_label} item(s) were {status}."
            + (f"\nReason: {notes}" if notes else "")
            + f"\nView details: {action_url}"
        )
        body_html = (
            f"<p>Your {count} {html.escape(change_label)} item(s) were {status}.</p>"
            + (f"<p>Reason: {html.escape(notes)}</p>" if notes else "")
            + f'<p><a href="{action_url}">View details</a></p>'
        )
        self._send_email_safe(
            employee.work_email or employee.personal_email,
            subject,
            body_html,
            body_text,
            batch.organization_id,
        )

    def _decision_action_path(self, request: EmployeeInfoChangeRequest) -> str:
        mapping = {
            InfoChangeType.BANK_DETAILS: "/people/self/tax-info",
            InfoChangeType.TAX_INFO: "/people/self/tax-info",
            InfoChangeType.PENSION_INFO: "/people/self/tax-info",
            InfoChangeType.NHF_INFO: "/people/self/tax-info",
            InfoChangeType.COMBINED: "/people/self/tax-info",
            InfoChangeType.QUALIFICATION: "/people/self/qualifications",
            InfoChangeType.CERTIFICATION: "/people/self/certifications",
            InfoChangeType.SKILL: "/people/self/skills",
            InfoChangeType.DEPENDENT: "/people/self/dependents",
            InfoChangeType.DOCUMENT: "/people/self/documents",
        }
        return mapping[request.change_type]
