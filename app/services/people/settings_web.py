"""
People Settings Web Service.

Provides context and update functions for HR/People settings UI pages.
"""
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance.core_org import Organization


# Payroll frequency options
PAYROLL_FREQUENCIES = [
    ("MONTHLY", "Monthly"),
    ("BIWEEKLY", "Bi-weekly"),
    ("WEEKLY", "Weekly"),
]

# Attendance mode options
ATTENDANCE_MODES = [
    ("MANUAL", "Manual entry"),
    ("BIOMETRIC", "Biometric device"),
    ("GEOFENCED", "Geofenced mobile"),
]

# Employee ID format placeholders
EMPLOYEE_ID_PLACEHOLDERS = [
    ("{PREFIX}", "Configurable prefix (e.g., EMP)"),
    ("{YYYY}", "4-digit year"),
    ("{YY}", "2-digit year"),
    ("{SEQ}", "Sequential number"),
    ("{SEQ:4}", "Sequential number with minimum 4 digits"),
]

# Leave year start month options (same as fiscal year months)
MONTHS = [
    (1, "January"),
    (2, "February"),
    (3, "March"),
    (4, "April"),
    (5, "May"),
    (6, "June"),
    (7, "July"),
    (8, "August"),
    (9, "September"),
    (10, "October"),
    (11, "November"),
    (12, "December"),
]

# Common timezone list (shared with finance)
COMMON_TIMEZONES = [
    ("UTC", "UTC"),
    ("America/New_York", "Eastern Time (US)"),
    ("America/Chicago", "Central Time (US)"),
    ("America/Denver", "Mountain Time (US)"),
    ("America/Los_Angeles", "Pacific Time (US)"),
    ("Europe/London", "London"),
    ("Europe/Paris", "Paris"),
    ("Europe/Berlin", "Berlin"),
    ("Asia/Tokyo", "Tokyo"),
    ("Asia/Shanghai", "Shanghai"),
    ("Asia/Singapore", "Singapore"),
    ("Australia/Sydney", "Sydney"),
    ("Africa/Lagos", "Lagos"),
    ("Africa/Johannesburg", "Johannesburg"),
]


class PeopleSettingsWebService:
    """Service for People/HR Settings UI."""

    # ========== HR Settings ==========

    async def get_hr_settings_context(
        self, db: AsyncSession, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get HR settings for editing."""
        result = await db.execute(
            select(Organization).where(
                Organization.organization_id == organization_id
            )
        )
        org = result.scalar_one_or_none()
        if not org:
            return {"organization": None, "error": "Organization not found"}

        return {
            "organization": org,
            "payroll_frequencies": PAYROLL_FREQUENCIES,
            "attendance_modes": ATTENDANCE_MODES,
            "months": MONTHS,
            "timezones": COMMON_TIMEZONES,
            "employee_id_placeholders": EMPLOYEE_ID_PLACEHOLDERS,
        }

    async def update_hr_settings(
        self,
        db: AsyncSession,
        organization_id: uuid.UUID,
        data: dict[str, Any],
    ) -> tuple[bool, Optional[str]]:
        """Update HR settings."""
        result = await db.execute(
            select(Organization).where(
                Organization.organization_id == organization_id
            )
        )
        org = result.scalar_one_or_none()
        if not org:
            return False, "Organization not found"

        # Update allowed HR fields
        allowed_fields = [
            "hr_employee_id_format",
            "hr_employee_id_prefix",
            "hr_payroll_frequency",
            "hr_leave_year_start_month",
            "hr_probation_days",
            "hr_attendance_mode",
            "timezone",  # Shared with finance but editable from HR
        ]

        for field in allowed_fields:
            if field in data:
                value = data[field]
                # Handle empty strings as None for optional fields
                if value == "":
                    value = None
                # Handle integer conversion for specific fields
                if field in ["hr_leave_year_start_month", "hr_probation_days"] and value:
                    try:
                        value = int(value)
                    except (ValueError, TypeError):
                        value = None
                setattr(org, field, value)

        await db.commit()
        return True, None

    # ========== Organization Profile (read-only for HR) ==========

    async def get_organization_context(
        self, db: AsyncSession, organization_id: uuid.UUID
    ) -> dict[str, Any]:
        """Get organization profile (read-only view for HR users)."""
        result = await db.execute(
            select(Organization).where(
                Organization.organization_id == organization_id
            )
        )
        org = result.scalar_one_or_none()
        if not org:
            return {"organization": None, "error": "Organization not found"}

        return {
            "organization": org,
        }


# Singleton instance
people_settings_web_service = PeopleSettingsWebService()
