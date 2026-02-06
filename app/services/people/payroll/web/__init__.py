"""
Payroll Web Service - Modular web view services for payroll module.

Usage:
    from app.services.people.payroll.web import payroll_web_service
"""

from .base import (
    COMPONENT_TYPES,
    DEFAULT_PAGE_SIZE,
    ENTRY_STATUSES,
    PAYROLL_FREQUENCIES,
    SLIP_STATUSES,
    parse_bool,
    parse_component_type,
    parse_date,
    parse_decimal,
    parse_entry_status,
    parse_int,
    parse_payroll_frequency,
    parse_slip_status,
    parse_uuid,
)
from .component_web import ComponentWebService
from .report_web import ReportWebService
from .run_web import RunWebService
from .slip_web import SlipWebService
from .structure_web import StructureWebService
from .tax_web import TaxWebService


class PayrollWebService(
    ComponentWebService,
    SlipWebService,
    StructureWebService,
    RunWebService,
    TaxWebService,
    ReportWebService,
):
    """
    Unified Payroll Web Service facade.

    Combines salary components, slips, structures, assignments,
    payroll runs, tax, and report web services into a single interface.
    """

    pass


# Module-level singleton
payroll_web_service = PayrollWebService()


__all__ = [
    # Utilities
    "parse_uuid",
    "parse_date",
    "parse_int",
    "parse_decimal",
    "parse_bool",
    "parse_component_type",
    "parse_slip_status",
    "parse_payroll_frequency",
    "parse_entry_status",
    # Constants
    "COMPONENT_TYPES",
    "SLIP_STATUSES",
    "PAYROLL_FREQUENCIES",
    "ENTRY_STATUSES",
    "DEFAULT_PAGE_SIZE",
    # Services
    "ComponentWebService",
    "SlipWebService",
    "StructureWebService",
    "RunWebService",
    "TaxWebService",
    "ReportWebService",
    "PayrollWebService",
    "payroll_web_service",
]
