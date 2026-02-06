"""
Project Mapping from ERPNext to DotMac ERP.

Maps ERPNext Project DocType to DotMac core_org.project.
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

# ERPNext Project status to DotMac ProjectStatus
PROJECT_STATUS_MAP = {
    "Open": "ACTIVE",
    "Pending Review": "ON_HOLD",
    "Working": "ACTIVE",
    "Completed": "COMPLETED",
    "Cancelled": "CANCELLED",
    "Overdue": "ACTIVE",  # Still active, just past deadline
}


def map_project_status(value: Any) -> str:
    """Map ERPNext project status."""
    if not value:
        return "ACTIVE"
    return PROJECT_STATUS_MAP.get(str(value), "ACTIVE")


class ProjectMapping(DocTypeMapping):
    """Map ERPNext Project to DotMac ERP core_org.project."""

    def __init__(self):
        super().__init__(
            source_doctype="Project",
            target_table="core_org.project",
            fields=[
                FieldMapping(
                    source="name",
                    target="_source_name",
                    required=True,
                ),
                FieldMapping(
                    source="project_name",
                    target="project_name",
                    required=True,
                    transformer=lambda v: clean_string(v, 200),
                ),
                # Status
                FieldMapping(
                    source="status",
                    target="status",
                    required=False,
                    default="ACTIVE",
                    transformer=map_project_status,
                ),
                # Timeline
                FieldMapping(
                    source="expected_start_date",
                    target="start_date",
                    required=False,
                    transformer=parse_date,
                ),
                FieldMapping(
                    source="expected_end_date",
                    target="end_date",
                    required=False,
                    transformer=parse_date,
                ),
                # Budget
                FieldMapping(
                    source="estimated_costing",
                    target="budget_amount",
                    required=False,
                    transformer=parse_decimal,
                ),
                # Company for org resolution
                FieldMapping(
                    source="company",
                    target="_company",
                    required=False,
                ),
                # Cost center reference
                FieldMapping(
                    source="cost_center",
                    target="_cost_center_source_name",
                    required=False,
                ),
                # Customer reference (for client projects)
                FieldMapping(
                    source="customer",
                    target="_customer_source_name",
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
        """Transform with project code generation."""
        result = super().transform_record(record)

        # Generate project code from ERPNext name
        erpnext_name = record.get("name", "")
        # ERPNext project names are often "PROJ-0001" format
        code = erpnext_name.upper().replace(" ", "-")
        result["project_code"] = clean_string(code, 20) or "PROJ"

        # Default budget currency
        result["budget_currency_code"] = "NGN"

        # Default capitalizable to False
        result["is_capitalizable"] = False

        return result
