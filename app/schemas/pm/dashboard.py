"""
Dashboard Pydantic Schemas.

Schemas for PM Dashboard and reporting.
"""
from datetime import date
from decimal import Decimal
from typing import Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel

from app.models.pm import MilestoneStatus, TaskPriority, TaskStatus


# =============================================================================
# Project Summary Schemas
# =============================================================================


class ProjectSummary(BaseModel):
    """High-level project summary for dashboard."""

    project_id: UUID
    project_code: str
    project_name: str
    status: str
    priority: str
    percent_complete: Decimal = Decimal("0.00")
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    budget_amount: Optional[Decimal] = None
    estimated_cost: Optional[Decimal] = None
    actual_cost: Optional[Decimal] = None
    is_overdue: bool = False
    days_remaining: Optional[int] = None


# =============================================================================
# Task Metrics Schemas
# =============================================================================


class TaskMetrics(BaseModel):
    """Task statistics for a project."""

    project_id: UUID
    total_tasks: int = 0
    open_tasks: int = 0
    in_progress_tasks: int = 0
    completed_tasks: int = 0
    overdue_tasks: int = 0
    tasks_by_status: Dict[str, int] = {}
    tasks_by_priority: Dict[str, int] = {}
    completion_rate: Decimal = Decimal("0.00")  # percentage


class TasksByStatusCount(BaseModel):
    """Count of tasks grouped by status."""

    status: TaskStatus
    count: int


class TasksByPriorityCount(BaseModel):
    """Count of tasks grouped by priority."""

    priority: TaskPriority
    count: int


# =============================================================================
# Budget Comparison Schemas
# =============================================================================


class BudgetComparison(BaseModel):
    """Budget vs actual comparison."""

    project_id: UUID
    project_name: str
    budget_amount: Optional[Decimal] = None
    estimated_cost: Optional[Decimal] = None
    actual_cost: Optional[Decimal] = None
    remaining_budget: Optional[Decimal] = None
    budget_variance: Optional[Decimal] = None  # positive = under, negative = over
    budget_variance_percent: Optional[Decimal] = None
    cost_to_complete: Optional[Decimal] = None
    estimated_at_completion: Optional[Decimal] = None


# =============================================================================
# Resource Utilization Schemas
# =============================================================================


class ResourceUtilization(BaseModel):
    """Resource utilization for a project or organization."""

    total_team_members: int = 0
    active_allocations: int = 0
    total_allocated_percent: Decimal = Decimal("0.00")
    average_allocation: Decimal = Decimal("0.00")
    total_hours_logged: Decimal = Decimal("0.00")
    billable_hours: Decimal = Decimal("0.00")
    billable_percent: Decimal = Decimal("0.00")


class TeamMemberUtilization(BaseModel):
    """Individual team member utilization."""

    employee_id: UUID
    employee_name: str
    allocation_percent: Decimal = Decimal("0.00")
    hours_logged: Decimal = Decimal("0.00")
    expected_hours: Decimal = Decimal("0.00")
    utilization_percent: Decimal = Decimal("0.00")


# =============================================================================
# Expense Summary Schemas
# =============================================================================


class ExpenseSummary(BaseModel):
    """Expense summary for a project."""

    project_id: UUID
    project_name: str
    total_expenses: Decimal = Decimal("0.00")
    expense_count: int = 0
    approved_amount: Decimal = Decimal("0.00")
    pending_amount: Decimal = Decimal("0.00")
    expenses_by_category: Dict[str, Decimal] = {}


class ExpenseByCategory(BaseModel):
    """Expense breakdown by category."""

    category: str
    amount: Decimal
    count: int
    percentage: Decimal


# =============================================================================
# Milestone Summary Schemas
# =============================================================================


class MilestoneSummary(BaseModel):
    """Milestone summary for a project."""

    project_id: UUID
    total_milestones: int = 0
    achieved: int = 0
    pending: int = 0
    missed: int = 0
    upcoming: List["UpcomingMilestone"] = []
    overdue: List["OverdueMilestone"] = []


class UpcomingMilestone(BaseModel):
    """Upcoming milestone details."""

    milestone_id: UUID
    milestone_name: str
    target_date: date
    days_until: int


class OverdueMilestone(BaseModel):
    """Overdue milestone details."""

    milestone_id: UUID
    milestone_name: str
    target_date: date
    days_overdue: int
    status: MilestoneStatus


# =============================================================================
# Full Dashboard Schema
# =============================================================================


class ProjectDashboard(BaseModel):
    """Complete project dashboard data."""

    summary: ProjectSummary
    task_metrics: TaskMetrics
    budget: BudgetComparison
    resources: ResourceUtilization
    milestones: MilestoneSummary
    expenses: ExpenseSummary
