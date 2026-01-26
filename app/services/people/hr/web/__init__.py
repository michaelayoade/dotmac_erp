"""HR Web Service.

Provides view-focused data for HR web routes.
"""

from .employee_web import hr_web_service
from .dashboard_web import people_dashboard_service
from .lifecycle_web import lifecycle_web_service
from .location_web import location_web_service

__all__ = [
    "hr_web_service",
    "people_dashboard_service",
    "lifecycle_web_service",
    "location_web_service",
]
