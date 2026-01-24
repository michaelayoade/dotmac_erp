"""
Payroll Services - People Module.

Provides salary slip management, GL integration, bulk payroll processing,
and NTA 2025 PAYE tax calculation.
"""
from app.services.people.payroll.payroll_gl_adapter import (
    PayrollGLAdapter,
    PayrollPostingResult,
    payroll_gl_adapter,
)
from app.services.people.payroll.salary_slip_service import (
    SalarySlipInput,
    SalarySlipService,
    salary_slip_service,
)
from app.services.people.payroll.payroll_service import (
    PayrollService,
    PayrollServiceError,
)
from app.services.people.payroll.paye_calculator import (
    PAYECalculator,
    PAYEBreakdown,
    TaxBandBreakdown,
    calculate_paye,
)

__all__ = [
    # GL Adapter
    "PayrollGLAdapter",
    "PayrollPostingResult",
    "payroll_gl_adapter",
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
