"""
Recruit Web Service - Modular web view services for recruitment module.

This module provides a facade that maintains a clean interface
while organizing code into resource-specific submodules:
- base.py - Common utilities and view transformers
- job_opening_web.py - Job opening-related methods
- applicant_web.py - Applicant-related methods
- interview_web.py - Interview-related methods
- offer_web.py - Job offer-related methods
- report_web.py - Recruitment report methods

Usage:
    from app.services.people.recruit.web import recruit_web_service
    # Or import the class:
    from app.services.people.recruit.web import RecruitWebService
"""

from .base import (
    # Parsing utilities
    parse_uuid,
    parse_date,
    parse_date_only,
    parse_int,
    parse_decimal,
    parse_status,
    # Formatting utilities
    format_date,
    format_datetime,
    format_currency,
    # Status labels
    job_opening_status_label,
    applicant_status_label,
    interview_status_label,
    offer_status_label,
    # Constants
    EMPLOYMENT_TYPES,
    PAY_FREQUENCIES,
    INTERVIEW_TYPES,
)

from .job_opening_web import JobOpeningWebService
from .applicant_web import ApplicantWebService
from .interview_web import InterviewWebService
from .offer_web import OfferWebService
from .report_web import ReportWebService


class RecruitWebService(
    JobOpeningWebService,
    ApplicantWebService,
    InterviewWebService,
    OfferWebService,
    ReportWebService,
):
    """
    Unified Recruit Web Service facade.

    Combines job opening, applicant, interview, offer, and report web services
    into a single interface for use in web routes.

    This class inherits from:
    - JobOpeningWebService: Job opening listing, creation, editing, status changes
    - ApplicantWebService: Applicant management and pipeline
    - InterviewWebService: Interview scheduling and feedback
    - OfferWebService: Job offer management
    - ReportWebService: Recruitment analytics and reports
    """

    pass


# Module-level singleton for use in routes
recruit_web_service = RecruitWebService()


__all__ = [
    # Parsing utilities
    "parse_uuid",
    "parse_date",
    "parse_date_only",
    "parse_int",
    "parse_decimal",
    "parse_status",
    # Formatting utilities
    "format_date",
    "format_datetime",
    "format_currency",
    # Status labels
    "job_opening_status_label",
    "applicant_status_label",
    "interview_status_label",
    "offer_status_label",
    # Constants
    "EMPLOYMENT_TYPES",
    "PAY_FREQUENCIES",
    "INTERVIEW_TYPES",
    # Service classes
    "JobOpeningWebService",
    "ApplicantWebService",
    "InterviewWebService",
    "OfferWebService",
    "ReportWebService",
    "RecruitWebService",
    # Singleton
    "recruit_web_service",
]
