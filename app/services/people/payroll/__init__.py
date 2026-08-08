"""
Payroll Services - People Module.

Provides salary slip management, GL integration, bulk payroll processing,
and NTA 2025 PAYE tax calculation.
"""

from app.services.people.payroll.paye_calculator import (
    PAYEBreakdown,
    PAYECalculator,
    TaxBandBreakdown,
    calculate_paye,
)
from app.services.people.payroll.payroll_gl_adapter import (
    PayrollGLAdapter,
    PayrollPostingResult,
)
from app.services.people.payroll.payroll_service import (
    PayrollService,
    PayrollServiceError,
)
from app.services.people.payroll.salary_slip_service import (
    SalarySlipInput,
    SalarySlipService,
    salary_slip_service,
)

__all__ = [
    # GL Adapter
    "PayrollGLAdapter",
    "PayrollPostingResult",
    # Salary Slip Service
    "SalarySlipInput",
    "SalarySlipService",
    "salary_slip_service",
    # Payroll Service
    "PayrollService",
    "PayrollServiceError",
    # PAYE Calculator (NTA 2025)
    "PAYECalculator",
    "PAYEBreakdown",
    "TaxBandBreakdown",
    "calculate_paye",
]

from app.services.setting_domain_declaration import ModuleSettingDomains  # noqa: E402

# Setting domain(s) this module owns — payroll runs and statutory rates.
# Validated by `app.services.setting_domains` at startup and at every write;
# see that module for why ownership lives here rather than in a central list.
SETTING_DOMAINS = ModuleSettingDomains(setting_domains=("payroll",))
