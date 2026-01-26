"""
Recruit Web Service - Base utilities and common functions.

Provides shared parsing, formatting, and view transformer utilities
for the recruitment web service layer.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional, TYPE_CHECKING, cast
from uuid import UUID

from app.models.people.recruit import (
    ApplicantStatus,
    InterviewStatus,
    JobOpeningStatus,
    OfferStatus,
)
from app.services.common import coerce_uuid

if TYPE_CHECKING:
    from app.models.people.recruit.job_opening import JobOpening
    from app.models.people.recruit.applicant import Applicant
    from app.models.people.recruit.interview import Interview
    from app.models.people.recruit.job_offer import JobOffer

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Parsing Utilities
# ─────────────────────────────────────────────────────────────────────────────


def parse_uuid(value: Optional[str]) -> Optional[UUID]:
    """Parse a string to UUID, returning None on failure."""
    if not value:
        return None
    try:
        return cast(Optional[UUID], coerce_uuid(value))
    except Exception:
        return None


def parse_date(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    """Parse a date string to datetime, optionally at end of day."""
    if not value:
        return None
    try:
        parsed = date.fromisoformat(value)
        if end_of_day:
            return datetime.combine(parsed, time.max)
        return datetime.combine(parsed, time.min)
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def parse_date_only(value: Optional[str]) -> Optional[date]:
    """Parse a date string to date object."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_int(value: Optional[str]) -> Optional[int]:
    """Parse a string to int, returning None on failure."""
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_decimal(value: Optional[str]) -> Optional[Decimal]:
    """Parse a string to Decimal, returning None on failure."""
    if not value:
        return None
    try:
        return Decimal(value)
    except Exception:
        return None


def parse_status(value: Optional[str], status_enum):
    """Parse a status string to enum, returning None on failure."""
    if not value:
        return None
    try:
        return status_enum(value)
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Formatting Utilities
# ─────────────────────────────────────────────────────────────────────────────


def format_date(d: Optional[date]) -> str:
    """Format a date for display."""
    if d is None:
        return ""
    return d.strftime("%Y-%m-%d")


def format_datetime(dt: Optional[datetime]) -> str:
    """Format a datetime for display."""
    if dt is None:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M")


def format_currency(amount: Optional[Decimal], currency_code: str = "NGN") -> str:
    """Format a currency amount for display."""
    if amount is None:
        return ""
    return f"{currency_code} {amount:,.2f}"


# ─────────────────────────────────────────────────────────────────────────────
# Status Labels
# ─────────────────────────────────────────────────────────────────────────────


def job_opening_status_label(status: JobOpeningStatus) -> dict:
    """Get display label and color for job opening status."""
    labels = {
        JobOpeningStatus.DRAFT: ("Draft", "gray"),
        JobOpeningStatus.OPEN: ("Open", "green"),
        JobOpeningStatus.ON_HOLD: ("On Hold", "yellow"),
        JobOpeningStatus.CLOSED: ("Closed", "blue"),
        JobOpeningStatus.CANCELLED: ("Cancelled", "red"),
    }
    label, color = labels.get(status, (status.value, "gray"))
    return {"text": label, "color": color}


def applicant_status_label(status: ApplicantStatus) -> dict:
    """Get display label and color for applicant status."""
    labels = {
        ApplicantStatus.NEW: ("New", "blue"),
        ApplicantStatus.SCREENING: ("Screening", "cyan"),
        ApplicantStatus.SHORTLISTED: ("Shortlisted", "yellow"),
        ApplicantStatus.INTERVIEW_SCHEDULED: ("Interview Scheduled", "purple"),
        ApplicantStatus.INTERVIEW_COMPLETED: ("Interview Completed", "blue"),
        ApplicantStatus.SELECTED: ("Selected", "green"),
        ApplicantStatus.OFFER_EXTENDED: ("Offer Extended", "orange"),
        ApplicantStatus.OFFER_ACCEPTED: ("Offer Accepted", "green"),
        ApplicantStatus.OFFER_DECLINED: ("Offer Declined", "red"),
        ApplicantStatus.HIRED: ("Hired", "green"),
        ApplicantStatus.REJECTED: ("Rejected", "red"),
        ApplicantStatus.WITHDRAWN: ("Withdrawn", "gray"),
    }
    label, color = labels.get(status, (status.value, "gray"))
    return {"text": label, "color": color}


def interview_status_label(status: InterviewStatus) -> dict:
    """Get display label and color for interview status."""
    labels = {
        InterviewStatus.SCHEDULED: ("Scheduled", "blue"),
        InterviewStatus.IN_PROGRESS: ("In Progress", "cyan"),
        InterviewStatus.COMPLETED: ("Completed", "green"),
        InterviewStatus.CANCELLED: ("Cancelled", "red"),
        InterviewStatus.NO_SHOW: ("No Show", "orange"),
        InterviewStatus.RESCHEDULED: ("Rescheduled", "yellow"),
    }
    label, color = labels.get(status, (status.value, "gray"))
    return {"text": label, "color": color}


def offer_status_label(status: OfferStatus) -> dict:
    """Get display label and color for offer status."""
    labels = {
        OfferStatus.DRAFT: ("Draft", "gray"),
        OfferStatus.PENDING_APPROVAL: ("Pending Approval", "yellow"),
        OfferStatus.APPROVED: ("Approved", "cyan"),
        OfferStatus.EXTENDED: ("Extended", "blue"),
        OfferStatus.ACCEPTED: ("Accepted", "green"),
        OfferStatus.DECLINED: ("Declined", "red"),
        OfferStatus.WITHDRAWN: ("Withdrawn", "orange"),
        OfferStatus.EXPIRED: ("Expired", "gray"),
    }
    label, color = labels.get(status, (status.value, "gray"))
    return {"text": label, "color": color}


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────


EMPLOYMENT_TYPES = ["FULL_TIME", "PART_TIME", "CONTRACT", "INTERN"]
PAY_FREQUENCIES = ["MONTHLY", "BI_WEEKLY", "WEEKLY"]
INTERVIEW_TYPES = ["IN_PERSON", "VIDEO", "PHONE"]
