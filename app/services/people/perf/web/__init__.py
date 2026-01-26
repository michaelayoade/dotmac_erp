"""
Performance Web Service - Modular web view services for performance module.

Usage:
    from app.services.people.perf.web import perf_web_service
"""

from .base import (
    parse_uuid,
    parse_date,
    parse_int,
    parse_decimal,
    parse_appraisal_status,
    parse_kpi_status,
    parse_cycle_status,
    parse_bool,
    FEEDBACK_TYPES,
    KPI_MEASUREMENT_TYPES,
)

from .perf_web import PerfWebService
from .cycle_web import CycleWebService


class PerformanceWebService(
    PerfWebService,
    CycleWebService,
):
    """
    Unified Performance Web Service facade.

    Combines performance appraisal, feedback, goals, cycles, KRAs,
    templates, scorecards, and report web services into a single interface.
    """

    pass


# Module-level singleton
perf_web_service = PerformanceWebService()


__all__ = [
    # Utilities
    "parse_uuid",
    "parse_date",
    "parse_int",
    "parse_decimal",
    "parse_appraisal_status",
    "parse_kpi_status",
    "parse_cycle_status",
    "parse_bool",
    # Constants
    "FEEDBACK_TYPES",
    "KPI_MEASUREMENT_TYPES",
    # Services
    "PerfWebService",
    "CycleWebService",
    "PerformanceWebService",
    "perf_web_service",
]
