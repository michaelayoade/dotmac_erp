"""
Operations Web Routes.

HTML template routes for the Operations modules (Inventory, Procurement, etc.).

Route structure:
- /operations             - Dashboard
- /operations/inv/*       - Inventory management pages
- /operations/projects/*  - Project management pages
- /operations/support/*   - Support/Helpdesk pages
- /operations/settings/*  - Operations settings pages
"""

from fastapi import APIRouter

from app.web.operations.dashboard import router as dashboard_router
from app.web.operations.inv import router as inv_router
from app.web.operations.projects import router as projects_router
from app.web.operations.settings import router as settings_router
from app.web.operations.support import router as support_router
from app.web.fleet import router as fleet_router

# Create main operations web router
router = APIRouter(prefix="/operations", tags=["operations-web"])

# Dashboard (must be first to catch /operations and /operations/dashboard)
router.include_router(dashboard_router)

# Inventory routes
router.include_router(inv_router)

# Project Management routes
router.include_router(projects_router)

# Support/Helpdesk routes
router.include_router(support_router)

# Fleet Management routes
router.include_router(fleet_router)

# Settings routes
router.include_router(settings_router)
