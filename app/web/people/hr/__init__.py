"""HR Web Routes - Modular Structure.

This package provides HTML template routes for HR functionality:
- employees: Employee CRUD and management
- lifecycle: Onboarding, offboarding, promotions, transfers
- organization: Departments, designations, employment types, grades
- locations: Branch/location management
- employee_extended: Documents, qualifications, certifications, dependents, skills
- skills: Skills catalog management
- competencies: Competency framework management
- job_descriptions: Job description management
- discipline: Disciplinary case management
"""

from fastapi import APIRouter

from .employees import router as employees_router
from .lifecycle import router as lifecycle_router
from .organization import router as organization_router
from .locations import router as locations_router
from .employee_extended import router as employee_extended_router
from .skills import router as skills_router
from .competencies import router as competencies_router
from .job_descriptions import router as job_descriptions_router
from .discipline import router as discipline_router
from .onboarding_admin import router as onboarding_admin_router
from .handbook import router as handbook_router

# Main HR router that includes all sub-routers
router = APIRouter(prefix="/hr", tags=["hr-web"])

# Include all sub-routers
router.include_router(employees_router)
router.include_router(lifecycle_router)
router.include_router(organization_router)
router.include_router(locations_router)
router.include_router(employee_extended_router)
router.include_router(skills_router)
router.include_router(competencies_router)
router.include_router(job_descriptions_router)
router.include_router(discipline_router)
router.include_router(onboarding_admin_router)
router.include_router(handbook_router)
