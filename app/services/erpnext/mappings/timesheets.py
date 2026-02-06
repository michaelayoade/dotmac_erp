"""
ERPNext Timesheet mapping for project time tracking sync.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from .base import DocTypeMapping, FieldMapping, parse_decimal

logger = logging.getLogger(__name__)


def parse_date_from_datetime(value: Any) -> Optional[date]:
    """Extract date from datetime field."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        # Try to parse as datetime first
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d"]:
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None
    return None


def map_billing_status(is_billed: Any, is_billable: Any) -> str:
    """Map ERPNext billing fields to DotMac BillingStatus."""
    if not is_billable:
        return "NON_BILLABLE"
    if is_billed:
        return "BILLED"
    return "NOT_BILLED"


@dataclass
class TimesheetDetailMapping(DocTypeMapping):
    """
    Mapping from ERPNext Timesheet Detail (child table) to DotMac pm.time_entry.

    ERPNext Timesheet fields (parent):
    - name: timesheet identifier
    - employee: linked employee name
    - total_hours: sum of hours

    ERPNext Timesheet Detail fields (child):
    - name: detail row identifier
    - parent: parent timesheet name
    - project: linked project
    - task: linked task
    - from_time: start datetime
    - to_time: end datetime
    - hours: hours for this entry
    - activity_type: type of work
    - description: work description
    - is_billable: whether billable
    - billing_hours: billed hours
    """

    source_doctype: str = "Timesheet Detail"
    target_table: str = "pm.time_entry"
    unique_key: str = "name"
    fields: list[FieldMapping] = field(
        default_factory=lambda: [
            FieldMapping("parent", "erpnext_timesheet_id"),
            FieldMapping("name", "erpnext_timesheet_detail_id"),
            FieldMapping("project", "_project_source_name"),
            FieldMapping("task", "_task_source_name"),
            FieldMapping(
                "from_time", "entry_date", transformer=parse_date_from_datetime
            ),
            FieldMapping("hours", "hours", transformer=parse_decimal),
            FieldMapping("description", "description"),
            FieldMapping("is_billable", "is_billable", default=False),
        ]
    )

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext Timesheet Detail to DotMac time_entry data."""
        result = super().transform_record(record)

        # Determine billing status
        is_billed = record.get("is_billed", False)
        is_billable = record.get("is_billable", False)
        result["billing_status"] = map_billing_status(is_billed, is_billable)

        # Store parent employee reference
        result["_employee_source_name"] = record.get("_parent_employee")

        return result


@dataclass
class TimesheetParentMapping:
    """
    Helper mapping for parent Timesheet document.

    Used to extract employee and other parent-level data.
    """

    @staticmethod
    def extract_parent_data(record: dict[str, Any]) -> dict[str, Any]:
        """Extract parent-level data from Timesheet."""
        return {
            "employee": record.get("employee"),
            "employee_name": record.get("employee_name"),
            "company": record.get("company"),
            "docstatus": record.get("docstatus"),
        }
