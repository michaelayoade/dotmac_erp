"""
People (HR/HRIS) API Routes.

This module contains all REST API endpoints for the People/HR functionality.

Route structure:
- /people/hr/*        - Core HR (employees, departments, designations)
- /people/payroll/*   - Payroll processing
- /people/leave/*     - Leave management
- /people/attendance/* - Attendance tracking
- /people/scheduling/* - Shift scheduling
- /people/recruit/*   - Recruitment
- /people/training/*  - Training management
- /people/perf/*      - Performance management
"""

from fastapi import APIRouter

from app.api.people.hr import router as hr_router
from app.api.people.payroll import router as payroll_router
from app.api.people.leave import router as leave_router
from app.api.people.attendance import router as attendance_router
from app.api.people.scheduling import router as scheduling_router
from app.api.people.recruit import router as recruit_router
from app.api.people.training import router as training_router
from app.api.people.perf import router as perf_router
from app.api.people.expense import router as expense_router
from app.api.people.lifecycle import router as lifecycle_router
from app.api.people.assets import router as assets_router
from app.api.people.discipline import router as discipline_router

# Create main people router
router = APIRouter(prefix="/people", tags=["people"])

# HR Core routes
router.include_router(hr_router)

# Payroll routes
router.include_router(payroll_router)

# Leave management routes
router.include_router(leave_router)

# Attendance routes
router.include_router(attendance_router)

# Scheduling routes
router.include_router(scheduling_router)

# Recruitment routes
router.include_router(recruit_router)

# Training routes
router.include_router(training_router)

# Performance management routes
router.include_router(perf_router)

# Expense management routes
router.include_router(expense_router)

# Lifecycle routes
router.include_router(lifecycle_router)

# Assets routes
router.include_router(assets_router)

# Discipline routes
router.include_router(discipline_router)
