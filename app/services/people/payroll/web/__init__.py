"""
Payroll Web Service - Modular web view services for payroll module.

Usage:
    from app.services.people.payroll.web import payroll_web_service
"""

from .base import (
    parse_uuid,
    parse_date,
    parse_int,
    parse_decimal,
    parse_bool,
    parse_component_type,
    parse_slip_status,
    parse_payroll_frequency,
    parse_entry_status,
    COMPONENT_TYPES,
    SLIP_STATUSES,
    PAYROLL_FREQUENCIES,
    ENTRY_STATUSES,
    DEFAULT_PAGE_SIZE,
)

from .component_web import ComponentWebService
from .slip_web import SlipWebService
from .structure_web import StructureWebService
from .run_web import RunWebService
from .tax_web import TaxWebService
from .report_web import ReportWebService


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
