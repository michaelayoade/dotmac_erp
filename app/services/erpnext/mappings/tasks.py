"""
ERPNext Task mapping for project management sync.
"""
from dataclasses import dataclass, field
from typing import Any

from .base import DocTypeMapping, FieldMapping, parse_date, parse_decimal, parse_int


# ERPNext Task status to DotMac TaskStatus mapping
TASK_STATUS_MAP = {
    "Open": "OPEN",
    "Working": "IN_PROGRESS",
    "Pending Review": "PENDING_REVIEW",
    "Completed": "COMPLETED",
    "Cancelled": "CANCELLED",
    "Overdue": "IN_PROGRESS",  # Map to in-progress, we track overdue separately
    "Template": "OPEN",  # Treat templates as open tasks
}


def map_task_status(value: Any) -> str:
    """Map ERPNext task status to DotMac TaskStatus enum value."""
    if value is None:
        return "OPEN"
    return TASK_STATUS_MAP.get(str(value), "OPEN")


# ERPNext priority to DotMac TaskPriority mapping
TASK_PRIORITY_MAP = {
    "Low": "LOW",
    "Medium": "MEDIUM",
    "High": "HIGH",
    "Urgent": "URGENT",
}


def map_task_priority(value: Any) -> str:
    """Map ERPNext task priority to DotMac TaskPriority enum value."""
    if value is None:
        return "MEDIUM"
    return TASK_PRIORITY_MAP.get(str(value), "MEDIUM")


@dataclass
class TaskMapping(DocTypeMapping):
    """
    Mapping from ERPNext Task DocType to DotMac pm.task.

    ERPNext Task fields:
    - name: unique identifier
    - subject: task name
    - status: task status
    - priority: Low/Medium/High/Urgent
    - project: linked project name
    - parent_task: parent task name for hierarchy
    - exp_start_date: expected start date
    - exp_end_date: expected end date
    - expected_time: estimated hours
    - actual_time: actual hours spent
    - progress: percentage complete (0-100)
    - description: task description
    - completed_on: actual completion date
    """

    source_doctype: str = "Task"
    target_table: str = "pm.task"
    unique_key: str = "name"
    fields: list[FieldMapping] = field(default_factory=lambda: [
        FieldMapping("name", "_source_name"),
        FieldMapping("subject", "task_name", required=True),
        FieldMapping("status", "status", transformer=map_task_status),
        FieldMapping("priority", "priority", transformer=map_task_priority),
        FieldMapping("project", "_project_source_name"),
        FieldMapping("parent_task", "_parent_task_source_name"),
        FieldMapping("exp_start_date", "start_date", transformer=parse_date),
        FieldMapping("exp_end_date", "due_date", transformer=parse_date),
        FieldMapping("expected_time", "estimated_hours", transformer=parse_decimal),
        FieldMapping("actual_time", "actual_hours", default=0, transformer=parse_decimal),
        FieldMapping("progress", "progress_percent", default=0, transformer=parse_int),
        FieldMapping("description", "description"),
        FieldMapping("completed_on", "actual_end_date", transformer=parse_date),
    ])

    def transform_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """Transform ERPNext Task to DotMac task data."""
        result = super().transform_record(record)

        # Generate task_code from ERPNext name (e.g., TASK-00001)
        source_name = record.get("name", "")
        if source_name:
            # Use first 30 chars of the ERPNext name as task code
            result["task_code"] = source_name[:30]

        # Set actual_start_date if task is in progress
        if result.get("status") in ("IN_PROGRESS", "COMPLETED", "PENDING_REVIEW"):
            result["actual_start_date"] = result.get("start_date")

        return result
