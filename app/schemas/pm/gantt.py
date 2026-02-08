"""
Gantt Chart Pydantic Schemas.

Schemas for Gantt chart data used in project visualization.
"""

from datetime import date
from uuid import UUID

from pydantic import BaseModel

from app.models.pm import DependencyType, TaskPriority, TaskStatus

# =============================================================================
# Gantt Chart Schemas
# =============================================================================


class GanttTask(BaseModel):
    """Task data for Gantt chart rendering."""

    id: str  # task_id as string for JS compatibility
    text: str  # task_name
    start_date: date | None = None
    end_date: date | None = None
    duration: int | None = None  # days
    progress: float = 0.0  # 0-1 scale
    parent: str | None = None  # parent_task_id as string
    type: str = "task"  # "task", "milestone", "project"
    priority: TaskPriority = TaskPriority.MEDIUM
    status: TaskStatus = TaskStatus.OPEN
    assigned_to: str | None = None
    project_id: str
    open: bool = True  # expanded in tree view


class GanttLink(BaseModel):
    """Dependency link data for Gantt chart."""

    id: str  # dependency_id as string
    source: str  # depends_on_task_id as string
    target: str  # task_id as string
    type: str  # "0" = FS, "1" = SS, "2" = FF, "3" = SF
    lag: int = 0  # lag_days


class GanttChartData(BaseModel):
    """Complete data for rendering a Gantt chart."""

    project_id: UUID
    project_name: str
    start_date: date | None = None
    end_date: date | None = None
    tasks: list[GanttTask]
    links: list[GanttLink]


# =============================================================================
# Helpers
# =============================================================================


def dependency_type_to_gantt_type(dep_type: DependencyType) -> str:
    """Convert DependencyType enum to Gantt chart link type number."""
    mapping = {
        DependencyType.FINISH_TO_START: "0",
        DependencyType.START_TO_START: "1",
        DependencyType.FINISH_TO_FINISH: "2",
        DependencyType.START_TO_FINISH: "3",
    }
    return mapping.get(dep_type, "0")
