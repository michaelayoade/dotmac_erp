"""
Project Management Pydantic Schemas.

This module provides schemas for:
- Tasks and task dependencies
- Milestones
- Resource allocation
- Time tracking
- Gantt charts
- Dashboard and reporting
"""
from app.schemas.pm.dashboard import (
    BudgetComparison,
    ExpenseByCategory,
    ExpenseSummary,
    MilestoneSummary,
    OverdueMilestone,
    ProjectDashboard,
    ProjectSummary,
    ResourceUtilization,
    TaskMetrics,
    TasksByPriorityCount,
    TasksByStatusCount,
    TeamMemberUtilization,
    UpcomingMilestone,
)
from app.schemas.pm.gantt import (
    GanttChartData,
    GanttLink,
    GanttTask,
    dependency_type_to_gantt_type,
)
from app.schemas.pm.milestone import (
    MilestoneAchieveRequest,
    MilestoneAchieveResponse,
    MilestoneCreate,
    MilestoneListResponse,
    MilestoneRead,
    MilestoneUpdate,
    MilestoneWithDetails,
)
from app.schemas.pm.resource_allocation import (
    EndAllocationRequest,
    ProjectAllocationSummary,
    ProjectTeamResponse,
    ResourceAllocationCreate,
    ResourceAllocationListResponse,
    ResourceAllocationRead,
    ResourceAllocationUpdate,
    ResourceAllocationWithDetails,
    TeamMemberSummary,
    UtilizationSummary,
)
from app.schemas.pm.task import (
    TaskAssignRequest,
    TaskBrief,
    TaskCompleteResponse,
    TaskCreate,
    TaskListResponse,
    TaskProgressRequest,
    TaskRead,
    TaskStartResponse,
    TaskUpdate,
    TaskWithDetails,
)
from app.schemas.pm.task_dependency import (
    TaskDependencyCreate,
    TaskDependencyListResponse,
    TaskDependencyRead,
    TaskDependencyWithDetails,
)
from app.schemas.pm.time_entry import (
    EmployeeTimeSummary,
    ProjectTimeSummary,
    TimeEntryCreate,
    TimeEntryListResponse,
    TimeEntryRead,
    TimeEntryUpdate,
    TimeEntryWithDetails,
    TimesheetDay,
    TimesheetWeek,
)

__all__ = [
    # Task
    "TaskCreate",
    "TaskUpdate",
    "TaskRead",
    "TaskBrief",
    "TaskWithDetails",
    "TaskListResponse",
    "TaskAssignRequest",
    "TaskProgressRequest",
    "TaskStartResponse",
    "TaskCompleteResponse",
    # Task Dependency
    "TaskDependencyCreate",
    "TaskDependencyRead",
    "TaskDependencyWithDetails",
    "TaskDependencyListResponse",
    # Milestone
    "MilestoneCreate",
    "MilestoneUpdate",
    "MilestoneRead",
    "MilestoneWithDetails",
    "MilestoneListResponse",
    "MilestoneAchieveRequest",
    "MilestoneAchieveResponse",
    # Resource Allocation
    "ResourceAllocationCreate",
    "ResourceAllocationUpdate",
    "ResourceAllocationRead",
    "ResourceAllocationWithDetails",
    "ResourceAllocationListResponse",
    "TeamMemberSummary",
    "ProjectTeamResponse",
    "UtilizationSummary",
    "ProjectAllocationSummary",
    "EndAllocationRequest",
    # Time Entry
    "TimeEntryCreate",
    "TimeEntryUpdate",
    "TimeEntryRead",
    "TimeEntryWithDetails",
    "TimeEntryListResponse",
    "TimesheetDay",
    "TimesheetWeek",
    "ProjectTimeSummary",
    "EmployeeTimeSummary",
    # Gantt
    "GanttTask",
    "GanttLink",
    "GanttChartData",
    "dependency_type_to_gantt_type",
    # Dashboard
    "ProjectSummary",
    "TaskMetrics",
    "TasksByStatusCount",
    "TasksByPriorityCount",
    "BudgetComparison",
    "ResourceUtilization",
    "TeamMemberUtilization",
    "ExpenseSummary",
    "ExpenseByCategory",
    "MilestoneSummary",
    "UpcomingMilestone",
    "OverdueMilestone",
    "ProjectDashboard",
]
