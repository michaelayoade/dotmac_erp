"""Type definitions for employee service.

These dataclasses define the contract for employee operations.
They are framework-agnostic (no Pydantic, no FastAPI).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any, Dict, List, Mapping, Optional, Tuple, Union

from app.models.people.hr import EmployeeStatus as EmploymentStatus
from app.models.person import Gender

if TYPE_CHECKING:
    from starlette.datastructures import FormData, UploadFile


# =============================================================================
# Form Parsing Utilities
# =============================================================================


class FormParser:
    """Utility for parsing form data with type coercion.

    Provides safe extraction of typed values from form data, handling
    empty strings, type conversion errors, and file upload fields.

    Example:
        parser = FormParser(form_data)
        name = parser.get_str("name")
        age = parser.int("age")
        salary = parser.decimal("salary")
    """

    def __init__(self, form: Mapping[str, Any]) -> None:
        self._form = form

    def get_str(self, key: str, default: str = "") -> str:
        """Extract string value, stripping whitespace."""
        value = self._form.get(key, default)
        # Handle UploadFile objects (return default)
        if hasattr(value, "filename"):  # UploadFile check without import
            return default
        if value is None:
            return default
        return str(value).strip()

    def str_or_none(self, key: str) -> Optional[str]:
        """Extract string value or None if empty."""
        value = self.get_str(key, "")
        return value if value else None

    def int(self, key: str) -> Optional[int]:
        """Extract integer value or None if invalid/empty."""
        value = self.get_str(key, "")
        if not value:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def decimal(self, key: str) -> Optional[Decimal]:
        """Extract Decimal value or None if invalid/empty."""
        value = self.get_str(key, "")
        if not value:
            return None
        try:
            return Decimal(value)
        except InvalidOperation:
            return None

    def date(self, key: str) -> Optional[date]:
        """Extract date from ISO format (YYYY-MM-DD) or None if invalid."""
        value = self.get_str(key, "")
        if not value:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    def enum(self, key: str, enum_class: type, default: Optional[Any] = None) -> Optional[Any]:
        """Extract enum value or default if invalid."""
        value = self.get_str(key, "")
        if not value:
            return default
        try:
            return enum_class(value)
        except ValueError:
            return default


@dataclass
class ValidationResult:
    """Result of data validation.

    Attributes:
        is_valid: Whether validation passed.
        errors: Dictionary of field -> error message.
    """

    is_valid: bool
    errors: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def success(cls) -> "ValidationResult":
        """Create a successful validation result."""
        return cls(is_valid=True)

    @classmethod
    def failure(cls, errors: Dict[str, str]) -> "ValidationResult":
        """Create a failed validation result."""
        return cls(is_valid=False, errors=errors)

__all__ = [
    "FormParser",
    "ValidationResult",
    "EmployeeFilters",
    "EmployeeCreateData",
    "EmployeeUpdateData",
    "EmployeeSummary",
    "OrgChartNode",
    "TerminationData",
    "BulkResult",
    "BulkUpdateData",
]


@dataclass
class EmployeeFilters:
    """Filters for listing employees."""

    status: Optional[Union[EmploymentStatus, str]] = None
    department_id: Optional[uuid.UUID] = None
    designation_id: Optional[uuid.UUID] = None
    reports_to_id: Optional[uuid.UUID] = None
    employment_type_id: Optional[uuid.UUID] = None
    search: Optional[str] = None  # Name, email, employee_code
    date_of_joining_from: Optional[date] = None
    date_of_joining_to: Optional[date] = None
    include_deleted: bool = False
    sort_key: Optional[str] = None
    sort_dir: Optional[str] = None


@dataclass
class EmployeeCreateData:
    """Data for creating an employee.

    Note: Personal info (name, email, phone, address) is stored in the Person model.
    The Employee model stores HR-specific data and links to Person via person_id.
    """

    # Employee code (auto-generated if not provided)
    employee_number: Optional[str] = None
    # Organization structure (UUIDs)
    department_id: Optional[uuid.UUID] = None
    designation_id: Optional[uuid.UUID] = None
    employment_type_id: Optional[uuid.UUID] = None
    grade_id: Optional[uuid.UUID] = None
    reports_to_id: Optional[uuid.UUID] = None
    cost_center_id: Optional[uuid.UUID] = None
    assigned_location_id: Optional[uuid.UUID] = None
    default_shift_type_id: Optional[uuid.UUID] = None
    # Employment dates
    date_of_joining: Optional[date] = None
    probation_end_date: Optional[date] = None
    confirmation_date: Optional[date] = None
    # Status
    status: Optional[EmploymentStatus] = None
    # Personal contact (separate from Person's work email/phone)
    personal_email: Optional[str] = None
    personal_phone: Optional[str] = None
    # Emergency contact
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    # Bank Details (for payroll)
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_sort_code: Optional[str] = None
    bank_account_name: Optional[str] = None
    # Notes
    notes: Optional[str] = None

@dataclass
class EmployeeUpdateData:
    """Data for updating an employee (all fields optional).

    Note: Personal info updates should be made to the Person record directly.
    """

    employee_number: Optional[str] = None
    department_id: Optional[uuid.UUID] = None
    designation_id: Optional[uuid.UUID] = None
    employment_type_id: Optional[uuid.UUID] = None
    grade_id: Optional[uuid.UUID] = None
    reports_to_id: Optional[uuid.UUID] = None
    cost_center_id: Optional[uuid.UUID] = None
    assigned_location_id: Optional[uuid.UUID] = None
    default_shift_type_id: Optional[uuid.UUID] = None
    date_of_joining: Optional[date] = None
    date_of_leaving: Optional[date] = None
    probation_end_date: Optional[date] = None
    confirmation_date: Optional[date] = None
    status: Optional[EmploymentStatus] = None
    # Personal contact (separate from Person's work email/phone)
    personal_email: Optional[str] = None
    personal_phone: Optional[str] = None
    # Emergency contact
    emergency_contact_name: Optional[str] = None
    emergency_contact_phone: Optional[str] = None
    # Bank Details
    bank_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_sort_code: Optional[str] = None
    bank_account_name: Optional[str] = None
    notes: Optional[str] = None
    # Tracks which fields were explicitly provided (for null handling)
    provided_fields: set[str] = field(default_factory=set, repr=False)


@dataclass
class EmployeeSummary:
    """Summary view of employee for search/autocomplete."""

    id: uuid.UUID
    name: str
    email: Optional[str]
    employee_number: Optional[str]
    department: Optional[str]
    designation: Optional[str]
    status: EmploymentStatus


@dataclass
class OrgChartNode:
    """Node in organization chart."""

    employee_id: uuid.UUID
    name: str
    designation: Optional[str]
    department: Optional[str]
    email: Optional[str]
    direct_reports: List["OrgChartNode"] = field(default_factory=list)


@dataclass
class TerminationData:
    """Data for employee termination."""

    date_of_leaving: date
    reason: Optional[str] = None
    exit_interview_notes: Optional[str] = None


@dataclass
class BulkUpdateData:
    """Data for bulk updating employees."""

    ids: List[uuid.UUID] = field(default_factory=list)
    department_id: Optional[uuid.UUID] = None
    designation_id: Optional[uuid.UUID] = None
    status: Optional[EmploymentStatus] = None
    reports_to_id: Optional[uuid.UUID] = None


@dataclass
class BulkResult:
    """Result of a bulk operation."""

    updated_count: int = 0
    deleted_count: int = 0
    failed_ids: List[uuid.UUID] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
