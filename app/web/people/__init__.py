"""
People (HR/HRIS) Web Routes.

HTML template routes for the People/HR modules.

Route structure:
- /people             - Dashboard
- /people/hr/*        - Employee, department, designation pages
- /people/payroll/*   - Payroll processing pages
- /people/leave/*     - Leave management pages
- /people/attendance/* - Attendance pages
- /people/scheduling/* - Shift scheduling pages
- /people/recruit/*   - Recruitment pages
- /people/training/*  - Training pages
- /people/perf/*      - Performance pages
- /people/settings/*  - HR-specific settings pages
"""

from fastapi import APIRouter

from app.web.people.attendance import router as attendance_router
from app.web.people.dashboard import router as dashboard_router
from app.web.people.hr import router as hr_router
from app.web.people.leave import router as leave_router
from app.web.people.payroll import router as payroll_router
from app.web.people.perf import router as perf_router
from app.web.people.recruit import router as recruit_router
from app.web.people.scheduling import router as scheduling_router
from app.web.people.self_service import router as self_service_router
from app.web.people.settings import router as settings_router
from app.web.people.training import router as training_router

# Create main people web router
router = APIRouter(prefix="/people", tags=["people-web"])

# Dashboard (must be first to catch /people and /people/dashboard)
router.include_router(dashboard_router)

# HR Core routes
router.include_router(hr_router)

# Payroll routes
router.include_router(payroll_router)

# Leave routes
router.include_router(leave_router)

# Attendance routes
router.include_router(attendance_router)

# Scheduling routes
router.include_router(scheduling_router)

# Recruitment routes
router.include_router(recruit_router)

# Training routes
router.include_router(training_router)

# Performance routes
router.include_router(perf_router)

# Self-service routes
router.include_router(self_service_router)

# Settings routes
router.include_router(settings_router)
