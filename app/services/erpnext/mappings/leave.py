"""
Leave Entity Mappings from ERPNext to DotMac ERP.

Maps ERPNext Leave DocTypes to DotMac leave schema:
- Leave Type → leave.leave_type
- Leave Allocation → leave.leave_allocation
- Leave Application → leave.leave_application
"""

import logging
from typing import Any

from .base import (
    DocTypeMapping,
    FieldMapping,
    clean_string,
    parse_date,
    parse_datetime,
    parse_decimal,
)

logger = logging.getLogger(__name__)

# ERPNext Leave Application status to DotMac LeaveApplicationStatus
LEAVE_STATUS_MAP = {
    "Open": "SUBMITTED",
    "Approved": "APPROVED",
    "Rejected": "REJECTED",
    "Cancelled": "CANCELLED",
}


def map_leave_status(value: Any) -> str:
    """Map ERPNext leave application status."""
    if not value:
        return "DRAFT"
    return LEAVE_STATUS_MAP.get(str(value), "SUBMITTED")


def map_allocation_policy(record: dict) -> str:
    """Determine allocation policy from ERPNext leave type flags."""
    if record.get("is_earned_leave"):
        return "EARNED"
    if record.get("is_lwp"):
        return "UNLIMITED"  # LWP typically has no allocation limit
    return "ANNUAL"


class LeaveTypeMapping(DocTypeMapping):
    """Map ERPNext Leave Type to DotMac ERP leave.leave_type."""

    def __init__(self):
        super().__init__(
            source_doctype="Leave Type",
            target_table="leave.leave_type",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="leave_type_name",
                    target="leave_type_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 100),
                ),
                FieldMapping(
                    source="max_leaves_allowed",
                    target="max_days_per_year",
                    required=False,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="max_continuous_days_allowed",
                    target="max_continuous_days",
                    required=False,
                    transformer=lambda v: int(v) if v else None,
                ),
                FieldMapping(
                    source="is_carry_forward",
                    target="allow_carry_forward",
                    required=False,
                    default=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="is_earned_leave",
                    target="_is_earned",  # Used for policy determination
                    required=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="is_compensatory",
                    target="is_compensatory",
                    required=False,
                    default=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="is_lwp",
                    target="is_lwp",
                    required=False,
                    default=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="allow_negative",
                    target="_allow_negative",  # For encashment settings
                    required=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="include_holiday",
                    target="include_holidays",
                    required=False,
                    default=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform with leave type code and policy generation."""
        result = super().transform_record(record)

        # Generate leave type code from name
        name = record.get("leave_type_name") or record.get("name", "")
        # Common mappings: "Casual Leave" -> "CL", "Annual Leave" -> "AL"
        code = "".join(word[0].upper() for word in name.split() if word)[:10]
        if not code:
            code = clean_string(name.upper().replace(" ", "_"), 30) or "LEAVE"
        result["leave_type_code"] = code

        # Determine allocation policy
        result["allocation_policy"] = map_allocation_policy(record)

        # Set active status
        result["is_active"] = True

        # Clean up internal fields
        result.pop("_is_earned", None)
        result.pop("_allow_negative", None)

        return result


class LeaveAllocationMapping(DocTypeMapping):
    """Map ERPNext Leave Allocation to DotMac ERP leave.leave_allocation."""

    def __init__(self):
        super().__init__(
            source_doctype="Leave Allocation",
            target_table="leave.leave_allocation",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                # Employee reference (for later resolution)
                FieldMapping(
                    source="employee",
                    target="_employee_source_name",
                    required=True,
                ),
                # Leave type reference (for later resolution)
                FieldMapping(
                    source="leave_type",
                    target="_leave_type_source_name",
                    required=True,
                ),
                # Period
                FieldMapping(
                    source="from_date",
                    target="from_date",
                    required=True,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="to_date",
                    target="to_date",
                    required=True,
                    transformer=parse_date,
                ),
                # Allocation amounts
                FieldMapping(
                    source="new_leaves_allocated",
                    target="new_leaves_allocated",
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="carry_forwarded_leaves",
                    target="carry_forward_leaves",
                    required=False,
                    default=0,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="total_leaves_allocated",
                    target="total_leaves_allocated",
                    required=True,
                    transformer=parse_decimal,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform leave allocation record."""
        result = super().transform_record(record)
        result["is_active"] = True
        return result


class LeaveApplicationMapping(DocTypeMapping):
    """Map ERPNext Leave Application to DotMac ERP leave.leave_application."""

    def __init__(self):
        super().__init__(
            source_doctype="Leave Application",
            target_table="leave.leave_application",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                # Employee reference
                FieldMapping(
                    source="employee",
                    target="_employee_source_name",
                    required=True,
                ),
                # Leave type reference
                FieldMapping(
                    source="leave_type",
                    target="_leave_type_source_name",
                    required=True,
                ),
                # Period
                FieldMapping(
                    source="from_date",
                    target="from_date",
                    required=True,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="to_date",
                    target="to_date",
                    required=True,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="total_leave_days",
                    target="total_leave_days",
                    required=False,
                    transformer=parse_decimal,
                ),
                # Half day
                FieldMapping(
                    source="half_day",
                    target="half_day",
                    required=False,
                    default=False,
                    transformer=bool,
                ),
                FieldMapping(
                    source="half_day_date",
                    target="half_day_date",
                    required=False,
                    transformer=parse_date,
                ),
                # Status
                FieldMapping(
                    source="status",
                    target="status",
                    required=False,
                    transformer=map_leave_status,
                ),
                # Description/reason
                FieldMapping(
                    source="description",
                    target="reason",
                    required=False,
                    transformer=lambda v: clean_string(v, 500),
                ),
                # Approver reference
                FieldMapping(
                    source="leave_approver",
                    target="_approver_user",
                    required=False,
                ),
                FieldMapping(
                    source="modified",
                    target="_source_modified",
                    required=False,
                    transformer=parse_datetime,
                ),
            ],
            unique_key="name",
        )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform leave application record."""
        result = super().transform_record(record)

        # Generate application number from ERPNext name
        erpnext_name = record.get("name", "")
        # ERPNext: "HR-LAP-2025-00001" -> "LAP-2025-00001"
        if erpnext_name.startswith("HR-"):
            result["application_number"] = erpnext_name[3:]
        else:
            result["application_number"] = clean_string(erpnext_name, 30)

        return result
