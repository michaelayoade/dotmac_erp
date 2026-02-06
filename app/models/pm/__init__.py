"""
Project Management Module Models.

This module provides models for:
- Tasks and task dependencies
- Milestones
- Resource allocation
- Time tracking
"""

from app.models.pm.milestone import Milestone, MilestoneStatus
from app.models.pm.resource_allocation import ResourceAllocation
from app.models.pm.task import Task, TaskPriority, TaskStatus
from app.models.pm.task_dependency import DependencyType, TaskDependency
from app.models.pm.project_template import ProjectTemplate
from app.models.pm.project_template_task import (
    ProjectTemplateTask,
    ProjectTemplateTaskDependency,
)
from app.models.pm.time_entry import BillingStatus, TimeEntry
from app.models.pm.comment import PMComment, PMCommentAttachment, PMCommentType

__all__ = [
    # Task
    "Task",
    "TaskStatus",
    "TaskPriority",
    # Task Dependency
    "TaskDependency",
    "DependencyType",
    # Project Templates
    "ProjectTemplate",
    "ProjectTemplateTask",
    "ProjectTemplateTaskDependency",
    # Milestone
    "Milestone",
    "MilestoneStatus",
    # Resource Allocation
    "ResourceAllocation",
    # Time Entry
    "TimeEntry",
    "BillingStatus",
    # Comments
    "PMComment",
    "PMCommentAttachment",
    "PMCommentType",
]
