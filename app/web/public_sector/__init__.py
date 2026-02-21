"""
Public Sector Web Routes.

HTML template routes for IPSAS Fund Accounting — standalone module with
its own sidebar, base template, and route prefix.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.web.public_sector.appropriations import router as appropriations_router
from app.web.public_sector.commitments import router as commitments_router
from app.web.public_sector.dashboard import router as dashboard_router
from app.web.public_sector.funds import router as funds_router
from app.web.public_sector.reports import router as reports_router
from app.web.public_sector.virements import router as virements_router

router = APIRouter(prefix="/public-sector", tags=["public-sector-web"])

router.include_router(dashboard_router)
router.include_router(funds_router)
router.include_router(appropriations_router)
router.include_router(commitments_router)
router.include_router(virements_router)
router.include_router(reports_router)
