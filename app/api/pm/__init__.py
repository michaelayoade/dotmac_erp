"""
Project Management API.

REST API endpoints for:
- Tasks
- Milestones
- Resources
- Time Entries
- Dashboard
"""

from fastapi import APIRouter, Depends

from app.api.deps import require_tenant_permission

from app.api.pm.milestones import router as milestones_router
from app.api.pm.projects import router as projects_router
from app.api.pm.resources import router as resources_router
from app.api.pm.tasks import router as tasks_router
from app.api.pm.time_entries import router as time_entries_router

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    dependencies=[Depends(require_tenant_permission("projects:access"))],
)

router.include_router(projects_router)
router.include_router(tasks_router)
router.include_router(milestones_router)
router.include_router(resources_router)
router.include_router(time_entries_router)

__all__ = ["router"]
